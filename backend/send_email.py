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
    """Send a rendered digest email to a user.

    In review mode (ALLOWED_RECIPIENTS is a single email, not *),
    all digests are redirected to the reviewer with a prefix showing
    who the digest was originally for.
    """
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print(f"  [dry-run] Would send {len(items)} items to {user['email']}")
        return

    html = render_digest(user, items)
    recipient = user["email"]
    subject = cfg["email"]["subject_template"].format(count=len(items))

    # Review mode: redirect all emails to the single allowed recipient
    if ALLOWED_RECIPIENTS != "*" and ALLOWED_RECIPIENTS:
        reviewer = ALLOWED_RECIPIENTS.split(",")[0].strip()
        if reviewer and recipient.lower() != reviewer.lower():
            user_name = recipient.split("@")[0].replace(".", " ").title()
            subject = f"[{user_name}] {subject}"
            recipient = reviewer

    # Run SMTP in a thread so we don't block the event loop
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, send_raw_email, recipient, subject, html)


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


def _esc(s: str) -> str:
    """HTML-escape a string."""
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def render_digest(user: dict, items: list[dict]) -> str:
    """Build the HTML digest email."""
    today = date.today()
    next_monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    br = cfg["email"]["branding"]
    footer = cfg["email"]["footer_text"]

    tag_colors = {
        "red": {"bg": "#fef2f2", "fg": "#ef4444"},
        "green": {"bg": "#ecfdf5", "fg": "#10b981"},
        "amber": {"bg": "#fffbeb", "fg": "#f59e0b"},
        "blue": {"bg": "#eff6ff", "fg": "#3b82f6"},
    }

    items_html = ""
    for item in items:
        colors = tag_colors.get(item.get("tag_color", "blue"), tag_colors["blue"])

        # Source
        source_url = item.get("source_url", "")
        if not source_url:
            sources = item.get("sources", [])
            if sources and isinstance(sources[0], str):
                source_url = sources[0]
        source_html = ""
        if source_url:
            domain = source_url.split("//")[-1].split("/")[0].replace("www.", "")
            source_html = f'<a href="{_esc(source_url)}" style="color:#6366f1; text-decoration:underline; font-size:11px;">{_esc(domain)}</a>'

        # Date
        date_html = ""
        if item.get("date"):
            try:
                d = date.fromisoformat(item["date"])
                date_html = f'<span style="font-size:11px; color:#94a3b8; margin-left:6px;">{d.strftime("%b %d")}</span>'
            except ValueError:
                pass

        # Signal strength indicator
        strength = item.get("signal_strength", "")
        strength_dot = ""
        try:
            s = int(strength)
            if s >= 5:
                strength_dot = '<span style="font-size:9px; color:#ef4444; vertical-align:middle; margin-left:4px;" title="High impact">&#9679;&#9679;&#9679;</span>'
            elif s >= 4:
                strength_dot = '<span style="font-size:9px; color:#f59e0b; vertical-align:middle; margin-left:4px;" title="Notable">&#9679;&#9679;</span>'
            elif s >= 3:
                strength_dot = '<span style="font-size:9px; color:#10b981; vertical-align:middle; margin-left:4px;" title="Relevant">&#9679;</span>'
        except (ValueError, TypeError):
            pass

        # Company + tag + strength + date header line
        header = f'<span style="font-weight:700; font-size:15px; color:#0f172a;">{_esc(item.get("company", ""))}</span>'
        header += f' <span style="font-size:10px; font-weight:600; padding:2px 6px; border-radius:100px; background:{colors["bg"]}; color:{colors["fg"]}; text-transform:uppercase; letter-spacing:0.04em; vertical-align:middle;">{_esc(item.get("tag", ""))}</span>'
        header += strength_dot
        if date_html:
            header += date_html

        # Headline
        headline = _esc(item.get("headline", ""))

        # Source link on its own line (survives forwarding)
        source_line = ""
        if source_url:
            src_short = source_url.replace("https://www.", "").replace("https://", "")
            if len(src_short) > 60:
                src_short = src_short[:57] + "..."
            source_line = f'<p style="margin:4px 0 0;"><a href="{_esc(source_url)}" style="color:#6366f1; text-decoration:underline; font-size:10px;">{_esc(src_short)}</a></p>'

        # So what (new field) / fallback to why
        so_what = _esc(item.get("so_what", item.get("why", "")))

        # Contact card (if signal recommends a person)
        contact_html = ""
        contact_name = item.get("contact_name", "")
        if contact_name:
            c_title = _esc(item.get("contact_title", ""))
            c_company = _esc(item.get("company", ""))
            c_linkedin = item.get("contact_linkedin", "")

            # If no direct LinkedIn URL, build a search link
            if not c_linkedin:
                import urllib.parse
                search_q = urllib.parse.quote_plus(f"{contact_name} {item.get('company', '')}")
                c_linkedin = f"https://www.linkedin.com/search/results/people/?keywords={search_q}"

            # Show short LinkedIn URL as visible text so it survives email forwarding
            li_short = c_linkedin.replace("https://www.", "").replace("https://", "")
            if len(li_short) > 50:
                li_short = li_short[:47] + "..."

            contact_html = f"""
            <div style="margin-top:8px; padding:8px 12px; background:#f8fafb; border:1px solid #e2e8e6; border-radius:6px;">
              <p style="margin:0; font-size:12px; font-weight:700; color:#093B5F;">{_esc(contact_name)}</p>
              <p style="margin:1px 0 0; font-size:10px; color:#64748b;">{c_title} at {c_company}</p>
              <p style="margin:4px 0 0;"><a href="{_esc(c_linkedin)}" style="color:#0077B5; text-decoration:underline; font-size:10px;">{_esc(li_short)}</a></p>
            </div>"""

        # Ready-to-send message (new field) / fallback to opening_line
        message = item.get("message", item.get("opening_line", ""))
        message_html = ""
        if message:
            message_html = f"""
            <div style="margin-top:8px; padding:8px 10px; background:#f0f0ff; border-radius:6px; border-left:2px solid #6366f1;">
              <p style="margin:0 0 3px; font-size:9px; font-weight:700; color:#6366f1; text-transform:uppercase; letter-spacing:0.06em;">READY TO SEND</p>
              <p style="margin:0; font-size:12px; color:#475569; line-height:1.5; font-style:italic;">"{_esc(message)}"</p>
            </div>"""

        items_html += f"""
        <tr><td style="padding:20px 24px; border-bottom:1px solid #f1f5f9;">
          <p style="margin:0 0 8px;">{header}</p>
          <p style="margin:0 0 4px; font-size:14px; line-height:1.5; color:#0f172a;">{headline}</p>
          {source_line}
          <p style="margin:6px 0 0; font-size:12px; line-height:1.6; color:#64748b;">{so_what}</p>
          {contact_html}
          {message_html}
        </td></tr>"""

    company_count = len(user.get("companies", []))
    user_name = user["email"].split("@")[0].replace(".", " ").title()

    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0; padding:0; background:#f8fafc; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;">
