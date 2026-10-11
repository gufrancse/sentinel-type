"""
Module evaluation harness - produces the accuracy numbers a project
report/viva needs: False Acceptance Rate (FAR), False Rejection Rate
(FRR), and the Equal Error Rate (EER), swept across match-score
thresholds.

WHAT THIS SIMULATES
--------------------------------------------------------------------
Real impostor data is something we deliberately don't have, so we
simulate:

  GENUINE trials : the enrolled user's own typing rhythm with natural
                    session-to-session noise (tiredness, different
                    keyboard, etc).
  IMPOSTOR trials: a different person's typing rhythm, simulating
                    someone else typing the same phrase.

USAGE
--------------------------------------------------------------------
    python scripts/evaluate_model.py

Outputs (next to this script, under evaluation_output/):
    far_frr_curve.png   - FAR and FRR vs threshold, with the EER point
                           and this project's current ALLOW_THRESHOLD
                           marked
    report.md           - the same numbers as readable Markdown,
                           ready to paste into a project report
"""

import os
import sys
import random
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from services import ml_model
from services.decision_engine import evaluate as evaluate_login, ALLOW_THRESHOLD

random.seed(42)  # reproducible numbers for the report

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "evaluation_output")
N_GENUINE_TRIALS = 300
N_IMPOSTOR_TRIALS = 300
N_ENROLLMENT_SAMPLES = 10
EVAL_USER_ID = "eval-harness"  # isolated from real users' saved models


def _noisy(mean, pct_noise):
    sigma = mean * pct_noise
    return max(0.001, random.gauss(mean, sigma))


def make_profile_samples(baseline, pct_noise, n):
    return [{
        "averageDwellTime": _noisy(baseline["dwell"], pct_noise),
        "averageFlightTime": _noisy(baseline["flight"], pct_noise),
        "typingDuration": _noisy(baseline["duration"], pct_noise),
        "typingSpeedWPM": _noisy(baseline["wpm"], pct_noise),
        "averageMouseSpeed": _noisy(baseline["mouse"], pct_noise * 1.5),
        "averageClickInterval": 0,
    } for _ in range(n)]


GENUINE_BASELINE = {"dwell": 100, "flight": 150, "duration": 7500, "wpm": 28, "mouse": 0.035}
IMPOSTOR_BASELINE = {"dwell": 165, "flight": 230, "duration": 11200, "wpm": 17, "mouse": 0.075}

GENUINE_NOISE = 0.10
IMPOSTOR_NOISE = 0.12


def build_profile_object(enrollment_samples):
    n = len(enrollment_samples)
    avg = lambda key: sum(s[key] for s in enrollment_samples) / n
    model_path = ml_model.train_and_save(EVAL_USER_ID, enrollment_samples)
    return SimpleNamespace(
        average_dwell_time=avg("averageDwellTime"),
        average_flight_time=avg("averageFlightTime"),
        average_typing_speed_wpm=avg("typingSpeedWPM"),
        average_typing_duration=avg("typingDuration"),
        average_mouse_speed=avg("averageMouseSpeed"),
        average_click_interval=avg("averageClickInterval"),
        ml_model_path=model_path,
    )


def collect_scores(profile, baseline, noise, n_trials):
    scores = []
    for sample in make_profile_samples(baseline, noise, n_trials):
        result = evaluate_login(EVAL_USER_ID, profile, sample)
        scores.append(result["match_score"])
    return scores


def far_frr_at(threshold, genuine_scores, impostor_scores):
    frr = sum(1 for s in genuine_scores if s < threshold) / len(genuine_scores)
    far = sum(1 for s in impostor_scores if s >= threshold) / len(impostor_scores)
    return far, frr


