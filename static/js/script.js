const loginForm = document.getElementById("loginForm");
const challengeInput = document.getElementById("challengeInput");
const loginButton = document.getElementById("loginButton");
const expectedChallenge = document.getElementById("challengePhrase").textContent.trim();

let keyDownTimes = {};
let lastKeyUpTime = null;
let keystrokeData = [];
let typingStartTime = null;

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
        behavioralSample: behavioralSample
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