"""
Scoop — Email delivery via Resend

Renders the digest into HTML and sends it.
Swap Resend for any SMTP/API provider by changing this file.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import httpx

RESEND_KEY = os.getenv("RESEND_KEY", "")
RESEND_API = "https://api.resend.com/emails"
FROM_EMAIL = os.getenv("FROM_EMAIL", "Scoop <digest@getscoop.io>")


async def send_digest_email(user: dict, items: list[dict]) -> None:
    """Send a rendered digest email to a user."""
    if not RESEND_KEY:
        print(f"  [dry-run] Would send {len(items)} items to {user['email']}")
        return

    html = _render_digest(user, items)
    subject = f"Your Scoop — {len(items)} signals this week"

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            RESEND_API,
            headers={
                "Authorization": f"Bearer {RESEND_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": FROM_EMAIL,
                "to": [user["email"]],
                "subject": subject,
                "html": html,
            },
        )
        resp.raise_for_status()


def _render_digest(user: dict, items: list[dict]) -> str:
    """Build the HTML email from the template."""
    today = date.today()
    next_monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)

    tag_colors = {
        "red": {"bg": "#fef2f2", "fg": "#ef4444"},
        "green": {"bg": "#ecfdf5", "fg": "#10b981"},
        "amber": {"bg": "#fffbeb", "fg": "#f59e0b"},
        "blue": {"bg": "#eff6ff", "fg": "#3b82f6"},
    }

    items_html = ""
    for item in items:
        colors = tag_colors.get(item.get("tag_color", "blue"), tag_colors["blue"])
        items_html += f"""
        <tr><td style="padding:24px 32px; border-bottom:1px solid #f1f5f9;">
          <table role="presentation" cellpadding="0" cellspacing="0" style="margin-bottom:8px;">
            <tr>
              <td><span style="display:inline-block; font-size:11px; font-weight:600; padding:3px 8px; border-radius:100px; text-transform:uppercase; letter-spacing:0.04em; background-color:{colors['bg']}; color:{colors['fg']};">{item['tag']}</span></td>
              <td style="padding-left:8px;"><span style="font-size:15px; font-weight:600; color:#0f172a;">{item['company']}</span></td>
            </tr>
          </table>
          <p style="margin:0 0 12px; font-size:15px; line-height:1.6; color:#475569;">{item['headline']}</p>
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
            <tr><td style="background-color:#eef2ff; padding:12px 16px; border-radius:6px; border-left:3px solid #6366f1;">
              <p style="margin:0; font-size:14px; line-height:1.6; color:#0f172a;"><strong>Why this matters:</strong> {item['why']}</p>
            </td></tr>
          </table>
        </td></tr>"""

    company_count = len(user.get("companies", []))
    user_name = user["email"].split("@")[0].title()

    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0; padding:0; background-color:#f8fafc; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f8fafc;">
<tr><td align="center" style="padding:32px 16px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="background-color:#ffffff; border-radius:12px; overflow:hidden;">
  <tr><td style="padding:24px 32px; border-bottom:1px solid #f1f5f9; background-color:#f8fafc;">
    <span style="font-size:16px; font-weight:700; color:#0f172a;">Your Weekly Scoop</span><br>
    <span style="font-size:13px; color:#94a3b8;">Week of {today.strftime('%B %d, %Y')}</span>
  </td></tr>
  <tr><td style="padding:24px 32px 0;">
    <p style="margin:0; font-size:15px; line-height:1.6; color:#475569;">Hi {user_name} — here are the {len(items)} most important things at your accounts this week.</p>
  </td></tr>
  {items_html}
  <tr><td style="padding:20px 32px; background-color:#f8fafc; border-top:1px solid #f1f5f9; text-align:center;">
    <p style="margin:0; font-size:13px; color:#94a3b8;">Tracking {company_count} accounts · Next digest: {next_monday.strftime('%B %d')}</p>
  </td></tr>
</table>
</td></tr></table>
</body></html>"""
