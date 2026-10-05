# SentinelType

**Behavioral Biometric Authentication & Intelligent Threat Detection System**

A password login with an added passive security layer: the system learns how you personally type (keystroke dynamics) and move your mouse, then checks future logins against that learned pattern using a per-user Isolation Forest anomaly model. Logins are also scored against contextual signals (IP, approximate location, device, login time), and the decision — allow, flag, or block — comes with a plain-English explanation.

This is a B.Tech CSE mini-project prototype. It is a standalone demo application — it does not connect to or replace any real third-party login system.

## What's implemented

| Module | Status |
|---|---|
| Registration & Login (password, hashed) | ✅ `register.html/js`, `login.html/js` |
| Fixed challenge-phrase capture | ✅ one shared phrase (`app.py: BEHAVIOR_CHALLENGE_PHRASE`), typed at enrollment and login, kept separate from the password field |
| Keystroke Dynamics | ✅ dwell time, flight time, typing speed (WPM), typing duration — `enroll.js` / `script.js` |
| Mouse Dynamics | ✅ movement speed, click interval — captured alongside keystrokes, excluded from the ML model (too noisy at small sample sizes) but used in the statistical explainer |
| Feature Extraction | ✅ `services/behavioral_verification.py` |
| Isolation Forest (per-user) | ✅ `services/ml_model.py` — trained at enrollment on 10 samples with leave-one-out calibration, saved to `instance/ml_models/` |
| Decision / Risk Engine | ✅ `services/decision_engine.py` — blends ML anomaly score + statistical percentage-difference score into one 0–100 match score |
| Explainable Reasoning | ✅ `services/behavioral_verification.py: explain_differences()` — human-readable reasons shown on a blocked login |
| IP + Approximate Location | ✅ `services/geolocation.py` — free `ip-api.com` lookup, no key required, graceful fallback if unavailable |
| Device/Browser Info | ✅ parsed from User-Agent — `services/utils.py` |
| Email Alerts | ✅ `services/email_alert.py` — Gmail SMTP, inline embedded map image (fetched server-side, no hotlinking), full event details |
| "Trust this device" | ✅ signed link in the alert email → `/trust/<token>` |
| Forgot Password | ✅ `/forgot-password`, `/reset-password/<token>` — signed, time-limited email link |
| Reset Typing Profile | ✅ `/reset-profile`, `/reset-profile/confirm/<token>` — password + email-confirmed wipe of the behavioral profile, for recovery from persistent false rejections |
| Security Dashboard | ✅ `/dashboard` — summary cards, login history table, Chart.js score trend, Leaflet map |
| Model Evaluation (FAR/FRR/EER) | ✅ `scripts/evaluate_model.py` — standard biometric evaluation metrics on simulated genuine/impostor trials |

### Not implemented (explicitly dropped)

WhatsApp and SMS alerts were attempted via Twilio and removed. WhatsApp's sandbox requires Meta-approved message templates for app-initiated alerts, and SMS requires a paid, purchased Twilio number rather than a personal one — both made the feature unreliable for a free/student setup. The code for both is still present but unused, in `services/sms_alert.py` and `services/whatsapp_alert.py`, in case this is revisited with a paid account later. **Email is the only active alert channel.**

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# edit .env — see below
```

**1. SECRET_KEY** — any long random string: `python -c "import secrets; print(secrets.token_hex(32))"`

**2. Email alerts (Gmail SMTP) — free**
1. Turn on 2-Step Verification: https://myaccount.google.com/security
2. Create an App Password: https://myaccount.google.com/apppasswords
3. Put your Gmail address + the 16-character app password into `MAIL_USERNAME` / `MAIL_APP_PASSWORD`.

If left blank, login still works — the alert is skipped and logged to the console instead of crashing anything.

**3. PUBLIC_BASE_URL** — the base URL used inside emailed links (password reset, trust-device, profile reset). Set to your real public URL once deployed (see Deployment below); for local-only testing, your machine's LAN IP works for same-WiFi devices.

**4. Database** — SQLite, auto-migrating. The app adds any missing columns on startup (`ensure_schema()` in `app.py`), so you never need to delete the database after an update.

## Run

```bash
python app.py
```

## How the flow works

1. **Register** → username + password (hashed with Werkzeug).
2. **Enroll** → type the fixed challenge phrase 10 times. Keystroke and mouse data are captured each time. A per-user Isolation Forest is trained on these 10 samples (leave-one-out calibrated) and saved under `instance/ml_models/`, alongside the plain average profile used for the human-readable explanations.
3. **Login** → password checked first. If correct, the same challenge phrase is typed again and scored against the saved profile:
   - ML anomaly score (Isolation Forest) + statistical percentage-difference score are blended into one 0–100 match score (`services/decision_engine.py`).
   - IP address is read from the request (`X-Forwarded-For` if behind a proxy, else `remote_addr`).
   - Approximate location (city/region/country) is looked up from that IP via `ip-api.com`.
   - Every attempt — allowed or blocked — is written to `login_history`.
   - **Score ≥ `ALLOW_THRESHOLD`** (currently 50, tuned using `scripts/evaluate_model.py` — see Evaluation below) → access granted, redirect to `/dashboard`.
   - **Score below threshold** → login blocked, session cleared, and after a configurable number of consecutive failures (`ALERT_AFTER_ATTEMPTS`), an email alert is sent with the score, IP, location, device, and plain-English reasons, plus a "Trust this device" link.
4. **Forgot Password / Reset Typing Profile** → safety nets reachable from the login page if the password is forgotten or behavioral verification keeps failing — both gated behind a signed, time-limited email link.
5. **Dashboard** → summary cards, a table of recent login attempts with score/IP/location/device/status, a Chart.js score-trend graph, and a map of recent login locations.

## Model evaluation (FAR / FRR / EER)

```bash
python scripts/evaluate_model.py
```

Simulates genuine login trials (the enrolled user's own rhythm with natural noise) and impostor trials (a different person's rhythm), sweeps the match-score threshold, and outputs:
- `evaluation_output/far_frr_curve.png` — False Acceptance Rate / False Rejection Rate curve with the Equal Error Rate marked
- `evaluation_output/report.md` — the same numbers as a pasteable report section

This is how `ALLOW_THRESHOLD` in `services/decision_engine.py` was tuned: the original value of 80 produced a measured False Rejection Rate as high as 79% (the real user getting blocked on their own correct password), traced to an overly strict threshold rather than a flaw in the behavioral model itself. Testing across thresholds found an Equal Error Rate of 0% at a much lower threshold, and the final value of 50 was chosen with a safety margin above that point.

## Honest limitations (worth including in your report)

- **10 enrollment samples is small** for an Isolation Forest — workable as a proof of concept; a production system would want more samples and periodic retraining.
- **Client-side timing can be spoofed.** Keystroke/mouse timing is computed in the browser and sent as JSON — a real system would need server-side timing validation or a signed channel. Worth stating explicitly as a limitation.
- **IP geolocation is approximate, city-level, not GPS-exact**, and free-tier rate-limited.
- **False positives and false negatives are both possible** — fatigue, a different keyboard, or mood can shift a genuine user's typing rhythm.
- **Single-device/session assumption** — continuous authentication across a session, or across multiple devices, is future scope.
- **"Trust this device" does not retrain the model** — it only marks that login attempt as trusted in the dashboard; it doesn't feed the sample back into the Isolation Forest. Worth naming as a clean future-scope item in a viva.

## Future scope

Continuous authentication after login, mobile touch dynamics, deep learning/LSTM-based models, larger behavioral datasets, adaptive profiles, and stronger device intelligence.