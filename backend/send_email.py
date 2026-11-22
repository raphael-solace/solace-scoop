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
from html.parser import HTMLParser

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


def send_raw_email(to: str, subject: str, html: str) -> str:
    """Send via Office 365, falling back to Gmail on delivery failure.

    Returns the transport that accepted the message. Blocked recipients and
    missing credentials are failures so callers never report a false send.
    """
    if ALLOWED_RECIPIENTS != "*":
        allowed = [r.lower().strip() for r in ALLOWED_RECIPIENTS.split(",") if r.strip()]
        if not allowed or to.lower().strip() not in allowed:
            raise RuntimeError(f"Email to {to} blocked by ALLOWED_RECIPIENTS")

    if not (SMTP_ADDRESS and SMTP_PASSWORD) and not (GMAIL_ADDRESS and GMAIL_APP_PASSWORD):
        raise RuntimeError("No SMTP credentials configured")

    def build_message(from_addr: str) -> MIMEMultipart:
        msg = MIMEMultipart("alternative")
        msg["From"] = f"{DISPLAY_NAME} <{from_addr}>"
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(_plain_text(html), "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))
        return msg

    office_error: Exception | None = None
    if SMTP_ADDRESS and SMTP_PASSWORD:
        server = None
        try:
            server = smtplib.SMTP("smtp.office365.com", 587, timeout=30)
            server.starttls()
            server.login(SMTP_ADDRESS, SMTP_PASSWORD)
        except (OSError, smtplib.SMTPException) as exc:
            office_error = exc
            if server:
                try:
                    server.quit()
                except Exception:
                    pass
            print(f"  [email] Office 365 setup failed ({type(exc).__name__}); trying Gmail")
        else:
            # Once DATA begins, never fall back automatically: a lost SMTP reply
            # is ambiguous and a second provider could deliver a duplicate.
            msg = build_message(SMTP_ADDRESS)
            try:
                server.sendmail(SMTP_ADDRESS, to, msg.as_string())
                return "office365"
            finally:
                try:
                    server.quit()
                except Exception:
                    pass

    if GMAIL_ADDRESS and GMAIL_APP_PASSWORD:
        msg = build_message(GMAIL_ADDRESS)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, to, msg.as_string())
        return "gmail"

    raise RuntimeError(f"Office 365 delivery failed: {type(office_error).__name__}")


async def send_digest_email(user: dict, items: list[dict], digest_id: str = "") -> None:
    """Send a rendered digest email to a user.

    In review mode (ALLOWED_RECIPIENTS is a single email, not *),
    all digests are redirected to the reviewer with a prefix showing
    who the digest was originally for.
    """
    if not (SMTP_ADDRESS and SMTP_PASSWORD) and not (GMAIL_ADDRESS and GMAIL_APP_PASSWORD):
        raise RuntimeError("No SMTP credentials configured; digest was not sent")

    html = render_digest(user, items, digest_id=digest_id)
    recipient = user["email"]

    companies = list(dict.fromkeys(
        item.get("company", "") for item in items if item.get("company")
    ))
    names = ", ".join(companies[:3])
    extra = f" +{len(companies) - 3}" if len(companies) > 3 else ""
    subject = (
        f"Your Scoop · {len(items)} signals · {names}{extra}"
        if items else "Your Scoop · a quiet week for your accounts"
    )

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
    """Send a timely account alert using the shared email layout."""
    if not (SMTP_ADDRESS and SMTP_PASSWORD) and not (GMAIL_ADDRESS and GMAIL_APP_PASSWORD):
        raise RuntimeError("No SMTP credentials configured; breaking scoop was not sent")
    html = render_digest(user, [item], digest_id=digest_id, breaking=True)
    subject = f"Breaking: {item.get('company', '')} — {item.get('headline', '')[:60]}"
    recipient = user["email"]
    if ALLOWED_RECIPIENTS != "*" and ALLOWED_RECIPIENTS:
        reviewer = ALLOWED_RECIPIENTS.split(",")[0].strip()
        if reviewer and recipient.lower() != reviewer.lower():
            recipient = reviewer
    await asyncio.get_running_loop().run_in_executor(
        None, send_raw_email, recipient, subject, html
    )


