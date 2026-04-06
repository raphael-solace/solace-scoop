"""
Scoop — Email delivery via Gmail SMTP

Uses a Gmail account with an App Password to send digests.
No external dependencies beyond the standard library.

Setup:
  1. Go to myaccount.google.com > Security > 2-Step Verification (enable it)
  2. Go to myaccount.google.com > Security > App passwords
  3. Create an app password for "Mail"
  4. Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD in .env

To swap to another provider (Resend, SendGrid, SES), just change
the send_raw_email() function.
"""

from __future__ import annotations

import asyncio
import os
import smtplib
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
GMAIL_DISPLAY_NAME = os.getenv("GMAIL_DISPLAY_NAME", "Scoop 🐶🗞️")


def send_raw_email(to: str, subject: str, html: str) -> None:
    """Send an email via Gmail SMTP. Runs synchronously."""
    msg = MIMEMultipart("alternative")
    msg["From"] = f"{GMAIL_DISPLAY_NAME} <{GMAIL_ADDRESS}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, to, msg.as_string())


async def send_digest_email(user: dict, items: list[dict]) -> None:
    """Send a rendered digest email to a user."""
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print(f"  [dry-run] Would send {len(items)} items to {user['email']}")
        return

    html = render_digest(user, items)
    subject = f"🐶🗞️ {len(items)} signals this week"

    # Run SMTP in a thread so we don't block the event loop
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, send_raw_email, user["email"], subject, html)


async def send_welcome_email(email: str) -> None:
    """Send a short welcome email after signup."""
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print(f"  [dry-run] Would send welcome to {email}")
        return

    name = email.split("@")[0].title()
    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="margin:0; padding:0; background:#f8fafc; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;">
<tr><td align="center" style="padding:32px 16px;">
<table width="560" cellpadding="0" cellspacing="0" style="background:#fff; border-radius:12px; overflow:hidden;">
  <tr><td style="padding:32px;">
    <p style="margin:0 0 16px; font-size:24px;">🐶🗞️</p>
    <p style="margin:0 0 16px; font-size:16px; font-weight:700; color:#0f172a;">Welcome to Scoop, {name}!</p>
    <p style="margin:0 0 16px; font-size:15px; line-height:1.6; color:#475569;">
      Your first digest arrives <strong>Monday at 7am</strong>. We'll cover all the accounts you listed
      and tell you exactly why each signal matters for your deals.
    </p>
    <p style="margin:0; font-size:15px; line-height:1.6; color:#475569;">
      That's it. No login, no dashboard. Just open your email on Monday morning.
    </p>
  </td></tr>
  <tr><td style="padding:16px 32px; background:#f8fafc; border-top:1px solid #f1f5f9;">
    <p style="margin:0; font-size:13px; color:#94a3b8; text-align:center;">Reply "stop" to unsubscribe anytime.</p>
  </td></tr>
</table>
</td></tr></table>
</body></html>"""

    subject = "🐶🗞️ Welcome to Scoop!"
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, send_raw_email, email, subject, html)


def render_digest(user: dict, items: list[dict]) -> str:
    """Build the HTML digest email."""
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
        is_risk = item.get("risk_or_opportunity", "") in ("risk", "both")

        # Compact tag line: [Tag] [RISK] Company
        risk_badge = ' <span style="font-size:10px; font-weight:700; color:#dc2626;">⚠ RISK</span>' if is_risk else ""

        # Why + window merged into one short block
        why_text = item.get("why", "")
        window = item.get("window", "")
        if window:
            why_text += f" <em style='color:#64748b;'>({window})</em>"

        # Action line
        action_html = ""
        if item.get("suggested_action"):
            action_html = f'<p style="margin:6px 0 0; font-size:12px; color:#6366f1; font-weight:600;">→ {item["suggested_action"]}</p>'

        # Opening line
        opener_html = ""
        if item.get("opening_line"):
            opener_html = f'<p style="margin:6px 0 0; font-size:12px; color:#64748b;">💬 <em>"{item["opening_line"]}"</em></p>'

        items_html += f"""
        <tr><td style="padding:16px 24px; border-bottom:1px solid #f1f5f9;">
          <p style="margin:0 0 4px; font-size:11px;"><span style="display:inline; font-weight:600; padding:2px 6px; border-radius:100px; background:{colors['bg']}; color:{colors['fg']}; text-transform:uppercase; letter-spacing:0.04em;">{item['tag']}</span>{risk_badge}</p>
          <p style="margin:0 0 6px; font-size:14px; font-weight:700; color:#0f172a;">{item['company']}</p>
          <p style="margin:0 0 8px; font-size:13px; line-height:1.5; color:#475569;">{item['headline']}</p>
          <p style="margin:0; font-size:12px; line-height:1.5; color:#0f172a; background:#eef2ff; padding:8px 12px; border-radius:4px; border-left:3px solid #6366f1;">{why_text}</p>
          {action_html}
          {opener_html}
        </td></tr>"""

    company_count = len(user.get("companies", []))
    user_name = user["email"].split("@")[0].title()

    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0; padding:0; background:#f8fafc; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;">
<tr><td align="center" style="padding:24px 12px;">
<table width="560" cellpadding="0" cellspacing="0" style="background:#fff; border-radius:10px; overflow:hidden;">
  <tr><td style="padding:16px 24px; border-bottom:1px solid #f1f5f9; background:#f8fafc;">
    <span style="font-size:14px; font-weight:700; color:#0f172a;">🐶🗞️ Your Scoop</span>
    <span style="font-size:12px; color:#94a3b8; float:right;">{today.strftime('%b %d, %Y')}</span>
  </td></tr>
  <tr><td style="padding:16px 24px 8px;">
    <p style="margin:0; font-size:13px; color:#475569;">Hi {user_name}, {len(items)} signals this week. Reply to ask follow-up questions.</p>
  </td></tr>
  {items_html}
  <tr><td style="padding:12px 24px; background:#f8fafc; border-top:1px solid #f1f5f9; text-align:center;">
    <p style="margin:0; font-size:11px; color:#94a3b8;">Tracking {company_count} accounts · Next: {next_monday.strftime('%b %d')} · Reply "stop" to unsubscribe</p>
  </td></tr>
</table>
</td></tr></table>
</body></html>"""
