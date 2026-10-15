const loginForm = document.getElementById("loginForm");
const usernameInput = document.getElementById("username");
const challengeInput = document.getElementById("challengeInput");
const challengePhraseEl = document.getElementById("challengePhrase");
const phraseStatusEl = document.getElementById("phraseStatus");
const loginButton = document.getElementById("loginButton");

let expectedChallenge = "";

let keyDownTimes = {};
let lastKeyUpTime = null;
let keystrokeData = [];
let typingStartTime = null;
let pasteDetected = false;

/* MODULE 3 — mouse dynamics, tracked across the whole page */
let mousePoints = [];
let clickTimestamps = [];


/* ================================
   RESET
================================ */

function resetKeystrokeData() {
    keyDownTimes = {};
    lastKeyUpTime = null;
    keystrokeData = [];
    typingStartTime = null;
    pasteDetected = false;
}

function resetMouseData() {
    mousePoints = [];
    clickTimestamps = [];
}

challengeInput.addEventListener("focus", () => {
    resetKeystrokeData();
    resetMouseData();
});

/* ================================
   PER-USER CHALLENGE PHRASE — fetched once username is known, instead
   of one phrase shared by every account.
================================ */

async function loadChallengePhrase() {
    const username = usernameInput.value.trim();

    if (!username) {
        expectedChallenge = "";
        challengePhraseEl.textContent = "";
        challengeInput.disabled = true;
        challengeInput.placeholder = "Enter your username first";
        phraseStatusEl.textContent = "Enter your username above to load your verification phrase.";
        return;
    }

    phraseStatusEl.textContent = "Loading your verification phrase...";

    try {
        const response = await fetch(`/api/challenge-phrase?username=${encodeURIComponent(username)}`);
        const data = await response.json();

        if (response.ok && data.phrase) {
            expectedChallenge = data.phrase;
            challengePhraseEl.textContent = data.phrase;
            challengeInput.disabled = false;
            challengeInput.placeholder = "Type the phrase above exactly";
            phraseStatusEl.textContent = "";
        } else {
            phraseStatusEl.textContent = "Could not load the phrase. Try re-entering your username.";
        }
    } catch (error) {
        console.error("Failed to load challenge phrase:", error);
        phraseStatusEl.textContent = "Could not load the phrase. Check your connection and try again.";
    }

    challengeInput.value = "";
    resetKeystrokeData();
    loginButton.disabled = true;
}

usernameInput.addEventListener("blur", loadChallengePhrase);
usernameInput.addEventListener("change", loadChallengePhrase);

/* Catches Ctrl+V, right-click paste, and drag-and-drop text — all of
   these bypass real typing rhythm, so we flag them instead of letting
   the behavioral score silently fail on near-zero dwell/flight times. */
challengeInput.addEventListener("paste", () => {
    pasteDetected = true;
});

challengeInput.addEventListener("drop", () => {
    pasteDetected = true;
});

/* ================================
   MOUSE TRACKING (whole page)
================================ */

document.addEventListener("mousemove", (event) => {
    mousePoints.push({ x: event.clientX, y: event.clientY, t: performance.now() });
    if (mousePoints.length > 500) mousePoints.shift();
});

document.addEventListener("mousedown", () => {
    clickTimestamps.push(performance.now());
});

function calculateMouseFeatures() {
    let totalDistance = 0;
    let totalTime = 0;

    for (let i = 1; i < mousePoints.length; i++) {
        const prev = mousePoints[i - 1];
        const curr = mousePoints[i];
        const dx = curr.x - prev.x;
        const dy = curr.y - prev.y;
        const dt = curr.t - prev.t;

        if (dt > 0) {
            totalDistance += Math.sqrt(dx * dx + dy * dy);
            totalTime += dt;
        }
    }

    const averageMouseSpeed = totalTime > 0 ? totalDistance / totalTime : 0;

    let clickIntervals = [];
    for (let i = 1; i < clickTimestamps.length; i++) {
        clickIntervals.push(clickTimestamps[i] - clickTimestamps[i - 1]);
    }

    const averageClickInterval =
        clickIntervals.length > 0
            ? clickIntervals.reduce((a, b) => a + b, 0) / clickIntervals.length
            : 0;

    return {
        averageMouseSpeed: Number(averageMouseSpeed.toFixed(4)),
        averageClickInterval: Number(averageClickInterval.toFixed(2))
    };
}


/* ================================
   KEYSTROKE CAPTURE — on the challenge field, NOT the password field.
   This is what actually gets compared against the enrollment baseline,
   since it's the same fixed phrase typed at enrollment.
================================ */

challengeInput.addEventListener("keydown", (event) => {
    const keyDownTime = performance.now();

    if (typingStartTime === null) {
        typingStartTime = keyDownTime;
    }

    let flightTime = null;
    if (lastKeyUpTime !== null) {
        flightTime = keyDownTime - lastKeyUpTime;
    }

    keyDownTimes[event.code] = { time: keyDownTime, flightTime: flightTime };
});

