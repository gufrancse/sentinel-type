"""
Small shared helpers used across services and routes:
- timestamp formatting (UTC ISO for the API/dashboard, IST for alert emails)
- phone number normalization (kept for the dormant SMS/WhatsApp code)
- basic User-Agent parsing for a human-readable device label
- score-to-risk-label mapping used by the dashboard and alert emails
"""
from datetime import datetime, timezone, timedelta
import re

IST_OFFSET = timedelta(hours=5, minutes=30)


def to_utc_iso(dt):
    """Formats a datetime as an ISO-8601 UTC string ending in Z.
    SQLite stores naive UTC timestamps, so a naive dt is assumed to already be UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def format_ist(dt=None):
    """Formats a datetime (assumed UTC if naive) as a readable IST string,
    used inside alert emails so the time shown matches India local time."""
    if dt is None:
        dt = datetime.utcnow()
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    ist_dt = dt + IST_OFFSET
    return ist_dt.strftime("%d %b %Y, %I:%M %p IST")


def normalize_phone(raw, default_country_code="+91"):
    """Best-effort normalization of a phone number to E.164-ish form.
    Kept for the dormant SMS/WhatsApp code in case it's revisited later."""
    if not raw:
        return None
    digits = re.sub(r"[^\d+]", "", raw.strip())
    if digits.startswith("whatsapp:"):
        digits = digits[len("whatsapp:"):]
    if digits.startswith("+"):
        return digits
    if digits.startswith("00"):
        return "+" + digits[2:]
    if digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10:
        return default_country_code + digits
    return "+" + digits


def parse_user_agent(ua):
    """Small, dependency-free User-Agent parser - good enough for a friendly
    device label, not meant to be exhaustive."""
    ua = ua or ""
    browser = "Unknown browser"
    if "Edg/" in ua:
        browser = "Edge"
    elif "Chrome/" in ua and "Chromium" not in ua:
        browser = "Chrome"
    elif "Firefox/" in ua:
        browser = "Firefox"
    elif "Safari/" in ua and "Chrome/" not in ua:
        browser = "Safari"

    os_name = "Unknown OS"
    if "Windows" in ua:
        os_name = "Windows"
    elif "Android" in ua:
        os_name = "Android"
    elif "iPhone" in ua or "iPad" in ua:
        os_name = "iOS"
    elif "Mac OS X" in ua:
        os_name = "macOS"
    elif "Linux" in ua:
        os_name = "Linux"

    device = "Mobile" if ("Mobile" in ua or "Android" in ua or "iPhone" in ua) else "Desktop"

    return {"browser": browser, "os": os_name, "device": device}


def device_label(ua):
    """Short human-readable device summary, e.g. 'Chrome on Windows'."""
    info = parse_user_agent(ua)
    return f"{info['browser']} on {info['os']}"


def score_label(score):
    """Maps a 0-100 behavioral match score to a plain-English risk label,
    used by the dashboard and alert emails. The actual allow/block decision
    lives in services/decision_engine.py - this is just for display."""
    if score is None:
        return "Unknown"
    if score >= 80:
        return "Low risk"
    if score >= 50:
        return "Medium risk"
    return "High risk"


ALERT_HEADLINES = {
    "behavior": "Suspicious Login Detected",
    "wrong_password": "Failed Login Attempt",
    "new_device": "Login From a New Device",
    "success": "New Login to Your Account",
    "default": "Security Alert",
}

def device_fingerprint(user_agent):
    """
    A coarse, durable device identifier: just OS + device type (e.g.
    "windows:desktop", "android:mobile"). Deliberately NOT based on the
    exact browser version or full User-Agent string, since that changes
    every time the browser auto-updates and would make every login look
    like a "new device". This is just precise enough to tell a laptop
    apart from a phone, which is what actually matters for behavioral
    biometrics.
    """
    info = parse_user_agent(user_agent)
    return f"{info['os']}:{info['device']}".lower()