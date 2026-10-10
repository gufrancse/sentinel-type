"""
Sends login alerts (email) for one event, and can do it in a background
thread so the login response is never slowed down by SMTP.

SMS/WhatsApp via Twilio were removed: Twilio requires a purchased number
for SMS (not a personal number) and WhatsApp's sandbox requires Meta-
approved message templates for app-initiated alerts, which made both
unreliable for a free/student setup. sms_alert.py is left in services/
untouched in case you want to revisit it later with a paid account.
"""
import os
import threading
from services.email_alert import send_login_alert


def channel_config():
    return {
        "email": bool(os.environ.get("MAIL_USERNAME") and os.environ.get("MAIL_APP_PASSWORD")),
    }


def _safe(fn, **kwargs):
    try:
        ok, detail = fn(**kwargs)
    except Exception as exc:
        ok, detail = False, f"Unexpected error: {exc}"
    return {"ok": bool(ok), "detail": detail}


def send_all(p, verify_delivery=False):
    kind = p.get("kind", "behavior")
    email = _safe(
        send_login_alert,
        to_email=p["to_email"], username=p["username"], ip_address=p["ip"],
        location_label=p["location_label"], match_score=p["match_score"],
        reasons=p["reasons"], trust_url=p.get("trust_url"),
        change_password_url=p.get("change_password_url"), block_url=p.get("block_url"),
        latitude=p.get("lat"), longitude=p.get("lon"), map_url=p.get("map_url"),
        attempt_time=p.get("attempt_time"), device=p.get("device"), isp=p.get("isp"),
        kind=kind,
    )
    return {"email": email}


def dispatch_in_background(app, history_id, payload):
    def worker():
        results = send_all(payload)
        for channel, res in results.items():
            status = "OK" if res["ok"] else "FAILED"
            print(f"[alert:{channel}] {status} - {res['detail']}")
        try:
            from extensions import db
            from models.login_history import LoginHistory
            with app.app_context():
                entry = db.session.get(LoginHistory, history_id)
                if entry is not None:
                    entry.email_alert_sent = results["email"]["ok"]
                    db.session.commit()
        except Exception as exc:
            print(f"[alert] could not save alert status: {exc}")
    threading.Thread(target=worker, daemon=True).start()