challengeInput.addEventListener("keyup", (event) => {
    const keyUpTime = performance.now();
    const keyData = keyDownTimes[event.code];
    if (!keyData) return;

    const dwellTime = keyUpTime - keyData.time;

    keystrokeData.push({
        key: event.code,
        dwellTime: Number(dwellTime.toFixed(2)),
        flightTime: keyData.flightTime !== null ? Number(keyData.flightTime.toFixed(2)) : null
    });

    lastKeyUpTime = keyUpTime;
    delete keyDownTimes[event.code];

    checkChallengeReady();
});

function checkChallengeReady() {
    loginButton.disabled = challengeInput.value !== expectedChallenge;
}


/* ================================
   CALCULATE TYPING FEATURES
================================ */

function calculateTypingFeatures() {
    if (keystrokeData.length === 0 || typingStartTime === null) {
        return null;
    }

    const dwellTimes = keystrokeData.map(item => item.dwellTime);
    const flightTimes = keystrokeData.map(item => item.flightTime).filter(t => t !== null);

    const averageDwellTime = dwellTimes.reduce((a, b) => a + b, 0) / dwellTimes.length;
    const averageFlightTime =
        flightTimes.length > 0 ? flightTimes.reduce((a, b) => a + b, 0) / flightTimes.length : 0;

    const typingDuration = performance.now() - typingStartTime;
    const totalKeystrokes = keystrokeData.length;
    const typingSpeedWPM =
        typingDuration > 0 ? (totalKeystrokes / 5) / (typingDuration / 60000) : 0;

    return {
        totalKeystrokes,
        averageDwellTime: Number(averageDwellTime.toFixed(2)),
        averageFlightTime: Number(averageFlightTime.toFixed(2)),
        typingDuration: Number(typingDuration.toFixed(2)),
        typingSpeedWPM: Number(typingSpeedWPM.toFixed(2))
    };
}

function createBehavioralSample(features) {
    if (!features) return null;

    const mouseFeatures = calculateMouseFeatures();

    return {
        averageDwellTime: features.averageDwellTime,
        averageFlightTime: features.averageFlightTime,
        typingDuration: features.typingDuration,
        typingSpeedWPM: features.typingSpeedWPM,
        averageMouseSpeed: mouseFeatures.averageMouseSpeed,
        averageClickInterval: mouseFeatures.averageClickInterval
    };
}


/* ================================
   LOGIN
================================ */

loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    if (challengeInput.value !== expectedChallenge) {
        alert("Please type the verification phrase exactly as shown.");
        return;
    }

    const features = calculateTypingFeatures();

    if (!features) {
        alert("No typing data captured — please type the phrase in the box.");
        return;
    }

    const behavioralSample = createBehavioralSample(features);

    const formData = new FormData(loginForm);

    const loginData = {
        username: formData.get("username"),
        password: formData.get("password"),
        typedChallenge: challengeInput.value,
        behavioralSample: behavioralSample,
        pasteDetected: pasteDetected
    };

    try {
        const response = await fetch("/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(loginData)
        });

        const data = await response.json();

        if (response.ok) {
            resetKeystrokeData();
            resetMouseData();

            if (data.redirect) {
                window.location.href = data.redirect;
                return;
            }

        } else if (data.requiresOtp) {
            document.getElementById("otpSection").style.display = "block";
            document.getElementById("otpStatus").textContent = data.message || "Enter the code sent to your email.";
            document.getElementById("otpStatus").style.color = "#94a3b8";
        } else {
            const reasonsText = data.reasons ? "\n\n" + data.reasons.join("\n") : "";
            alert((data.message || "Login failed.") + reasonsText);

            // Let them try again — reset the challenge field
            challengeInput.value = "";
            resetKeystrokeData();
            loginButton.disabled = true;
        }

    } catch (error) {
        console.error("Login request failed:", error);
        alert("Login request failed. Please try again.");
    }
});
document.getElementById("otpSubmitBtn").addEventListener("click", async () => {
    const otp = document.getElementById("otpInput").value.trim();
    const statusEl = document.getElementById("otpStatus");

    if (otp.length !== 6) {
        statusEl.textContent = "Enter the 6-digit code.";
        statusEl.style.color = "salmon";
        return;
    }

    try {
        const response = await fetch("/verify-otp", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ otp, username: usernameInput.value.trim() })
        });

        const data = await response.json();

        if (response.ok) {
            statusEl.textContent = data.message || "Verified!";
            statusEl.style.color = "lightgreen";
            setTimeout(() => {
                window.location.href = data.redirect || "/dashboard";
            }, 800);
        } else {
            statusEl.textContent = data.message || "Verification failed.";
            statusEl.style.color = "salmon";
        }
    } catch (error) {
        statusEl.textContent = "Request failed. Please try again.";
        statusEl.style.color = "salmon";
    }
});