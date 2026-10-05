from extensions import db


class BehavioralSample(db.Model):
    __tablename__ = "behavioral_samples"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True
    )

    # Keystroke dynamics
    average_dwell_time = db.Column(db.Float, nullable=False)
    average_flight_time = db.Column(db.Float, nullable=False)
    typing_duration = db.Column(db.Float, nullable=False)
    typing_speed_wpm = db.Column(db.Float, nullable=False)

    # Mouse dynamics (Module 3)
    average_mouse_speed = db.Column(db.Float, nullable=False, default=0)
    average_click_interval = db.Column(db.Float, nullable=False, default=0)

    created_at = db.Column(
        db.DateTime,
        server_default=db.func.current_timestamp(),
        nullable=False
    )
