from extensions import db


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=True)

    email = db.Column(db.String(255), unique=True, nullable=False)
    email_verified = db.Column(db.Boolean, nullable=False, default=False)
    phone_number = db.Column(db.String(20), nullable=True)  # optional, only needed for SMS alerts

    # Consecutive blocked behavioral attempts (password correct, behavior
    # didn't match). Reset to 0 on a successful login. Used to only alert
    # after repeated failures instead of on every single natural variation.
    failed_behavior_attempts = db.Column(db.Integer, nullable=False, default=0)
    account_locked = db.Column(db.Boolean, nullable=False, default=False)

    challenge_attempts = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)
    challenge_phrase = db.Column(db.String(64), nullable=True)

    otp_hash = db.Column(db.String(255), nullable=True)
    otp_expires_at = db.Column(db.DateTime, nullable=True)
    otp_history_id = db.Column(db.Integer, nullable=True)
    otp_wrong_count = db.Column(db.Integer, nullable=False, default=0)
    otp_resend_count = db.Column(db.Integer, nullable=False, default=0)
    otp_lockout_stage = db.Column(db.Integer, nullable=False, default=0)
    otp_locked_until = db.Column(db.DateTime, nullable=True)
    challenge_phrase = db.Column(db.String(64), nullable=True)

    created_at = db.Column(
        db.DateTime,
        server_default=db.func.current_timestamp()
    )

    login_history = db.relationship(
        "LoginHistory",
        backref="user",
        lazy=True,
        cascade="all, delete-orphan"
    )