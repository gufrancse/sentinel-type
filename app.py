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
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import os
import re
from dotenv import load_dotenv
from math import isfinite

from services.decision_engine import evaluate as evaluate_login
from services import ml_model
from services.geolocation import get_client_ip, lookup_location, build_map_url
from services.alerts import dispatch_in_background
from services.email_alert import send_action_link_email
from services.utils import normalize_phone, format_ist, device_label
from dashboard_routes import dashboard_bp

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ["SECRET_KEY"]
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///sentinel_type.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)
migrate = Migrate(app, db)

serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"])
TRUST_TOKEN_MAX_AGE = 7 * 24 * 60 * 60  # 7 days

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# --------------------------------------------------------------------
# Fixed behavioral challenge phrase (Module 2/3).
#
# IMPORTANT: this is typed at BOTH enrollment and every login, on a
# field that is separate from the real password. Using the SAME text
# both times is what makes the typing-rhythm comparison meaningful —
# comparing dwell/flight time across two DIFFERENT pieces of text
# (e.g. a random enrollment phrase vs. the user's actual password)
# is inherently noisy and was causing genuine users to get blocked.
#
# It looks like a password (mixed case + digit + special char) on
# purpose, per your request — but it is NOT secret and must never be
# reused as anyone's real account password.
#
# LENGTH MATTERS: averageDwellTime/averageFlightTime/typingSpeedWPM are
# statistical averages computed over however many keystrokes are in this
# phrase. With the old 11-character phrase, each login sample was built
# from only ~11 keystrokes - too few for those averages to be stable,
# so completely genuine logins were swinging by 20-40% run to run just
# from normal statistical noise, not real behavioral difference. A
# ~20-character phrase roughly doubles the keystrokes per sample, which
# roughly halves that noise (variance of a mean shrinks as 1/n).
# --------------------------------------------------------------------
BEHAVIOR_CHALLENGE_PHRASE = "Ph0enix#Delta-92!Kx"

# Consecutive failed attempts (wrong password OR behavior mismatch) before
# we fire email alerts. We alert on the Nth consecutive failure and again
# on every Nth after that. Set ALERT_AFTER_ATTEMPTS=1 in .env to get an
# alert on every single failure (handy for demos).
ALERT_AFTER_ATTEMPTS = max(1, int(os.environ.get("ALERT_AFTER_ATTEMPTS", "3")))

# After an alert goes out, stay quiet for this many minutes so a flood of
# failures can't spam the account owner.
ALERT_COOLDOWN_MINUTES = max(0, int(os.environ.get("ALERT_COOLDOWN_MINUTES", "2")))

