"""
SMS alerts via Twilio. Returns (ok, detail).

Env vars: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER

IMPORTANT: TWILIO_FROM_NUMBER must be a phone number *bought/assigned by
Twilio* (Console -> Phone Numbers). It can NOT be your own personal
number. On a free trial you can only text numbers you have verified
under Console -> Verified Caller IDs.
"""

import os

from services.twilio_util import describe_exception, wait_for_status
from services.utils import ALERT_HEADLINES, normalize_phone, score_label


def send_login_alert_sms(to_phone_number, username, ip_address, location_label,
                         match_score, attempt_time=None, kind="behavior",
                         verify_delivery=False):
    to_number = normalize_phone(to_phone_number)
    if not to_number:
        return False, "No valid phone number on file (use +91XXXXXXXXXX format)"

    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_number = (os.environ.get("TWILIO_FROM_NUMBER") or "").strip()

    if not (sid and token and from_number):
        return False, "TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM_NUMBER missing in .env"

    if normalize_phone(from_number) == to_number:
        return False, ("TWILIO_FROM_NUMBER is your own phone number. It must be a Twilio number "
                       "(Console -> Phone Numbers), otherwise SMS can't be sent.")

    headline = ALERT_HEADLINES.get(kind, ALERT_HEADLINES["behavior"])
    when = f" at {attempt_time}" if attempt_time else ""
    body = (
        f"SentinelType: {headline} on '{username}'{when}. "
        f"Score {score_label(match_score)}, IP {ip_address}, {location_label}. "
        + ("Test only." if kind == "test" else "If this wasn't you, change your password now.")
    )

    try:
        from twilio.rest import Client
        client = Client(sid, token)
        msg = client.messages.create(body=body, from_=from_number, to=to_number)
    except Exception as exc:
        return False, describe_exception(exc)

    if verify_delivery:
        ok, detail = wait_for_status(client, msg.sid)
        return (ok is not False), f"SMS to {to_number}: {detail}"

    return True, f"SMS queued to {to_number}"