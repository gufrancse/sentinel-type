"""
Resolves an IP address to an approximate location (city/region/country,
lat/lon, ISP) using ip-api.com's free tier (no API key, 45 req/min).

Local development note: when you test on your own laptop the request
comes from 127.0.0.1, which has no location. In that case we ask
ip-api for *this machine's* public IP instead (cached for 10 minutes),
so the dashboard/alerts show your real public IP + city + map pin while
you demo. On a deployed server, real client IPs are public, so the
normal lookup path is used.
"""

import ipaddress
import time

import requests

_FIELDS = "status,message,query,city,regionName,country,lat,lon,isp"
_SELF_CACHE = {"ts": 0.0, "data": None}
_SELF_CACHE_TTL = 600  # seconds


def get_client_ip(request):
    """Best-effort real client IP (handles reverse proxies like ngrok/Render)."""
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _is_private(ip_address):
    try:
        ip = ipaddress.ip_address(ip_address)
    except ValueError:
        return True
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


def _fetch(ip_address=None):
    """One ip-api lookup. ip_address=None -> lookup of the caller (this server)."""
    url = f"http://ip-api.com/json/{ip_address}" if ip_address else "http://ip-api.com/json/"
    try:
        data = requests.get(url, params={"fields": _FIELDS}, timeout=3).json()
    except (requests.RequestException, ValueError):
        return None

    if data.get("status") != "success":
        return None

    return {
        "ip": data.get("query") or ip_address,
        "city": data.get("city"),
        "region": data.get("regionName"),
        "country": data.get("country"),
        "lat": data.get("lat"),
        "lon": data.get("lon"),
        "isp": data.get("isp"),
    }


def lookup_location(ip_address):
    """
    Always returns a dict with: ip, city, region, country, lat, lon, isp.
    'ip' may differ from the input (local dev -> this machine's public IP).
    lat/lon are None when unknown; never raises, so login can't break here.
    """
    unknown = {"ip": ip_address, "city": "Unknown", "region": None,
               "country": None, "lat": None, "lon": None, "isp": None}

    if _is_private(ip_address):
        now = time.time()
        if _SELF_CACHE["data"] and now - _SELF_CACHE["ts"] < _SELF_CACHE_TTL:
            return dict(_SELF_CACHE["data"])

        # If ip-api failed very recently, don't make every login wait again
        if now - _SELF_CACHE.get("fail_ts", 0) < 60:
            return {**unknown, "city": "Local/Dev"}

        data = _fetch(None)
        if data:
            _SELF_CACHE.update(ts=now, data=data)
            return dict(data)

        _SELF_CACHE["fail_ts"] = now
        return {**unknown, "city": "Local/Dev"}

    return _fetch(ip_address) or unknown


def build_map_url(lat, lon):
    """Clickable Google Maps link, or None if no coordinates."""
    if lat is None or lon is None:
        return None
    return f"https://www.google.com/maps?q={lat},{lon}"