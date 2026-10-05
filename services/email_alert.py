"""
Module 8 — Real-Time Email Notification System (Gmail SMTP, free).

Env vars (see .env.example):
    MAIL_USERNAME       your Gmail address
    MAIL_APP_PASSWORD   a Gmail *App Password* (16 chars; spaces are fine)
                        https://myaccount.google.com/apppasswords
                        (needs 2-Step Verification on the account)

Returns (ok, detail) so callers/dashboard can show the exact reason when
an email doesn't go out.

Map image: hotlinking a map tile directly in the email (<img src="https://
tile...">) does NOT work reliably - when Gmail/Outlook open the email,
THEIR servers fetch the image with a generic/missing User-Agent, and
OpenStreetMap's tile policy actively blocks that (the "Access blocked -
not following tile usage policy" error you saw). The fix: WE fetch the
tile ourselves, right here in Python, with a proper identifying
User-Agent (which OSM's policy explicitly allows for low-volume,
identified use), and embed the image BYTES directly into the email as
an inline attachment (Content-ID). Gmail then just displays an attached
picture - no external fetch happens when the email is opened, so it
can't be blocked. If the one-time fetch fails for any reason (no
internet on the server, OSM briefly down), we fall back to a CSS pin
card so the email still looks complete either way.
"""

import os
import smtplib
import ssl
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

import requests

from services.utils import ALERT_HEADLINES, score_label

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465

# OSM's tile usage policy requires a real, identifying User-Agent for
# server-side fetches like this (as opposed to opaque email-client
# hotlinking, which is what was getting blocked before).
_TILE_USER_AGENT = "SentinelType-CollegeProject/1.0 (behavioral-auth demo; contact: set-your-email-in-.env)"


def _fetch_map_tile(lat, lon, zoom=13):
    """
    Downloads ONE OpenStreetMap tile covering (lat, lon).
    Returns PNG bytes, or None if the fetch fails for any reason (this
    must never raise - a missing map image should never break an alert).
    """
    try:
        import math
        n = 2 ** zoom
        xtile = max(0, min(n - 1, int((lon + 180.0) / 360.0 * n)))
        lat_rad = math.radians(lat)
        ytile = max(0, min(n - 1, int(
            (1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2.0 * n
        )))
        url = f"https://tile.openstreetmap.org/{zoom}/{xtile}/{ytile}.png"

        resp = requests.get(url, headers={"User-Agent": _TILE_USER_AGENT}, timeout=6)
        if resp.status_code == 200 and resp.headers.get("Content-Type", "").startswith("image"):
            return resp.content

    except Exception:
        pass

    return None


def send_action_link_email(to_email, username, title, message_html, button_label, action_url, expiry_note):
    """
    Small reusable template for one-click confirmation emails (password
    reset, "reset my typing profile", etc). Returns (ok, detail) like the
    other senders here.
    """
    mail_username = (os.environ.get("MAIL_USERNAME") or "").strip()
    mail_password = (os.environ.get("MAIL_APP_PASSWORD") or "").replace(" ", "").strip()

    if not mail_username or not mail_password:
        return False, "MAIL_USERNAME / MAIL_APP_PASSWORD missing in .env"
    if not to_email:
        return False, "This account has no email address"

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

    message = MIMEMultipart("alternative")
    message["Subject"] = f"SentinelType: {title}"
    message["From"] = f"SentinelType <{mail_username}>"
    message["To"] = to_email
    message.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context(), timeout=15) as server:
            server.login(mail_username, mail_password)
            server.sendmail(mail_username, to_email, message.as_string())
        return True, f"Email sent to {to_email}"
    except smtplib.SMTPAuthenticationError:
        return False, ("Gmail rejected the login - use a 16-character App Password "
                       "(not your normal password) and make sure 2-Step Verification is on.")
    except Exception as exc:
        return False, f"Email failed: {exc}"


def _score_color(score):
    if score is None:
        return "#f59e0b"
    return "#f59e0b" if score >= 60 else "#dc2626"


def send_login_alert(to_email, username, ip_address, location_label, match_score,
                     reasons, trust_url=None, latitude=None, longitude=None,
                     map_url=None, attempt_time=None, device=None, isp=None,
                     kind="behavior"):
    mail_username = (os.environ.get("MAIL_USERNAME") or "").strip()
    mail_password = (os.environ.get("MAIL_APP_PASSWORD") or "").replace(" ", "").strip()

    if not mail_username or not mail_password:
        return False, "MAIL_USERNAME / MAIL_APP_PASSWORD missing in .env"
    if not to_email:
        return False, "This account has no email address"

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
    tile_bytes = None
    if latitude is not None and longitude is not None:
        maps_link = map_url or f"https://www.google.com/maps?q={latitude},{longitude}"
        tile_bytes = _fetch_map_tile(latitude, longitude)

        if tile_bytes:
            # Real embedded map image (referenced via cid: below, attached later)
            map_section = f"""
            <div style="text-align:center;margin:0 0 20px 0;">
              <a href="{maps_link}" target="_blank" style="text-decoration:none;">
                <img src="cid:maptile" width="220" height="220" alt="Approximate login location"
                     style="border-radius:10px;border:1px solid #e2e8f0;display:inline-block;">
              </a>
              <div style="color:#64748b;font-size:11.5px;margin-top:6px;">{escape(location_label or "Unknown location")}</div>
              <a href="{maps_link}" target="_blank"
                 style="display:inline-block;margin-top:6px;font-size:13px;color:#2563eb;text-decoration:none;font-weight:600;">
                 &#128205; Open exact location in Google Maps &rarr;
              </a>
            </div>"""
        else:
            # Fallback: fetch failed (e.g. no internet on the server right now) -
            # a pure CSS pin card, which can never fail to render.
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
    message = MIMEMultipart("related")
    message["Subject"] = f"{prefix}SentinelType alert: {headline} ({username})"
    message["From"] = f"SentinelType <{mail_username}>"
    message["To"] = to_email

    alt_part = MIMEMultipart("alternative")
    alt_part.attach(MIMEText(html_body, "html"))
    message.attach(alt_part)

    if tile_bytes:
        image_part = MIMEImage(tile_bytes, _subtype="png")
        image_part.add_header("Content-ID", "<maptile>")
        image_part.add_header("Content-Disposition", "inline", filename="map.png")
        message.attach(image_part)

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context(), timeout=15) as server:
            server.login(mail_username, mail_password)
            server.sendmail(mail_username, to_email, message.as_string())
        return True, f"Email sent to {to_email} (check Inbox and Spam)"

    except smtplib.SMTPAuthenticationError:
        return False, ("Gmail rejected the login - use a 16-character App Password "
                       "(not your normal password) and make sure 2-Step Verification is on.")
    except Exception as exc:
        return False, f"Email failed: {exc}"