<tr><td align="center" style="padding:24px 12px;">
<table width="600" cellpadding="0" cellspacing="0" style="background:#fff; border-radius:12px; overflow:hidden; box-shadow:0 4px 24px rgba(9,59,95,0.06);">

  <!-- Header -->
  <tr><td style="padding:20px 24px; background:{br['header_bg']};">
    <table width="100%" cellpadding="0" cellspacing="0"><tr>
      <td><img src="{br['header_logo']}" alt="Solace" style="height:24px; vertical-align:middle; filter:brightness(0) invert(1);"><span style="font-size:13px; font-weight:700; color:{br['badge_color']}; letter-spacing:0.08em; vertical-align:middle; margin-left:10px;">{br['badge_text']}</span></td>
      <td style="text-align:right;"><span style="font-size:12px; color:rgba(255,255,255,0.5);">{today.strftime('%b %d, %Y')}</span></td>
    </tr></table>
  </td></tr>

  <!-- Greeting -->
  <tr><td style="padding:20px 24px 12px;">
    <p style="margin:0 0 4px; font-family:'Instrument Serif',Georgia,serif; font-size:24px; color:#093B5F;">Hi {user_name}</p>
    <p style="margin:0; font-size:13px; color:#94a3b8;">{len(items)} signal{'s' if len(items) != 1 else ''} across {company_count} account{'s' if company_count != 1 else ''} this week</p>
  </td></tr>

  {items_html}

  <!-- Footer -->
  <tr><td style="padding:20px 24px; background:{br['header_bg']}; text-align:center;">
    <p style="margin:0 0 4px; font-size:11px; color:rgba(255,255,255,0.4);">Next digest: {next_monday.strftime('%b %d')} · {footer}</p>
    <p style="margin:0; font-size:10px; color:rgba(255,255,255,0.25);">Manage your accounts at solace-scoop</p>
  </td></tr>

</table>
</td></tr></table>
</body></html>"""
