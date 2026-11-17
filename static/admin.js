(function () {
  const btnStart = document.getElementById("btnStart");
  const btnStop = document.getElementById("btnStop");
  const btnExport = document.getElementById("btnExport");
  const statusPill = document.getElementById("statusPill");
  const sessionInfo = document.getElementById("sessionInfo");
  const moodleLine = document.getElementById("moodleLine");
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

  let currentSessionId = null;

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
    if (value === null || value === undefined) return { cls: "neutral", label: "—" };
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
          <div class="bar-fill" style="width:${Math.max(2, (i.value || 0) / max * 100)}%; background:${COLORS[i.status.cls]}"></div>
        </div>
        <div class="bar-val">${i.value === null || i.value === undefined ? "—" : i.value + " " + unit}</div>
      </div>
    `).join("") || '<p class="muted">Niciun rezultat inca.</p>';
  }

  function fmt(v, d = 1) {
    if (v === null || v === undefined || Number.isNaN(v)) return null;
    return Math.round(v * Math.pow(10, d)) / Math.pow(10, d);
  }

  async function refresh() {
    const sRes = await fetch("/api/session", { cache: "no-store" });
    const s = await sRes.json();
    currentSessionId = s.session_id;
    setPill(s.state);
    sessionInfo.textContent = s.session_id ? ("sesiune: " + s.session_id) : "fara sesiune activa";
    moodleLine.textContent = "Moodle: " + s.moodle_url;
    btnExport.href = "/api/export.csv?session_id=" + (s.session_id || "");

    const rRes = await fetch("/api/results?session_id=" + (s.session_id || ""), { cache: "no-store" });
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

    const latencies = ips.map((ip) => results[ip]?.latency?.avg).filter((v) => v !== undefined && v !== null);
    const moodles = ips.map((ip) => results[ip]?.moodle_load_ms).filter((v) => v !== undefined && v !== null);
    const throughputs = ips.map((ip) => results[ip]?.throughput_mbps).filter((v) => v !== undefined && v !== null);
    const signals = ips.map((ip) => wifi[ip]?.signal_percent).filter((v) => v !== undefined && v !== null);

    const completed = Object.keys(results).length;
    statGrid.innerHTML = `
      <div class="stat-tile"><div class="label">Laptopuri finalizate</div><div class="value">${completed}<span class="unit"> / ${s.expected_clients}</span></div></div>
      <div class="stat-tile"><div class="label">Latenta medie</div><div class="value">${fmt(latencies.length ? latencies.reduce((a,b)=>a+b,0)/latencies.length : null) ?? "—"}<span class="unit"> ms</span></div></div>
      <div class="stat-tile"><div class="label">Viteza medie</div><div class="value">${fmt(throughputs.length ? throughputs.reduce((a,b)=>a+b,0)/throughputs.length : null) ?? "—"}<span class="unit"> Mbps</span></div></div>
      <div class="stat-tile"><div class="label">Incarcare Moodle medie</div><div class="value">${fmt(moodles.length ? moodles.reduce((a,b)=>a+b,0)/moodles.length : null, 0) ?? "—"}<span class="unit"> ms</span></div></div>
      <div class="stat-tile"><div class="label">Semnal WiFi mediu</div><div class="value">${fmt(signals.length ? signals.reduce((a,b)=>a+b,0)/signals.length : null, 0) ?? "—"}<span class="unit"> %</span></div></div>
    `;

    renderBars(chartMoodle, ips.map((ip) => {
      const v = results[ip]?.moodle_load_ms;
      const timeout = results[ip]?.moodle_timeout;
      return { name: display(ip), value: timeout ? null : fmt(v, 0), status: statusFor("moodle", timeout ? 999999 : v) };
    }), "ms");

    renderBars(chartSignal, ips.map((ip) => {
      const v = wifi[ip]?.signal_percent;
      return { name: display(ip), value: v, status: statusFor("signal", v) };
    }), "%");

    tbody.innerHTML = ips.map((ip) => {
      const res = results[ip] || {};
      const w = wifi[ip] || {};
      const lat = res.latency || {};
      const latSt = statusFor("latency", lat.avg);
      const moodleSt = statusFor("moodle", res.moodle_timeout ? 999999 : res.moodle_load_ms);
      const sigSt = statusFor("signal", w.signal_percent);
      return `<tr>
        <td>${display(ip)}</td>
        <td class="num">${lat.avg ?? "—"} ${lat.avg !== undefined && lat.avg !== null ? badge(latSt) : ""}</td>
        <td class="num">${lat.jitter ?? "—"}</td>
        <td class="num">${res.throughput_mbps ?? "—"}</td>
        <td class="num">${res.moodle_timeout ? "timeout" : (res.moodle_load_ms ?? "—")} ${res.moodle_load_ms !== undefined && res.moodle_load_ms !== null ? badge(moodleSt) : ""}</td>
        <td>${w.ssid ?? "—"}</td>
        <td class="num">${w.signal_percent ?? "—"} ${w.signal_percent !== undefined && w.signal_percent !== null ? badge(sigSt) : ""}</td>
        <td>${w.channel ?? "—"}</td>
      </tr>`;
    }).join("");
  }

  refresh();
  setInterval(refresh, 2000);
})();
