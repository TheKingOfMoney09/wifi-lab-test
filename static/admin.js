(function () {
  "use strict";

  const btnStart = document.getElementById("btnStart");
  const btnStop = document.getElementById("btnStop");
  const btnExport = document.getElementById("btnExport");
  const statusPill = document.getElementById("statusPill");
  const sessionInfo = document.getElementById("sessionInfo");
  const moodleLine = document.getElementById("moodleLine");
  const healthBox = document.getElementById("health");
  const statGrid = document.getElementById("statGrid");
  const chartMoodle = document.getElementById("chartMoodle");
  const chartSignal = document.getElementById("chartSignal");
  const tbody = document.querySelector("#resultsTable tbody");

  const COLORS = {
    good: "#0ca30c",
    warning: "#fab219",
    serious: "#ec835a",
    critical: "#d03b3b",
    neutral: "#2a78d6",
  };

  // de ce a esuat masuratoarea Moodle, pe scurt, in romana
  const MOODLE_ERROR_LABEL = {
    timeout: "timeout",
    network: "fara raspuns",
    "mixed-content": "blocat de browser",
    "no-url": "fara URL",
  };
  const moodleErrorLabel = (code) => MOODLE_ERROR_LABEL[code] || code;

  let currentSessionId = null;
  let lastHealth = 0;

  btnStart.addEventListener("click", async () => {
    await fetch("/api/session/start", { method: "POST" });
    refresh();
  });
  btnStop.addEventListener("click", async () => {
    await fetch("/api/session/stop", { method: "POST" });
    refresh();
  });

  function setPill(state) {
    statusPill.className = "status-pill " + state;
    statusPill.textContent =
      state === "running" ? "test in curs" :
      state === "stopped" ? "test finalizat" : "asteptare";
  }

  function statusFor(metric, value) {
    if (value === null || value === undefined) return { cls: "neutral", label: "-" };
    if (metric === "latency") {
      if (value < 50) return { cls: "good", label: "OK" };
      if (value < 150) return { cls: "warning", label: "Atentie" };
      return { cls: "critical", label: "Critic" };
    }
    if (metric === "moodle") {
      if (value < 1000) return { cls: "good", label: "OK" };
      if (value < 3000) return { cls: "warning", label: "Atentie" };
      return { cls: "critical", label: "Critic" };
    }
    if (metric === "signal") {
      if (value >= 70) return { cls: "good", label: "OK" };
      if (value >= 40) return { cls: "warning", label: "Atentie" };
      return { cls: "critical", label: "Critic" };
    }
    if (metric === "throughput") {
      if (value >= 20) return { cls: "good", label: "OK" };
      if (value >= 5) return { cls: "warning", label: "Atentie" };
      return { cls: "critical", label: "Critic" };
    }
    return { cls: "neutral", label: "" };
  }

  function badge(st) {
    return `<span class="badge ${st.cls}">${st.label}</span>`;
  }

  function renderBars(container, items, unit) {
    const max = Math.max(1, ...items.map((i) => i.value || 0));
    container.innerHTML = items.map((i) => `
      <div class="bar-row">
        <div class="name" title="${i.name}">${i.name}</div>
        <div class="bar-track">
          <div class="bar-fill" style="width:${i.value === null || i.value === undefined ? 0 : Math.max(2, (i.value || 0) / max * 100)}%; background:${COLORS[i.status.cls]}"></div>
        </div>
        <div class="bar-val">${i.value === null || i.value === undefined ? "-" : i.value + " " + unit}</div>
      </div>
    `).join("") || '<p class="muted">Niciun rezultat inca.</p>';
  }

  function fmt(v, d = 1) {
    if (v === null || v === undefined || Number.isNaN(v)) return null;
    return Math.round(v * Math.pow(10, d)) / Math.pow(10, d);
  }

  function checkBadge(ok, label) {
    return `<span class="badge ${ok ? "good" : "critical"}">${label}</span>`;
  }

  function moodleText(res) {
    if (!res) return "<span class=\"badge neutral\">necunoscut</span>";
    if (res.ok) return checkBadge(true, "raspunde") + ` <span class="muted">${res.ms} ms</span>`;
    const reasons = {
      refused: "conexiune refuzata — nimic nu asculta pe acel port",
      timeout: "nu raspunde in 3 s (firewall sau alt IP)",
      dns: "numele serverului nu poate fi rezolvat",
    };
    const text = reasons[res.reason] || "nu raspunde";
    return checkBadge(false, "nu raspunde") +
      `<span class="muted" title="${(res.error || "").replace(/[<>"]/g, "")}"> ${text}</span>`;
  }

  function renderHealth(h) {
    // Verificarea Moodle se face de pe laptopul profesorului, deci separa
    // "Moodle e defect" de "elevii nu ajung la Moodle".
    const warnings = (h.warnings || []).map((w) => `<div class="warn-line">${w}</div>`).join("");
    healthBox.innerHTML = `
      <div class="health-row"><strong>Server:</strong> ${checkBadge(true, "pornit")}
        <span class="muted">v${h.version} - ${h.student_url}</span></div>
      <div class="health-row"><strong>Moodle (${h.moodle ? h.moodle.url : "?"}):</strong> ${moodleText(h.moodle)}</div>
      <div class="health-row"><strong>Date primite:</strong>
        <span class="muted">${h.counts.results} rezultate, ${h.counts.wifi_signals} rapoarte de semnal WiFi</span></div>
      ${warnings}
    `;
  }

  async function refreshHealth() {
    if (Date.now() - lastHealth < 5000) return;
    lastHealth = Date.now();
    try {
      const h = await (await fetch("/api/health", { cache: "no-store" })).json();
      renderHealth(h);
    } catch (e) {
      healthBox.innerHTML = '<span class="badge critical">server indisponibil</span>';
    }
  }

  async function refresh() {
    const sRes = await fetch("/api/session", { cache: "no-store" });
    const s = await sRes.json();
    currentSessionId = s.session_id;
    setPill(s.state);
    sessionInfo.textContent = s.session_id ? ("sesiune: " + s.session_id) : "fara sesiune activa";
    moodleLine.textContent = "Moodle: " + s.moodle_url;
    btnExport.href = s.session_id ? ("/api/export.csv?session_id=" + encodeURIComponent(s.session_id)) : "#";

    const rRes = await fetch("/api/results" + (s.session_id ? "?session_id=" + encodeURIComponent(s.session_id) : ""), { cache: "no-store" });
    const r = await rRes.json();
    const results = r.results || {};
    const wifi = r.wifi || {};

    // Gruparea e doar dupa IP (unic per calculator); numele real al laptopului
    // (trimis de scriptul WiFi) sau cel introdus in browser apare in paranteze.
    const hostname = (ip) => wifi[ip]?.client_id || results[ip]?.client_id || null;
    const display = (ip) => { const h = hostname(ip); return h && h !== ip ? `${ip} (${h})` : ip; };
    const ipSortKey = (ip) => ip.split(".").map((n) => String(parseInt(n, 10) || 0).padStart(3, "0")).join(".");
    const ips = Array.from(new Set([...Object.keys(results), ...Object.keys(wifi)]))
      .sort((a, b) => ipSortKey(a).localeCompare(ipSortKey(b)));

    const latencies = ips.map((ip) => results[ip]?.latency?.avg).filter((v) => typeof v === "number");
    const moodles = ips.map((ip) => results[ip] || {})
      .filter((res) => typeof res.moodle_load_ms === "number" && !res.moodle_timeout && !res.moodle_error)
      .map((res) => res.moodle_load_ms);
    const throughputs = ips.map((ip) => results[ip]?.throughput_mbps).filter((v) => typeof v === "number");
    const signals = ips.map((ip) => wifi[ip]?.signal_percent).filter((v) => typeof v === "number");
    const timeouts = ips.filter((ip) => results[ip]?.moodle_timeout || results[ip]?.moodle_error).length;

    const completed = Object.keys(results).length;
    const avg = (arr) => (arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : null);
    statGrid.innerHTML = `
      <div class="stat-tile"><div class="label">Laptopuri finalizate</div><div class="value">${completed}<span class="unit"> / ${s.expected_clients}</span></div></div>
      <div class="stat-tile"><div class="label">Latenta medie</div><div class="value">${fmt(avg(latencies)) ?? "-"}<span class="unit"> ms</span></div></div>
      <div class="stat-tile"><div class="label">Viteza medie</div><div class="value">${fmt(avg(throughputs)) ?? "-"}<span class="unit"> Mbps</span></div></div>
      <div class="stat-tile"><div class="label">Incarcare Moodle medie</div><div class="value">${fmt(avg(moodles), 0) ?? "-"}<span class="unit"> ms</span></div></div>
      <div class="stat-tile"><div class="label">Semnal WiFi mediu</div><div class="value">${fmt(avg(signals), 0) ?? "-"}<span class="unit"> %</span></div></div>
      <div class="stat-tile"><div class="label">Moodle: esecuri</div><div class="value">${timeouts}<span class="unit"> laptopuri</span></div></div>
    `;

    renderBars(chartMoodle, ips.map((ip) => {
      const res = results[ip] || {};
      const v = res.moodle_load_ms;
      return {
        name: display(ip),
        value: res.moodle_timeout || res.moodle_error ? null : fmt(v, 0),
        status: statusFor("moodle", res.moodle_timeout ? 999999 : (v ?? null)),
      };
    }), "ms");

    renderBars(chartSignal, ips.map((ip) => {
      const v = wifi[ip]?.signal_percent;
      return { name: display(ip), value: v ?? null, status: statusFor("signal", v ?? null) };
    }), "%");

    tbody.innerHTML = ips.map((ip) => {
      const res = results[ip] || {};
      const w = wifi[ip] || {};
      const lat = res.latency || {};
      const latSt = statusFor("latency", lat.avg ?? null);
      const moodleSt = statusFor("moodle", res.moodle_timeout ? 999999 : (res.moodle_load_ms ?? null));
      const sigSt = statusFor("signal", w.signal_percent ?? null);
      const moodleCell = res.moodle_timeout
        ? 'timeout ' + badge(moodleSt)
        : (res.moodle_error
          ? (moodleErrorLabel(res.moodle_error) + " " + badge(moodleSt))
          : ((res.moodle_load_ms ?? "-") + " " + (res.moodle_load_ms != null ? badge(moodleSt) : "")));
      return `<tr>
        <td>${display(ip)}</td>
        <td class="num">${lat.avg ?? "-"} ${lat.avg != null ? badge(latSt) : ""}</td>
        <td class="num">${lat.jitter ?? "-"}</td>
        <td class="num">${res.throughput_mbps ?? "-"}</td>
        <td class="num">${moodleCell}</td>
        <td>${w.ssid ?? "-"}</td>
        <td class="num">${w.signal_percent ?? "-"} ${w.signal_percent != null ? badge(sigSt) : ""}</td>
        <td class="num">${w.channel ?? "-"}</td>
        <td>${w.band ?? "-"}</td>
      </tr>`;
    }).join("");
  }

  // helper folosit doar ca sa excludem din media Moodle laptopurile cu timeout

  refreshHealth();
  refresh();
  setInterval(() => { refreshHealth(); refresh(); }, 2000);
})();