def find_eer(genuine_scores, impostor_scores, thresholds):
    best = min(
        thresholds,
        key=lambda t: abs(far_frr_at(t, genuine_scores, impostor_scores)[0]
                          - far_frr_at(t, genuine_scores, impostor_scores)[1])
    )
    far, frr = far_frr_at(best, genuine_scores, impostor_scores)
    return best, (far + frr) / 2


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print(f"Enrolling simulated user on {N_ENROLLMENT_SAMPLES} genuine samples...")
    enrollment_samples = make_profile_samples(GENUINE_BASELINE, GENUINE_NOISE, N_ENROLLMENT_SAMPLES)
    profile = build_profile_object(enrollment_samples)

    print(f"Scoring {N_GENUINE_TRIALS} genuine login trials...")
    genuine_scores = collect_scores(profile, GENUINE_BASELINE, GENUINE_NOISE, N_GENUINE_TRIALS)

    print(f"Scoring {N_IMPOSTOR_TRIALS} impostor login trials...")
    impostor_scores = collect_scores(profile, IMPOSTOR_BASELINE, IMPOSTOR_NOISE, N_IMPOSTOR_TRIALS)

    thresholds = list(range(30, 96))
    far_curve = [far_frr_at(t, genuine_scores, impostor_scores)[0] * 100 for t in thresholds]
    frr_curve = [far_frr_at(t, genuine_scores, impostor_scores)[1] * 100 for t in thresholds]

    eer_threshold, eer_value = find_eer(genuine_scores, impostor_scores, thresholds)
    current_far, current_frr = far_frr_at(ALLOW_THRESHOLD, genuine_scores, impostor_scores)

    plt.figure(figsize=(8, 5))
    plt.plot(thresholds, far_curve, label="FAR - % impostors wrongly allowed", color="#dc2626")
    plt.plot(thresholds, frr_curve, label="FRR - % genuine users wrongly blocked", color="#2563eb")
    plt.axvline(ALLOW_THRESHOLD, color="#16a34a", linestyle="--",
               label=f"Current ALLOW_THRESHOLD = {ALLOW_THRESHOLD:g}")
    plt.scatter([eer_threshold], [eer_value * 100], color="black", zorder=5,
               label=f"EER = {eer_value * 100:.1f}% @ threshold {eer_threshold}")
    plt.xlabel("Match-score threshold (%)")
    plt.ylabel("Error rate (%)")
    plt.title("SentinelType: FAR / FRR vs Decision Threshold")
    plt.legend(loc="upper center", fontsize=9)
    plt.grid(alpha=0.25)
    plt.tight_layout()
    chart_path = os.path.join(OUTPUT_DIR, "far_frr_curve.png")
    plt.savefig(chart_path, dpi=150)
    print(f"Chart saved: {chart_path}")

    report = f"""# SentinelType - Behavioral Model Evaluation

## Method

Since collecting real impostor attack data isn't feasible for a student
project, this evaluation simulates genuine and impostor login trials:

- **Genuine trials**: {N_GENUINE_TRIALS} simulated logins using the enrolled
  user's own baseline typing rhythm, with {GENUINE_NOISE*100:.0f}% natural
  session-to-session noise (fatigue, different keyboard, mood, etc.)
- **Impostor trials**: {N_IMPOSTOR_TRIALS} simulated logins using a
  *different* person's typing rhythm (a distinct baseline with its own
  {IMPOSTOR_NOISE*100:.0f}% internal noise), simulating someone else who
  knows the password but types differently.
- Enrollment used {N_ENROLLMENT_SAMPLES} genuine samples, matching the
  project's actual enrollment flow, same code path as production.

## Results

| Metric | Value |
|---|---|
| Equal Error Rate (EER) | **{eer_value*100:.2f}%** at threshold {eer_threshold} |
| FAR at current threshold ({ALLOW_THRESHOLD:g}) | {current_far*100:.2f}% |
| FRR at current threshold ({ALLOW_THRESHOLD:g}) | {current_frr*100:.2f}% |

**FAR (False Acceptance Rate)** - % of impostor attempts wrongly let in.
**FRR (False Rejection Rate)** - % of the real user's own logins wrongly blocked.
**EER (Equal Error Rate)** - the threshold where FAR and FRR are balanced.

## Honest limitations

- This is a **simulation**, not a real multi-user study.
- A larger genuine-user sample size would give more stable estimates.
- These numbers characterize the keystroke+mouse ML signal in isolation;
  the production decision also factors in device/IP/time context, which
  are not modeled here.
"""
    report_path = os.path.join(OUTPUT_DIR, "report.md")
    with open(report_path, "w") as f:
        f.write(report)
    print(f"Report saved: {report_path}")

    print("\n--- Summary ---")
    print(f"EER = {eer_value*100:.2f}% at threshold {eer_threshold}")
    print(f"At threshold {ALLOW_THRESHOLD:g}: FAR={current_far*100:.2f}%  FRR={current_frr*100:.2f}%")

    ml_model.delete_model(profile.ml_model_path)


if __name__ == "__main__":
    main()