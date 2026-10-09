const typingText = document.getElementById("typingText");
const submitButton = document.getElementById("submitSample");
const sampleCountElement = document.getElementById("sampleCount");
const statusMessage = document.getElementById("statusMessage");

const challengeElement = document.getElementById("enrollmentSentence");

const EXPECTED_TEXT = challengeElement.textContent.trim();
const TOTAL_SAMPLES = 7;

// Same sanity-check ranges as the backend (app.py) - kept in sync so a
// sample never gets accepted here only to be rejected later after all
// samples are typed. These only catch broken/garbage input, not behavior
// that's "wrong" - real security is the ML match done after enrollment.
const LIMITS = {
    averageDwellTime: [10, 1000],
    averageFlightTime: [0, 8000],
    typingDuration: [100, 180000],
    typingSpeedWPM: [1, 200]
};

function checkRealisticBounds(features) {
    for (const [field, [min, max]] of Object.entries(LIMITS)) {
        const value = features[field];
        if (value < min || value > max) {
            return `That sample looked unusual (${field}). Let's try this one again - type a bit more naturally.`;
        }
    }
    return null;
}

let keyDownTimes = {};
let lastKeyUpTime = null;
let keystrokeData = [];
let typingStartTime = null;

/* MODULE 3 — mouse dynamics, reset per sample */
let mousePoints = [];
let clickTimestamps = [];

let completedSamples = [];


/* ================================
   RESET CURRENT SAMPLE
================================ */

function resetCurrentSample() {
    keyDownTimes = {};
    lastKeyUpTime = null;
    keystrokeData = [];
    typingStartTime = null;

    mousePoints = [];
    clickTimestamps = [];

    submitButton.disabled = true;
}


/* ================================
   FOCUS
================================ */

typingText.addEventListener("focus", () => {
    resetCurrentSample();
});


/* ================================
   MOUSE TRACKING (per sample)
================================ */

document.addEventListener("mousemove", (event) => {
    mousePoints.push({
        x: event.clientX,
        y: event.clientY,
        t: performance.now()
    });

    if (mousePoints.length > 500) {
        mousePoints.shift();
    }
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
   KEY DOWN
================================ */

typingText.addEventListener("keydown", (event) => {

    const keyDownTime = performance.now();

    if (typingStartTime === null) {
        typingStartTime = keyDownTime;
    }

    let flightTime = null;

    if (lastKeyUpTime !== null) {
        flightTime = keyDownTime - lastKeyUpTime;
    }

    keyDownTimes[event.code] = {
        time: keyDownTime,
        flightTime: flightTime
    };
});


/* ================================
   KEY UP
================================ */

typingText.addEventListener("keyup", (event) => {

    const keyUpTime = performance.now();

    const keyData = keyDownTimes[event.code];

    if (!keyData) {
        return;
    }

    const dwellTime = keyUpTime - keyData.time;

    const keystroke = {
        key: event.code,
        dwellTime: Number(dwellTime.toFixed(2)),
        flightTime:
            keyData.flightTime !== null
                ? Number(keyData.flightTime.toFixed(2))
                : null
    };

    keystrokeData.push(keystroke);

    lastKeyUpTime = keyUpTime;

    delete keyDownTimes[event.code];

    checkSampleReady();
});


/* ================================
   CHECK SAMPLE READY
================================ */

function checkSampleReady() {

    const typedText = typingText.value;

    if (typedText === EXPECTED_TEXT) {
        submitButton.disabled = false;
        statusMessage.textContent = "Sample ready. Click Submit Sample.";
    } else {
        submitButton.disabled = true;

        if (typedText.length > EXPECTED_TEXT.length) {
            statusMessage.textContent = "Text is longer than expected. Please correct it.";
        } else {
            statusMessage.textContent = "Please type the complete phrase exactly.";
        }
    }
}


/* ================================
   CALCULATE TYPING FEATURES
================================ */

function calculateTypingFeatures() {

    if (keystrokeData.length === 0 || typingStartTime === null) {
        return null;
    }

    const dwellTimes = keystrokeData.map(item => item.dwellTime);

    const flightTimes = keystrokeData
        .map(item => item.flightTime)
        .filter(time => time !== null);

    const averageDwellTime =
        dwellTimes.reduce((sum, time) => sum + time, 0) / dwellTimes.length;

    const averageFlightTime =
        flightTimes.length > 0
            ? flightTimes.reduce((sum, time) => sum + time, 0) / flightTimes.length
            : 0;

    const typingDuration = performance.now() - typingStartTime;

    const totalKeystrokes = keystrokeData.length;

    const typingSpeedWPM =
        typingDuration > 0
            ? (totalKeystrokes / 5) / (typingDuration / 60000)
            : 0;

    return {
        totalKeystrokes,
        averageDwellTime: Number(averageDwellTime.toFixed(2)),
        averageFlightTime: Number(averageFlightTime.toFixed(2)),
        typingDuration: Number(typingDuration.toFixed(2)),
        typingSpeedWPM: Number(typingSpeedWPM.toFixed(2))
    };
}


/* ================================
   SUBMIT ONE SAMPLE
================================ */

submitButton.addEventListener("click", async () => {

    const typedText = typingText.value;

    if (typedText !== EXPECTED_TEXT) {
        statusMessage.textContent = "Please type the phrase exactly.";
        return;
    }

    const features = calculateTypingFeatures();

    if (!features) {
        statusMessage.textContent = "Unable to calculate typing features.";
        return;
    }

            const boundsIssue = checkRealisticBounds(features);

        if (boundsIssue) {

            statusMessage.textContent = boundsIssue;

            typingText.value = "";
            resetCurrentSample();

            return;
        }

    const mouseFeatures = calculateMouseFeatures();

    completedSamples.push({
        averageDwellTime: features.averageDwellTime,
        averageFlightTime: features.averageFlightTime,
        typingDuration: features.typingDuration,
        typingSpeedWPM: features.typingSpeedWPM,
        averageMouseSpeed: mouseFeatures.averageMouseSpeed,
        averageClickInterval: mouseFeatures.averageClickInterval
    });

    sampleCountElement.textContent = completedSamples.length;

    const progressBarFill = document.getElementById("progressBarFill");
    if (progressBarFill) {
        progressBarFill.style.width = `${(completedSamples.length / TOTAL_SAMPLES) * 100}%`;
    }

    if (completedSamples.length === TOTAL_SAMPLES) {
        statusMessage.textContent = `${TOTAL_SAMPLES} samples collected. Saving enrollment...`;
        await submitEnrollment();
        return;
    }

    typingText.value = "";
    resetCurrentSample();

    statusMessage.textContent =
        `Sample ${completedSamples.length} saved. ` +
        `Please complete sample ${completedSamples.length + 1}.`;
});


/* ================================
   SEND 10 SAMPLES TO BACKEND
================================ */

async function submitEnrollment() {

    submitButton.disabled = true;
    typingText.disabled = true;

    try {
        const response = await fetch("/api/enrollment", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ samples: completedSamples })
        });

        const data = await response.json();

        if (!response.ok) {
            typingText.disabled = false;
            statusMessage.textContent = data.message || "Enrollment failed.";
            return;
        }

        statusMessage.textContent = data.message || "Enrollment completed! You can now log in.";

        setTimeout(() => {
            window.location.href = data.redirect || "/";
        }, 1800);

    } catch (error) {
        console.error("Enrollment request failed:", error);
        typingText.disabled = false;
        statusMessage.textContent = "Could not save enrollment. Please try again.";
    }
}