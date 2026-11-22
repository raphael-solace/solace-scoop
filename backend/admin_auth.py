"""Self-contained authentication for the read-only admin API."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import os
import secrets
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Callable

ADMIN_TOKEN_VERSION = 1
ADMIN_TOKEN_TTL_SECONDS = 30 * 60
MAX_USERNAME_LENGTH = 128
MAX_PASSWORD_LENGTH = 1024
MAX_TOKEN_LENGTH = 2048

_SCRYPT_N = 16384
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_MAXMEM = 64 * 1024 * 1024
_TOKEN_HEADER = {"alg": "HS256", "typ": "SCOOP-ADMIN", "v": ADMIN_TOKEN_VERSION}
_TOKEN_HEADER_KEYS = frozenset(_TOKEN_HEADER)
_TOKEN_CLAIM_KEYS = frozenset({"v", "sub", "iat", "exp", "nonce"})


class AdminConfigurationError(RuntimeError):
    """Raised when required admin authentication configuration is unsafe."""


class InvalidAdminToken(ValueError):
    """Raised when an admin bearer token is invalid or expired."""


@dataclass(frozen=True)
class AdminConfig:
    username: str
    salt: bytes
    password_digest: bytes
    session_secret: bytes


def _decode_base64url(value: str, *, min_bytes: int, max_bytes: int) -> bytes:
    if not value or "=" in value or len(value) > max_bytes * 2:
        raise ValueError("invalid base64url value")
    if any(not (char.isalnum() or char in "-_") for char in value):
        raise ValueError("invalid base64url value")
    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid base64url value") from exc
    if not min_bytes <= len(decoded) <= max_bytes or _encode_base64url(decoded) != value:
        raise ValueError("invalid base64url value")
    return decoded


def _encode_base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _load_config() -> AdminConfig:
    username = os.getenv("ADMIN_USERNAME", "")
    password_hash = os.getenv("ADMIN_PASSWORD_HASH", "")
    session_secret_text = os.getenv("ADMIN_SESSION_SECRET", "")

    try:
        username_bytes = username.encode("utf-8")
        session_secret = session_secret_text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise AdminConfigurationError("Admin authentication is not configured") from exc

    if (
        not username
        or len(username) > MAX_USERNAME_LENGTH
        or len(username_bytes) > MAX_USERNAME_LENGTH * 4
        or not 32 <= len(session_secret) <= 4096
    ):
        raise AdminConfigurationError("Admin authentication is not configured")

    parts = password_hash.split("$")
    if parts[:5] != [
        "scrypt",
        "v=1",
        f"n={_SCRYPT_N}",
        f"r={_SCRYPT_R}",
        f"p={_SCRYPT_P}",
    ] or len(parts) != 7:
        raise AdminConfigurationError("Admin authentication is not configured")
    try:
        salt = _decode_base64url(parts[5], min_bytes=16, max_bytes=64)
        password_digest = _decode_base64url(parts[6], min_bytes=32, max_bytes=64)
    except ValueError as exc:
        raise AdminConfigurationError("Admin authentication is not configured") from exc

    return AdminConfig(username, salt, password_digest, session_secret)


def verify_admin_credentials(username: str, password: str) -> bool:
    """Verify configured credentials while always taking the scrypt path."""
    config = _load_config()
    if not isinstance(username, str) or not isinstance(password, str):
        username = ""
        password = ""

    username_bytes = username.encode("utf-8", errors="replace")
    password_bytes = password.encode("utf-8", errors="replace")
    bounded = (
        len(username) <= MAX_USERNAME_LENGTH
        and len(username_bytes) <= MAX_USERNAME_LENGTH * 4
        and len(password) <= MAX_PASSWORD_LENGTH
        and len(password_bytes) <= MAX_PASSWORD_LENGTH * 4
    )
    if not bounded:
        username_bytes = b""
        password_bytes = b""

    username_matches = secrets.compare_digest(
        username_bytes, config.username.encode("utf-8")
    )
    candidate_digest = hashlib.scrypt(
        password_bytes,
        salt=config.salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        maxmem=_SCRYPT_MAXMEM,
        dklen=len(config.password_digest),
    )
    password_matches = secrets.compare_digest(candidate_digest, config.password_digest)
    return bounded and username_matches and password_matches


def issue_admin_token(username: str, *, now: float | None = None) -> str:
    """Issue a fixed-format, 30-minute HMAC-SHA256 admin bearer token."""
    config = _load_config()
    if not secrets.compare_digest(
        username.encode("utf-8", errors="replace"), config.username.encode("utf-8")
    ):
        raise InvalidAdminToken("Invalid admin subject")

    issued_at = int(time.time() if now is None else now)
    header = _encode_base64url(
        json.dumps(_TOKEN_HEADER, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    claims = {
        "exp": issued_at + ADMIN_TOKEN_TTL_SECONDS,
        "iat": issued_at,
        "nonce": _encode_base64url(secrets.token_bytes(16)),
        "sub": config.username,
        "v": ADMIN_TOKEN_VERSION,
    }
    payload = _encode_base64url(
        json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signed = f"{header}.{payload}".encode("ascii")
    signature = hmac.new(config.session_secret, signed, hashlib.sha256).digest()
    return f"{header}.{payload}.{_encode_base64url(signature)}"


def validate_admin_token(token: str, *, now: float | None = None) -> dict:
    """Strictly validate an admin token and return its non-secret claims."""
    config = _load_config()
    if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LENGTH:
        raise InvalidAdminToken("Invalid admin token")
    parts = token.split(".")
    if len(parts) != 3:
        raise InvalidAdminToken("Invalid admin token")

    header_part, payload_part, signature_part = parts
    signed = f"{header_part}.{payload_part}".encode("ascii", errors="replace")
    expected_signature = hmac.new(config.session_secret, signed, hashlib.sha256).digest()
    signature_valid = False
    try:
        supplied_signature = _decode_base64url(
            signature_part, min_bytes=32, max_bytes=32
        )
    except ValueError:
        supplied_signature = bytes(32)
    signature_valid = secrets.compare_digest(supplied_signature, expected_signature)

    try:
        header_raw = _decode_base64url(header_part, min_bytes=1, max_bytes=256)
        payload_raw = _decode_base64url(payload_part, min_bytes=1, max_bytes=1024)
        header = json.loads(header_raw)
        claims = json.loads(payload_raw)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        raise InvalidAdminToken("Invalid admin token") from None

    current_time = int(time.time() if now is None else now)
    valid_shape = (
        isinstance(header, dict)
        and frozenset(header) == _TOKEN_HEADER_KEYS
        and header == _TOKEN_HEADER
        and isinstance(claims, dict)
        and frozenset(claims) == _TOKEN_CLAIM_KEYS
        and claims.get("v") == ADMIN_TOKEN_VERSION
        and isinstance(claims.get("sub"), str)
        and isinstance(claims.get("iat"), int)
        and not isinstance(claims.get("iat"), bool)
        and isinstance(claims.get("exp"), int)
        and not isinstance(claims.get("exp"), bool)
        and isinstance(claims.get("nonce"), str)
    )
    if not valid_shape:
        raise InvalidAdminToken("Invalid admin token")

    try:
        nonce = _decode_base64url(claims["nonce"], min_bytes=16, max_bytes=16)
    except ValueError:
        nonce = b""
    subject_valid = secrets.compare_digest(
        claims["sub"].encode("utf-8", errors="replace"),
        config.username.encode("utf-8"),
    )
    timestamps_valid = (
        claims["exp"] == claims["iat"] + ADMIN_TOKEN_TTL_SECONDS
        and claims["iat"] <= current_time + 5
        and claims["exp"] > current_time
    )
    if not (signature_valid and subject_valid and timestamps_valid and len(nonce) == 16):
        raise InvalidAdminToken("Invalid admin token")
    return claims


@dataclass
class _FailureState:
    failures: deque[float] = field(default_factory=deque)
    locked_until: float = 0.0
    last_seen: float = 0.0


class LoginRateLimiter:
    """Bounded process-local failed-login limiter keyed by direct client IP."""

    def __init__(
        self,
        *,
        max_failures: int = 5,
        window_seconds: int = 15 * 60,
        lockout_seconds: int = 15 * 60,
        max_entries: int = 2048,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.lockout_seconds = lockout_seconds
        self.max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[bytes, _FailureState] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def _key(username: str, direct_ip: str) -> bytes:
        del username
        return hashlib.sha256(direct_ip[:256].encode("utf-8", errors="replace")).digest()

    def _trim_failures(self, state: _FailureState, now: float) -> None:
        cutoff = now - self.window_seconds
        while state.failures and state.failures[0] <= cutoff:
            state.failures.popleft()
        if state.locked_until <= now:
            state.locked_until = 0.0

    def _prune_and_bound(self, now: float) -> None:
        for key, state in list(self._entries.items()):
            self._trim_failures(state, now)
            if not state.failures and not state.locked_until:
                del self._entries[key]
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def retry_after(self, username: str, direct_ip: str) -> int:
        now = self._clock()
        key = self._key(username, direct_ip)
        with self._lock:
            self._prune_and_bound(now)
            state = self._entries.get(key)
            if not state:
                return 0
            self._trim_failures(state, now)
            state.last_seen = now
            self._entries.move_to_end(key)
            return max(0, math.ceil(state.locked_until - now))

    def record_failure(self, username: str, direct_ip: str) -> int:
        now = self._clock()
        key = self._key(username, direct_ip)
        with self._lock:
            self._prune_and_bound(now)
            state = self._entries.setdefault(key, _FailureState())
            self._trim_failures(state, now)
            state.failures.append(now)
            state.last_seen = now
            if len(state.failures) >= self.max_failures:
                state.failures.clear()
                state.locked_until = now + self.lockout_seconds
            self._entries.move_to_end(key)
            self._prune_and_bound(now)
            return max(0, math.ceil(state.locked_until - now))

    def record_success(self, username: str, direct_ip: str) -> None:
        with self._lock:
            self._entries.pop(self._key(username, direct_ip), None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    @property
    def entry_count(self) -> int:
        with self._lock:
            return len(self._entries)


login_rate_limiter = LoginRateLimiter()
