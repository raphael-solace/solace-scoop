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
    subject = f"🐶🗞️ Your Scoop: {len(items)} signals this week"

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
        items_html += f"""
        <tr><td style="padding:24px 32px; border-bottom:1px solid #f1f5f9;">
          <table cellpadding="0" cellspacing="0" style="margin-bottom:8px;">
            <tr>
              <td><span style="display:inline-block; font-size:11px; font-weight:600; padding:3px 8px; border-radius:100px; text-transform:uppercase; letter-spacing:0.04em; background-color:{colors['bg']}; color:{colors['fg']};">{item['tag']}</span></td>
              <td style="padding-left:8px;"><span style="font-size:15px; font-weight:600; color:#0f172a;">{item['company']}</span></td>
            </tr>
          </table>
          <p style="margin:0 0 12px; font-size:15px; line-height:1.6; color:#475569;">{item['headline']}</p>
          <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:8px;">
            <tr><td style="background:#eef2ff; padding:12px 16px; border-radius:6px; border-left:3px solid #6366f1;">
              <p style="margin:0; font-size:14px; line-height:1.6; color:#0f172a;"><strong>Why this matters:</strong> {item['why']}</p>
            </td></tr>
          </table>
          {"<p style='margin:0; font-size:13px; line-height:1.5; color:#6366f1; font-weight:600;'>→ " + item['suggested_action'] + "</p>" if item.get('suggested_action') else ""}
        </td></tr>"""

    company_count = len(user.get("companies", []))
    user_name = user["email"].split("@")[0].title()

    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0; padding:0; background:#f8fafc; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;">
<tr><td align="center" style="padding:32px 16px;">
<table width="600" cellpadding="0" cellspacing="0" style="background:#fff; border-radius:12px; overflow:hidden;">
  <tr><td style="padding:24px 32px; border-bottom:1px solid #f1f5f9; background:#f8fafc;">
    <span style="font-size:24px;">🐶🗞️</span><br>
    <span style="font-size:16px; font-weight:700; color:#0f172a;">Your Weekly Scoop</span><br>
    <span style="font-size:13px; color:#94a3b8;">Week of {today.strftime('%B %d, %Y')}</span>
  </td></tr>
  <tr><td style="padding:24px 32px 0;">
    <p style="margin:0; font-size:15px; line-height:1.6; color:#475569;">Hi {user_name}, here are the {len(items)} most important things at your accounts this week.</p>
  </td></tr>
  {items_html}
  <tr><td style="padding:20px 32px; background:#f8fafc; border-top:1px solid #f1f5f9; text-align:center;">
    <p style="margin:0; font-size:13px; color:#94a3b8;">Tracking {company_count} accounts · Next digest: {next_monday.strftime('%B %d')}</p>
    <p style="margin:8px 0 0; font-size:12px; color:#cbd5e1;">Reply "stop" to unsubscribe.</p>
  </td></tr>
</table>
</td></tr></table>
</body></html>"""
