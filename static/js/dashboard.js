(() => {
    "use strict";

    const $ = (id) => document.getElementById(id);

    const esc = (value) =>
        String(value ?? "").replace(/[&<>"']/g, (ch) => (
            { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
        ));

    const DECISIONS = {
        allowed:        { label: "Allowed",        cls: "ok",   color: "#34d399" },
        blocked:        { label: "Blocked",        cls: "bad",  color: "#f87171" },
        wrong_password: { label: "Wrong password", cls: "warn", color: "#fbbf24" },
    };

    const state = { data: null, filter: "all", query: "" };
    let map = null, markerLayer = null, scoreChart = null, outcomeChart = null;
    const markerByHistoryId = {};

    /* ------------------------------------------------ time helpers
       The server sends UTC ISO strings ("...Z"); the browser converts
       them to the viewer's own timezone, so times are always correct. */

    const fmtDate = (iso) => iso
        ? new Date(iso).toLocaleString(undefined, {
            day: "2-digit", month: "short", year: "numeric",
            hour: "2-digit", minute: "2-digit", hour12: true,
        })
        : "-";

    const fmtShort = (iso) => iso
        ? new Date(iso).toLocaleString(undefined, {
            day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: true,
        })
        : "";

    function timeAgo(iso) {
        if (!iso) return "";
        const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
        if (seconds < 60) return "just now";
        if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
        if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
        return `${Math.floor(seconds / 86400)} d ago`;
    }

    function toast(message, type = "ok") {
        const el = document.createElement("div");
        el.className = `toast ${type}`;
        el.textContent = message;
        $("toasts").appendChild(el);
        setTimeout(() => el.remove(), 3500);
    }

    /* ------------------------------------------------ data */

    async function load() {
        try {
            const res = await fetch("/api/dashboard-data", { headers: { Accept: "application/json" } });
            if (res.status === 401) { window.location.href = "/"; return; }
            state.data = await res.json();
            render();
        } catch (err) {
            console.error(err);
            toast("Could not load dashboard data", "bad");
        }
    }

    function render() {
        const d = state.data;
        renderTopAndHero(d);
        renderStats(d.stats);
        renderSecurity(d);
        renderDna(d.profile);
        renderCharts(d);
        renderMap(d.history);
        renderTable();
        $("tzNote").textContent =
            `Times are shown in your local timezone (${Intl.DateTimeFormat().resolvedOptions().timeZone}). ` +
            `Showing the latest ${d.history.length} attempts.`;
    }

    /* ------------------------------------------------ header + stats */

    function renderTopAndHero(d) {
        const s = d.stats;
        const chip = $("riskChip");
        chip.className = `chip ${s.risk}`;
        chip.textContent = `${s.risk.charAt(0).toUpperCase() + s.risk.slice(1)} risk`;

        const parts = [];
        parts.push(s.lastSuccess
            ? `Last successful login: <b>${esc(fmtDate(s.lastSuccess))}</b> (${esc(timeAgo(s.lastSuccess))})`
            : "No successful login yet");
        if (s.lastFailure) {
            parts.push(`Last failed attempt: <b>${esc(fmtDate(s.lastFailure))}</b> (${esc(timeAgo(s.lastFailure))})`);
        }
        $("heroSub").innerHTML = parts.join(" &nbsp;&middot;&nbsp; ");
    }

    function renderStats(s) {
        const cards = [
            { label: "Total attempts",     value: s.total },
            { label: "Allowed",            value: s.allowed, cls: "ok" },
            { label: "Blocked (behavior)", value: s.blocked, cls: "bad" },
            { label: "Wrong passwords",    value: s.wrongPassword, cls: "warn" },
            { label: "Avg match score",    value: s.avgScore == null ? "-" : `${s.avgScore}%` },
            { label: "Places / IPs seen",  value: `${s.uniqueLocations} / ${s.uniqueIps}` },
            { label: "Alerts sent",        value: s.alertsSent },
        ];
        $("stats").innerHTML = cards.map((c) =>
            `<div class="stat ${c.cls || ""}"><div class="label">${esc(c.label)}</div><div class="value">${esc(c.value)}</div></div>`
        ).join("");
    }

    /* ------------------------------------------------ security panel */

    function renderSecurity(d) {
        const sec = d.security, user = d.user;
        const pct = Math.min(100, Math.round((sec.strikes / sec.threshold) * 100));

        const channel = (name, icon, on, detail) =>
            `<div class="sec-row"><span><span class="dot ${on ? "on" : ""}"></span>${icon} ${name}</span>
             <span class="muted small">${esc(detail)}</span></div>`;

        let html = `
            <div class="small muted">Consecutive failed attempts</div>
            <div class="meter"><i style="width:${pct}%"></i></div>
            <div class="small muted" style="margin-bottom:10px">
                <b style="color:#e2e8f0">${sec.strikes}</b> of ${sec.threshold} before an alert is sent
                (then max 1 alert every ${sec.cooldownMinutes} min)
            </div>
            ${channel("Email", "&#9993;", sec.channels.email && sec.hasEmail, user.email || "no email")}
        `;

        if (!sec.channels.email) {
            html += `<div class="note-warn">No alert channel is configured in your .env file yet (set MAIL_USERNAME / MAIL_APP_PASSWORD).</div>`;
        }

        html += `
            <div class="sec-actions">
                <button id="testAlertBtn" class="btn" type="button">Send test alert</button>
                <div id="testAlertResult"></div>
            </div>`;

        // keep the previous test result on screen across the 30s auto-refresh
        const previous = $("testAlertResult") ? $("testAlertResult").innerHTML : "";
        const wasBusy = $("testAlertBtn") ? $("testAlertBtn").disabled : false;

        $("securityBody").innerHTML = html;
        $("testAlertResult").innerHTML = previous;
        $("testAlertBtn").disabled = wasBusy;
        if (wasBusy) $("testAlertBtn").textContent = "Sending...";
        $("testAlertBtn").addEventListener("click", sendTestAlert);
    }

    async function sendTestAlert() {
        const button = $("testAlertBtn");
        const out = $("testAlertResult");
        button.disabled = true;
        button.textContent = "Sending...";
        out.innerHTML = `<p class="muted small">Sending on every channel and checking delivery - this can take up to ~25 seconds.</p>`;

        try {
            const res = await fetch("/api/test-alert", { method: "POST" });
            const json = await res.json();

            if (!res.ok) {
                out.innerHTML = `<div class="result bad"><b>&#10060; ${esc(json.error || "Failed")}</b></div>`;
                return;
            }

            const names = { email: "Email" };
            out.innerHTML = ["email"].map((key) => {
                const r = json.results[key];
                return `<div class="result ${r.ok ? "ok" : "bad"}">
                            <b>${r.ok ? "&#9989;" : "&#10060;"} ${names[key]}</b>
                            <span>${esc(r.detail)}</span></div>`;
            }).join("");
        } catch (err) {
            out.innerHTML = `<div class="result bad"><b>&#10060; Request failed</b><span>${esc(err.message)}</span></div>`;
        } finally {
            const btn = $("testAlertBtn");
            if (btn) { btn.disabled = false; btn.textContent = "Send test alert"; }
        }
    }

    /* ------------------------------------------------ typing DNA */

    function renderDna(profile) {
        if (!profile) {
            $("dnaNote").textContent = "";
            $("dnaBody").innerHTML = `<p class="muted">You haven't completed enrollment yet.</p>`;
            return;
        }
        $("dnaNote").textContent = `Built from ${profile.samples} samples on ${fmtDate(profile.enrolledAt)}`;
        const tiles = [
            ["Key hold time", `${profile.dwell} ms`, "how long you hold each key"],
            ["Gap between keys", `${profile.flight} ms`, "flight time between keystrokes"],
            ["Typing speed", `${profile.wpm} WPM`, "words per minute"],
            ["Phrase time", `${profile.duration} s`, "time to type the phrase"],
        ];
        $("dnaBody").innerHTML = tiles.map(([label, value, hint]) =>
            `<div class="tile"><span>${esc(label)}</span><b>${esc(value)}</b><span>${esc(hint)}</span></div>`
        ).join("");
    }

    /* ------------------------------------------------ charts */

    function renderCharts(d) {
        if (typeof Chart === "undefined") return;   // CDN blocked - skip charts

        Chart.defaults.color = "#94a3b8";
        Chart.defaults.borderColor = "rgba(148,163,184,.15)";

        const scored = d.history.filter((h) => h.score != null).slice().reverse();

        const trend = {
            labels: scored.map((h) => fmtShort(h.ts)),
            datasets: [
                {
                    label: "Match score (%)",
                    data: scored.map((h) => h.score),
                    borderColor: "#67e8f9",
                    backgroundColor: "rgba(103,232,249,.12)",
                    pointBackgroundColor: scored.map((h) => (DECISIONS[h.decision] || DECISIONS.allowed).color),
                    pointRadius: 5, tension: 0.3, fill: true,
                },
                {
                    label: "Pass mark (80%)",
                    data: scored.map(() => 80),
                    borderColor: "rgba(251,191,36,.7)", borderDash: [6, 6],
                    pointRadius: 0, fill: false,
                },
            ],
        };

        if (scoreChart) {
            scoreChart.data = trend;
            scoreChart.update();
        } else {
            scoreChart = new Chart($("scoreChart"), {
                type: "line",
                data: trend,
                options: {
                    maintainAspectRatio: false,
                    scales: { y: { min: 0, max: 100 } },
                    plugins: { legend: { labels: { color: "#cbd5e1" } } },
                },
            });
        }

        const s = d.stats;
        const outcome = {
            labels: ["Allowed", "Blocked", "Wrong password"],
            datasets: [{
                data: [s.allowed, s.blocked, s.wrongPassword],
                backgroundColor: ["#34d399", "#f87171", "#fbbf24"],
                borderWidth: 0,
            }],
        };

        if (outcomeChart) {
            outcomeChart.data = outcome;
            outcomeChart.update();
        } else {
            outcomeChart = new Chart($("outcomeChart"), {
                type: "doughnut",
                data: outcome,
                options: {
                    maintainAspectRatio: false, cutout: "66%",
                    plugins: { legend: { position: "bottom", labels: { color: "#cbd5e1" } } },
                },
            });
        }
    }

    /* ------------------------------------------------ map */

    function renderMap(history) {
        const empty = $("mapEmpty");
        const located = history.filter((h) => h.lat != null && h.lon != null);

        if (typeof L === "undefined") {
            empty.classList.remove("hidden");
            empty.textContent = "The map library could not be loaded (check your internet connection).";
            return;
        }

        if (!map) {
            map = L.map("map", { zoomControl: true, worldCopyJump: true }).setView([22, 79], 4);
             L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
                attribution: "&copy; OpenStreetMap contributors",
                subdomains: "abc", maxZoom: 19,
            }).addTo(map);
            markerLayer = L.layerGroup().addTo(map);
            window.addEventListener("resize", () => map.invalidateSize());
        }

        markerLayer.clearLayers();
        Object.keys(markerByHistoryId).forEach((k) => delete markerByHistoryId[k]);

        if (!located.length) {
            empty.classList.remove("hidden");
            empty.innerHTML = "No location data yet.<br>Locations appear for new logins once your IP can be resolved.";
            $("mapNote").textContent = "";
            return;
        }
        empty.classList.add("hidden");

        // one marker per place (rounded coordinates), sized by number of attempts
        const groups = {};
        located.forEach((h) => {
            const key = `${h.lat.toFixed(2)},${h.lon.toFixed(2)}`;
            (groups[key] = groups[key] || []).push(h);
        });

        const bounds = [];
        Object.values(groups).forEach((rows) => {
            const first = rows[0];   // history is newest-first
            const bad = rows.filter((r) => r.decision !== "allowed").length;
            const color = bad ? "#f87171" : "#34d399";

            const marker = L.circleMarker([first.lat, first.lon], {
                radius: 8 + Math.min(rows.length, 10),
                color, weight: 2, fillColor: color, fillOpacity: 0.45,
            }).addTo(markerLayer);

            marker.bindPopup(
                `<div class="pop-title">${esc(first.location)}</div>` +
                `${rows.length} attempt${rows.length > 1 ? "s" : ""} &middot; ` +
                `${rows.length - bad} allowed, ${bad} failed<br>` +
                `IP: ${esc(first.ip)}${first.isp ? " (" + esc(first.isp) + ")" : ""}<br>` +
                `Latest: ${esc(fmtDate(first.ts))}<br>` +
                `<a href="${esc(first.mapUrl)}" target="_blank" rel="noopener">Open in Google Maps</a>`
            );

            rows.forEach((r) => { markerByHistoryId[r.id] = marker; });
            bounds.push([first.lat, first.lon]);
        });

        $("mapNote").textContent =
            `${Object.keys(groups).length} place${Object.keys(groups).length > 1 ? "s" : ""} - approximate (based on IP address)`;

        if (!map._fittedOnce) {
            map.fitBounds(bounds, { padding: [50, 50], maxZoom: 9 });
            map._fittedOnce = true;
        }
        setTimeout(() => map.invalidateSize(), 150);
    }

    /* ------------------------------------------------ table */

    function visibleRows() {
        const q = state.query.trim().toLowerCase();
        return state.data.history.filter((h) => {
            if (state.filter !== "all" && h.decision !== state.filter) return false;
            if (!q) return true;
            const haystack = [h.ip, h.location, h.isp, h.browser, h.os, h.device, h.reasons.join(" "),
                (DECISIONS[h.decision] || {}).label].join(" ").toLowerCase();
            return haystack.includes(q);
        });
    }

    function renderTable() {
        const rows = visibleRows();
        const body = $("historyBody");

        if (!rows.length) {
            body.innerHTML = `<tr class="empty-row"><td colspan="9">No login attempts match this view.</td></tr>`;
            return;
        }

        body.innerHTML = rows.map((h) => {
            const meta = DECISIONS[h.decision] || { label: h.decision, cls: "warn", color: "#fbbf24" };
            const scoreCell = h.score == null
                ? `<span class="muted">-</span>`
                : `<div class="scorebar"><i style="width:${Math.max(2, h.score)}%;background:${h.score >= 80 ? "#34d399" : h.score >= 60 ? "#fbbf24" : "#f87171"}"></i></div><b>${h.score}%</b>`;

            const alerts = [
                ["email", "&#9993;", "Email alert"],
            ].map(([key, icon, title]) =>
                
                `<span class="${h.alerts[key] ? "" : "off"}" title="${title}${h.alerts[key] ? " sent" : " not sent"}">${icon}</span>`
            ).join("");

            const locateBtn = h.lat != null
                ? `<br><button class="linkbtn" data-locate="${h.id}" type="button">&#128205; Show on map</button>`
                : "";

            const trustBtn = h.decision !== "allowed" && !h.trusted
                ? `<button class="btn ghost tiny" data-trust="${h.id}" type="button">This was me</button>`
                : "";

            return `<tr>
                <td>${esc(fmtDate(h.ts))}<small>${esc(timeAgo(h.ts))}</small></td>
                <td><span class="badge ${meta.cls}">${esc(meta.label)}</span>${h.trusted ? '<span class="badge trust">trusted</span>' : ""}</td>
                <td>${scoreCell}</td>
                <td>${esc(h.browser)} &middot; ${esc(h.os)}<small>${esc(h.device)}</small></td>
                <td><code>${esc(h.ip || "-")}</code>${h.isp ? `<small>${esc(h.isp)}</small>` : ""}</td>
                <td>${esc(h.location)}${locateBtn}</td>
                <td class="alerts">${alerts}</td>
                <td class="reasons">${h.reasons.length ? h.reasons.map(esc).join("<br>") : '<span class="muted">-</span>'}</td>
                <td>${trustBtn}</td>
            </tr>`;
        }).join("");
    }

    /* ------------------------------------------------ events */

    document.addEventListener("click", async (event) => {
        const locate = event.target.closest("[data-locate]");
        if (locate && map) {
            const marker = markerByHistoryId[locate.dataset.locate];
            if (marker) {
                document.querySelector(".map-card").scrollIntoView({ behavior: "smooth", block: "center" });
                map.flyTo(marker.getLatLng(), 9, { duration: 1 });
                marker.openPopup();
            }
            return;
        }

        const trust = event.target.closest("[data-trust]");
        if (trust) {
            trust.disabled = true;
            const res = await fetch(`/api/history/${trust.dataset.trust}/trust`, { method: "POST" });
            if (res.ok) { toast("Marked as trusted"); load(); }
            else { toast("Could not update", "bad"); trust.disabled = false; }
            return;
        }

        const chip = event.target.closest(".fchip");
        if (chip) {
            document.querySelectorAll(".fchip").forEach((c) => c.classList.toggle("active", c === chip));
            state.filter = chip.dataset.filter;
            if (state.data) renderTable();
        }
    });

    $("searchBox").addEventListener("input", (event) => {
        state.query = event.target.value;
        if (state.data) renderTable();
    });

    $("refreshBtn").addEventListener("click", async () => {
        await load();
        toast("Dashboard refreshed");
    });

    // auto-refresh every 30s while the tab is visible
    setInterval(() => { if (!document.hidden) load(); }, 30000);

    load();
})();