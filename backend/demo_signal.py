"""
Generate a Scoop demo signal for a specific company.
Used for LinkedIn outreach — run this to get a compelling signal
you can paste into a DM.

Usage:
  python demo_signal.py "Datadog" "observability platform"
  python demo_signal.py "Snowflake" "cloud data platform"
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from digest import generate_digest_preview


async def main():
    if len(sys.argv) < 3:
        print("Usage: python demo_signal.py <company> <what_prospect_sells>")
        print('Example: python demo_signal.py "Datadog" "observability platform"')
        sys.exit(1)

    company = sys.argv[1]
    product = sys.argv[2]

    print(f"Generating signal for {company} (prospect sells: {product})...\n")
    items = await generate_digest_preview([company], product)

    if not items:
        print("No signals found. Try a different company.")
        return

    for item in items:
        print(f"[{item['tag']}] {item['company']}")
        print(f"{item['headline']}")
        print(f"\nWhy this matters: {item['why']}")
        if item.get("suggested_action"):
            print(f"\n→ {item['suggested_action']}")
        print()

    # Also print a ready-to-paste LinkedIn version
    item = items[0]
    print("--- LINKEDIN READY ---")
    print(f"""I ran a quick scan on {item['company']}:

{item['headline']}

{item['why']}

This is what Scoop sends every Monday for your top accounts. Free, 2 min setup: https://noptus.github.io/scoop""")


if __name__ == "__main__":
    asyncio.run(main())
