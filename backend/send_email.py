"""
Scoop - Email delivery via SMTP

Supports Office 365 (primary) and Gmail (fallback).
Set SMTP_ADDRESS and SMTP_PASSWORD in .env to use Office 365.
Falls back to GMAIL_ADDRESS / GMAIL_APP_PASSWORD if SMTP vars are not set.
"""

from __future__ import annotations

import asyncio
import os
import smtplib
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import cfg

# Primary: Office 365
SMTP_ADDRESS = os.getenv("SMTP_ADDRESS", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

# Fallback: Gmail
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")

DISPLAY_NAME = os.getenv("SMTP_DISPLAY_NAME", os.getenv("GMAIL_DISPLAY_NAME", "Scoop"))

ALLOWED_RECIPIENTS = os.getenv("ALLOWED_RECIPIENTS", "").strip()

APP_URL = os.getenv("APP_URL", "")
API_URL = os.getenv("API_URL", "")
CRON_SECRET = os.getenv("CRON_SECRET", "")


def _feedback_url(digest_id: str, idx: int, vote: str) -> str:
    """Generate an HMAC-signed feedback URL for a signal."""
    import hashlib, hmac as _hmac
    base = API_URL or APP_URL
    if not base or not CRON_SECRET:
        return ""
    sig = _hmac.new(CRON_SECRET.encode(), f"{digest_id}:{idx}:{vote}".encode(), hashlib.sha256).hexdigest()
    return f"{base}/api/feedback?digest_id={digest_id}&idx={idx}&vote={vote}&sig={sig}"


def send_raw_email(to: str, subject: str, html: str) -> None:
    """Send an email via Office 365 (primary) or Gmail (fallback)."""
    if ALLOWED_RECIPIENTS != "*":
        allowed = [r.lower().strip() for r in ALLOWED_RECIPIENTS.split(",") if r.strip()]
        if not allowed or to.lower().strip() not in allowed:
            print(f"  [BLOCKED] Email to {to} blocked — not in ALLOWED_RECIPIENTS")
            return

    from_addr = SMTP_ADDRESS or GMAIL_ADDRESS
    msg = MIMEMultipart("alternative")
    msg["From"] = f"{DISPLAY_NAME} <{from_addr}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(html, "html"))

    if SMTP_ADDRESS and SMTP_PASSWORD:
        with smtplib.SMTP("smtp.office365.com", 587, timeout=30) as server:
            server.starttls()
            server.login(SMTP_ADDRESS, SMTP_PASSWORD)
            server.sendmail(SMTP_ADDRESS, to, msg.as_string())
    else:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, to, msg.as_string())


async def send_digest_email(user: dict, items: list[dict], digest_id: str = "") -> None:
    """Send a rendered digest email to a user.

    In review mode (ALLOWED_RECIPIENTS is a single email, not *),
    all digests are redirected to the reviewer with a prefix showing
    who the digest was originally for.
    """
    if not (SMTP_ADDRESS and SMTP_PASSWORD) and not (GMAIL_ADDRESS and GMAIL_APP_PASSWORD):
        print(f"  [dry-run] Would send {len(items)} items to {user['email']}")
        return

    html = render_digest(user, items, digest_id=digest_id)
    recipient = user["email"]

    # Build subject: "X Scoops this week: Company A, Company B"
    companies = []
    for item in items:
        c = item.get("company", "")
        if c and c not in companies:
            companies.append(c)
    companies_str = ", ".join(c.title() if c.islower() else c for c in companies)
    subject = f"{len(items)} Scoops this week: {companies_str}"

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


