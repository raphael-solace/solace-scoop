"""Official-source ingestion helpers for the shared company signal pool.

Adapters store public metadata, bounded excerpts, timestamps and direct links.
They never store full third-party article bodies.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import urljoin
import httpx
from defusedxml import ElementTree

SEC_USER_AGENT = os.getenv(
    "SEC_USER_AGENT", "Solace Scoop admin=raphael.caillon@solace.com"
)


@dataclass
class SourceItem:
    source_item_id: str
    url: str
    title: str
    summary: str = ""
    published_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    raw: dict | None = None

    @property
    def content_hash(self) -> str:
        value = f"{self.title}|{self.summary}|{self.url}"
        return hashlib.sha256(value.encode()).hexdigest()


def _plain_text(value: str | None, limit: int = 1000) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    return " ".join(html.unescape(text).split())[:limit]


def _parse_datetime(value: str | None) -> Optional[datetime]:
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            result = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result


class _SourceDiscoveryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.feeds: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        href = values.get("href", "")
        if tag.lower() == "link" and "alternate" in values.get("rel", "").lower() \
                and values.get("type", "").lower() in {
                    "application/rss+xml", "application/atom+xml"
                }:
            self.feeds.append(href)
        if tag.lower() == "a" and href:
            self.links.append(href)


def discover_official_sources(content: str, page_url: str) -> dict:
    """Discover advertised feeds and public ATS identifiers from first-party HTML."""
    parser = _SourceDiscoveryParser()
    parser.feed(content)
    feeds = sorted({urljoin(page_url, href) for href in parser.feeds if href})
    careers = []
    patterns = [
        ("greenhouse_board", r"boards\.greenhouse\.io/([^/?#]+)"),
        ("lever_site", r"jobs(?:\.eu)?\.lever\.co/([^/?#]+)"),
        ("ashby_board", r"jobs\.ashbyhq\.com/([^/?#]+)"),
        ("smartrecruiters_company", r"careers\.smartrecruiters\.com/([^/?#]+)"),
    ]
    for href in parser.links:
        absolute = urljoin(page_url, href)
        for scheme, pattern in patterns:
            match = re.search(pattern, absolute, re.I)
            if match:
                careers.append({"scheme": scheme, "value": match.group(1), "url": absolute})
    deduped = {(row["scheme"], row["value"]): row for row in careers}
    return {"feeds": feeds, "careers": list(deduped.values())}


def parse_feed(content: bytes, base_url: str) -> list[SourceItem]:
    """Parse RSS 2.0 or Atom using only bounded public metadata."""
    root = ElementTree.fromstring(content)
    items: list[SourceItem] = []
    if root.tag.endswith("feed"):
        for entry in root.findall("{*}entry"):
            link = next(
                (node.get("href", "") for node in entry.findall("{*}link")
                 if node.get("rel", "alternate") == "alternate"),
                "",
            )
            title = (entry.findtext("{*}title") or "").strip()
            item_id = (entry.findtext("{*}id") or link or title).strip()
            summary = (entry.findtext("{*}summary") or entry.findtext("{*}content") or "").strip()
            if title and link:
                items.append(SourceItem(
                    item_id, urljoin(base_url, link), title, _plain_text(summary),
                    _parse_datetime(entry.findtext("{*}published")),
                    _parse_datetime(entry.findtext("{*}updated")),
                ))
    else:
        for entry in root.findall(".//item"):
            link = (entry.findtext("link") or "").strip()
            title = (entry.findtext("title") or "").strip()
            item_id = (entry.findtext("guid") or link or title).strip()
            summary = (entry.findtext("description") or "").strip()
            if title and link:
                items.append(SourceItem(
                    item_id, urljoin(base_url, link), title, _plain_text(summary),
                    _parse_datetime(entry.findtext("pubDate")), None,
                ))
    return items


async def fetch_feed(
    url: str, etag: str | None = None, last_modified: str | None = None
) -> tuple[list[SourceItem], dict]:
    headers = {"User-Agent": "Solace Scoop/1.0 (raphael.caillon@solace.com)"}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        response = await client.get(url, headers=headers)
    if response.status_code == 304:
        return [], {"not_modified": True, "etag": etag, "last_modified": last_modified}
    response.raise_for_status()
    return parse_feed(response.content, str(response.url)), {
        "etag": response.headers.get("etag"),
        "last_modified": response.headers.get("last-modified"),
    }


async def _get_json(url: str, *, headers: dict | None = None) -> dict | list:
    request_headers = {
        "User-Agent": "Solace Scoop/1.0 (raphael.caillon@solace.com)",
        **(headers or {}),
    }
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        response = await client.get(url, headers=request_headers)
    response.raise_for_status()
    return response.json()


async def fetch_greenhouse_jobs(board_token: str) -> list[SourceItem]:
    payload = await _get_json(
        f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true"
    )
    items = []
    for job in payload.get("jobs", []):
        url = job.get("absolute_url", "")
        title = job.get("title", "")
        if not url or not title:
            continue
        location = (job.get("location") or {}).get("name", "")
        summary = f"{title}" + (f" — {location}" if location else "")
        items.append(SourceItem(
            str(job["id"]), url, title, summary,
            _parse_datetime(job.get("first_published")),
            _parse_datetime(job.get("updated_at")), job,
        ))
    return items


async def fetch_lever_jobs(site: str, *, eu: bool = False) -> list[SourceItem]:
    host = "api.eu.lever.co" if eu else "api.lever.co"
    payload = await _get_json(f"https://{host}/v0/postings/{site}?mode=json")
    items = []
    for job in payload if isinstance(payload, list) else []:
        url = job.get("hostedUrl", "")
        title = job.get("text", "")
        if not url or not title:
            continue
        categories = job.get("categories") or {}
        location = categories.get("location", "")
        summary = f"{title}" + (f" — {location}" if location else "")
        items.append(SourceItem(str(job["id"]), url, title, summary, raw=job))
    return items


async def fetch_ashby_jobs(board_name: str) -> list[SourceItem]:
    payload = await _get_json(
        f"https://api.ashbyhq.com/posting-api/job-board/{board_name}"
    )
    items = []
    for job in payload.get("jobs", []):
        url = job.get("jobUrl", "")
        title = job.get("title", "")
        if not url or not title:
            continue
        location = job.get("location", "")
        summary = f"{title}" + (f" — {location}" if location else "")
        items.append(SourceItem(
            str(job.get("id") or url), url, title, summary,
            _parse_datetime(job.get("publishedAt")), None, job,
        ))
    return items


async def fetch_smartrecruiters_jobs(company_identifier: str) -> list[SourceItem]:
    items = []
    offset = 0
    max_jobs = 1000
    jobs: list[dict] = []
    while offset < max_jobs:
        payload = await _get_json(
            f"https://api.smartrecruiters.com/v1/companies/{company_identifier}/postings"
            f"?limit=100&offset={offset}"
        )
        page = payload.get("content", [])
        jobs.extend(page)
        if len(page) < 100 or len(jobs) >= int(payload.get("totalFound", 0) or 0):
            break
        offset += len(page)
    for job in jobs:
        url = job.get("ref", "") or job.get("applyUrl", "")
        title = job.get("name", "")
        if not url or not title:
            continue
        location_data = job.get("location") or {}
        location = ", ".join(
            value for value in (
                location_data.get("city"), location_data.get("region"),
                location_data.get("country"),
            ) if value
        )
        summary = f"{title}" + (f" — {location}" if location else "")
        items.append(SourceItem(
            str(job.get("id") or url), url, title, summary,
            _parse_datetime(job.get("releasedDate")), None, job,
        ))
    return items


async def fetch_sec_submissions(cik: str) -> list[SourceItem]:
    """Fetch official EDGAR filing metadata for a validated ten-digit CIK."""
    normalized = str(cik).zfill(10)
    if not normalized.isdigit() or len(normalized) != 10:
        raise ValueError("SEC CIK must be a ten-digit number")
    url = f"https://data.sec.gov/submissions/CIK{normalized}.json"
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, headers={"User-Agent": SEC_USER_AGENT})
    response.raise_for_status()
    payload = response.json()
    recent = payload.get("filings", {}).get("recent", {})
    fields = [
        "accessionNumber", "filingDate", "reportDate", "acceptanceDateTime",
        "form", "items", "primaryDocument", "primaryDocDescription",
    ]
    count = len(recent.get("accessionNumber", []))
    items: list[SourceItem] = []
    bare_cik = str(int(normalized))
    for index in range(count):
        row = {field: (recent.get(field) or [""] * count)[index] for field in fields}
        accession = row["accessionNumber"]
        compact = accession.replace("-", "")
        primary = row.get("primaryDocument") or ""
        filing_url = (
            f"https://www.sec.gov/Archives/edgar/data/{bare_cik}/{compact}/{primary}"
            if primary else
            f"https://www.sec.gov/Archives/edgar/data/{bare_cik}/{compact}/{accession}-index.html"
        )
        form = row.get("form", "")
        reported_items = row.get("items", "")
        title = f"{payload.get('name', normalized)} filed {form}"
        if reported_items:
            title += f" ({reported_items})"
        items.append(SourceItem(
            accession, filing_url, title,
            (row.get("primaryDocDescription") or "")[:1000],
            _parse_datetime(row.get("acceptanceDateTime")) or _parse_datetime(row.get("filingDate")),
            None, row,
        ))
    return items


def serialize_source_item(item: SourceItem) -> dict:
    return {
        "source_item_id": item.source_item_id,
        "url": item.url,
        "title": item.title,
        "summary": item.summary,
        "published_at": item.published_at,
        "updated_at": item.updated_at,
        "content_hash": item.content_hash,
        "raw": item.raw or {},
    }
