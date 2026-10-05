from extensions import db


class LoginHistory(db.Model):
    """
    Module 10 — Login History Dashboard.
    Every login attempt (allowed or blocked) is logged here so the
    dashboard can show past attempts + a match-score trend graph.
    """
    __tablename__ = "login_history"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True
    )

    timestamp = db.Column(
        db.DateTime,
        server_default=db.func.current_timestamp(),
        nullable=False
    )

    # Network / location info (used in dashboard + alerts)
    ip_address = db.Column(db.String(64), nullable=True)
    city = db.Column(db.String(100), nullable=True)
    region = db.Column(db.String(100), nullable=True)
    country = db.Column(db.String(100), nullable=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    isp = db.Column(db.String(150), nullable=True)

    # Raw browser User-Agent (dashboard shows it as "Chrome on Windows")
    user_agent = db.Column(db.String(255), nullable=True)

    # Isolation Forest match/risk score, 0-100 (Module 5)
    match_score = db.Column(db.Float, nullable=True)

    # "allowed", "blocked" (behavior mismatch) or "wrong_password"
    decision = db.Column(db.String(20), nullable=False)

    # Human-readable explanation, e.g. "Typing speed 45% slower than usual"
    # stored as newline-separated text (Module 7)
    reasoning = db.Column(db.Text, nullable=True)

    # Alerts (Module 8)
    email_alert_sent = db.Column(db.Boolean, nullable=False, default=False)
    sms_alert_sent = db.Column(db.Boolean, nullable=False, default=False)
    whatsapp_alert_sent = db.Column(db.Boolean, nullable=False, default=False)

    # Set when an alert was dispatched for this attempt (used for the
    # alert cool-down so a flood of failures can't spam the owner)
    alert_dispatched_at = db.Column(db.DateTime, nullable=True)

    # Module 9 — "Trust this device" — set True once the genuine user
    # confirms a blocked attempt was actually them
    marked_trusted = db.Column(db.Boolean, nullable=False, default=False)

    def location_label(self):
        parts = [p for p in [self.city, self.region, self.country] if p]
        return ", ".join(parts) if parts else "Unknown location"

    def map_url(self):
        if self.latitude is None or self.longitude is None:
            return None
        return f"https://www.google.com/maps?q={self.latitude},{self.longitude}"