async def send_breaking_scoop_email(user: dict, item: dict, digest_id: str = "") -> None:
    """Send a single breaking scoop alert for a critical signal."""
    if not (SMTP_ADDRESS and SMTP_PASSWORD) and not (GMAIL_ADDRESS and GMAIL_APP_PASSWORD):
        print(f"  [dry-run] Would send breaking scoop to {user['email']}")
        return

    company = item.get("company", "")
    headline = item.get("headline", "")
    so_what = _esc(item.get("so_what", item.get("why", "")))
    contact_name = item.get("contact_name", "")
    contact_title = _esc(item.get("contact_title", ""))
    contact_linkedin = item.get("contact_linkedin", "")
    message = item.get("message", item.get("opening_line", ""))
    source_url = item.get("source_url", "")
    if not source_url:
        sources = item.get("sources", [])
        if sources and isinstance(sources[0], str):
            source_url = sources[0]

    br = cfg["email"]["branding"]

    # Source link
    source_html = ""
    if source_url:
        src_short = source_url.replace("https://www.", "").replace("https://", "")
        if len(src_short) > 60:
            src_short = src_short[:57] + "..."
        source_html = f'<a href="{_esc(source_url)}" style="color:#6366f1; text-decoration:underline; font-size:11px;">{_esc(src_short)}</a>'

    # Contact
    contact_html = ""
    if contact_name:
        li_html = ""
        if contact_linkedin and "/in/" in contact_linkedin:
            li_html = f' &middot; <a href="{_esc(contact_linkedin)}" style="color:#0077B5; text-decoration:underline; font-size:11px;">LinkedIn</a>'
        contact_html = f'<p style="margin:12px 0 0; font-size:12px; color:#093B5F; font-weight:600;">{_esc(contact_name)}, {contact_title}{li_html}</p>'

    # Message
    message_html = ""
    if message:
        li_action = _linkedin_url(item)
        mail_action = _mailto_url(item)
        li_label = "Message on LinkedIn" if "/in/" in (contact_linkedin or "") else "Find on LinkedIn"
        action_buttons = f"""
          <a href="{_esc(li_action)}" style="display:inline-block; margin-top:8px; margin-right:8px; padding:6px 12px; background:#0077B5; color:#ffffff; font-size:11px; font-weight:600; text-decoration:none; border-radius:5px;">{li_label} &rarr;</a>"""
        if mail_action:
            action_buttons += f"""
          <a href="{_esc(mail_action)}" style="display:inline-block; margin-top:8px; padding:6px 12px; background:#093B5F; color:#ffffff; font-size:11px; font-weight:600; text-decoration:none; border-radius:5px;">Draft email &rarr;</a>"""
        message_html = f"""
        <div style="margin-top:12px; padding:10px 12px; background:#fef2f2; border-radius:6px; border-left:3px solid #ef4444;">
          <p style="margin:0 0 3px; font-size:9px; font-weight:700; color:#ef4444; text-transform:uppercase; letter-spacing:0.06em;">SUGGESTED OUTREACH</p>
          <p style="margin:0; font-size:12px; color:#475569; line-height:1.5; font-style:italic;">"{_esc(message)}"</p>
          {action_buttons}
        </div>"""

    # Feedback
    feedback_html = ""
    if digest_id:
        up_url = _feedback_url(digest_id, 0, "up")
        down_url = _feedback_url(digest_id, 0, "down")
        if up_url:
            feedback_html = f"""
        <div style="margin-top:12px; padding-top:8px; border-top:1px solid #fee2e2;">
          <a href="{up_url}" style="font-size:11px; color:#10b981; text-decoration:none; margin-right:16px;">&#x1F44D; Useful</a>
          <a href="{down_url}" style="font-size:11px; color:#94a3b8; text-decoration:none;">&#x1F44E; Not relevant</a>
        </div>"""

    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0; padding:0; background:#fef2f2; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#fef2f2;">
<tr><td align="center" style="padding:24px 12px;">
<table width="560" cellpadding="0" cellspacing="0" style="background:#fff; border-radius:12px; overflow:hidden; box-shadow:0 4px 24px rgba(239,68,68,0.08);">
  <tr><td style="padding:16px 24px; background:#7f1d1d;">
    <table width="100%"><tr>
      <td><span style="font-size:13px; font-weight:700; color:#fecaca; letter-spacing:0.08em;">BREAKING SCOOP</span></td>
      <td style="text-align:right;"><span style="font-size:11px; color:rgba(255,255,255,0.5);">{_esc(item.get('date', ''))}</span></td>
    </tr></table>
  </td></tr>
  <tr><td style="padding:24px;">
    <p style="margin:0 0 4px; font-size:11px; font-weight:600; color:#ef4444; text-transform:uppercase; letter-spacing:0.04em;">{_esc(company)}</p>
    <p style="margin:0 0 12px; font-size:16px; font-weight:700; line-height:1.4; color:#0f172a;">{_esc(headline)}</p>
    {source_html}
    <p style="margin:12px 0 0; font-size:13px; line-height:1.6; color:#475569;">{so_what}</p>
    {contact_html}
    {message_html}
    {feedback_html}
  </td></tr>
  <tr><td style="padding:14px 24px; background:#7f1d1d; text-align:center;">
    <p style="margin:0; font-size:10px; color:rgba(255,255,255,0.4);">{cfg['email']['footer_text']}</p>
  </td></tr>