async def send_readiness_email(report: dict) -> None:
    """Send the Sunday operational readiness summary to the configured owner."""
    recipient = cfg.get("readiness", {}).get(
        "recipient", "raphael.caillon@solace.com"
    )
    status = report.get("status", "NOT READY")
    status_color = {"READY": "#10b981", "DEGRADED": "#f59e0b"}.get(status, "#ef4444")
    br = cfg["email"]["branding"]

    failures = report.get("failed_users", [])
    failure_html = ""
    for row in failures:
        reasons = "; ".join(_esc(reason) for reason in row.get("errors", []))
        failure_html += (
            f'<li style="margin:4px 0;"><strong>{_esc(row.get("email", ""))}</strong>: '
            f'{reasons}</li>'
        )
    if failure_html:
        failure_html = f'<ul style="margin:10px 0 0; padding-left:18px;">{failure_html}</ul>'

    provider_rows = ""
    for route, values in sorted(report.get("provider_metrics", {}).items()):
        details = ", ".join(f"{_esc(key)}={value}" for key, value in sorted(values.items()))
        provider_rows += f'<tr><td style="padding:5px 0; color:#475569;">{_esc(route)}</td><td style="padding:5px 0; text-align:right; color:#64748b;">{details}</td></tr>'
    if not provider_rows:
        provider_rows = '<tr><td colspan="2" style="padding:5px 0; color:#94a3b8;">No provider calls recorded in this process.</td></tr>'

    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px 12px;">
