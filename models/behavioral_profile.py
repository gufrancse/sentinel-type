from extensions import db


class BehavioralProfile(db.Model):
    __tablename__ = "behavioral_profiles"
    __table_args__ = (
        db.UniqueConstraint("user_id", "device_fingerprint", name="uq_profile_user_device"),
    )

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True
    )
    
    device_fingerprint = db.Column(db.String(64), nullable=False, default="unknown")

    # --- Keystroke dynamics baseline ---
    average_dwell_time = db.Column(db.Float, nullable=False)
    average_flight_time = db.Column(db.Float, nullable=False)
    average_typing_speed_wpm = db.Column(db.Float, nullable=False)
    average_typing_duration = db.Column(db.Float, nullable=False)

    # --- Mouse dynamics baseline (Module 3) ---
    average_mouse_speed = db.Column(db.Float, nullable=False, default=0)
    average_click_interval = db.Column(db.Float, nullable=False, default=0)

    sample_count = db.Column(
        db.Integer,
        nullable=False,
        default=0
    )

    # Path to the trained per-user Isolation Forest model (Module 5)
    ml_model_path = db.Column(db.String(255), nullable=True)

    created_at = db.Column(
        db.DateTime,
        server_default=db.func.current_timestamp()
    )

    updated_at = db.Column(
        db.DateTime,
        server_default=db.func.current_timestamp(),
        onupdate=db.func.current_timestamp()
    )