# Public URL used in email links. Set it to your ngrok / Render URL when
# sharing the app, otherwise links point at localhost.
PUBLIC_BASE_URL = (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")

app.config["ALERT_AFTER_ATTEMPTS"] = ALERT_AFTER_ATTEMPTS
app.config["ALERT_COOLDOWN_MINUTES"] = ALERT_COOLDOWN_MINUTES

app.register_blueprint(dashboard_bp)

# Columns added in newer versions. ensure_schema() adds any that are missing
# to an existing sentinel_type.db, so you never have to delete the database
# (and lose your enrolled users) after an update.
EXPECTED_COLUMNS = {
    "users": {
        "failed_behavior_attempts": "INTEGER NOT NULL DEFAULT 0",
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


ensure_schema()

REQUIRED_SAMPLE_FIELDS = [
    "averageDwellTime",
    "averageFlightTime",
    "typingDuration",
    "typingSpeedWPM",
]

# Mouse dynamics fields are optional/additive (Module 3) — older/legacy
# clients that don't send them still work, just with a weaker signal.
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
    """Normalizes an incoming behavioral sample into the canonical
    feature dict used everywhere downstream (ML model, decision engine,
    stored records)."""
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
    return render_template("login.html", behavior_challenge=BEHAVIOR_CHALLENGE_PHRASE)


@app.route("/register")
def register_page():
    return render_template("register.html")


@app.route("/enroll")
def enroll_page():
    user_id = session.get("user_id")

    if user_id is None:
        return jsonify({
            "success": False,
            "message": "You must register or login first"
        }), 401

    profile = db.session.scalar(
        select(BehavioralProfile).where(BehavioralProfile.user_id == user_id)
    )

    if profile is not None:
        return jsonify({
            "success": False,
            "message": "Behavioral enrollment already completed"
        }), 409

    return render_template("enroll.html", enrollment_challenge=BEHAVIOR_CHALLENGE_PHRASE)


# --------------------------------------------------------------------
# Trust this device (Module 9)
# --------------------------------------------------------------------

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

    if password == BEHAVIOR_CHALLENGE_PHRASE:
        return jsonify({
            "success": False,
            "message": "Please don't use the behavioral sample phrase as your password"
        }), 400

    existing_user = db.session.scalar(select(User).where(User.username == username))
    if existing_user is not None:
        return jsonify({"success": False, "message": "Username already exists"}), 409

    existing_email = db.session.scalar(select(User).where(User.email == email))
    if existing_email is not None:
        return jsonify({"success": False, "message": "Email already registered"}), 409

    user = User(
        username=username,
        password_hash=generate_password_hash(password),
        email=email,
        phone_number=phone_number,
    )

    db.session.add(user)
    db.session.commit()

    session.clear()
    session["user_id"] = user.id

    print(f"New user registered: id={user.id}, username={user.username}")

    return jsonify({
        "success": True,
        "message": "Registration successful",
        "redirect": "/enroll"
    }), 201


# --------------------------------------------------------------------
# Account recovery: forgot password, and "reset my typing profile"
#
# Typing rhythm naturally drifts over days/devices/mood. Rather than
# leaving a genuine user permanently locked out by the behavioral check,
# this gives them a safe, email-verified way back in: confirm you own
# the account's email, then redo enrollment from scratch. It reuses the
# same signed-link mechanism as password reset, just with its own salt
# so the two token types can never be confused with each other.
# --------------------------------------------------------------------

RESET_TOKEN_MAX_AGE = 60 * 60  # 1 hour


@app.route("/forgot-password")
def forgot_password_page():
    return render_template("forgot_password.html")


@app.route("/forgot-password", methods=["POST"])
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

    # Always return the same generic message whether or not the email
    # exists, so this can't be used to check which emails are registered.
    if user is None:
        return jsonify(generic)

    token = serializer.dumps(user.id, salt="pwreset")
    base_url = PUBLIC_BASE_URL or request.url_root.rstrip("/")
    reset_url = f"{base_url}/reset-password/{token}"

    send_action_link_email(
        to_email=user.email,
        username=user.username,
        title="Reset your password",
        message_html="Click below to set a new password. This link works once and expires in 1 hour.",
        button_label="Reset my password",
        action_url=reset_url,
        expiry_note="This link expires in 1 hour.",
    )

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
    if password == BEHAVIOR_CHALLENGE_PHRASE:
        return jsonify({
            "success": False,
            "message": "Please don't use the behavioral sample phrase as your password"
        }), 400

    user = db.session.get(User, user_id)
    if user is None:
        return jsonify({"success": False, "message": "Account not found"}), 404

    user.password_hash = generate_password_hash(password)
    user.failed_behavior_attempts = 0
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
        # Same message as a normal failed login - doesn't reveal which part was wrong.
        return jsonify({"success": False, "message": "Invalid username or password"}), 401

    token = serializer.dumps(user.id, salt="profilereset")
    base_url = PUBLIC_BASE_URL or request.url_root.rstrip("/")
    confirm_url = f"{base_url}/reset-profile/confirm/{token}"

    email_sent, _ = send_action_link_email(
        to_email=user.email,
        username=user.username,
        title="Confirm typing-profile reset",
        message_html=(
            "We got a request to reset your typing profile (used for behavioral "
            "login verification). Click below to confirm and re-enroll a fresh "
            "profile. This link works once and expires in 1 hour."
        ),
        button_label="Reset my typing profile",
        action_url=confirm_url,
        expiry_note="This link expires in 1 hour.",
    )

    if not email_sent:
        return jsonify({
            "success": False,
            "message": "Password confirmed, but the confirmation email couldn't be sent "
                       "(check MAIL_USERNAME / MAIL_APP_PASSWORD in .env)."
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
# Login  (Modules 1, 2, 3, 4, 5, 6, 7, 8, 10 all meet here)
# --------------------------------------------------------------------

def record_attempt(user, ip_address, location, user_agent, decision, match_score, reasons):
    """Writes one row to login_history (every attempt is logged - Module 10)."""
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
    """
    Counts one more consecutive failure. On every Nth failure an alert is
    sent (email, in a background thread). Returns True if an alert was
    dispatched.
    """
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
def process_login():
    data = request.get_json(silent=True)

    if not data:
        return jsonify({"success": False, "message": "Invalid login request"}), 400

    username = data.get("username", "").strip()
    password = data.get("password", "")
    behavioral_sample = data.get("behavioralSample")

    if not username or not password:
        return jsonify({"success": False, "message": "Username and password are required"}), 400

    # IP + location + device are captured for EVERY attempt, allowed or
    # blocked, so the dashboard has a full history (Module 10).
    # (On localhost this resolves to your machine's public IP for demos.)
    ip_address = get_client_ip(request)
    location = lookup_location(ip_address)
    ip_address = location.get("ip") or ip_address
    user_agent = (request.headers.get("User-Agent") or "")[:255]

    user = db.session.scalar(select(User).where(User.username == username))

    if user is None:
        return jsonify({"success": False, "message": "Invalid username or password"}), 401

    if not check_password_hash(user.password_hash, password):
        # Wrong password on a real account: log it and count it as a strike,
        # so repeated guessing triggers an alert.
        reasons = ["Incorrect password was entered"]
        entry = record_attempt(user, ip_address, location, user_agent,
                               "wrong_password", None, reasons)
        register_failure(user, entry, location, ip_address, user_agent,
                         "password", None, reasons)
        return jsonify({"success": False, "message": "Invalid username or password"}), 401

    session["user_id"] = user.id

    profile = db.session.scalar(
        select(BehavioralProfile).where(BehavioralProfile.user_id == user.id)
    )

    if profile is None:
        return jsonify({
            "success": True,
            "message": "Login successful. Behavioral enrollment required.",
            "redirect": "/enroll"
        })

    if not behavioral_sample:
        return jsonify({"success": False, "message": "Behavioral sample is required"}), 400

    typed_challenge = (data.get("typedChallenge") or "").strip()
    if typed_challenge != BEHAVIOR_CHALLENGE_PHRASE:
        return jsonify({
            "success": False,
            "message": "Please type the verification phrase exactly as shown"
        }), 400

    is_valid, validation_error = validate_behavioral_sample(behavioral_sample)
    if not is_valid:
        return jsonify({"success": False, "message": "Invalid behavioral sample"}), 400

    sample = sample_to_feature_dict(behavioral_sample)

    # --- Module 5/6/7: ML score + decision + explanation ---
    result = evaluate_login(user.id, profile, sample)

    match_score = result["match_score"]
    decision = result["decision"]
    reasons = result["reasons"]

    # --- Module 10: log every attempt ---
    entry = record_attempt(user, ip_address, location, user_agent,
                           decision, match_score, reasons)

    if decision == "blocked":
        session.clear()

        alert_triggered = register_failure(user, entry, location, ip_address, user_agent,
                                           "behavior", match_score, reasons)

        return jsonify({
            "success": False,
            "message": "Behavioral verification failed",
            "score": match_score,
            "reasons": reasons,
            "attempt": user.failed_behavior_attempts,
            "alertTriggered": alert_triggered
        }), 401

    # Genuine login - reset the strike counter
    user.failed_behavior_attempts = 0
    db.session.commit()

    return jsonify({
        "success": True,
        "message": "Login successful",
        "nextStep": "authenticated",
        "behaviorScore": match_score,
        "redirect": "/dashboard"
    })


# --------------------------------------------------------------------
# Enrollment  (Modules 1, 2, 3, 4, 5)
# --------------------------------------------------------------------

@app.route("/api/enrollment", methods=["POST"])
def enrollment():
    data = request.get_json(silent=True)

    if not data:
        return jsonify({"success": False, "message": "Invalid enrollment request"}), 400

    samples = data.get("samples")

    if not isinstance(samples, list):
        return jsonify({"success": False, "message": "Samples must be a list"}), 400

    if len(samples) != 10:
        return jsonify({"success": False, "message": "Exactly 10 samples are required"}), 400

    user_id = session.get("user_id")

    if user_id is None:
        return jsonify({"success": False, "message": "User is not logged in"}), 401

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
        if not 0 <= parsed["averageFlightTime"] <= 5000:
            return jsonify({"success": False, "message": f"Unrealistic flight time in sample {index}"}), 400
        if not 100 <= parsed["typingDuration"] <= 120000:
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
        select(BehavioralProfile).where(BehavioralProfile.user_id == user_id)
    )

    if profile is None:
        profile = BehavioralProfile(
            user_id=user_id,
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

    # --- Module 5: train the per-user Isolation Forest on these samples ---
    model_path = ml_model.train_and_save(user_id, validated_samples)
    profile.ml_model_path = model_path

    db.session.commit()

    print(f"Enrollment completed: user={user_id}, samples={sample_count}, profile={profile.id}")

    # Log the user out after enrollment so they go through a real login
    # (with the behavioral check) right after, instead of skipping straight
    # to the dashboard on the same session.
    session.clear()

    return jsonify({
        "success": True,
        "message": "Behavioral enrollment completed successfully. You can now log in.",
        "sampleCount": sample_count,
        "profileCreated": True,
        "redirect": "/"
    }), 201


if __name__ == "__main__":
    app.run(debug=True)