<table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:12px;overflow:hidden;">
<tr><td style="padding:16px 24px;background:{br['header_bg']};color:#fff;font-weight:700;">SCOOP WEEKLY READINESS</td></tr>
<tr><td style="padding:24px;">
<p style="margin:0 0 18px;font-size:24px;font-weight:700;color:{status_color};">{_esc(status)}</p>
<table width="100%" cellpadding="0" cellspacing="0">
<tr><td style="padding:7px 0;color:#64748b;">Eligible people</td><td style="text-align:right;font-weight:700;">{report.get('eligible_users', 0)}</td></tr>
<tr><td style="padding:7px 0;color:#64748b;">Ready digests</td><td style="text-align:right;font-weight:700;">{report.get('ready_users', 0)}</td></tr>
<tr><td style="padding:7px 0;color:#64748b;">Failed digests</td><td style="text-align:right;font-weight:700;">{len(report.get('failed_users', []))}</td></tr>
<tr><td style="padding:7px 0;color:#64748b;">Pipeline failures</td><td style="text-align:right;font-weight:700;">{report.get('pipeline_failures', 0)}</td></tr>
<tr><td style="padding:7px 0;color:#64748b;">Total articles (ready digests)</td><td style="text-align:right;font-weight:700;">{report.get('total_articles', 0)}</td></tr>
<tr><td style="padding:7px 0;color:#64748b;">Average articles / ready person</td><td style="text-align:right;font-weight:700;">{report.get('average_articles_per_person', 0)}</td></tr>
<tr><td style="padding:7px 0;color:#64748b;">Article range</td><td style="text-align:right;font-weight:700;">{report.get('minimum_articles', 0)}–{report.get('maximum_articles', 0)}</td></tr>
<tr><td style="padding:7px 0;color:#64748b;">Zero-news digests</td><td style="text-align:right;font-weight:700;">{report.get('zero_article_digests', 0)}</td></tr>
</table>
{failure_html}
<h3 style="margin:22px 0 8px;color:#093B5F;">Provider activity</h3>
<table width="100%" cellpadding="0" cellspacing="0">{provider_rows}</table>
</td></tr>
<tr><td style="padding:14px 24px;background:{br['header_bg']};text-align:center;color:rgba(255,255,255,.5);font-size:11px;">{cfg['email']['footer_text']}</td></tr>
</table></td></tr></table></body></html>"""

    subject = f"Scoop Sunday status: {status} — {report.get('ready_users', 0)}/{report.get('eligible_users', 0)} ready"
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
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#fff; border-radius:12px; overflow:hidden;">
  <tr><td style="padding:16px 24px; background:{br['header_bg']};">
    <img src="{br['header_logo']}" alt="Solace" style="height:22px; display:inline-block; vertical-align:middle; filter:brightness(0) invert(1);"><span style="font-size:12px; font-weight:700; color:{br['badge_color']}; letter-spacing:0.08em; vertical-align:middle; margin-left:8px;">{br['badge_text']}</span>
  </td></tr>
  <tr><td style="padding:32px;">
    <p style="margin:0 0 16px; font-size:16px; font-weight:700; color:#0f172a;">Welcome to Scoop, {name}!</p>
    <p style="margin:0 0 16px; font-size:15px; line-height:1.6; color:#475569;">
      Your weekly digest arrives on Mondays, with relevant company news,
      customer context, and suggested next steps for the accounts you follow.
    </p>
    <p style="margin:0; font-size:15px; line-height:1.6; color:#475569;">
      <a href="{_esc((APP_URL or 'https://raphael-solace.github.io/solace-scoop').rstrip('/') + '/account.html#accounts')}" style="display:inline-block;padding:12px 18px;background:#093B5F;color:#fff;text-decoration:none;border-radius:6px;font-weight:600;">Review your accounts &rarr;</a>
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
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#fff; border-radius:12px; overflow:hidden;">
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
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#fff; border-radius:12px; overflow:hidden;">
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


def _plain_text(html: str) -> str:
    """Retain readable paragraphs and destination links for plain-text clients."""
    class EmailText(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []
            self.hidden = 0
            self.href = ""

        def handle_starttag(self, tag, attrs):
            if tag in ("style", "script"):
                self.hidden += 1
            if tag in ("p", "tr", "div", "br", "h1", "h2"):
                self.parts.append("\n")
            if tag == "a":
                self.href = dict(attrs).get("href", "")

        def handle_endtag(self, tag):
            if tag in ("style", "script"):
                self.hidden = max(0, self.hidden - 1)
            if tag == "a" and self.href:
                self.parts.append(f" ({self.href}) ")
                self.href = ""

        def handle_data(self, value):
            if not self.hidden:
                self.parts.append(value)

    parser = EmailText()
    parser.feed(html)
    return "\n".join(
        line for part in "".join(parser.parts).splitlines()
        if (line := " ".join(part.split()))
    )


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


def render_digest(
    user: dict, items: list[dict], digest_id: str = "", *, breaking: bool = False
) -> str:
    """Render every signal, with expanded outreach for the three leading stories."""
    from urllib.parse import urlsplit

    today = date.today()
    next_monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    branding = cfg["email"]["branding"]
    account_url = (APP_URL or "https://raphael-solace.github.io/solace-scoop").rstrip("/") + "/account.html"
    user_name = user["email"].split("@")[0].split(".")[0].title()
    tracked_count = len(user.get("companies", []))
    news_accounts = {
        " ".join(item.get("company", "").casefold().split())
        for item in items if item.get("company")
    }
    summary = (
        f"{len(items)} signals · {len(news_accounts)} accounts in the news"
        if items else "Your accounts, checked for fresh news"
    )
    tag_colors = {
        "red": ("#fff1ef", "#9b302b"),
        "green": ("#eaf8f1", "#116149"),
        "amber": ("#fff7e7", "#855613"),
        "blue": ("#edf4fa", "#245776"),
    }
    heading = "A timely account update" if breaking else "Your customer brief"
    badge = "SCOOP ALERT" if breaking else "SCOOP"
    rows = []
    if not items:
        rows.append("""
        <tr><td class="pad" style="padding:28px 32px;">
          <p style="margin:0 0 10px;font-size:21px;color:#093b5f;">A quiet week for your accounts</p>
          <p style="margin:0;font-size:15px;line-height:1.7;color:#526274;">
            We found no new, verified signals within the last 14 days.
            We are continuing to watch your accounts.
          </p>
        </td></tr>""")

    for index, item in enumerate(items):
        expanded = index < 3
        company = _esc(item.get("company", ""))
        headline = _esc(item.get("headline", ""))
        context = _esc(item.get("so_what", item.get("why", "")))
        source = item.get("source_url") or next(iter(item.get("sources") or []), "")
        parsed = urlsplit(source) if isinstance(source, str) else urlsplit("")
        source_html = ""
        if parsed.scheme in ("https", "http") and parsed.hostname:
            domain = _esc(parsed.hostname.removeprefix("www."))
            source_html = f'<a href="{_esc(source)}" style="color:#245776;text-decoration:underline;">{domain}</a>'
        published = ""
        try:
            published = date.fromisoformat(item.get("date", "")).strftime("%d %b %Y")
        except (ValueError, TypeError):
            pass
        metadata = " &nbsp;·&nbsp; ".join(part for part in (source_html, published) if part)
        background, foreground = tag_colors.get(item.get("tag_color"), tag_colors["blue"])
        tag = _esc(item.get("tag", ""))
        tag_html = (
            f'<span style="display:inline-block;padding:4px 8px;background:{background};'
            f'color:{foreground};border-radius:5px;font-size:11px;font-weight:bold;">{tag}</span>'
            if tag else ""
        )
        contact_html = ""
        contact_name = item.get("contact_name", "")
        if expanded and contact_name:
            title = _esc(item.get("contact_title", ""))
            contact_html = (
                '<p style="margin:15px 0 0;font-size:13px;line-height:1.6;color:#526274;">'
                f'<strong style="color:#093b5f;">{_esc(contact_name)}</strong>'
                f'{" · " + title if title else ""}</p>'
            )
        outreach_html = ""
        message = item.get("message", item.get("opening_line", ""))
        if expanded and message:
            linkedin_url = _esc(_linkedin_url(item))
            mail_url = _esc(_mailto_url(item))
            linkedin_label = "View contact" if "/in/" in (item.get("contact_linkedin") or "") else "Find a contact"
            outreach_html = f"""
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:16px;background:#f0f7f4;border-left:3px solid #00a87d;">
              <tr><td style="padding:16px;">
                <p style="margin:0 0 7px;font-size:11px;font-weight:bold;letter-spacing:0.6px;color:#116149;">SUGGESTED OUTREACH</p>
                <p style="margin:0;font-size:14px;line-height:1.65;color:#28483d;">{_esc(message)}</p>
                <p style="margin:13px 0 0;font-size:13px;line-height:2;">
                  <a href="{mail_url}" style="display:inline-block;padding:5px 12px;border-radius:5px;background:#093b5f;color:#ffffff;text-decoration:none;font-weight:bold;">Draft email &rarr;</a>
                  &nbsp; <a href="{linkedin_url}" style="color:#245776;text-decoration:underline;">{linkedin_label}</a>
                </p>
              </td></tr>
            </table>"""
        feedback_html = ""
        if digest_id:
            up_url = _feedback_url(digest_id, index, "up")
            down_url = _feedback_url(digest_id, index, "down")
            if up_url:
                feedback_html = (
                    '<p style="margin:15px 0 0;font-size:12px;line-height:1.7;">'
                    f'<a href="{_esc(up_url)}" style="color:#116149;text-decoration:underline;">Useful</a>'
                    f' &nbsp;·&nbsp; <a href="{_esc(down_url)}" style="color:#607080;text-decoration:underline;">Less relevant</a></p>'
                )
        if index == 3:
            rows.append("""
            <tr><td class="pad" style="padding:24px 32px 4px;background:#f6f8f7;">
              <h2 style="margin:0;font-size:18px;color:#093b5f;">More account news</h2>
            </td></tr>""")
        rows.append(f"""
        <tr><td class="pad" style="padding:26px 32px;border-bottom:1px solid #e6ece9;">
          <p style="margin:0 0 10px;font-size:13px;font-weight:bold;color:#116149;">{company} &nbsp; {tag_html}</p>
          <h2 style="margin:0 0 9px;font-size:{21 if expanded else 17}px;line-height:1.4;font-weight:bold;color:#093b5f;">{headline}</h2>
          <p style="margin:0 0 12px;font-size:12px;line-height:1.7;color:#607080;overflow-wrap:anywhere;">{metadata}</p>
          <p style="margin:0;font-size:14px;line-height:1.7;color:#526274;">{context}</p>
          {contact_html}{outreach_html}{feedback_html}
        </td></tr>""")

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>@media only screen and (max-width:600px){{.pad{{padding-left:20px!important;padding-right:20px!important}}.outer{{padding:12px 6px!important}}}}</style>
</head><body style="margin:0;padding:0;background:#eef3f0;font-family:Inter,Arial,sans-serif;color:#093b5f;">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">{_esc(summary)}. Customer context and suggested next steps, picked for you.</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#eef3f0;">
<tr><td class="outer" align="center" style="padding:28px 12px;">
<!--[if mso]><table role="presentation" width="640" align="center"><tr><td><![endif]-->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;text-align:left;background:#ffffff;border-radius:14px;overflow:hidden;">
  <tr><td class="pad" style="padding:22px 32px;background:{branding['header_bg']};border-bottom:4px solid #00c895;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td style="color:#ffffff;font-size:24px;font-weight:bold;letter-spacing:-0.6px;">Solace <span style="font-size:12px;letter-spacing:1.5px;color:#6fe7bd;">{badge}</span></td>
      <td align="right" style="text-align:right;white-space:nowrap;font-size:12px;color:#cee0eb;">{today.strftime('%d %b %Y')}</td>
    </tr></table>
  </td></tr>
  <tr><td class="pad" style="padding:30px 32px 25px;border-bottom:1px solid #e6ece9;">
    <p style="margin:0 0 8px;font-size:14px;color:#607080;">Hi {_esc(user_name)},</p>
    <h1 style="margin:0 0 12px;font-family:'Instrument Serif',Georgia,serif;font-size:34px;line-height:1.15;font-weight:normal;color:#093b5f;">{heading}</h1>
    <p style="margin:0;font-size:14px;line-height:1.7;color:#526274;">{_esc(summary)}</p>
  </td></tr>
  {''.join(rows)}
  <tr><td class="pad" style="padding:28px 32px;background:#f0f7f4;">
    <a href="{_esc(account_url)}" style="display:inline-block;padding:13px 20px;background:#093b5f;border-radius:6px;color:#ffffff;font-size:14px;font-weight:bold;text-decoration:none;">Open your full report &rarr;</a>
    <p style="margin:13px 0 0;font-size:12px;line-height:1.7;color:#526274;">Review your signals, contacts, and suggested outreach in Scoop.</p>
  </td></tr>
  <tr><td class="pad" style="padding:22px 32px;font-size:12px;line-height:1.8;color:#607080;">
    <p style="margin:0;">{tracked_count} accounts tracked &nbsp;·&nbsp; <a href="{_esc(account_url + '#accounts')}" style="color:#245776;text-decoration:underline;">Manage your accounts</a></p>
    <p style="margin:5px 0 0;">Next digest {next_monday.strftime('%d %b')} &nbsp;·&nbsp; {_esc(cfg['email']['footer_text'])}</p>
  </td></tr>
</table>
<!--[if mso]></td></tr></table><![endif]-->
</td></tr></table></body></html>"""
