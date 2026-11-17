(function () {
  const elId = document.getElementById("clientId");
  const btnSave = document.getElementById("btnSave");
  const statusPill = document.getElementById("statusPill");
  const sessionInfo = document.getElementById("sessionInfo");
  const cardProgress = document.getElementById("card-progress");
  const progressText = document.getElementById("progressText");
  const cardResults = document.getElementById("card-results");
  const resultList = document.getElementById("resultList");

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
      btnSave.textContent = "Salvat ✓";
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

  function mean(arr) { return arr.reduce((a, b) => a + b, 0) / arr.length; }
  function stddev(arr) {
    const m = mean(arr);
    return Math.sqrt(mean(arr.map((x) => (x - m) * (x - m))));
  }

  async function measureLatency(samples) {
    const times = [];
    for (let i = 0; i < samples; i++) {
      const t0 = performance.now();
      await fetch("/api/ping", { cache: "no-store" });
      times.push(performance.now() - t0);
    }
    return {
      avg: Math.round(mean(times) * 10) / 10,
      min: Math.round(Math.min(...times) * 10) / 10,
      max: Math.round(Math.max(...times) * 10) / 10,
      jitter: Math.round(stddev(times) * 10) / 10,
    };
  }

  async function measureThroughput(bytes) {
    const t0 = performance.now();
    const resp = await fetch("/api/payload?size=" + bytes, { cache: "no-store" });
    const blob = await resp.blob();
    const seconds = (performance.now() - t0) / 1000;
    const mbps = (blob.size * 8 / 1e6) / seconds;
    return Math.round(mbps * 100) / 100;
  }

  async function measureMoodleLoad(url, timeoutMs) {
    // fetch 'no-cors' ignora X-Frame-Options (care ar bloca un <iframe>) si
    // masoara timpul real de raspuns al serverului Moodle sub incarcare.
    const target = url + (url.includes("?") ? "&" : "?") + "_=" + Date.now();
    const t0 = performance.now();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      await fetch(target, { mode: "no-cors", cache: "no-store", signal: controller.signal });
      return { ms: Math.round(performance.now() - t0), timeout: false };
    } catch (e) {
      return { ms: Math.round(performance.now() - t0), timeout: true };
    } finally {
      clearTimeout(timer);
    }
  }

  function getConnectionInfo() {
    const c = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
    if (!c) return null;
    return {
      effectiveType: c.effectiveType,
      downlink: c.downlink,
      rtt: c.rtt,
    };
  }

  async function runTest(sessionId, moodleUrl) {
    running = true;
    cardProgress.style.display = "";
    cardResults.style.display = "none";

    let latency = null, throughput_mbps = null, moodle = { ms: null, timeout: false }, connection = null;

    try {
      progressText.textContent = "Masor latenta (ping)...";
      latency = await measureLatency(10);
    } catch (e) { /* trimitem mai departe ce avem */ }

    try {
      progressText.textContent = "Masor viteza de descarcare...";
      throughput_mbps = await measureThroughput(2 * 1024 * 1024);
    } catch (e) { /* trimitem mai departe ce avem */ }

    try {
      progressText.textContent = "Masor timpul de incarcare Moodle...";
      moodle = await measureMoodleLoad(moodleUrl, 15000);
    } catch (e) { /* trimitem mai departe ce avem */ }

    try {
      connection = getConnectionInfo();
    } catch (e) { /* ignoram */ }

    try {
      progressText.textContent = "Trimit rezultatele catre profesor...";
      await fetch("/api/results", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          client_id: clientId,
          session_id: sessionId,
          latency,
          throughput_mbps,
          moodle_load_ms: moodle.ms,
          moodle_timeout: moodle.timeout,
          connection,
        }),
      });

      cardProgress.style.display = "none";
      cardResults.style.display = "";
      resultList.innerHTML = `
        <div><span>Latenta medie</span><strong>${latency ? latency.avg + " ms" : "—"}</strong></div>
        <div><span>Jitter</span><strong>${latency ? latency.jitter + " ms" : "—"}</strong></div>
        <div><span>Viteza descarcare</span><strong>${throughput_mbps !== null ? throughput_mbps + " Mbps" : "—"}</strong></div>
        <div><span>Timp incarcare Moodle</span><strong>${moodle.timeout ? "timeout (peste 15s)" : (moodle.ms !== null ? moodle.ms + " ms" : "—")}</strong></div>
      `;
    } catch (e) {
      cardProgress.style.display = "none";
      cardResults.style.display = "";
      resultList.innerHTML = `<div><span>Eroare la trimiterea rezultatelor catre server.</span></div>`;
    } finally {
      running = false;
    }
  }

  poll();
  setInterval(poll, 1500);
})();
