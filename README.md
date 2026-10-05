# SentinelType

Behavioral Biometric Authentication System — passive keystroke + mouse
dynamics verification layered on top of normal password login, with an
Isolation Forest anomaly model, explainable reasoning, IP/location
tracking, email + SMS alerts, "trust this device", and a login-history
dashboard.

## What's implemented (maps to your module list)

| Module | Status |
|---|---|
| 1. Registration & Enrollment | ✅ `register.html/js`, `/enroll` |
| 2. Keystroke Dynamics | ✅ (already existed) `script.js` / `enroll.js` |
| 3. Mouse Dynamics | ✅ **new** — mouse speed + click interval, captured on both login and enrollment |
| 4. Feature Extraction | ✅ `app.py: sample_to_feature_dict` |
| 5. Isolation Forest (per-user) | ✅ **new** — `services/ml_model.py`, trained at enrollment, one model per user under `instance/ml_models/` |
| 6. Decision Engine (≥80% allow / <80% block) | ✅ **new** — `services/decision_engine.py` |
| 7. Explainable Reasoning | ✅ **new** — `services/behavioral_verification.py: explain_differences` |
| 8. Email Alerts | ✅ **new** — `services/email_alert.py` (Gmail SMTP) |
| 9. "Trust this device" | ✅ **new** — signed link in the alert email → `/trust/<token>` |
| 10. Login History Dashboard | ✅ **new** — `/dashboard`, table + Chart.js trend graph |
| SMS Alerts (your ask, listed as *future scope* in the doc) | ✅ **new** — `services/sms_alert.py` via Twilio |
| IP address + location in dashboard & alerts (your ask, also *future scope* in the doc) | ✅ **new** — `services/geolocation.py` (free ip-api.com lookup, no key needed) |

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# edit .env — see below
```

### 1. `SECRET_KEY`
Any long random string. `python -c "import secrets; print(secrets.token_hex(32))"`

### 2. Email alerts (Gmail SMTP) — free
1. Turn on 2-Step Verification: https://myaccount.google.com/security
2. Create an App Password: https://myaccount.google.com/apppasswords
3. Put your Gmail address + that 16-character app password into `MAIL_USERNAME` / `MAIL_APP_PASSWORD`.

If you leave these blank, login still works fine — the alert is just skipped and logged to the console instead of crashing anything.

### 3. SMS alerts (Twilio) — **optional, costs money**
1. Create an account at https://www.twilio.com/ and get a phone number.
2. Free trial can only SMS numbers you've verified in the Twilio console; real numbers need a paid account.
3. Fill `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`.

Users also need a `phone_number` on file (asked at registration, optional field) for SMS to fire at all.

### 4. Database
This is a fresh schema change (email/phone, mouse-dynamics columns, `login_history` table). **Easiest path for a college project:** just delete any existing `sentinel_type.db` and run:

```bash
flask db upgrade
```

(A migration `a7f3c9d2e1b4` is included if you'd rather migrate an existing DB — see the note inside it about the `email` column being `NOT NULL UNIQUE`.)

### 5. Run

```bash
python app.py
```

## How the flow now works

1. **Register** → username + email (required, for alerts) + optional phone (for SMS) + password.
2. **Enroll** → type the challenge phrase 10 times. Keystroke *and* mouse
   movement during each sample are captured. A per-user **Isolation
   Forest** is trained on these 10 samples and saved to
   `instance/ml_models/user_<id>.joblib`, alongside the plain average
   profile (used for the human-readable explanations).
3. **Login** → password checked as before. If it passes and enrollment
   exists, the new sample is scored by that user's Isolation Forest →
   a 0–100 **match score**.
   - **IP address** is read from the request (`X-Forwarded-For` if
     behind a proxy, else `remote_addr`).
   - **Location** (city/region/country) is looked up from that IP via
     the free `ip-api.com` service.
   - Every attempt — allowed or blocked — is written to `login_history`
     (Module 10).
   - **Score ≥ 80** → login allowed, redirect to `/dashboard`.
   - **Score < 80** → login blocked, session cleared, and:
     - an **email** is sent with the score, IP, location, plain-English
       reasons, and a "Yes, this was me" trust link
     - an **SMS** is sent too, if the user has a phone number and
       Twilio is configured
4. **Trust link** → marks that specific login attempt as trusted in the
   dashboard (doesn't currently retrain the model — see Limitations).
5. **Dashboard** → last 50 attempts in a table (time, allowed/blocked,
   score, IP, location, reasons) + a Chart.js line graph of match score
   over time, color-coded green/red.

## Honest limitations (worth putting in your report)

- **10 enrollment samples is tiny** for an Isolation Forest — it works
  as a proof of concept, but a production system would want dozens of
  samples and periodic retraining. The percentage-difference explainer
  is what makes the *reasoning* meaningful even with so little data.
- **Client-side features can be spoofed.** The behavioral sample is
  computed in the browser and POSTed as JSON — anyone using
  curl/Postman can fabricate "perfect" numbers. A real system would
  need server-side timing validation or a signed/encrypted channel.
  Worth mentioning explicitly in your "Limitations" report section.
- **Trust-device doesn't yet retrain the model.** Clicking "Yes, this
  was me" only flags that row in the dashboard; it doesn't feed the
  sample back into the Isolation Forest. That's a clean "Future Scope"
  item if asked in viva.
- **ip-api.com free tier** is rate-limited (45 requests/minute) and
  gives city-level, not precise, location — fine for a demo, mention
  as a limitation if pushed on accuracy.
- **Single device/session assumption**, same as your original doc's
  "Future Scope" section (multi-device calibration, continuous
  session auth, etc. — still open).
