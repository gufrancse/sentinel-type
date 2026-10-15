from services import ml_model
from services.behavioral_verification import calculate_behavior_score, explain_differences
ALLOW_THRESHOLD = 50.0
CHALLENGE_THRESHOLD = 40.0
ML_WEIGHT = 0.4
DIFF_WEIGHT = 0.6
BASELINE_ADAPTATION_THRESHOLD = 80.0
ADAPTATION_ALPHA = 0.1 
def evaluate(user_id, profile, sample: dict):
    ml_score = ml_model.score_sample(user_id, sample, model_path=profile.ml_model_path)
    diff_score, differences = calculate_behavior_score(profile, sample)
    diff_match = round(max(0.0, 100 - diff_score), 2)
    if ml_score is not None:
        match_score = round(ML_WEIGHT * ml_score + DIFF_WEIGHT * diff_match, 2)
    else:
        match_score = diff_match
        if match_score >= ALLOW_THRESHOLD:
            decision = "allowed"
        elif match_score >= CHALLENGE_THRESHOLD:
            decision = "challenge"
        else:
            decision = "blocked"
    reasons = explain_differences(differences)
    return {
        "match_score": match_score,
        "decision": decision,
        "reasons": reasons,
        "differences": differences,
    }
def adapt_profile(profile, sample):
    profile.average_dwell_time = (
        (1 - ADAPTATION_ALPHA) * profile.average_dwell_time
        + ADAPTATION_ALPHA * sample["averageDwellTime"]
    )
    profile.average_flight_time = (
        (1 - ADAPTATION_ALPHA) * profile.average_flight_time
        + ADAPTATION_ALPHA * sample["averageFlightTime"]
    )
    profile.average_typing_speed_wpm = (
        (1 - ADAPTATION_ALPHA) * profile.average_typing_speed_wpm
        + ADAPTATION_ALPHA * sample["typingSpeedWPM"]
    )
    profile.average_typing_duration = (
        (1 - ADAPTATION_ALPHA) * profile.average_typing_duration
        + ADAPTATION_ALPHA * sample["typingDuration"]
    )
    if sample.get("averageMouseSpeed", 0) > 0:
        profile.average_mouse_speed = (
            (1 - ADAPTATION_ALPHA) * profile.average_mouse_speed
            + ADAPTATION_ALPHA * sample["averageMouseSpeed"]
        )
    if sample.get("averageClickInterval", 0) > 0:
        profile.average_click_interval = (
            (1 - ADAPTATION_ALPHA) * profile.average_click_interval
            + ADAPTATION_ALPHA * sample["averageClickInterval"]
        )