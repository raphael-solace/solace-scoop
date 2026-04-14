"""
Scoop - Email delivery via Gmail SMTP

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

from config import cfg

GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
GMAIL_DISPLAY_NAME = os.getenv("GMAIL_DISPLAY_NAME", "Scoop 🐶🗞️")


ALLOWED_RECIPIENTS = os.getenv("ALLOWED_RECIPIENTS", "").strip()


def send_raw_email(to: str, subject: str, html: str) -> None:
    """Send an email via Gmail SMTP. Runs synchronously."""
    if ALLOWED_RECIPIENTS != "*":
        allowed = [r.lower().strip() for r in ALLOWED_RECIPIENTS.split(",") if r.strip()]
        if not allowed or to.lower().strip() not in allowed:
            print(f"  [BLOCKED] Email to {to} blocked — not in ALLOWED_RECIPIENTS")
            return

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
    subject = cfg["email"]["subject_template"].format(count=len(items))

    # Run SMTP in a thread so we don't block the event loop
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, send_raw_email, user["email"], subject, html)


async def send_welcome_email(email: str) -> None:
    """Send a short welcome email after signup."""
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print(f"  [dry-run] Would send welcome to {email}")
        return

    name = email.split("@")[0].title()
    br = cfg["email"]["branding"]
    em = cfg["email"]
    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="margin:0; padding:0; background:#f8fafc; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;">
<tr><td align="center" style="padding:32px 16px;">
<table width="560" cellpadding="0" cellspacing="0" style="background:#fff; border-radius:12px; overflow:hidden;">
  <tr><td style="padding:16px 24px; background:{br['header_bg']};">
    <img src="{br['header_logo']}" alt="Solace" style="height:22px; display:inline-block; vertical-align:middle; filter:brightness(0) invert(1);"><span style="font-size:12px; font-weight:700; color:{br['badge_color']}; letter-spacing:0.08em; vertical-align:middle; margin-left:8px;">{br['badge_text']}</span>
  </td></tr>
  <tr><td style="padding:32px;">
    <p style="margin:0 0 16px; font-size:16px; font-weight:700; color:#0f172a;">Welcome to Scoop, {name}!</p>
    <p style="margin:0 0 16px; font-size:15px; line-height:1.6; color:#475569;">
      Your first digest arrives <strong>{em['first_digest_time']}</strong>. We'll cover all the accounts you listed
      with champion updates, EDA signals, partner activity, and competitive intel.
    </p>
    <p style="margin:0; font-size:15px; line-height:1.6; color:#475569;">
      That's it. No login, no dashboard. Just open your email on Monday morning.
    </p>
  </td></tr>
  <tr><td style="padding:14px 24px; background:{br['header_bg']}; text-align:center;">
    <p style="margin:0; font-size:11px; color:rgba(255,255,255,0.5);">{em['footer_text']}</p>
  </td></tr>
</table>
</td></tr></table>
</body></html>"""

    subject = em["welcome_subject"]
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, send_raw_email, email, subject, html)


async def send_otp_email(email: str, code: str) -> None:
    """Send a 6-digit OTP code to the user."""
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print(f"  [dry-run] OTP for {email}: {code}")
        return

    br = cfg["email"]["branding"]
    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="margin:0; padding:0; background:#f8fafc; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;">
<tr><td align="center" style="padding:32px 16px;">
<table width="560" cellpadding="0" cellspacing="0" style="background:#fff; border-radius:12px; overflow:hidden;">
  <tr><td style="padding:16px 24px; background:{br['header_bg']};">
    <img src="{br['header_logo']}" alt="Solace" style="height:22px; display:inline-block; vertical-align:middle; filter:brightness(0) invert(1);"><span style="font-size:12px; font-weight:700; color:{br['badge_color']}; letter-spacing:0.08em; vertical-align:middle; margin-left:8px;">{br['badge_text']}</span>
  </td></tr>
  <tr><td style="padding:32px; text-align:center;">
    <p style="margin:0 0 16px; font-size:16px; font-weight:700; color:#0f172a;">Your sign-in code</p>
    <p style="margin:0 0 24px; font-size:40px; font-weight:700; color:#093B5F; letter-spacing:0.2em; font-family:monospace;">{code}</p>
    <p style="margin:0; font-size:13px; color:#94a3b8;">This code expires in 10 minutes. If you didn't request it, just ignore this email.</p>
  </td></tr>
  <tr><td style="padding:14px 24px; background:{br['header_bg']}; text-align:center;">
    <p style="margin:0; font-size:11px; color:rgba(255,255,255,0.5);">{cfg['email']['footer_text']}</p>
  </td></tr>