</table>
</td></tr></table>
</body></html>"""

    subject = f"Breaking: {company} — {headline[:60]}"
    recipient = user["email"]

    if ALLOWED_RECIPIENTS != "*" and ALLOWED_RECIPIENTS:
        reviewer = ALLOWED_RECIPIENTS.split(",")[0].strip()
        if reviewer and recipient.lower() != reviewer.lower():
            recipient = reviewer

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, send_raw_email, recipient, subject, html)


async def send_brand_monitor_email(recipients: list[str], mentions: list[dict]) -> None:
    """Send 'Solace in the Wild' daily mentions digest."""
    if not (SMTP_ADDRESS and SMTP_PASSWORD) and not (GMAIL_ADDRESS and GMAIL_APP_PASSWORD):
        print(f"  [dry-run] Would send {len(mentions)} mentions to {recipients}")
        return

    br = cfg["email"]["branding"]
    today = date.today()

    # Build mentions HTML
    mentions_html = ""
    type_colors = {
        "news": "#3b82f6", "blog": "#8b5cf6", "social": "#06b6d4",
        "competitive_comparison": "#f59e0b", "customer_reference": "#10b981",
        "analyst": "#6366f1", "conference": "#ec4899",
    }
    sentiment_icons = {
        "positive": "&#x2705;", "neutral": "&#x2796;",
        "negative": "&#x26A0;", "competitive_threat": "&#x1F6A8;",
    }

    for m in mentions:
        mtype = m.get("mention_type", "news")
        color = type_colors.get(mtype, "#3b82f6")
        sentiment = m.get("sentiment", "neutral")
        icon = sentiment_icons.get(sentiment, "")
        source_url = m.get("source_url", "")
        source_name = _esc(m.get("source_name", ""))
        if not source_name and source_url:
            source_name = source_url.split("//")[-1].split("/")[0].replace("www.", "")

        link_html = ""
        if source_url:
            link_html = f'<a href="{_esc(source_url)}" style="color:#6366f1; text-decoration:underline; font-size:11px;">{_esc(source_name)}</a>'

        mentions_html += f"""
        <tr><td style="padding:16px 24px; border-bottom:1px solid #f1f5f9;">
          <p style="margin:0 0 6px;">
            <span style="font-size:9px; font-weight:600; padding:2px 6px; border-radius:100px; background:{color}22; color:{color}; text-transform:uppercase; letter-spacing:0.04em;">{_esc(mtype.replace('_', ' '))}</span>
            <span style="margin-left:8px; font-size:12px;">{icon}</span>
          </p>
          <p style="margin:0 0 4px; font-size:14px; font-weight:600; line-height:1.4; color:#0f172a;">{_esc(m.get('headline', ''))}</p>
          <p style="margin:0 0 4px; font-size:12px; line-height:1.5; color:#64748b;">{_esc(m.get('snippet', ''))}</p>
          {link_html}
        </td></tr>"""

    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0; padding:0; background:#faf5ff; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#faf5ff;">
<tr><td align="center" style="padding:24px 12px;">
<table width="600" cellpadding="0" cellspacing="0" style="background:#fff; border-radius:12px; overflow:hidden; box-shadow:0 4px 24px rgba(99,102,241,0.06);">
  <tr><td style="padding:16px 24px; background:#4c1d95;">
    <table width="100%"><tr>
      <td><span style="font-size:13px; font-weight:700; color:#c4b5fd; letter-spacing:0.08em;">SOLACE IN THE WILD</span></td>
      <td style="text-align:right;"><span style="font-size:11px; color:rgba(255,255,255,0.5);">{today.strftime('%b %d, %Y')}</span></td>
    </tr></table>
  </td></tr>
  <tr><td style="padding:16px 24px 8px;">
    <p style="margin:0; font-size:14px; color:#64748b;">{len(mentions)} mention{'s' if len(mentions) != 1 else ''} found today</p>
  </td></tr>
  {mentions_html}
  <tr><td style="padding:14px 24px; background:#4c1d95; text-align:center;">
    <p style="margin:0; font-size:10px; color:rgba(255,255,255,0.4);">{cfg['email']['footer_text']}</p>
  </td></tr>
</table>
</td></tr></table>
</body></html>"""

    subject = f"Solace in the Wild: {len(mentions)} mention{'s' if len(mentions) != 1 else ''} today"

    loop = asyncio.get_event_loop()
    for recipient in recipients:
        await loop.run_in_executor(None, send_raw_email, recipient, subject, html)


