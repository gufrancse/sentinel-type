"""
Dashboard, logout, and the small JSON/API endpoints the dashboard page
(static/js/dashboard.js) calls to render itself - summary cards, the
login-history table, the score-trend chart, and the map.
"""
from datetime import datetime, timedelta

from flask import Blueprint, render_template, redirect, session, jsonify, request

from extensions import db
from models.user import User
from models.login_history import LoginHistory
from services.utils import to_utc_iso, format_ist, device_label, score_label
from services.alerts import channel_config, send_all
from services.decision_engine import ALLOW_THRESHOLD

dashboard_bp = Blueprint("dashboard", __name__)


def _current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return db.session.get(User, user_id)


def _mask_email(email):
    if not email or "@" not in email:
        return email
    name, domain = email.split("@", 1)
    if len(name) <= 2:
        masked = name[0] + "*"
    else:
        masked = name[0] + "*" * (len(name) - 2) + name[-1]
    return f"{masked}@{domain}"


def _mask_phone(phone):
    if not phone:
        return None
    return f"****{phone[-4:]}"


def _risk_level(user):
    """Simple contextual risk read-out for the dashboard's security panel:
    strikes since last success, plus failures in the last 24h."""
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

    history = (
        LoginHistory.query.filter_by(user_id=user.id)
        .order_by(LoginHistory.timestamp.desc())
        .limit(50)
        .all()
    )

    total_attempts = LoginHistory.query.filter_by(user_id=user.id).count()
    successful = LoginHistory.query.filter_by(user_id=user.id, decision="allowed").count()
    suspicious = LoginHistory.query.filter_by(user_id=user.id, decision="blocked").count()
    wrong_password = LoginHistory.query.filter_by(user_id=user.id, decision="wrong_password").count()

    history_json = []
    for entry in history:
        reasons = (entry.reasoning or "").split("\n") if entry.reasoning else []
        history_json.append({
            "id": entry.id,
            "timestamp": to_utc_iso(entry.timestamp),
            "ip": entry.ip_address,
            "location": entry.location_label(),
            "latitude": entry.latitude,
            "longitude": entry.longitude,
            "mapUrl": entry.map_url(),
            "device": device_label(entry.user_agent),
            "matchScore": entry.match_score,
            "scoreLabel": score_label(entry.match_score),
            "decision": entry.decision,
            "reasons": reasons,
            "emailAlertSent": entry.email_alert_sent,
            "trusted": entry.marked_trusted,
        })

    return jsonify({
        "serverTime": to_utc_iso(datetime.utcnow()),
        "stats": {
            "totalAttempts": total_attempts,
            "successfulLogins": successful,
            "suspiciousAttempts": suspicious,
            "wrongPasswordAttempts": wrong_password,
        },
        "security": {
            "riskLevel": _risk_level(user),
            "failedBehaviorStreak": user.failed_behavior_attempts,
            "allowThreshold": ALLOW_THRESHOLD,
            "channels": channel_config(),
        },
        "profile": {
            "username": user.username,
            "email": _mask_email(user.email),
            "phone": _mask_phone(user.phone_number),
            "memberSince": to_utc_iso(user.created_at),
        },
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
            return jsonify({"error": "cooldown", "waitSeconds": wait}), 429

    session["last_test_alert_at"] = now.isoformat()

    payload = {
        "to_email": user.email,
        "username": user.username,
        "ip": request.remote_addr,
        "location_label": "Test alert (manual trigger)",
        "match_score": 0,
        "reasons": ["This is a test alert triggered manually from the dashboard."],
        "attempt_time": format_ist(now),
        "device": device_label(request.headers.get("User-Agent")),
        "isp": None,
        "kind": "test",
    }
    results = send_all(payload)
    return jsonify(results)


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