</table>
</td></tr></table>
</body></html>"""

    subject = f"Scoop sign-in code: {code}"
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

        # Source link
        source_url = item.get("source_url", "")
        if not source_url:
            sources = item.get("sources", [])
            if sources:
                source_url = sources[0] if isinstance(sources[0], str) else ""
        source_link = ""
        if source_url:
            source_domain = source_url.split("//")[-1].split("/")[0].replace("www.", "")
            source_link = f' <a href="{source_url}" style="color:#6366f1; text-decoration:none; font-size:12px;">{source_domain}</a>'

        # Header row: Company - Tag - Link
        header_html = f'<span style="font-weight:700; color:#0f172a; font-size:14px;">{item["company"]}</span>'
        header_html += f' <span style="font-size:11px; font-weight:600; padding:2px 6px; border-radius:100px; background:{colors["bg"]}; color:{colors["fg"]}; text-transform:uppercase; letter-spacing:0.04em; vertical-align:middle;">{item["tag"]}</span>'
        if source_link:
            header_html += f' {source_link}'

        # Headline (one sentence)
        headline_html = item.get("headline", "")

        # Why paragraph
        why_text = item.get("why", "")
        window = item.get("window", "")
        if window:
            why_text += f" ({window})"

        # Scoop thinks section
        scoop_html = ""
        action = item.get("suggested_action", "")
        opener = item.get("opening_line", "")
        if action or opener:
            scoop_parts = ""
            if action:
                scoop_parts += f'<p style="margin:0 0 4px; font-size:12px; color:#0f172a;">→ {action}</p>'
            if opener:
                scoop_parts += f'<p style="margin:0; font-size:12px; color:#64748b;">💬 <em>"{opener}"</em></p>'
            scoop_html = f"""
            <div style="margin-top:10px; padding:10px 12px; background:#f0f0ff; border-radius:6px; border-left:3px solid #6366f1;">
              <p style="margin:0 0 6px; font-size:10px; font-weight:700; color:#6366f1; text-transform:uppercase; letter-spacing:0.08em;">Scoop thinks</p>
              {scoop_parts}
            </div>"""

        items_html += f"""
        <tr><td style="padding:16px 24px; border-bottom:1px solid #f1f5f9;">
          <p style="margin:0 0 8px;">{header_html}</p>
          <p style="margin:0 0 6px; font-size:13px; line-height:1.5; color:#0f172a;">{headline_html}</p>
          <p style="margin:0; font-size:12px; line-height:1.6; color:#64748b;">{why_text}</p>
          {scoop_html}
        </td></tr>"""

    company_count = len(user.get("companies", []))
    user_name = user["email"].split("@")[0].title()
    br = cfg["email"]["branding"]
    footer = cfg["email"]["footer_text"]

    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0; padding:0; background:#f8fafc; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;">
<tr><td align="center" style="padding:24px 12px;">
<table width="560" cellpadding="0" cellspacing="0" style="background:#fff; border-radius:10px; overflow:hidden;">
  <!-- Branded header -->
  <tr><td style="padding:16px 24px; background:{br['header_bg']}; border-bottom:none;">
    <table width="100%" cellpadding="0" cellspacing="0"><tr>
      <td><img src="{br['header_logo']}" alt="Solace" style="height:22px; display:inline-block; vertical-align:middle; filter:brightness(0) invert(1);"><span style="font-size:12px; font-weight:700; color:{br['badge_color']}; letter-spacing:0.08em; vertical-align:middle; margin-left:8px;">{br['badge_text']}</span></td>
      <td style="text-align:right;"><span style="font-size:12px; color:rgba(255,255,255,0.6);">{today.strftime('%b %d, %Y')}</span></td>
    </tr></table>
  </td></tr>
  <tr><td style="padding:16px 24px 8px;">
    <p style="margin:0; font-size:13px; color:#475569;">Hi {user_name}, {len(items)} signal{'s' if len(items) != 1 else ''} this week across your accounts.</p>
  </td></tr>
  {items_html}
  <tr><td style="padding:14px 24px; background:{br['header_bg']}; text-align:center;">
    <p style="margin:0; font-size:11px; color:rgba(255,255,255,0.5);">Tracking {company_count} account{'s' if company_count != 1 else ''} · Next digest: {next_monday.strftime('%b %d')} · {footer}</p>
  </td></tr>
</table>
</td></tr></table>
</body></html>"""
