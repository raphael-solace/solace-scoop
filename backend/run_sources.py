"""Seed and poll the reviewed official-source catalog.

Usage:
  python run_sources.py --seed
  python run_sources.py --poll-global
  python run_sources.py --discover https://company.example/newsroom
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

from config import cfg

CATALOG_PATH = Path(__file__).with_name("source_catalog.yaml")


def load_catalog() -> list[dict]:
    data = yaml.safe_load(CATALOG_PATH.read_text()) or {}
    return data.get("sources", [])


async def discover(url: str) -> None:
    import json
    import httpx
    from sources import discover_official_sources

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        response = await client.get(
            url, headers={"User-Agent": "Solace Scoop/1.0 (raphael.caillon@solace.com)"}
        )
    response.raise_for_status()
    print(json.dumps(discover_official_sources(response.text, str(response.url)), indent=2))


async def seed() -> None:
    from db import seed_source_catalog

    entries = load_catalog()
    count = await seed_source_catalog(entries)
    print(f"Seeded {count} source catalog entries")


async def poll_global() -> None:
    if not cfg.get("sources", {}).get("enabled", False):
        print("Official source ingestion disabled in config; no polling performed.")
        return
    from db import (
        classify_company_source_items,
        get_enabled_company_sources,
        match_global_source_items,
        get_enabled_global_sources,
        seed_source_catalog,
        update_global_source_fetch,
        upsert_source_items,
    )
    from sources import (
        fetch_ashby_jobs,
        fetch_feed,
        fetch_greenhouse_jobs,
        fetch_lever_jobs,
        fetch_sec_submissions,
        fetch_smartrecruiters_jobs,
        serialize_source_item,
    )

    await seed_source_catalog(load_catalog())
    sources = await get_enabled_global_sources()
    total = failures = 0
    supported = {"rss", "atom", "sec_submissions"}
    unsupported = [source["name"] for source in sources if source["adapter_type"] not in supported]
    if unsupported:
        raise RuntimeError(f"Enabled source adapter(s) not implemented: {', '.join(unsupported)}")
    for source in sources:
        config = source.get("config") or {}
        if isinstance(config, str):
            import json
            config = json.loads(config)
        try:
            items, metadata = await fetch_feed(
                source["endpoint_template"],
                config.get("etag"),
                config.get("last_modified"),
            )
            serialized = [serialize_source_item(item) for item in items]
            total += await upsert_source_items(str(source["id"]), serialized)
            await update_global_source_fetch(
                str(source["id"]), metadata.get("etag"),
                metadata.get("last_modified"),
            )
            print(f"[{source['name']}] {len(serialized)} item(s)")
        except Exception as exc:
            failures += 1
            await update_global_source_fetch(
                str(source["id"]), config.get("etag"),
                config.get("last_modified"), type(exc).__name__,
            )
            print(f"[{source['name']}] failed: {type(exc).__name__}")
    company_adapters = {
        "sec_submissions": fetch_sec_submissions,
        "greenhouse_jobs": fetch_greenhouse_jobs,
        "lever_jobs": fetch_lever_jobs,
        "ashby_jobs": fetch_ashby_jobs,
        "smartrecruiters_jobs": fetch_smartrecruiters_jobs,
    }
    for adapter_name, fetcher in company_adapters.items():
        for source in await get_enabled_company_sources(adapter_name):
            try:
                items = await fetcher(source["external_id"])
                serialized = [serialize_source_item(item) for item in items]
                for item in serialized:
                    item["source_item_id"] = (
                        f"{source['external_id']}:{item['source_item_id']}"
                    )
                total += await upsert_source_items(
                    str(source["source_id"]), serialized,
                    company_id=str(source["company_id"]),
                )
                print(
                    f"[{adapter_name}:{source['canonical_name']}] "
                    f"{len(serialized)} item(s)"
                )
            except Exception as exc:
                failures += 1
                print(
                    f"[{adapter_name}:{source['canonical_name']}] "
                    f"failed: {type(exc).__name__}"
                )

    matching = await match_global_source_items()
    classification = await classify_company_source_items()
    print(
        f"Official source poll complete: stored={total} "
        f"matched={matching['linked']} ambiguous={matching['ambiguous']} "
        f"classified={classification['classified']} "
        f"ignored={classification['ignored']} failures={failures}"
    )
    if failures:
        raise RuntimeError(f"{failures} official source adapter(s) failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run official source ingestion")
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--poll-global", action="store_true")
    parser.add_argument("--discover")
    args = parser.parse_args()
    if args.discover:
        asyncio.run(discover(args.discover))
    elif args.seed:
        asyncio.run(seed())
    elif args.poll_global:
        asyncio.run(poll_global())
    else:
        parser.error("choose --seed or --poll-global")
