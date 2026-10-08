"""
Module 8 — Real-Time Email Notification System.

Originally built on Gmail SMTP, but Render's free tier blocks outbound
SMTP traffic entirely (both port 465 and 587 time out - confirmed by
testing). Outbound HTTPS works fine, so this now sends through Brevo's
transactional email HTTPS API instead of raw SMTP, which does the same
job over a port that isn't blocked.

Env vars (see .env.example):
    BREVO_API_KEY    API key from https://app.brevo.com (Settings > SMTP & API)
    MAIL_USERNAME    the sender email address, verified as a Brevo "Sender"
"""

import os
from html import escape

import requests

from services.utils import ALERT_HEADLINES, score_label

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


def _send_via_brevo(to_email, subject, html_body):
    api_key = (os.environ.get("BREVO_API_KEY") or "").strip()
    sender_email = (os.environ.get("MAIL_USERNAME") or "").strip()

    if not api_key:
        return False, "BREVO_API_KEY missing in .env"
    if not sender_email:
        return False, "MAIL_USERNAME (used as sender) missing in .env"
    if not to_email:
        return False, "This account has no email address"

    payload = {
        "sender": {"name": "SentinelType", "email": sender_email},
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": html_body,
    }

    try:
        resp = requests.post(
            BREVO_API_URL,
            headers={
                "api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=payload,
            timeout=15,
        )
        if resp.status_code in (200, 201):
            return True, f"Email sent to {to_email}"
        return False, f"Brevo rejected the email ({resp.status_code}): {resp.text[:200]}"
    except Exception as exc:
        return False, f"Email failed: {exc}"


def send_action_link_email(to_email, username, title, message_html, button_label, action_url, expiry_note):
    """
    Small reusable template for one-click confirmation emails (password
    reset, "reset my typing profile", etc). Returns (ok, detail).
    """
    html_body = f"""
    <div style="font-family:'Segoe UI',Arial,sans-serif;max-width:480px;margin:0 auto;
                background:#f8fafc;padding:28px;border-radius:16px;">
      <div style="background-color:#4f46e5;background:linear-gradient(120deg,#4f46e5,#0ea5e9);
                  padding:20px 22px;border-radius:14px;margin-bottom:20px;">
        <p style="color:rgba(255,255,255,0.85);font-size:12px;letter-spacing:1px;
                  text-transform:uppercase;margin:0 0 4px 0;">SentinelType</p>
        <h2 style="color:#ffffff;margin:0;font-size:19px;">{escape(title)}</h2>
      </div>
      <p style="font-size:14px;color:#334155;margin:0 0 18px 0;">Hi {escape(username)},</p>
      <p style="font-size:14px;color:#334155;margin:0 0 20px 0;line-height:1.5;">{message_html}</p>
      <a href="{action_url}" target="_blank"
         style="display:inline-block;background:#2563eb;color:#fff;padding:12px 22px;border-radius:10px;
                text-decoration:none;font-weight:600;font-size:14px;">{escape(button_label)}</a>
      <p style="color:#94a3b8;font-size:11.5px;margin-top:22px;line-height:1.5;">
        {escape(expiry_note)}<br>
        If you didn't request this, you can safely ignore this email.
      </p>
    </div>"""

    return _send_via_brevo(to_email, f"SentinelType: {title}", html_body)


def _score_color(score):
    if score is None:
        return "#f59e0b"
    return "#f59e0b" if score >= 60 else "#dc2626"


def send_login_alert(to_email, username, ip_address, location_label, match_score,
                     reasons, trust_url=None, latitude=None, longitude=None,
                     map_url=None, attempt_time=None, device=None, isp=None,
                     kind="behavior"):
    headline = ALERT_HEADLINES.get(kind, ALERT_HEADLINES["behavior"])
    is_test = kind == "test"

    reasons_html = "".join(
        f'<li style="margin-bottom:6px;">{escape(str(r))}</li>' for r in (reasons or [])
    ) or "<li>Behavior did not match the usual pattern</li>"

    def row(label, value, first=False):
        border = "" if first else "border-top:1px solid #e2e8f0;"
        return (
            f'<tr style="{border}"><td style="padding:8px 0;color:#64748b;font-size:13px;">{label}</td>'
            f'<td style="padding:8px 0;text-align:right;font-weight:600;color:#0f172a;font-size:13px;">{value}</td></tr>'
        )

    score_html = (
        f'<span style="background:{_score_color(match_score)};color:#fff;padding:3px 10px;'
        f'border-radius:999px;font-size:13px;font-weight:700;">{escape(score_label(match_score))}</span>'
    )

    rows = row("Account", escape(username), first=True)
    rows += row("When", escape(attempt_time or "-"))
    rows += row("Match score", score_html)
    rows += row("IP address", escape(str(ip_address)))
    rows += row("Location", escape(location_label or "Unknown"))
    if isp:
        rows += row("Network", escape(isp))
    if device:
        rows += row("Device", escape(device))

    map_section = ""
    if latitude is not None and longitude is not None:
        maps_link = map_url or f"https://www.google.com/maps?q={latitude},{longitude}"
        map_section = f"""
        <div style="background:#eef2ff;border:1px solid #e2e8f0;border-radius:12px;
                    padding:22px 20px 18px;text-align:center;margin:0 0 20px 0;">
          <div style="width:32px;height:32px;background:#dc2626;border-radius:50% 50% 50% 0;
                      transform:rotate(-45deg);margin:6px auto 18px;position:relative;">
            <div style="width:12px;height:12px;background:#eef2ff;border-radius:50%;
                        position:absolute;top:10px;left:10px;"></div>
          </div>
          <div style="font-weight:700;color:#1e293b;font-size:14px;">{escape(location_label or "Unknown location")}</div>
          <a href="{maps_link}" target="_blank"
             style="display:inline-block;margin-top:12px;font-size:13px;color:#2563eb;text-decoration:none;font-weight:600;">
             &#128205; Open exact location in Google Maps &rarr;
          </a>
        </div>"""

    trust_button = ""
    if trust_url and not is_test:
        trust_button = f"""
        <a href="{trust_url}" target="_blank"
           style="display:inline-block;background:#2563eb;color:#fff;padding:12px 22px;border-radius:10px;
                  text-decoration:none;font-weight:600;font-size:14px;">&#9989; Yes, this was me</a>"""

    why_block = "" if is_test else f"""
        <p style="font-size:13px;color:#475569;margin:0 0 6px 0;font-weight:600;">Why it was flagged:</p>
        <ul style="font-size:13px;color:#475569;margin:0 0 22px 0;padding-left:20px;">{reasons_html}</ul>"""

    footer = (
        "This was only a test - nothing is wrong with your account."
        if is_test else "If this wasn't you, change your password immediately."
    )

    html_body = f"""
    <div style="font-family:'Segoe UI',Arial,sans-serif;max-width:520px;margin:0 auto;
                background:#f8fafc;padding:28px;border-radius:16px;">
      <div style="background-color:#4f46e5;background:linear-gradient(120deg,#4f46e5,#0ea5e9);
                  padding:22px 24px;border-radius:14px;margin-bottom:20px;">
        <p style="color:rgba(255,255,255,0.85);font-size:12px;letter-spacing:1px;
                  text-transform:uppercase;margin:0 0 4px 0;">SentinelType Security Alert</p>
        <h2 style="color:#ffffff;margin:0;font-size:20px;">{escape(headline)}</h2>
      </div>
      <table style="width:100%;border-collapse:collapse;margin-bottom:18px;">{rows}</table>
      {map_section}
      {why_block}
      {trust_button}
      <p style="color:#94a3b8;font-size:11.5px;margin-top:24px;line-height:1.5;">
        {footer}<br>Sent automatically by SentinelType's behavioral authentication system.
      </p>
    </div>"""

    prefix = "[TEST] " if is_test else ""
    subject = f"{prefix}SentinelType alert: {headline} ({username})"
    return _send_via_brevo(to_email, subject, html_body)