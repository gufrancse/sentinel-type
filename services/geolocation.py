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

from math import radians, sin, cos, sqrt, atan2

# Faster than this isn't humanly possible to travel between two logins -
# used to flag physically impossible back-to-back logins. This is a strong
# signal of a shared/stolen password being used from two places at once,
# not just unusual typing behavior.
MAX_PLAUSIBLE_SPEED_KMH = 900


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance between two lat/lon points, in kilometers."""
    R = 6371.0
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
    return 2 * R * atan2(sqrt(a), sqrt(1 - a))


def check_impossible_travel(prev_lat, prev_lon, prev_time, curr_lat, curr_lon, curr_time):
    """
    Compares a user's previous allowed login (location + time) against the
    current attempt, and flags travel that's physically impossible.

    Returns (is_impossible, distance_km, hours_elapsed, speed_kmh).
    """
    distance_km = haversine_km(prev_lat, prev_lon, curr_lat, curr_lon)

    hours_elapsed = (curr_time - prev_time).total_seconds() / 3600.0
    if hours_elapsed <= 0:
        hours_elapsed = 0.01  # avoid divide-by-zero on near-simultaneous attempts

    speed_kmh = distance_km / hours_elapsed

    # Require a meaningful distance too, so IP/GPS jitter between two quick
    # requests from the same city doesn't trigger a false positive.
    is_impossible = distance_km > 50 and speed_kmh > MAX_PLAUSIBLE_SPEED_KMH

    return is_impossible, round(distance_km, 1), round(hours_elapsed, 2), round(speed_kmh, 1)