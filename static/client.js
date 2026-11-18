(function () {
  "use strict";

  const elId = document.getElementById("clientId");
  const btnSave = document.getElementById("btnSave");
  const statusPill = document.getElementById("statusPill");
  const sessionInfo = document.getElementById("sessionInfo");
  const cardProgress = document.getElementById("card-progress");
  const progressText = document.getElementById("progressText");
  const cardResults = document.getElementById("card-results");
  const resultList = document.getElementById("resultList");

  const PING_SAMPLES = 15;          // prima cerere e de incalzire, nu se numara
  const PAYLOAD_BYTES = 2 * 1024 * 1024;
  const MOODLE_TIMEOUT_MS = 15000;
  const POLL_MS = 1500;

  let clientId = localStorage.getItem("wifitest_client_id") || "";
  let idIsManual = localStorage.getItem("wifitest_client_id_manual") === "1";
  if (!clientId) {
    clientId = "PC-" + Math.random().toString(16).slice(2, 6).toUpperCase();
    localStorage.setItem("wifitest_client_id", clientId);
  }
  elId.value = clientId;

  let lastHandledSession = localStorage.getItem("wifitest_last_session") || "";
  let moodleUrl = "";
  let running = false;

  btnSave.addEventListener("click", () => {
    const v = elId.value.trim();
    if (v) {
      clientId = v;
      idIsManual = true;
      localStorage.setItem("wifitest_client_id", clientId);
      localStorage.setItem("wifitest_client_id_manual", "1");
      btnSave.textContent = "Salvat";
      setTimeout(() => (btnSave.textContent = "Salveaza"), 1200);
    }
  });

  async function checkWhoami() {
    if (idIsManual) return;
    try {
      const r = await fetch("/api/whoami", { cache: "no-store" });
      const data = await r.json();
      if (data.client_id && data.client_id !== clientId) {
        clientId = data.client_id;
        elId.value = clientId;
        localStorage.setItem("wifitest_client_id", clientId);
      }
    } catch (e) { /* ignoram, mai incercam la urmatorul poll */ }
  }

  function setPill(state) {
    statusPill.className = "status-pill " + state;
    statusPill.textContent =
      state === "running" ? "test in curs" :
      state === "stopped" ? "test finalizat" : "asteptare";
  }

  async function poll() {
    checkWhoami();
    try {
      const r = await fetch("/api/session", { cache: "no-store" });
      const s = await r.json();
      moodleUrl = s.moodle_url;
      setPill(s.state);
      sessionInfo.textContent = s.session_id ? ("sesiune: " + s.session_id) : "";

      if (s.state === "running" && s.session_id && s.session_id !== lastHandledSession && !running) {
        lastHandledSession = s.session_id;
        localStorage.setItem("wifitest_last_session", lastHandledSession);
        runTest(s.session_id, moodleUrl);
      }
    } catch (e) {
      sessionInfo.textContent = "server indisponibil...";
    }
  }

  // O fila din fundal e limitata de browser la un poll pe minut, asa ca testul
  // ar porni cu intarziere. Reimprospatam imediat cand fila revine in prim-plan.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) poll();
  });

  function mean(arr) { return arr.reduce((a, b) => a + b, 0) / arr.length; }
  function stddev(arr) {
    if (arr.length < 2) return 0;
    const m = mean(arr);
    return Math.sqrt(mean(arr.map((x) => (x - m) * (x - m))));
  }
  function percentile(arr, p) {
    const s = [...arr].sort((a, b) => a - b);
    return s[Math.min(s.length - 1, Math.floor(p * s.length))];
  }
  const round1 = (v) => Math.round(v * 10) / 10;

  async function measureLatency(samples) {
    try { await fetch("/api/ping?warmup=1", { cache: "no-store" }); } catch (e) { /* ignoram */ }
    const times = [];
    let lost = 0;
    for (let i = 0; i < samples; i++) {
      const t0 = performance.now();
      try {
        await fetch("/api/ping?r=" + Math.random().toString(36).slice(2), { cache: "no-store" });
        times.push(performance.now() - t0);
      } catch (e) {
        lost++;
      }
    }
    if (!times.length) return null;
    return {
      avg: round1(mean(times)),
      min: round1(Math.min(...times)),
      max: round1(Math.max(...times)),
      p95: round1(percentile(times, 0.95)),
      jitter: round1(stddev(times)),
      samples: times.length,
      loss_pct: round1((lost / samples) * 100),
    };
  }

  async function measureThroughput(bytes) {
    const t0 = performance.now();
    const resp = await fetch("/api/payload?size=" + bytes, { cache: "no-store" });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    let received = 0;
    if (resp.body && resp.body.getReader) {
      // citim in flux: masuram cat timp dureaza chiar transferul pe WiFi,
      // fara timpul de copiere in memorie al unui blob
      const reader = resp.body.getReader();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        received += value.length;
      }
    } else {
      received = (await resp.blob()).size;
    }
    const seconds = (performance.now() - t0) / 1000;
    if (!received || seconds <= 0) throw new Error("transfer gol");
    return Math.round((received * 8 / 1e6 / seconds) * 100) / 100;
  }

  function describeFetchError(e) {
    const msg = String((e && e.message) || e || "");
    if (/mixed content/i.test(msg)) return "mixed-content";
    if (/abort/i.test(msg)) return "timeout";
    if (/failed to fetch|networkerror|load failed|network error/i.test(msg)) return "network";
    return msg.slice(0, 80) || "unknown";
  }

  async function measureMoodleLoad(url, timeoutMs) {
    if (!url) return { ms: null, timeout: false, error: "no-url" };
    const target = url + (url.includes("?") ? "&" : "?") + "_=" + Date.now();
    const t0 = performance.now();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      // 'no-cors' evita X-Frame-Options si ne lasa sa masuram timpul real al
      // serverului Moodle. Body-ul e opac, deci durata totala o luam din
      // Resource Timing (acolo apare si descarcarea completa, nu doar antetele).
      await fetch(target, { mode: "no-cors", cache: "no-store", signal: controller.signal });
      let ms = performance.now() - t0;
      const entry = await waitForTimingEntry(target, 800);
      if (entry && entry.duration > 0) ms = entry.duration;
      return { ms: Math.round(ms), timeout: false, error: null };
    } catch (e) {
      const reason = describeFetchError(e);
      return { ms: Math.round(performance.now() - t0), timeout: reason === "timeout", error: reason };
    } finally {
      clearTimeout(timer);
    }
  }

  async function waitForTimingEntry(url, maxWaitMs) {
    try { performance.setResourceTimingBufferSize(1000); } catch (e) { /* ignoram */ }
    const until = performance.now() + maxWaitMs;
    for (;;) {
      const hit = performance.getEntriesByType("resource").filter((e) => e.name === url).pop();
      if (hit) return hit;
      if (performance.now() > until) return null;
      await new Promise((r) => setTimeout(r, 50));
    }
  }

  function getConnectionInfo() {
    const c = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
    if (!c) return null;
    return { effectiveType: c.effectiveType, downlink: c.downlink, rtt: c.rtt };
  }

  async function postResults(payload) {
    const body = JSON.stringify(payload);
    for (let attempt = 1; attempt <= 2; attempt++) {
      try {
        const r = await fetch("/api/results", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body,
        });
        if (r.ok) return true;
      } catch (e) { /* reincercam o data */ }
      await new Promise((res) => setTimeout(res, 700));
    }
    return false;
  }

  function moodleLabel(moodle) {
    if (moodle.error === "no-url") return "URL Moodle lipsa in config.json";
    if (moodle.error === "timeout") return "timeout (peste " + (MOODLE_TIMEOUT_MS / 1000) + " s)";
    if (moodle.error === "network") return "nu raspunde (retea / firewall)";
    if (moodle.error === "mixed-content") return "blocat de browser (pagina https, Moodle http)";
    if (moodle.error) return moodle.error;
    return moodle.ms !== null ? moodle.ms + " ms" : "-";
  }

  async function runTest(sessionId, moodleUrlValue) {
    running = true;
    cardProgress.style.display = "";
    cardResults.style.display = "none";

    let latency = null, throughput = null;
    let moodle = { ms: null, timeout: false, error: null };
    let connection = null;

    try {
      progressText.textContent = "Masor latenta (ping)...";
      latency = await measureLatency(PING_SAMPLES);
    } catch (e) { /* trimitem mai departe ce avem */ }

    try {
      progressText.textContent = "Masor viteza de descarcare...";
      throughput = await measureThroughput(PAYLOAD_BYTES);
    } catch (e) { /* trimitem mai departe ce avem */ }

    try {
      progressText.textContent = "Masor timpul de incarcare Moodle...";
      moodle = await measureMoodleLoad(moodleUrlValue, MOODLE_TIMEOUT_MS);
      if (moodle.error === "timeout" || moodle.error === "network") {
        // o singura reincercare: prima cerere poate prinde Moodle-ul rece
        progressText.textContent = "Reincerc Moodle...";
        const retry = await measureMoodleLoad(moodleUrlValue, MOODLE_TIMEOUT_MS);
        if (!retry.error || (retry.ms !== null && retry.ms < moodle.ms)) moodle = retry;
      }
    } catch (e) { /* trimitem mai departe ce avem */ }

    try {
      connection = getConnectionInfo();
    } catch (e) { /* ignoram */ }

    progressText.textContent = "Trimit rezultatele catre profesor...";
    const sent = await postResults({
      client_id: clientId,
      session_id: sessionId,
      latency,
      throughput_mbps: throughput,
      moodle_load_ms: moodle.ms,
      moodle_timeout: moodle.timeout,
      moodle_error: moodle.error,
      connection,
      page_visible: !document.hidden,
    });

    cardProgress.style.display = "none";
    cardResults.style.display = "";
    resultList.innerHTML = `
      <div><span>Latenta medie</span><strong>${latency ? latency.avg + " ms" : "-"}</strong></div>
      <div><span>Jitter / p95</span><strong>${latency ? latency.jitter + " ms / " + latency.p95 + " ms" : "-"}</strong></div>
      <div><span>Viteza descarcare</span><strong>${throughput !== null ? throughput + " Mbps" : "-"}</strong></div>
      <div><span>Timp incarcare Moodle</span><strong>${moodleLabel(moodle)}</strong></div>
      <div><span>Trimitere catre server</span><strong>${sent ? "ok" : "EROARE - anunta profesorul"}</strong></div>
    `;
    if (!sent) {
      resultList.innerHTML += '<div><span>Rezultatul nu a ajuns la server. Verifica reteaua si reincarca pagina.</span></div>';
    }
    running = false;
  }

  poll();
  setInterval(poll, POLL_MS);
})();