async def send_welcome_email(email: str) -> None:
    """Send a short welcome email after signup."""
    if not (SMTP_ADDRESS and SMTP_PASSWORD) and not (GMAIL_ADDRESS and GMAIL_APP_PASSWORD):
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
    if not (SMTP_ADDRESS and SMTP_PASSWORD) and not (GMAIL_ADDRESS and GMAIL_APP_PASSWORD):
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


async def send_no_account_email(email: str) -> None:
    """Sent when someone tries to sign in with an email that has no account.

    Instead of silently doing nothing, invite them to create an account.
    """
    if not (SMTP_ADDRESS and SMTP_PASSWORD) and not (GMAIL_ADDRESS and GMAIL_APP_PASSWORD):
        print(f"  [dry-run] Would send no-account invite to {email}")
        return

    br = cfg["email"]["branding"]
    signup_url = f"{APP_URL.rstrip('/')}/index.html" if APP_URL else "https://raphael-solace.github.io/solace-scoop/index.html"
    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="margin:0; padding:0; background:#f8fafc; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;">
<tr><td align="center" style="padding:32px 16px;">
<table width="560" cellpadding="0" cellspacing="0" style="background:#fff; border-radius:12px; overflow:hidden;">
  <tr><td style="padding:16px 24px; background:{br['header_bg']};">
    <img src="{br['header_logo']}" alt="Solace" style="height:22px; display:inline-block; vertical-align:middle; filter:brightness(0) invert(1);"><span style="font-size:12px; font-weight:700; color:{br['badge_color']}; letter-spacing:0.08em; vertical-align:middle; margin-left:8px;">{br['badge_text']}</span>
  </td></tr>
  <tr><td style="padding:32px;">
    <p style="margin:0 0 16px; font-size:16px; font-weight:700; color:#0f172a;">You don't have a Scoop account yet</p>
    <p style="margin:0 0 24px; font-size:15px; line-height:1.6; color:#475569;">
      Someone (probably you) tried to sign in with this email, but there's no Scoop account for it yet.
      Set one up in about a minute &mdash; pick the accounts you want to follow and we'll send you a
      weekly digest with champion updates, EDA signals, partner activity, and competitive intel.
    </p>
    <p style="margin:0 0 8px;">
      <a href="{signup_url}" style="display:inline-block; padding:12px 24px; background:#093B5F; color:#fff; font-size:14px; font-weight:600; text-decoration:none; border-radius:8px;">Create your account</a>
    </p>
    <p style="margin:16px 0 0; font-size:13px; color:#94a3b8;">If you didn't try to sign in, you can safely ignore this email.</p>
  </td></tr>
  <tr><td style="padding:14px 24px; background:{br['header_bg']}; text-align:center;">
    <p style="margin:0; font-size:11px; color:rgba(255,255,255,0.5);">{cfg['email']['footer_text']}</p>
  </td></tr>
