"""
CLI entrypoint for the digest pipeline.
Called by GitHub Actions cron or manually.

Usage:
  python run_digest.py              # Process all users
  python run_digest.py --email x    # Process one user
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()


async def main(email: str | None = None) -> None:
    from db import get_all_users, get_user_by_email
    from digest import generate_digest_for_user
    from send_email import send_digest_email

    if email:
        users = [await get_user_by_email(email)]
        users = [u for u in users if u]
    else:
        users = await get_all_users()

    if not users:
        print("No users to process.")
        return

    print(f"Processing {len(users)} user(s)...")

    for user in users:
        try:
            items = await generate_digest_for_user(user)
            if items:
                await send_digest_email(user, items)
                print(f"  Sent digest to {user['email']} ({len(items)} items)")
            else:
                print(f"  No signals found for {user['email']}")
        except Exception as e:
            print(f"  Error processing {user['email']}: {e}", file=sys.stderr)

    print("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Scoop digest pipeline")
    parser.add_argument("--email", help="Process a specific user")
    args = parser.parse_args()
    asyncio.run(main(args.email))
