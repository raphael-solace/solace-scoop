#!/usr/bin/env python3
"""Generate the initial admin credentials without putting the password in argv."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys


SCRYPT_N = 16_384
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 64


def urlsafe_b64_no_padding(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    return (
        f"scrypt$v=1$n={SCRYPT_N}$r={SCRYPT_R}$p={SCRYPT_P}$"
        f"{urlsafe_b64_no_padding(salt)}${urlsafe_b64_no_padding(digest)}"
    )


def write_credentials(path: Path, username: str, password: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as credential_file:
        credential_file.write(f"ADMIN_USERNAME={username}\n")
        credential_file.write(f"ADMIN_PASSWORD={password}\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate Scoop admin credentials. The Secrets Manager JSON is written "
            "to stdout; the one-time login is written to a mode-0600 file."
        )
    )
    parser.add_argument(
        "--credential-output",
        type=Path,
        required=True,
        help="new file in which to save the generated username and password",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    username = "admin"
    password = secrets.token_urlsafe(32)
    secret_value = {
        "ADMIN_USERNAME": username,
        "ADMIN_PASSWORD_HASH": password_hash(password),
        "ADMIN_SESSION_SECRET": secrets.token_urlsafe(48),
    }

    try:
        write_credentials(args.credential_output, username, password)
    except FileExistsError:
        print(
            f"Refusing to overwrite credential file: {args.credential_output}",
            file=sys.stderr,
        )
        return 1

    json.dump(secret_value, sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