</table>
</td></tr></table>
</body></html>"""

    subject = "Create your Scoop account"
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, send_raw_email, email, subject, html)


def _esc(s: str) -> str:
    """HTML-escape a string."""
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _linkedin_url(item: dict) -> str:
    """Best available LinkedIn action for a signal.

    1. If a real profile URL was found -> link straight to it.
    2. Else if a contact name is known -> LinkedIn people search for that name at the company.
    3. Else -> LinkedIn people search for a likely buyer role at the company,
       so every card has a next step even without a named contact.
    """
    from urllib.parse import quote

    linkedin = item.get("contact_linkedin", "") or ""
    if "/in/" in linkedin:
        return linkedin

    company = item.get("company", "") or ""
    name = item.get("contact_name", "") or ""
    if name:
        q = f"{name} {company}".strip()
    else:
        # Target the personas Solace sells to when no specific person is named.
        q = f'{company} "Head of Integration" OR "Enterprise Architect" OR CIO OR CTO'
    return f"https://www.linkedin.com/search/results/people/?keywords={quote(q)}"


def _mailto_url(item: dict) -> str:
    """Prefilled mailto with the ready-to-send message as the body, so the rep
    can forward/adapt it in one click instead of retyping."""
    from urllib.parse import quote

    company = item.get("company", "") or ""
    message = item.get("message", item.get("opening_line", "")) or ""
    if not message:
        return ""
    subject = f"{company} — quick thought"
    to = item.get("contact_email", "") or ""
    return f"mailto:{to}?subject={quote(subject)}&body={quote(message)}"


def render_digest(user: dict, items: list[dict], digest_id: str = "") -> str:
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
    for item_idx, item in enumerate(items):
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

        # Contact card (only if we have a verified person from the article)
        contact_html = ""
        contact_name = item.get("contact_name", "")
        if contact_name:
            c_title = _esc(item.get("contact_title", ""))
            c_company = _esc(item.get("company", ""))
            c_linkedin = item.get("contact_linkedin", "")

            # Only show LinkedIn link if it's an actual profile URL (not a search URL)
            li_html = ""
            if c_linkedin and "/in/" in c_linkedin:
                li_short = c_linkedin.replace("https://www.", "").replace("https://", "")
                if len(li_short) > 50:
                    li_short = li_short[:47] + "..."
                li_html = f'<p style="margin:4px 0 0;"><a href="{_esc(c_linkedin)}" style="color:#0077B5; text-decoration:underline; font-size:10px;">{_esc(li_short)}</a></p>'

            contact_html = f"""
            <div style="margin-top:8px; padding:8px 12px; background:#f8fafb; border:1px solid #e2e8e6; border-radius:6px;">
              <p style="margin:0; font-size:12px; font-weight:700; color:#093B5F;">{_esc(contact_name)}</p>
              <p style="margin:1px 0 0; font-size:10px; color:#64748b;">{c_title} at {c_company}</p>
              {li_html}
            </div>"""

        # Ready-to-send message (new field) / fallback to opening_line
        message = item.get("message", item.get("opening_line", ""))
        message_html = ""
        if message:
            li_action = _linkedin_url(item)
            mail_action = _mailto_url(item)
            li_label = "Message on LinkedIn" if "/in/" in (item.get("contact_linkedin", "") or "") else "Find on LinkedIn"
            action_buttons = f"""
              <a href="{_esc(li_action)}" style="display:inline-block; margin-top:8px; margin-right:8px; padding:6px 12px; background:#0077B5; color:#ffffff; font-size:11px; font-weight:600; text-decoration:none; border-radius:5px;">{li_label} &rarr;</a>"""
            if mail_action:
                action_buttons += f"""
              <a href="{_esc(mail_action)}" style="display:inline-block; margin-top:8px; padding:6px 12px; background:#093B5F; color:#ffffff; font-size:11px; font-weight:600; text-decoration:none; border-radius:5px;">Draft email &rarr;</a>"""
            message_html = f"""
            <div style="margin-top:8px; padding:8px 10px; background:#f0f0ff; border-radius:6px; border-left:2px solid #6366f1;">
              <p style="margin:0 0 3px; font-size:9px; font-weight:700; color:#6366f1; text-transform:uppercase; letter-spacing:0.06em;">READY TO SEND</p>
              <p style="margin:0; font-size:12px; color:#475569; line-height:1.5; font-style:italic;">"{_esc(message)}"</p>
              {action_buttons}
            </div>"""

        # Feedback buttons
        feedback_html = ""
        if digest_id:
            up_url = _feedback_url(digest_id, item_idx, "up")
            down_url = _feedback_url(digest_id, item_idx, "down")
            if up_url:
                feedback_html = f"""
            <div style="margin-top:10px; padding-top:8px; border-top:1px solid #f1f5f9;">
              <a href="{up_url}" style="font-size:11px; color:#10b981; text-decoration:none; margin-right:16px;">&#x1F44D; Useful</a>
              <a href="{down_url}" style="font-size:11px; color:#94a3b8; text-decoration:none;">&#x1F44E; Not relevant</a>
            </div>"""

        items_html += f"""
        <tr><td style="padding:20px 24px; border-bottom:1px solid #f1f5f9;">
          <p style="margin:0 0 8px;">{header}</p>
          <p style="margin:0 0 4px; font-size:14px; line-height:1.5; color:#0f172a;">{headline}</p>
          {source_line}
          <p style="margin:6px 0 0; font-size:12px; line-height:1.6; color:#64748b;">{so_what}</p>
          {contact_html}
          {message_html}
          {feedback_html}
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
