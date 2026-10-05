"""
Module 7 — Explainable Reasoning Layer.

The Isolation Forest model (services/ml_model.py) decides the actual
match/risk score. This module exists purely to turn the raw numeric
differences between a login sample and the user's stored baseline into
plain-English reasons, e.g.:

    "Typing speed 45% slower than usual"
    "Unusual pause pattern between keys"

so alerts and the dashboard don't just show a black-box number.
"""

FEATURE_LABELS = {
    "dwellTime": "Key dwell time (how long keys are held)",
    "flightTime": "Flight time (gap between keystrokes)",
    "typingSpeedWPM": "Typing speed",
    "typingDuration": "Overall typing duration",
    "mouseSpeed": "Mouse movement speed",
    "clickInterval": "Time between clicks",
}


def calculate_percentage_difference(profile_value, sample_value):
    """Percentage difference between the stored baseline and a new sample."""
    if profile_value is None or profile_value <= 0:
        return float("inf") if sample_value else 0.0

    difference = abs(sample_value - profile_value)
    return (difference / profile_value) * 100


def calculate_behavior_score(profile, sample):
    """
    Compare a behavioral sample against the user's stored baseline.

    Returns:
        score: overall average percentage difference across all features
        differences: per-feature percentage differences
    """

    differences = {
        "dwellTime": calculate_percentage_difference(
            profile.average_dwell_time, sample["averageDwellTime"]
        ),
        "flightTime": calculate_percentage_difference(
            profile.average_flight_time, sample["averageFlightTime"]
        ),
        "typingSpeedWPM": calculate_percentage_difference(
            profile.average_typing_speed_wpm, sample["typingSpeedWPM"]
        ),
        "typingDuration": calculate_percentage_difference(
            profile.average_typing_duration, sample["typingDuration"]
        ),
        "mouseSpeed": calculate_percentage_difference(
            getattr(profile, "average_mouse_speed", 0) or 0,
            sample.get("averageMouseSpeed", 0)
        ),
        "clickInterval": calculate_percentage_difference(
            getattr(profile, "average_click_interval", 0) or 0,
            sample.get("averageClickInterval", 0)
        ),
    }

    differences = {k: round(min(v, 999), 2) for k, v in differences.items()}

    score = sum(differences.values()) / len(differences)

    return round(score, 2), differences


def explain_differences(differences, top_n=3):
    """
    Turn the largest per-feature differences into human-readable reasons.
    Only features that deviate meaningfully (>20%) are mentioned.
    """
    significant = [
        (feature, diff) for feature, diff in differences.items() if diff > 20
    ]

    significant.sort(key=lambda item: item[1], reverse=True)

    if not significant:
        return ["Typing and mouse behavior closely matched the stored profile."]

    reasons = []
    for feature, diff in significant[:top_n]:
        label = FEATURE_LABELS.get(feature, feature)
        reasons.append(f"{label} differed by {diff:.0f}% from the usual pattern")

    return reasons
