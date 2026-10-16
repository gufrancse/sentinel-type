from flask import Flask, render_template, request, jsonify, session, redirect
from extensions import db
from models.user import User
from models.behavioral_sample import BehavioralSample
from models.behavioral_profile import BehavioralProfile
from models.login_history import LoginHistory
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, inspect, text
from werkzeug.security import generate_password_hash, check_password_hash
from flask_migrate import Migrate
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import os
import re
import secrets
from dotenv import load_dotenv
from math import isfinite
from services.decision_engine import evaluate as evaluate_login, adapt_profile, BASELINE_ADAPTATION_THRESHOLD
from services import ml_model
from services.geolocation import get_client_ip, lookup_location, build_map_url, check_impossible_travel
from services.alerts import dispatch_in_background
from services.email_alert import send_action_link_email, send_login_alert
from services.utils import normalize_phone, format_ist, device_label, device_fingerprint, generate_challenge_phrase
from dashboard_routes import dashboard_bp

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ["SECRET_KEY"]
database_url = os.environ.get("DATABASE_URL", "sqlite:///sentinel_type.db")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = bool(os.environ.get("RENDER"))

db.init_app(app)
migrate = Migrate(app, db)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"])
TRUST_TOKEN_MAX_AGE = 7 * 24 * 60 * 60  # 7 days

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Fixed phrase typed at both enrollment and every login (separate from the
# real password) so dwell/flight-time comparisons are apples-to-apples.
# Not secret - never reuse it as an actual account password.
DECOY_CHALLENGE_PHRASE = "Nova31$Ember64"

# Consecutive failures before an email alert fires (and every Nth after).
ALERT_AFTER_ATTEMPTS = max(1, int(os.environ.get("ALERT_AFTER_ATTEMPTS", "3")))
# Minutes of silence after an alert, so a flood of failures can't spam the owner.
ALERT_COOLDOWN_MINUTES = max(0, int(os.environ.get("ALERT_COOLDOWN_MINUTES", "2")))
# Public URL used in email links (Render/ngrok URL in production).
PUBLIC_BASE_URL = (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")

app.config["ALERT_AFTER_ATTEMPTS"] = ALERT_AFTER_ATTEMPTS
app.config["ALERT_COOLDOWN_MINUTES"] = ALERT_COOLDOWN_MINUTES

app.register_blueprint(dashboard_bp)

# Columns added in newer versions - ensure_schema() adds any missing ones
# to an existing database so you never lose enrolled users on an update.
EXPECTED_COLUMNS = {
    "users": {
        "failed_behavior_attempts": "INTEGER NOT NULL DEFAULT 0",
        "email_verified": "BOOLEAN NOT NULL DEFAULT FALSE",
        "account_locked": "BOOLEAN NOT NULL DEFAULT FALSE",
        "challenge_attempts": "INTEGER NOT NULL DEFAULT 0",
        "locked_until": "TIMESTAMP",
        "otp_hash": "VARCHAR(255)",
        "otp_expires_at": "TIMESTAMP",
        "otp_history_id": "INTEGER",
        "challenge_phrase": "VARCHAR(64)",
        "otp_wrong_count": "INTEGER NOT NULL DEFAULT 0",
        "otp_resend_count": "INTEGER NOT NULL DEFAULT 0",
        "otp_lockout_stage": "INTEGER NOT NULL DEFAULT 0",
        "otp_locked_until": "TIMESTAMP",
    },
    "login_history": {
        "latitude": "FLOAT",
        "longitude": "FLOAT",
        "isp": "VARCHAR(150)",
        "user_agent": "VARCHAR(255)",
        "whatsapp_alert_sent": "BOOLEAN NOT NULL DEFAULT 0",
        "alert_dispatched_at": "DATETIME",
    },
}


def ensure_schema():
    with app.app_context():
        db.create_all()
        inspector = inspect(db.engine)
        for table, columns in EXPECTED_COLUMNS.items():
            existing = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name not in existing:
                    db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                    print(f"[schema] added column {table}.{name}")
        db.session.commit()

        # One-profile-per-user -> one-profile-per-device migration (Postgres only).
        if db.engine.dialect.name == "postgresql":
            bp_columns = {c["name"] for c in inspector.get_columns("behavioral_profiles")}
            if "device_fingerprint" not in bp_columns:
                db.session.execute(text(
                    "ALTER TABLE behavioral_profiles "
                    "ADD COLUMN device_fingerprint VARCHAR(64) NOT NULL DEFAULT 'unknown'"
                ))
                db.session.execute(text(
                    "ALTER TABLE behavioral_profiles "
                    "DROP CONSTRAINT IF EXISTS behavioral_profiles_user_id_key"
                ))
                db.session.execute(text(
                    "ALTER TABLE behavioral_profiles "
                    "ADD CONSTRAINT uq_profile_user_device UNIQUE (user_id, device_fingerprint)"
                ))
                db.session.commit()
                print("[schema] migrated behavioral_profiles to per-device profiles")


ensure_schema()

REQUIRED_SAMPLE_FIELDS = [
    "averageDwellTime",
    "averageFlightTime",
    "typingDuration",
    "typingSpeedWPM",
]

# Mouse fields are optional/additive - older clients without them still work.
OPTIONAL_SAMPLE_FIELDS = [
    "averageMouseSpeed",
    "averageClickInterval",
]


def validate_behavioral_sample(data):
    if not isinstance(data, dict):
        return False, "Behavioral sample must be an object"

    if not all(field in data for field in REQUIRED_SAMPLE_FIELDS):
        return False, "Incomplete behavioral sample"

    for field in REQUIRED_SAMPLE_FIELDS + OPTIONAL_SAMPLE_FIELDS:
        if field not in data:
            continue

        value = data[field]

        if not isinstance(value, (int, float)):
            return False, f"Invalid value for {field}"

        if value < 0:
            return False, f"Invalid value for {field}"

    return True, None


def sample_to_feature_dict(sample):
    """Normalizes an incoming sample into the canonical feature dict used
    everywhere downstream (ML model, decision engine, stored records)."""
    return {
        "averageDwellTime": float(sample["averageDwellTime"]),
        "averageFlightTime": float(sample["averageFlightTime"]),
        "typingDuration": float(sample["typingDuration"]),
        "typingSpeedWPM": float(sample["typingSpeedWPM"]),
        "averageMouseSpeed": float(sample.get("averageMouseSpeed", 0) or 0),
        "averageClickInterval": float(sample.get("averageClickInterval", 0) or 0),
    }


# --------------------------------------------------------------------
# Static pages
# --------------------------------------------------------------------

@app.route("/")
def login():
    return render_template("login.html")


@app.route("/register")
def register_page():
    return render_template("register.html")


@app.route("/api/challenge-phrase")
@limiter.limit("20 per minute")
def api_challenge_phrase():
    username = (request.args.get("username") or "").strip()
    if not username:
        return jsonify({"success": False, "message": "Username is required"}), 400

    user = db.session.scalar(select(User).where(User.username == username))

    if user is None or not user.challenge_phrase:
        # Same shape as a real response either way, so this endpoint can't
        # be used to probe which usernames exist.
        return jsonify({"success": True, "phrase": DECOY_CHALLENGE_PHRASE})

    return jsonify({"success": True, "phrase": user.challenge_phrase})


@app.route("/enroll")
def enroll_page():
    user_id = session.get("user_id")

    if user_id is None:
        return jsonify({
            "success": False,
            "message": "You must register or login first"
        }), 401

    user_agent = (request.headers.get("User-Agent") or "")[:255]
    device_key = device_fingerprint(user_agent)

    profile = db.session.scalar(
        select(BehavioralProfile).where(
            BehavioralProfile.user_id == user_id,
            BehavioralProfile.device_fingerprint == device_key,
        )
    )

    if profile is not None:
        return jsonify({
            "success": False,
            "message": "Behavioral enrollment already completed for this device"
        }), 409

    user = db.session.get(User, user_id)
    if user is None:
        return jsonify({"success": False, "message": "Account not found"}), 404

    if not user.challenge_phrase:
        user.challenge_phrase = generate_challenge_phrase()
        db.session.commit()

    return render_template("enroll.html", enrollment_challenge=user.challenge_phrase)


# --------------------------------------------------------------------
# Device trust / approval
# --------------------------------------------------------------------

@app.route("/approve-device/<token>")
def approve_device(token):
    try:
        payload = serializer.loads(token, salt="newdevice", max_age=NEW_DEVICE_TOKEN_MAX_AGE)
    except SignatureExpired:
        return "This approval link has expired. Please try logging in again to get a new one.", 400
    except BadSignature:
        return "This approval link is invalid.", 400

    user = db.session.get(User, payload["user_id"])
    if user is None:
        return "Account not found.", 404

    session.clear()
    session["user_id"] = user.id

    return redirect("/enroll")


@app.route("/block-account/<token>")
def block_account(token):
    try:
        user_id = serializer.loads(token, salt="blockaccount", max_age=RESET_TOKEN_MAX_AGE)
    except SignatureExpired:
        return "This link has expired.", 400
    except BadSignature:
        return "This link is invalid.", 400

    user = db.session.get(User, user_id)
    if user is None:
        return "Account not found.", 404

    user.account_locked = True
    db.session.commit()

    return (
        "Your account has been locked for safety. No one - including you - can "
        "log in until you reset your password. "
        "<a href=\"/forgot-password\">Reset your password</a> to unlock it."
    )


@app.route("/trust/<token>")
def trust_device(token):
    try:
        login_history_id = serializer.loads(token, max_age=TRUST_TOKEN_MAX_AGE)
    except SignatureExpired:
        return "This confirmation link has expired.", 400
    except BadSignature:
        return "Invalid confirmation link.", 400

    entry = db.session.get(LoginHistory, login_history_id)

    if entry is None:
        return "Attempt not found.", 404

    entry.marked_trusted = True
    db.session.commit()

    return (
        "Thanks — this attempt has been marked as trusted. "
        "If you didn't recognize it, please change your password immediately."
    )


# --------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------

@app.route("/register", methods=["POST"])
@limiter.limit("5 per minute")
def register():
    data = request.get_json(silent=True)

    if not data:
        return jsonify({"success": False, "message": "Invalid registration request"}), 400

    username = data.get("username", "").strip()
    password = data.get("password", "")
    email = data.get("email", "").strip()
    raw_phone = (data.get("phoneNumber") or "").strip()
    phone_number = normalize_phone(raw_phone) if raw_phone else None

    if raw_phone and phone_number is None:
        return jsonify({
            "success": False,
            "message": "Enter a valid phone number, e.g. +919876543210"
        }), 400

    if not username or not password or not email:
        return jsonify({
            "success": False,
            "message": "Username, password and email are required"
        }), 400

    if len(username) < 3:
        return jsonify({"success": False, "message": "Username must be at least 3 characters"}), 400

    if len(password) < 8:
        return jsonify({"success": False, "message": "Password must be at least 8 characters"}), 400

    if not EMAIL_RE.match(email):
        return jsonify({"success": False, "message": "Enter a valid email address"}), 400
    
    existing_user = db.session.scalar(select(User).where(User.username == username))

    resuming_abandoned_registration = False
    if existing_user is not None:
        has_profile = db.session.scalar(
            select(BehavioralProfile.id).where(BehavioralProfile.user_id == existing_user.id).limit(1)
        ) is not None

        if has_profile:
            return jsonify({"success": False, "message": "Username already exists"}), 409

        # Username was registered before but behavioral enrollment was never
        # completed on any device - let them restart cleanly instead of
        # being stuck forever on an abandoned registration.
        resuming_abandoned_registration = True

    existing_email = db.session.scalar(select(User).where(User.email == email))
    if existing_email is not None and (existing_user is None or existing_email.id != existing_user.id):
        return jsonify({"success": False, "message": "Email already registered"}), 409

    if resuming_abandoned_registration:
        existing_user.password_hash = generate_password_hash(password)
        existing_user.email = email
        existing_user.email_verified = False
        existing_user.phone_number = phone_number
        user = existing_user
    else:
        user = User(
            username=username,
            password_hash=generate_password_hash(password),
            email=email,
            phone_number=phone_number,
            challenge_phrase=generate_challenge_phrase(),
        )
        db.session.add(user)

    db.session.commit()

    session.clear()
    session["user_id"] = user.id

    verify_token = serializer.dumps(user.id, salt="emailverify")
    base_url = PUBLIC_BASE_URL or request.url_root.rstrip("/")
    verify_url = f"{base_url}/verify-email/{verify_token}"

    email_ok, email_detail = send_action_link_email(
        to_email=user.email,
        username=user.username,
        title="Verify your email",
        message_html=(
            "Thanks for signing up for SentinelType! Click below to verify "
            "this email address. You can finish behavioral enrollment now - "
            "just verify before your next login."
        ),
        button_label="Verify my email",
        action_url=verify_url,
        expiry_note="This link expires in 24 hours.",
    )
    print(f"[verify-email] ok={email_ok} detail={email_detail}")
    print(f"New user registered: id={user.id}, username={user.username}")

    return jsonify({
        "success": True,
        "message": "Registration successful. Check your email to verify your account.",
        "redirect": "/enroll"
    }), 201


@app.route("/verify-email/<token>")
def verify_email(token):
    try:
        user_id = serializer.loads(token, salt="emailverify", max_age=EMAIL_VERIFY_TOKEN_MAX_AGE)
    except SignatureExpired:
        return "This verification link has expired. Please request a new one from the login page.", 400
    except BadSignature:
        return "This verification link is invalid.", 400

    user = db.session.get(User, user_id)
    if user is None:
        return "Account not found.", 404

    user.email_verified = True
    db.session.commit()

    return (
        "Your email has been verified! You can now log in at "
        f"<a href=\"/\">SentinelType</a>."
    )


# --------------------------------------------------------------------
# Account recovery: forgot password, reset typing profile
# --------------------------------------------------------------------

RESET_TOKEN_MAX_AGE = 60 * 60  # 1 hour
EMAIL_VERIFY_TOKEN_MAX_AGE = 24 * 60 * 60  # 24 hours
NEW_DEVICE_TOKEN_MAX_AGE = 24 * 60 * 60  # 24 hours

@app.route("/forgot-username")
def forgot_username_page():
    return render_template("forgot_username.html")


@app.route("/forgot-username", methods=["POST"])
@limiter.limit("5 per minute")
def forgot_username():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()

    generic = {
        "success": True,
        "message": "If that email is registered, the username has been sent to it."
    }

    if not email:
        return jsonify({"success": False, "message": "Enter your email address"}), 400

    user = db.session.scalar(select(User).where(User.email == email))

    if user is None:
        return jsonify(generic)

    base_url = PUBLIC_BASE_URL or request.url_root.rstrip("/")
    login_url = f"{base_url}/"

    email_ok, email_detail = send_action_link_email(
        to_email=user.email,
        username=user.username,
        title="Your SentinelType username",
        message_html=(
            "You (or someone) asked to recover the username linked to this email. "
            f"Your username is:<br><br><strong style=\"font-size:18px;\">{user.username}</strong>"
            "<br><br>If you didn't request this, you can safely ignore this email."
        ),
        button_label="Go to login",
        action_url=login_url,
        expiry_note="",
    )
    print(f"[forgot-username email] ok={email_ok} detail={email_detail}")

    return jsonify(generic)

@app.route("/forgot-password")
def forgot_password_page():
    return render_template("forgot_password.html")


@app.route("/forgot-password", methods=["POST"])
@limiter.limit("5 per minute")
def forgot_password():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()

    generic = {
        "success": True,
        "message": "If that email is registered, a reset link has been sent to it."
    }

    if not email:
        return jsonify({"success": False, "message": "Enter your email address"}), 400

    user = db.session.scalar(select(User).where(User.email == email))

    if user is None:
        return jsonify(generic)

    token = serializer.dumps(user.id, salt="pwreset")
    base_url = PUBLIC_BASE_URL or request.url_root.rstrip("/")
    reset_url = f"{base_url}/reset-password/{token}"

    email_ok, email_detail = send_action_link_email(
        to_email=user.email,
        username=user.username,
        title="Reset your password",
        message_html="Click below to set a new password. This link works once and expires in 1 hour.",
        button_label="Reset my password",
        action_url=reset_url,
        expiry_note="This link expires in 1 hour.",
    )
    print(f"[forgot-password email] ok={email_ok} detail={email_detail}")

    return jsonify(generic)


@app.route("/reset-password/<token>")
def reset_password_page(token):
    try:
        serializer.loads(token, salt="pwreset", max_age=RESET_TOKEN_MAX_AGE)
    except SignatureExpired:
        return render_template("reset_password.html", token=None,
                               error="This reset link has expired. Request a new one.")
    except BadSignature:
        return render_template("reset_password.html", token=None,
                               error="This reset link is invalid.")

    return render_template("reset_password.html", token=token, error=None)


@app.route("/reset-password/<token>", methods=["POST"])
def reset_password_submit(token):
    try:
        user_id = serializer.loads(token, salt="pwreset", max_age=RESET_TOKEN_MAX_AGE)
    except (SignatureExpired, BadSignature):
        return jsonify({"success": False, "message": "This reset link is invalid or has expired."}), 400

    data = request.get_json(silent=True) or {}
    password = data.get("password", "")

    if len(password) < 8:
        return jsonify({"success": False, "message": "Password must be at least 8 characters"}), 400
    
    user = db.session.get(User, user_id)
    if user is None:
        return jsonify({"success": False, "message": "Account not found"}), 404

    user.password_hash = generate_password_hash(password)
    user.failed_behavior_attempts = 0
    user.account_locked = False
    db.session.commit()

    return jsonify({"success": True, "message": "Password updated. You can log in now.", "redirect": "/"})


@app.route("/reset-profile")
def reset_profile_page():
    return render_template("reset_profile.html")


@app.route("/reset-profile", methods=["POST"])
def reset_profile_request():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password", "")

    user = db.session.scalar(select(User).where(User.username == username))

    if user is None or not check_password_hash(user.password_hash, password):
        return jsonify({"success": False, "message": "Invalid username or password"}), 401

    token = serializer.dumps(user.id, salt="profilereset")
    base_url = PUBLIC_BASE_URL or request.url_root.rstrip("/")
    confirm_url = f"{base_url}/reset-profile/confirm/{token}"

    email_sent, email_detail = send_action_link_email(
        to_email=user.email,
        username=user.username,
        title="Confirm typing-profile reset",
        message_html=(
            "We got a request to reset your typing profile. Click below to "
            "confirm and re-enroll a fresh profile. This link works once and "
            "expires in 1 hour."
        ),
        button_label="Reset my typing profile",
        action_url=confirm_url,
        expiry_note="This link expires in 1 hour.",
    )

    print(f"[reset-profile email] ok={email_sent} detail={email_detail}")

    if not email_sent:
        return jsonify({
            "success": False,
            "message": "Password confirmed, but the confirmation email couldn't be sent."
        }), 500

    return jsonify({
        "success": True,
        "message": f"Confirmation link sent to {user.email}. Check your inbox to finish."
    })


@app.route("/reset-profile/confirm/<token>")
def reset_profile_confirm(token):
    try:
        user_id = serializer.loads(token, salt="profilereset", max_age=RESET_TOKEN_MAX_AGE)
    except SignatureExpired:
        return "This confirmation link has expired. Please request a new one from the login page.", 400
    except BadSignature:
        return "This confirmation link is invalid.", 400

    user = db.session.get(User, user_id)
    if user is None:
        return "Account not found.", 404

    profile = db.session.scalar(
        select(BehavioralProfile).where(BehavioralProfile.user_id == user_id)
    )
    if profile is not None:
        ml_model.delete_model(profile.ml_model_path)
        db.session.delete(profile)

    db.session.execute(
        BehavioralSample.__table__.delete().where(BehavioralSample.user_id == user_id)
    )
    user.failed_behavior_attempts = 0
    db.session.commit()

    session.clear()
    session["user_id"] = user.id

    return redirect("/enroll")


# --------------------------------------------------------------------
# Login
# --------------------------------------------------------------------

def record_attempt(user, ip_address, location, user_agent, decision, match_score, reasons):
    """Writes one row to login_history for every attempt, allowed or blocked."""
    entry = LoginHistory(
        user_id=user.id,
        ip_address=ip_address,
        city=location.get("city"),
        region=location.get("region"),
        country=location.get("country"),
        latitude=location.get("lat"),
        longitude=location.get("lon"),
        isp=location.get("isp"),
        user_agent=user_agent,
        match_score=match_score,
        decision=decision,
        reasoning="\n".join(reasons),
    )
    db.session.add(entry)
    db.session.commit()
    return entry


def alert_in_cooldown(user_id):
    if ALERT_COOLDOWN_MINUTES <= 0:
        return False
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=ALERT_COOLDOWN_MINUTES)
    recent = db.session.scalar(
        select(LoginHistory.id)
        .where(LoginHistory.user_id == user_id, LoginHistory.alert_dispatched_at >= cutoff)
        .limit(1)
    )
    return recent is not None


def register_failure(user, entry, location, ip_address, user_agent, kind, match_score, reasons):
    """Counts one more consecutive failure; alerts on every Nth one."""
    user.failed_behavior_attempts = (user.failed_behavior_attempts or 0) + 1
    strikes = user.failed_behavior_attempts

    should_alert = strikes >= ALERT_AFTER_ATTEMPTS and strikes % ALERT_AFTER_ATTEMPTS == 0
    dispatched = False

    if should_alert and alert_in_cooldown(user.id):
        print(f"[alert] suppressed for {user.username}: an alert was already sent in the last "
              f"{ALERT_COOLDOWN_MINUTES} min")
        should_alert = False

    if should_alert:
        entry.alert_dispatched_at = datetime.now(timezone.utc).replace(tzinfo=None)

    db.session.commit()

    if should_alert:
        base_url = PUBLIC_BASE_URL or request.url_root.rstrip("/")
        trust_url = f"{base_url}/trust/{serializer.dumps(entry.id)}"
        change_password_url = f"{base_url}/reset-password/{serializer.dumps(user.id, salt='pwreset')}"
        block_url = f"{base_url}/block-account/{serializer.dumps(user.id, salt='blockaccount')}"

        dispatch_in_background(app, entry.id, {
            "kind": kind,
            "to_email": user.email,
            "phone": user.phone_number,
            "username": user.username,
            "ip": ip_address,
            "location_label": entry.location_label(),
            "match_score": match_score,
            "reasons": reasons,
            "trust_url": trust_url,
            "change_password_url": change_password_url,
            "block_url": block_url,
            "lat": location.get("lat"),
            "lon": location.get("lon"),
            "map_url": build_map_url(location.get("lat"), location.get("lon")),
            "attempt_time": format_ist(),
            "device": device_label(user_agent),
            "isp": location.get("isp"),
        })
        dispatched = True

    return dispatched


@app.route("/login", methods=["POST"])
@limiter.limit("10 per minute")
def process_login():
    data = request.get_json(silent=True)

    if not data:
        return jsonify({"success": False, "message": "Invalid login request"}), 400

    username = data.get("username", "").strip()
    password = data.get("password", "")
    behavioral_sample = data.get("behavioralSample")

    if not username or not password:
        return jsonify({"success": False, "message": "Username and password are required"}), 400

    ip_address = get_client_ip(request)
    location = lookup_location(ip_address)
    ip_address = location.get("ip") or ip_address
    user_agent = (request.headers.get("User-Agent") or "")[:255]

    user = db.session.scalar(select(User).where(User.username == username))

    if user is None:
        return jsonify({"success": False, "message": "Invalid username or password"}), 401

    if not check_password_hash(user.password_hash, password):
        reasons = ["Incorrect password was entered"]
        entry = record_attempt(user, ip_address, location, user_agent,
                               "wrong_password", None, reasons)
        register_failure(user, entry, location, ip_address, user_agent,
                         "password", None, reasons)
        return jsonify({"success": False, "message": "Invalid username or password"}), 401

    if not user.email_verified:
        return jsonify({
            "success": False,
            "message": "Please verify your email before logging in. Check your inbox for the verification link."
        }), 403

    if user.account_locked:
        return jsonify({
            "success": False,
            "message": "This account has been locked for security. Reset your password to unlock it."
        }), 403

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    if user.locked_until and now_utc < user.locked_until:
        remaining = int((user.locked_until - now_utc).total_seconds() / 60) + 1
        return jsonify({
            "success": False,
            "message": f"Too many suspicious attempts. Try again in {remaining} minute(s), or reset your password to unlock immediately."
        }), 403

    if user.otp_locked_until and now_utc < user.otp_locked_until:
        remaining = int((user.otp_locked_until - now_utc).total_seconds() / 60) + 1
        return jsonify({
            "success": False,
            "message": f"Too many incorrect codes. Try again in {remaining} minute(s), or reset your password to unlock immediately."
        }), 403

    if user.otp_hash and user.otp_expires_at and now_utc < user.otp_expires_at:
        return jsonify({
            "success": False,
            "requiresOtp": True,
            "message": "A verification code is already pending for this account. Check your email and enter it."
        }), 401

    device_key = device_fingerprint(user_agent)

    profile = db.session.scalar(
        select(BehavioralProfile).where(
            BehavioralProfile.user_id == user.id,
            BehavioralProfile.device_fingerprint == device_key,
        )
    )

    if profile is None:
        has_any_profile = db.session.scalar(
            select(BehavioralProfile.id).where(BehavioralProfile.user_id == user.id).limit(1)
        ) is not None

        if not has_any_profile:
            session["user_id"] = user.id
            return jsonify({
                "success": True,
                "message": "Login successful. Behavioral enrollment required.",
                "redirect": "/enroll"
            })

        # Profile exists for a different device - never auto-trust a new
        # device, only an email click (proving inbox access) can approve it.
        reasons = [f"Login from an unrecognized device ({device_label(user_agent)})"]
        entry = record_attempt(user, ip_address, location, user_agent,
                               "new_device", None, reasons)

        if alert_in_cooldown(user.id):
            print(f"[new-device alert] suppressed for {user.username}: alert already sent recently")
        else:
            entry.alert_dispatched_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.session.commit()

            base_url = PUBLIC_BASE_URL or request.url_root.rstrip("/")
            approve_token = serializer.dumps(
                {"user_id": user.id, "device_fingerprint": device_key},
                salt="newdevice"
            )
            approve_url = f"{base_url}/approve-device/{approve_token}"
            change_password_url = f"{base_url}/reset-password/{serializer.dumps(user.id, salt='pwreset')}"
            block_url = f"{base_url}/block-account/{serializer.dumps(user.id, salt='blockaccount')}"

            email_ok, email_detail = send_login_alert(
                to_email=user.email,
                username=user.username,
                ip_address=ip_address,
                location_label=entry.location_label(),
                match_score=None,
                reasons=reasons,
                trust_url=approve_url,
                change_password_url=change_password_url,
                block_url=block_url,
                latitude=location.get("lat"),
                longitude=location.get("lon"),
                map_url=build_map_url(location.get("lat"), location.get("lon")),
                attempt_time=format_ist(),
                device=device_label(user_agent),
                isp=location.get("isp"),
                kind="new_device",
            )
            print(f"[new-device email] ok={email_ok} detail={email_detail}")

        return jsonify({
            "success": False,
            "message": "New device detected. Check your email to approve it before logging in here."
        }), 403

    session["user_id"] = user.id

    if not behavioral_sample:
        return jsonify({"success": False, "message": "Behavioral sample is required"}), 400

    typed_challenge = (data.get("typedChallenge") or "").strip()
    if not user.challenge_phrase or typed_challenge != user.challenge_phrase:
        return jsonify({
            "success": False,
            "message": "Please type the verification phrase exactly as shown"
        }), 400


    
    if data.get("pasteDetected"):
        reasons = [
            "Verification phrase appears to have been pasted rather than "
            "typed, which bypasses typing-rhythm verification"
        ]
        entry = record_attempt(user, ip_address, location, user_agent,
                               "blocked", 0.0, reasons)
        alert_triggered = register_failure(user, entry, location, ip_address,
                                           user_agent, "behavior", 0.0, reasons)
        session.clear()
        return jsonify({
            "success": False,
            "message": "Behavioral verification failed",
            "score": 0.0,
            "reasons": reasons,
            "attempt": user.failed_behavior_attempts,
            "alertTriggered": alert_triggered
        }), 401

    is_valid, validation_error = validate_behavioral_sample(behavioral_sample)
    if not is_valid:
        return jsonify({"success": False, "message": "Invalid behavioral sample"}), 400

    sample = sample_to_feature_dict(behavioral_sample)

    result = evaluate_login(user.id, profile, sample)

    match_score = result["match_score"]
    decision = result["decision"]
    reasons = result["reasons"]

    # --- Impossible travel check ---
    # Compare this attempt's location/time against the user's last
    # ALLOWED login. If the implied travel speed is physically impossible,
    # block regardless of behavioral match - a passed typing test doesn't
    # explain being in two places at once.
    previous_login = db.session.scalar(
        select(LoginHistory)
        .where(
            LoginHistory.user_id == user.id,
            LoginHistory.decision == "allowed",
            LoginHistory.latitude.isnot(None),
            LoginHistory.longitude.isnot(None),
        )
        .order_by(LoginHistory.timestamp.desc())
        .limit(1)
    )

    if previous_login is not None and location.get("lat") is not None and location.get("lon") is not None:
        is_impossible, distance_km, hours_elapsed, speed_kmh = check_impossible_travel(
            previous_login.latitude, previous_login.longitude, previous_login.timestamp,
            location["lat"], location["lon"],
            datetime.now(timezone.utc).replace(tzinfo=None)
        )

        if is_impossible:
            decision = "blocked"
            reasons = [
                f"Impossible travel: previous login was {distance_km} km away, "
                f"{hours_elapsed} hours ago (would require {speed_kmh} km/h to be genuine)"
            ] + reasons

    entry = record_attempt(user, ip_address, location, user_agent,
                           decision, match_score, reasons)

    if decision != "allowed":
        user.challenge_attempts = (user.challenge_attempts or 0) + 1

        # First two bad attempts: just let them retry - a genuine user can
        # fumble the phrase once or twice without being treated as a threat.
        if user.challenge_attempts < 3:
            db.session.commit()
            session.clear()
            return jsonify({
                "success": False,
                "message": "Behavioral verification failed. Please try again."
            }), 401

        # 3rd (or later) consecutive bad attempt - require an email OTP
        # before this device gets another chance.
        otp_code = f"{secrets.randbelow(1000000):06d}"
        user.otp_hash = generate_password_hash(otp_code)
        user.otp_expires_at = now_utc + timedelta(minutes=2)
        user.otp_history_id = entry.id
        user.otp_wrong_count = 0
        user.otp_resend_count = 0
        db.session.commit()

        base_url = PUBLIC_BASE_URL or request.url_root.rstrip("/")
        email_ok, email_detail = send_action_link_email(
            to_email=user.email,
            username=user.username,
            title="Your SentinelType verification code",
            message_html=(
                "Your typing pattern didn't match closely enough across a few attempts, "
                "so we need one more check before letting you in. Enter this code to "
                "finish logging in:<br><br>"
                f"<strong style=\"font-size:28px;letter-spacing:4px;\">{otp_code}</strong>"
                "<br><br>This code expires in 2 minutes."
            ),
            button_label="Go to login",
            action_url=f"{base_url}/",
            expiry_note="This code expires in 2 minutes.",
        )
        print(f"[otp email] ok={email_ok} detail={email_detail}")

        register_failure(user, entry, location, ip_address, user_agent,
                         "behavior", match_score, reasons)

        session.clear()
        return jsonify({
            "success": False,
            "requiresOtp": True,
            "message": "Your typing pattern didn't match closely enough. Enter the verification code sent to your email."
        }), 401

    user.failed_behavior_attempts = 0
    user.challenge_attempts = 0

    if match_score >= BASELINE_ADAPTATION_THRESHOLD:
        adapt_profile(profile, sample)

    db.session.commit()

    dispatch_in_background(app, entry.id, {
        "kind": "success",
        "to_email": user.email,
        "phone": user.phone_number,
        "username": user.username,
        "ip": ip_address,
        "location_label": entry.location_label(),
        "match_score": match_score,
        "reasons": [],
        "lat": location.get("lat"),
        "lon": location.get("lon"),
        "map_url": build_map_url(location.get("lat"), location.get("lon")),
        "attempt_time": format_ist(),
        "device": device_label(user_agent),
        "isp": location.get("isp"),
    })

    return jsonify({
        "success": True,
        "message": "Login successful",
        "nextStep": "authenticated",
        "behaviorScore": match_score,
        "redirect": "/dashboard"
    })


# --------------------------------------------------------------------
# Enrollment
# --------------------------------------------------------------------
@app.route("/verify-otp", methods=["POST"])
@limiter.limit("10 per minute")
def verify_otp():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    otp_input = (data.get("otp") or "").strip()

    if not username or not otp_input:
        return jsonify({"success": False, "message": "Username and code are required."}), 400

    user = db.session.scalar(select(User).where(User.username == username))
    if user is None or not user.otp_hash or not user.otp_expires_at:
        return jsonify({"success": False, "message": "No pending verification. Please log in again."}), 400

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    if user.otp_locked_until and now_utc < user.otp_locked_until:
        remaining = int((user.otp_locked_until - now_utc).total_seconds() / 60) + 1
        return jsonify({"success": False, "message": f"Too many incorrect codes. Try again in {remaining} minute(s)."}), 403

    if now_utc > user.otp_expires_at:
        user.otp_hash = None
        user.otp_expires_at = None
        user.otp_history_id = None
        db.session.commit()
        return jsonify({"success": False, "message": "This code has expired. Please log in again."}), 400

    if not check_password_hash(user.otp_hash, otp_input):
        user.otp_wrong_count = (user.otp_wrong_count or 0) + 1

        if user.otp_wrong_count >= 4:
            if user.otp_lockout_stage == 0:
                user.otp_locked_until = now_utc + timedelta(minutes=15)
                user.otp_lockout_stage = 1
                lock_msg = "Too many incorrect codes. You can try again in 15 minutes, or reset your password to unlock immediately."
            elif user.otp_lockout_stage == 1:
                user.otp_locked_until = now_utc + timedelta(minutes=30)
                user.otp_lockout_stage = 2
                lock_msg = "Too many incorrect codes again. You can try again in 30 minutes, or reset your password to unlock immediately."
            else:
                user.account_locked = True
                lock_msg = "Too many incorrect codes. This account is now locked - reset your password to unlock it."

            user.otp_wrong_count = 0
            user.otp_resend_count = 0
            user.otp_hash = None
            user.otp_expires_at = None
            user.otp_history_id = None
            db.session.commit()

            return jsonify({"success": False, "message": lock_msg}), 403

        db.session.commit()
        remaining_tries = 4 - user.otp_wrong_count
        return jsonify({
            "success": False,
            "message": f"Incorrect code. {remaining_tries} attempt(s) left before a temporary lock."
        }), 401

    entry = db.session.get(LoginHistory, user.otp_history_id) if user.otp_history_id else None
    if entry:
        entry.decision = "allowed"
        entry.reasoning = (entry.reasoning or "") + "\nVerified via email OTP"

    match_score = entry.match_score if entry else None

    user.failed_behavior_attempts = 0
    user.challenge_attempts = 0
    user.otp_hash = None
    user.otp_expires_at = None
    user.otp_history_id = None
    user.otp_wrong_count = 0
    user.otp_resend_count = 0
    user.otp_lockout_stage = 0
    user.otp_locked_until = None
    db.session.commit()

    session.clear()
    session["user_id"] = user.id

    ip_address = get_client_ip(request)
    location = lookup_location(ip_address)
    user_agent = (request.headers.get("User-Agent") or "")[:255]

    dispatch_in_background(app, entry.id if entry else None, {
        "kind": "success",
        "to_email": user.email,
        "phone": user.phone_number,
        "username": user.username,
        "ip": ip_address,
        "location_label": entry.location_label() if entry else "Unknown location",
        "match_score": match_score,
        "reasons": ["Verified via email OTP after a partial behavioral match"],
        "lat": location.get("lat"),
        "lon": location.get("lon"),
        "map_url": build_map_url(location.get("lat"), location.get("lon")),
        "attempt_time": format_ist(),
        "device": device_label(user_agent),
        "isp": location.get("isp"),
    })

    return jsonify({"success": True, "message": "Verified! Logging you in.", "redirect": "/dashboard"})


@app.route("/resend-otp", methods=["POST"])
@limiter.limit("5 per minute")
def resend_otp():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()

    user = db.session.scalar(select(User).where(User.username == username))
    if user is None or not user.otp_history_id:
        return jsonify({"success": False, "message": "No pending verification. Please log in again."}), 400

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    if user.otp_locked_until and now_utc < user.otp_locked_until:
        remaining = int((user.otp_locked_until - now_utc).total_seconds() / 60) + 1
        return jsonify({"success": False, "message": f"Too many attempts. Try again in {remaining} minute(s)."}), 403

    if user.otp_lockout_stage >= 1:
        if user.otp_resend_count >= 2:
            if user.otp_lockout_stage == 1:
                user.otp_locked_until = now_utc + timedelta(minutes=30)
                user.otp_lockout_stage = 2
                lock_msg = "Too many resend requests. You can try again in 30 minutes, or reset your password to unlock immediately."
            else:
                user.account_locked = True
                lock_msg = "Too many resend requests. This account is now locked - reset your password to unlock it."

            user.otp_wrong_count = 0
            user.otp_resend_count = 0
            user.otp_hash = None
            user.otp_expires_at = None
            user.otp_history_id = None
            db.session.commit()
            return jsonify({"success": False, "message": lock_msg}), 403

        user.otp_resend_count += 1

    otp_code = f"{secrets.randbelow(1000000):06d}"
    user.otp_hash = generate_password_hash(otp_code)
    user.otp_expires_at = now_utc + timedelta(minutes=2)
    user.otp_wrong_count = 0
    db.session.commit()

    base_url = PUBLIC_BASE_URL or request.url_root.rstrip("/")
    email_ok, email_detail = send_action_link_email(
        to_email=user.email,
        username=user.username,
        title="Your new SentinelType verification code",
        message_html=(
            "Here's your new verification code:<br><br>"
            f"<strong style=\"font-size:28px;letter-spacing:4px;\">{otp_code}</strong>"
            "<br><br>This code expires in 2 minutes."
        ),
        button_label="Go to login",
        action_url=f"{base_url}/",
        expiry_note="This code expires in 2 minutes.",
    )
    print(f"[resend-otp email] ok={email_ok} detail={email_detail}")

    return jsonify({"success": True, "message": "A new code has been sent to your email."})

@app.route("/api/enrollment", methods=["POST"])
def enrollment():
    data = request.get_json(silent=True)

    if not data:
        return jsonify({"success": False, "message": "Invalid enrollment request"}), 400

    samples = data.get("samples")

    if not isinstance(samples, list):
        return jsonify({"success": False, "message": "Samples must be a list"}), 400

    if len(samples) != 7:
        return jsonify({"success": False, "message": "Exactly 7 samples are required"}), 400

    user_id = session.get("user_id")

    if user_id is None:
        return jsonify({"success": False, "message": "User is not logged in"}), 401

    user_agent = (request.headers.get("User-Agent") or "")[:255]
    device_key = device_fingerprint(user_agent)

    validated_samples = []

    for index, sample in enumerate(samples, start=1):
        if not isinstance(sample, dict):
            return jsonify({"success": False, "message": f"Invalid sample at position {index}"}), 400

        for field in REQUIRED_SAMPLE_FIELDS:
            if field not in sample:
                return jsonify({"success": False, "message": f"Missing {field} in sample {index}"}), 400

        try:
            parsed = sample_to_feature_dict(sample)
        except (TypeError, ValueError):
            return jsonify({"success": False, "message": f"Invalid numeric value in sample {index}"}), 400

        if not all(isfinite(v) for v in parsed.values()):
            return jsonify({"success": False, "message": f"Non-finite value in sample {index}"}), 400

        if not 10 <= parsed["averageDwellTime"] <= 1000:
            return jsonify({"success": False, "message": f"Unrealistic dwell time in sample {index}"}), 400
        if not 0 <= parsed["averageFlightTime"] <= 8000:
            return jsonify({"success": False, "message": f"Unrealistic flight time in sample {index}"}), 400
        if not 100 <= parsed["typingDuration"] <= 180000:
            return jsonify({"success": False, "message": f"Unrealistic typing duration in sample {index}"}), 400
        if not 1 <= parsed["typingSpeedWPM"] <= 200:
            return jsonify({"success": False, "message": f"Unrealistic typing speed in sample {index}"}), 400

        validated_samples.append(parsed)

    sample_count = len(validated_samples)

    def _avg(key):
        return sum(s[key] for s in validated_samples) / sample_count

    average_dwell_time = _avg("averageDwellTime")
    average_flight_time = _avg("averageFlightTime")
    average_typing_speed_wpm = _avg("typingSpeedWPM")
    average_typing_duration = _avg("typingDuration")
    average_mouse_speed = _avg("averageMouseSpeed")
    average_click_interval = _avg("averageClickInterval")

    for sample_data in validated_samples:
        db.session.add(BehavioralSample(
            user_id=user_id,
            average_dwell_time=sample_data["averageDwellTime"],
            average_flight_time=sample_data["averageFlightTime"],
            typing_duration=sample_data["typingDuration"],
            typing_speed_wpm=sample_data["typingSpeedWPM"],
            average_mouse_speed=sample_data["averageMouseSpeed"],
            average_click_interval=sample_data["averageClickInterval"],
        ))

    profile = db.session.scalar(
        select(BehavioralProfile).where(
            BehavioralProfile.user_id == user_id,
            BehavioralProfile.device_fingerprint == device_key,
        )
    )

    if profile is None:
        profile = BehavioralProfile(
            user_id=user_id,
            device_fingerprint=device_key,
            average_dwell_time=average_dwell_time,
            average_flight_time=average_flight_time,
            average_typing_speed_wpm=average_typing_speed_wpm,
            average_typing_duration=average_typing_duration,
            average_mouse_speed=average_mouse_speed,
            average_click_interval=average_click_interval,
            sample_count=sample_count
        )
        db.session.add(profile)
    else:
        profile.average_dwell_time = average_dwell_time
        profile.average_flight_time = average_flight_time
        profile.average_typing_speed_wpm = average_typing_speed_wpm
        profile.average_typing_duration = average_typing_duration
        profile.average_mouse_speed = average_mouse_speed
        profile.average_click_interval = average_click_interval
        profile.sample_count = sample_count

    model_path = ml_model.train_and_save(user_id, validated_samples, device_fingerprint=device_key)
    profile.ml_model_path = model_path

    db.session.commit()

    print(f"Enrollment completed: user={user_id}, samples={sample_count}, profile={profile.id}")

    session.clear()

    return jsonify({
        "success": True,
        "message": "Behavioral enrollment completed successfully. You can now log in.",
        "sampleCount": sample_count,
        "profileCreated": True,
        "redirect": "/"
    }), 201

# --------------------------------------------------------------------
# External integration API - lets any third-party application's own
# backend call SentinelType as a pluggable risk-scoring service, without
# using SentinelType's own session/login system. Protected by a shared
# secret API key (server-to-server only - never call this from a browser,
# since the key would be exposed).
# --------------------------------------------------------------------

SENTINEL_API_KEY = os.environ.get("SENTINEL_API_KEY", "")


def require_api_key():
    key = request.headers.get("X-Sentinel-Api-Key", "")
    return bool(SENTINEL_API_KEY) and key == SENTINEL_API_KEY


@app.route("/api/v1/enroll", methods=["POST"])
@limiter.limit("20 per minute")
def api_v1_enroll():
    if not require_api_key():
        return jsonify({"success": False, "message": "Invalid or missing API key"}), 401

    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    samples = data.get("samples")

    if not username:
        return jsonify({"success": False, "message": "username is required"}), 400
    if not isinstance(samples, list) or len(samples) < 5:
        return jsonify({"success": False, "message": "At least 5 behavioral samples are required"}), 400

    user = db.session.scalar(select(User).where(User.username == username))
    if user is None:
        # This account only holds the behavioral baseline - the third-party
        # app keeps owning the real password, SentinelType never sees it.
        user = User(
            username=username,
            password_hash=generate_password_hash(secrets.token_hex(16)),
            email=f"{username}@external.local",
            email_verified=True,
            challenge_phrase=generate_challenge_phrase(),
        )
        db.session.add(user)
        db.session.flush()

    validated_samples = []
    for sample in samples:
        is_valid, err = validate_behavioral_sample(sample)
        if not is_valid:
            return jsonify({"success": False, "message": err}), 400
        validated_samples.append(sample_to_feature_dict(sample))

    sample_count = len(validated_samples)

    def _avg(key):
        return sum(s[key] for s in validated_samples) / sample_count

    device_key = "external"
    profile = db.session.scalar(
        select(BehavioralProfile).where(
            BehavioralProfile.user_id == user.id,
            BehavioralProfile.device_fingerprint == device_key,
        )
    )
    if profile is None:
        profile = BehavioralProfile(
            user_id=user.id, device_fingerprint=device_key, sample_count=0,
            average_dwell_time=0, average_flight_time=0,
            average_typing_speed_wpm=0, average_typing_duration=0,
        )
        db.session.add(profile)

    profile.average_dwell_time = _avg("averageDwellTime")
    profile.average_flight_time = _avg("averageFlightTime")
    profile.average_typing_speed_wpm = _avg("typingSpeedWPM")
    profile.average_typing_duration = _avg("typingDuration")
    profile.average_mouse_speed = _avg("averageMouseSpeed")
    profile.average_click_interval = _avg("averageClickInterval")
    profile.sample_count = sample_count

    for s in validated_samples:
        db.session.add(BehavioralSample(
            user_id=user.id,
            average_dwell_time=s["averageDwellTime"],
            average_flight_time=s["averageFlightTime"],
            typing_duration=s["typingDuration"],
            typing_speed_wpm=s["typingSpeedWPM"],
            average_mouse_speed=s["averageMouseSpeed"],
            average_click_interval=s["averageClickInterval"],
        ))

    model_path = ml_model.train_and_save(user.id, validated_samples, device_fingerprint=device_key)
    profile.ml_model_path = model_path
    db.session.commit()

    return jsonify({
        "success": True,
        "message": "Enrolled",
        "challengePhrase": user.challenge_phrase
    }), 201


@app.route("/api/v1/verify", methods=["POST"])
@limiter.limit("30 per minute")
def api_v1_verify():
    if not require_api_key():
        return jsonify({"success": False, "message": "Invalid or missing API key"}), 401

    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    sample = data.get("behavioralSample")

    user = db.session.scalar(select(User).where(User.username == username))
    if user is None:
        return jsonify({"success": False, "message": "Unknown username - enroll first"}), 404

    device_key = "external"
    profile = db.session.scalar(
        select(BehavioralProfile).where(
            BehavioralProfile.user_id == user.id,
            BehavioralProfile.device_fingerprint == device_key,
        )
    )
    if profile is None:
        return jsonify({"success": False, "message": "No behavioral baseline enrolled for this user"}), 404

    is_valid, err = validate_behavioral_sample(sample)
    if not is_valid:
        return jsonify({"success": False, "message": err}), 400

    result = evaluate_login(user.id, profile, sample_to_feature_dict(sample))

    return jsonify({
        "success": True,
        "matchScore": result["match_score"],
        "decision": result["decision"],
        "reasons": result["reasons"],
    })

if __name__ == "__main__":
    app.run(debug=True)