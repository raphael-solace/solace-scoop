"""
CLI entrypoint for the digest pipeline.

Usage:
  python run_digest.py                     # Research + send (all users)
  python run_digest.py --email x@y.com     # Research + send (one user)
  python run_digest.py --research-only     # Research and save to DB only
  python run_digest.py --send-only         # Send last saved digest only

Auto-detect: if run on Sunday, defaults to --research-only.
             if run on Monday, defaults to --send-only.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date

from dotenv import load_dotenv

load_dotenv()


async def main(
    email: str | None = None,
    mode: str = "both",
) -> None:
    from db import get_all_users, get_user_by_email, save_digest, get_last_digest
    from digest import generate_digest_for_user
    from send_email import send_digest_email

    if email:
        user = await get_user_by_email(email)
        users = [user] if user else []
    else:
        users = await get_all_users()

    if not users:
        print("No users to process.")
        return

    print(f"Processing {len(users)} user(s), mode={mode}...")

    for user in users:
        try:
            if mode in ("both", "research-only"):
                print(f"\n  Researching {user['email']}...")
                items = await generate_digest_for_user(user)
                if items:
                    await save_digest(user["id"], items)
                    print(f"  Saved {len(items)} signals to DB")
                else:
                    print(f"  No signals found for {user['email']}")
                    continue

            if mode in ("both", "send-only"):
                if mode == "send-only":
                    # Load last saved digest from DB
                    digest = await get_last_digest(user["id"])
                    if not digest:
                        print(f"  No saved digest for {user['email']}, skipping send")
                        continue
                    items = digest.get("items", [])
                    if not items:
                        print(f"  Empty digest for {user['email']}, skipping send")
                        continue

                await send_digest_email(user, items)
                print(f"  Sent digest to {user['email']} ({len(items)} items)")

        except Exception as e:
            print(f"  Error processing {user['email']}: {e}", file=sys.stderr)

    print("\nDone.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Scoop digest pipeline")
    parser.add_argument("--email", help="Process a specific user")
    parser.add_argument("--research-only", action="store_true", help="Research and save to DB only")
    parser.add_argument("--send-only", action="store_true", help="Send last saved digest only")
    args = parser.parse_args()

    if args.research_only:
        mode = "research-only"
    elif args.send_only:
        mode = "send-only"
    else:
        # Auto-detect by day of week
        today = date.today()
        if today.weekday() == 6:  # Sunday
            mode = "research-only"
            print("Auto-detected Sunday: research-only mode")
        elif today.weekday() == 0:  # Monday
            mode = "send-only"
            print("Auto-detected Monday: send-only mode")
        else:
            mode = "both"

    asyncio.run(main(args.email, mode))
