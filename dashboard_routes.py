"""
Dashboard, logout, and the small JSON/API endpoints the dashboard page
(static/js/dashboard.js) calls to render itself - summary cards, the
login-history table, the score-trend chart, and the map.
"""
from datetime import datetime, timedelta

from flask import Blueprint, render_template, redirect, session, jsonify, request, current_app

from extensions import db
from models.user import User
from models.login_history import LoginHistory
from models.behavioral_profile import BehavioralProfile
from services.utils import to_utc_iso, format_ist, parse_user_agent
from services.alerts import channel_config, send_all

dashboard_bp = Blueprint("dashboard", __name__)


def _current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return db.session.get(User, user_id)


def _risk_level(user):
    recent_cutoff = datetime.utcnow() - timedelta(hours=24)
    recent_failures = LoginHistory.query.filter(
        LoginHistory.user_id == user.id,
        LoginHistory.decision != "allowed",
        LoginHistory.timestamp >= recent_cutoff,
    ).count()
    if user.failed_behavior_attempts >= 3 or recent_failures >= 5:
        return "high"
    if user.failed_behavior_attempts >= 1 or recent_failures >= 2:
        return "medium"
    return "low"


@dashboard_bp.route("/dashboard")
def dashboard():
    user = _current_user()
    if not user:
        return redirect("/")
    return render_template("dashboard.html", username=user.username)


@dashboard_bp.route("/logout")
def logout():
    session.clear()
    return redirect("/")


@dashboard_bp.route("/api/dashboard-data")
def api_dashboard_data():
    user = _current_user()
    if not user:
        return jsonify({"error": "not_authenticated"}), 401

    all_history = (
        LoginHistory.query.filter_by(user_id=user.id)
        .order_by(LoginHistory.timestamp.desc())
        .all()
    )
    recent = all_history[:50]

    total = len(all_history)
    allowed_rows = [h for h in all_history if h.decision == "allowed"]
    blocked_rows = [h for h in all_history if h.decision == "blocked"]
    wrong_pw_rows = [h for h in all_history if h.decision == "wrong_password"]

    scored = [h.match_score for h in all_history if h.match_score is not None]
    avg_score = round(sum(scored) / len(scored)) if scored else None

    unique_ips = len({h.ip_address for h in all_history if h.ip_address})
    unique_locations = len({h.location_label() for h in all_history if h.location_label() != "Unknown location"})
    alerts_sent = sum(1 for h in all_history if h.email_alert_sent)

    last_success = allowed_rows[0].timestamp if allowed_rows else None
    failure_rows = [h for h in all_history if h.decision != "allowed"]
    last_failure = failure_rows[0].timestamp if failure_rows else None

    history_json = []
    for entry in recent:
        reasons = [r for r in (entry.reasoning or "").split("\n") if r.strip()]
        ua_info = parse_user_agent(entry.user_agent)
        history_json.append({
            "id": entry.id,
            "ts": to_utc_iso(entry.timestamp),
            "score": entry.match_score,
            "decision": entry.decision,
            "browser": ua_info["browser"],
            "os": ua_info["os"],
            "device": ua_info["device"],
            "ip": entry.ip_address,
            "isp": entry.isp,
            "location": entry.location_label(),
            "lat": entry.latitude,
            "lon": entry.longitude,
            "mapUrl": entry.map_url(),
            "reasons": reasons,
            "alerts": {"email": entry.email_alert_sent},
            "trusted": entry.marked_trusted,
        })

    profile = BehavioralProfile.query.filter_by(user_id=user.id).first()
    profile_json = None
    if profile:
        profile_json = {
            "samples": profile.sample_count,
            "enrolledAt": to_utc_iso(profile.created_at),
            "dwell": round(profile.average_dwell_time, 1),
            "flight": round(profile.average_flight_time, 1),
            "wpm": round(profile.average_typing_speed_wpm, 1),
            "duration": round(profile.average_typing_duration, 2),
        }

    return jsonify({
        "stats": {
            "risk": _risk_level(user),
            "lastSuccess": to_utc_iso(last_success),
            "lastFailure": to_utc_iso(last_failure),
            "total": total,
            "allowed": len(allowed_rows),
            "blocked": len(blocked_rows),
            "wrongPassword": len(wrong_pw_rows),
            "avgScore": avg_score,
            "uniqueLocations": unique_locations,
            "uniqueIps": unique_ips,
            "alertsSent": alerts_sent,
        },
        "security": {
            "strikes": user.failed_behavior_attempts,
            "threshold": current_app.config.get("ALERT_AFTER_ATTEMPTS", 3),
            "cooldownMinutes": current_app.config.get("ALERT_COOLDOWN_MINUTES", 2),
            "channels": channel_config(),
            "hasEmail": bool(user.email),
        },
        "user": {"email": user.email},
        "profile": profile_json,
        "history": history_json,
    })


@dashboard_bp.route("/api/test-alert", methods=["POST"])
def api_test_alert():
    user = _current_user()
    if not user:
        return jsonify({"error": "not_authenticated"}), 401

    last_test = session.get("last_test_alert_at")
    now = datetime.utcnow()
    if last_test:
        last_test_dt = datetime.fromisoformat(last_test)
        if (now - last_test_dt).total_seconds() < 20:
            wait = 20 - int((now - last_test_dt).total_seconds())
            return jsonify({"error": f"Please wait {wait}s before testing again"}), 429

    session["last_test_alert_at"] = now.isoformat()

    payload = {
        "to_email": user.email,
        "username": user.username,
        "ip": request.remote_addr,
        "location_label": "Test alert (manual trigger)",
        "match_score": 0,
        "reasons": ["This is a test alert triggered manually from the dashboard."],
        "attempt_time": format_ist(now),
        "device": "Manual test",
        "isp": None,
        "kind": "test",
    }
    results = send_all(payload)
    return jsonify({"results": results})


@dashboard_bp.route("/api/history/<int:history_id>/trust", methods=["POST"])
def api_mark_trusted(history_id):
    user = _current_user()
    if not user:
        return jsonify({"error": "not_authenticated"}), 401

    entry = db.session.get(LoginHistory, history_id)
    if not entry or entry.user_id != user.id:
        return jsonify({"error": "not_found"}), 404

    entry.marked_trusted = True
    db.session.commit()
    return jsonify({"ok": True})