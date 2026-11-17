#!/usr/bin/env python3
"""Test de laborator — server central pentru testarea rețelei și a accesului la Moodle.

Pornire (pe calculatorul cu Moodle):
    python3 lab_server.py
Elevii deschid în browser:   http://<IP-ul acestui calculator>:8090
Profesorul deschide, pe acest calculator:   http://127.0.0.1:8090/admin

Doar Python 3 (biblioteca standard). Variabile opționale: PORT (8090), MOODLE_PORT (8083).
"""
import csv
import io
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
import urllib.request
from html import unescape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get("PORT", "8090"))
MOODLE_PORT = int(os.environ.get("MOODLE_PORT", "8083"))
BLOB = os.urandom(1024 * 1024)

lock = threading.Lock()
clients = {}  # id -> date despre fiecare calculator
state = {"run": 0, "running": False, "start_at": 0, "mode": "sim", "duration": 60, "pause": 1.0}
stats = {"requests": 0, "bytes": 0}
_moodle_cache = {}
_local_ips = {"t": 0, "ips": set()}


def now_ms():
    return int(time.time() * 1000)


def lan_ip():
    out = subprocess.run(["ip", "route", "get", "1.1.1.1"], capture_output=True, text=True).stdout
    m = re.search(r"src (\S+)", out)
    return m.group(1) if m else "127.0.0.1"


def local_ips():
    if time.time() - _local_ips["t"] > 10:
        out = subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.split()
        _local_ips.update(t=time.time(), ips={"127.0.0.1", "::1", *out})
    return _local_ips["ips"]


def moodle_assets(host):
    """Lista de fișiere pe care le încarcă pagina de login Moodle (HTML + CSS/JS/imagini)."""
    if not re.fullmatch(r"[\w.-]+", host or ""):
        return {"error": "host invalid"}
    hit = _moodle_cache.get(host)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    base = f"http://{host}:{MOODLE_PORT}"
    page = base + "/login/index.php"
    req = urllib.request.Request(f"http://127.0.0.1:{MOODLE_PORT}/login/index.php",
                                 headers={"Host": f"{host}:{MOODLE_PORT}", "User-Agent": "lab-test"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            html = r.read().decode("utf-8", "replace")
    except OSError as e:
        return {"error": f"Moodle nu răspunde: {e}"}
    found = re.findall(r'<(?:script|img)\b[^>]*\bsrc="([^"]+)"', html)
    found += re.findall(r'<link\b[^>]*\bhref="([^"]+)"', html)
    urls, seen = [page], {page}
    for u in map(unescape, found):
        if u.startswith(base) and u not in seen:
            seen.add(u)
            urls.append(u)
    res = {"urls": urls}
    _moodle_cache[host] = (time.time(), res)
    return res


def ip_key(ip):
    try:
        return (0, *map(int, ip.split(".")))
    except ValueError:
        return (1, ip)


def summary():
    t = now_ms()
    with lock:
        rows = [{**{k: v for k, v in c.items() if not k.startswith("_")},
                 "online": t - c.get("last_seen", 0) < 5000,
                 "wifi_online": t - c.get("wifi_seen", 0) < 10000} for c in clients.values()]
    rows.sort(key=lambda r: ip_key(r["ip"]))
    return {"clients": rows, "state": state, "stats": stats, "now": t,
            "url": f"http://{lan_ip()}:{PORT}"}


RESULT_FIELDS = ["phase", "ping_avg", "ping_jitter", "ping_max", "loss",
                 "pages", "page_avg", "page_p95", "page_max", "errors", "down_mbps", "up_mbps"]
# trimise de scriptul Windows (netsh wlan show interfaces)
WIFI_FIELDS = {"pc_name": str, "ssid": str, "bssid": str, "band": str, "radio": str,
               "signal": int, "rssi": int, "channel": int, "rx_rate": float, "tx_rate": float}
RUN_WIFI = ["sig_min", "sig_avg"]  # semnalul în timpul testului
CSV_FIELDS = ["ip", "pc_name"] + RESULT_FIELDS + [k for k in WIFI_FIELDS if k != "pc_name"] + RUN_WIFI


def wifi_update(c, d, t):
    for k, cast in WIFI_FIELDS.items():
        v = d.get(k)
        try:
            c[k] = None if v in (None, "") else (cast(v)[:40] if cast is str else cast(v))
        except (TypeError, ValueError):
            c[k] = None
    c["wifi_seen"] = t
    in_run = state["running"] and state["start_at"] <= t <= state["start_at"] + state["duration"] * 1000 + 30000
    if in_run and c["signal"] is not None:
        s = c.setdefault("_sig", [])
        s.append(c["signal"])
        c.update(sig_min=min(s), sig_avg=round(sum(s) / len(s)))


WIFI_PS1 = r"""# Test semnal WiFi - laborator. Citeste semnalul cu netsh si il trimite la server la fiecare 3 secunde.
$server = "__SERVER__"
$host.UI.RawUI.WindowTitle = "Test semnal WiFi - laborator"
Write-Host "Trimit semnalul WiFi catre $server la fiecare 3 secunde." -ForegroundColor Cyan
Write-Host "Lasa fereastra deschisa in timpul testului. La final o poti inchide." -ForegroundColor Cyan
Write-Host ""
while ($true) {
    $d = @{ pc_name = $env:COMPUTERNAME }
    $rates = @()
    foreach ($line in (netsh wlan show interfaces)) {
        if ($line -notmatch '^\s*([^:]+?)\s*:\s*(.*?)\s*$') { continue }
        $k = $matches[1]; $v = $matches[2]
        if ($k -eq 'SSID') { $d.ssid = $v }
        elseif ($k -match 'BSSID') { $d.bssid = $v }
        elseif ($v -match '^(\d+)\s*%$') { $d.signal = [int]$matches[1] }
        elseif ($k -match '^(?i)rssi$') { $d.rssi = $v }
        elseif ($k -match '^(?i)(channel|canal)$') { $d.channel = $v }
        elseif ($v -match '^802\.11') { $d.radio = $v }
        elseif ($v -match '^[\d.,]+\s*GHz$') { $d.band = $v }
        elseif ($k -match '\(Mbps\)') { $rates += ($v -replace ',', '.') }
    }
    if ($rates.Count -gt 0) { $d.rx_rate = $rates[0] }
    if ($rates.Count -gt 1) { $d.tx_rate = $rates[1] }
    $time = Get-Date -Format "HH:mm:ss"
    try {
        Invoke-RestMethod -Uri "$server/api/wifi" -Method Post -Body ($d | ConvertTo-Json) -ContentType "application/json" -TimeoutSec 5 -ErrorAction Stop | Out-Null
        Write-Host ("{0}  {1}  semnal {2}%  canal {3}  {4}  - trimis" -f $time, $d.ssid, $d.signal, $d.channel, $d.band)
    } catch {
        Write-Host "$time  Nu ajung la server ($server). Verifica reteaua." -ForegroundColor Yellow
    }
    Start-Sleep -Seconds 3
}
"""

# fișier .bat care conține și scriptul PowerShell (un singur fișier, dublu-clic)
WIFI_BAT_HEAD = ("<# :\r\n@echo off\r\ntitle Test semnal WiFi - laborator\r\n"
                 "powershell -NoProfile -ExecutionPolicy Bypass -Command \"iex ((Get-Content -LiteralPath '%~f0') -join [char]10)\"\r\n"
                 "exit /b\r\n#>\r\n")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    disable_nagle_algorithm = True  # altfel fiecare răspuns mic întârzie ~40 ms

    def log_message(self, *a):
        pass

    def send(self, body, ctype="application/json", code=200):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)
        with lock:
            stats["requests"] += 1
            stats["bytes"] += len(data)

    def send_bytes(self, n, ctype="application/octet-stream"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(n))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        left = n
        while left > 0:
            k = min(left, len(BLOB))
            self.wfile.write(BLOB[:k])
            left -= k
        with lock:
            stats["requests"] += 1
            stats["bytes"] += n

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        left, chunks = n, []
        while left > 0:
            c = self.rfile.read(min(left, 65536))
            if not c:
                break
            chunks.append(c)
            left -= len(c)
        return b"".join(chunks)

    def ip(self):
        return self.client_address[0].removeprefix("::ffff:")

    def is_local(self):
        return self.ip() in local_ips()

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        p = u.path
        if p == "/":
            return self.send(STUDENT_PAGE, "text/html; charset=utf-8")
        if p == "/ping":
            return self.send(b"ok", "text/plain")
        if p == "/sim/asset":
            kb = max(1, min(int(q.get("kb", "10")), 1024))
            return self.send_bytes(kb * 1024)
        if p == "/dl":
            mb = max(1, min(int(q.get("mb", "10")), 50))
            return self.send_bytes(mb * 1024 * 1024)
        if p == "/api/state":
            # calculatorul e identificat după adresa IP; dacă pagina e deschisă în mai multe
            # taburi pe același calculator, doar primul tab participă la test
            ip, tab, t = self.ip(), q.get("tab", "")[:20], now_ms()
            with lock:
                c = clients.setdefault(ip, {"ip": ip, "phase": "așteaptă"})
                dup = bool(c.get("tab")) and c["tab"] != tab and t - c.get("tab_seen", 0) < 5000
                if not dup:
                    c.update(tab=tab, tab_seen=t)
                c["last_seen"] = t
                if c.get("phase") == "doar semnal":
                    c["phase"] = "așteaptă"
                wifi = ({k: c.get(k) for k in ("ssid", "signal", "channel", "band")} | {"age": (t - c["wifi_seen"]) // 1000}
                        if c.get("wifi_seen") else None)
            return self.send(json.dumps({**state, "now": t, "ip": ip, "dup": dup, "wifi": wifi}))
        if p in ("/wifi.ps1", "/test-semnal-wifi.bat"):
            server = "http://" + re.sub(r"[^\w.:\[\]-]", "", self.headers.get("Host") or f"{lan_ip()}:{PORT}")
            ps1 = WIFI_PS1.replace("__SERVER__", server)
            if p.endswith(".ps1"):
                return self.send(ps1, "text/plain; charset=utf-8")
            data = (WIFI_BAT_HEAD + ps1.replace("\r\n", "\n").replace("\n", "\r\n")).encode("ascii")
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", 'attachment; filename="test-semnal-wifi.bat"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        if p == "/api/moodle-assets":
            return self.send(json.dumps(moodle_assets(q.get("host", ""))))
        # ---- doar pentru profesor (de pe acest calculator) ----
        if not self.is_local():
            return self.send("Acces permis doar de pe calculatorul serverului.", "text/plain; charset=utf-8", 403)
        if p == "/admin":
            return self.send(ADMIN_PAGE, "text/html; charset=utf-8")
        if p == "/api/clients":
            return self.send(json.dumps(summary()))
        if p == "/api/export.csv":
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
            w.writeheader()
            for r in summary()["clients"]:
                w.writerow(r)
            self.send_response(200)
            data = ("﻿" + buf.getvalue()).encode()
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="test-laborator-{time.strftime("%Y%m%d-%H%M")}.csv"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        self.send("not found", "text/plain", 404)

    def do_POST(self):
        p = urlparse(self.path).path
        data = self.body()
        if p == "/sim/upload":
            return self.send(json.dumps({"bytes": len(data)}))
        if p == "/api/result":
            try:
                r = json.loads(data)
            except ValueError:
                return self.send("bad json", "text/plain", 400)
            with lock:
                c = clients.get(self.ip())
                if c and c.get("tab") == r.get("tab") and r.get("run") == state["run"]:
                    c.update({k: r[k] for k in RESULT_FIELDS + ["progress"] if k in r})
            return self.send("{}")
        if p == "/api/wifi":
            try:
                d = json.loads(data.decode("utf-8-sig", "replace"))
            except ValueError:
                return self.send("bad json", "text/plain", 400)
            ip = self.ip()
            with lock:
                c = clients.setdefault(ip, {"ip": ip, "phase": "doar semnal", "last_seen": 0})
                wifi_update(c, d, now_ms())
            return self.send('{"status":"ok"}')
        if p == "/api/control":
            if not self.is_local():
                return self.send("forbidden", "text/plain", 403)
            c = json.loads(data or b"{}")
            with lock:
                act = c.get("action")
                if act == "start":
                    state.update(run=state["run"] + 1, running=True, start_at=now_ms() + 3000,
                                 mode=c.get("mode") if c.get("mode") in ("sim", "moodle") else "sim",
                                 duration=max(10, min(int(c.get("duration", 60)), 600)),
                                 pause=max(0.0, min(float(c.get("pause", 1)), 30)))
                    for cl in clients.values():
                        for k in RESULT_FIELDS + RUN_WIFI + ["progress", "_sig"]:
                            cl.pop(k, None)
                        cl["phase"] = "pornește"
                elif act == "stop":
                    state["running"] = False
                elif act == "reset":
                    state["running"] = False
                    t = now_ms()
                    for k in [k for k, v in clients.items()
                              if t - v.get("last_seen", 0) > 5000 and t - v.get("wifi_seen", 0) > 10000]:
                        del clients[k]
            return self.send(json.dumps(state))
        self.send("not found", "text/plain", 404)


COMMON_CSS = r"""
:root{--bg:#f4f5f7;--card:#fff;--fg:#1a1d23;--mut:#667085;--line:#e4e7ec;--acc:#2554d9;
--good:#15803d;--ok:#b45309;--bad:#c81e1e;--goodbg:#dcfce7;--okbg:#fef3c7;--badbg:#fee2e2}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#181b21;--fg:#e8eaee;--mut:#9aa1ad;
--line:#2a2e36;--acc:#7aa2ff;--good:#4ade80;--ok:#fbbf24;--bad:#f87171;--goodbg:#14301f;--okbg:#3a2c0d;--badbg:#3b1616}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1200px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:22px;margin:0}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
.mut{color:var(--mut)}
button{background:var(--acc);color:#fff;border:0;border-radius:8px;padding:10px 18px;font:inherit;font-weight:600;cursor:pointer}
button.sec{background:transparent;color:var(--fg);border:1px solid var(--line)}
button:disabled{opacity:.45;cursor:default}
input,select{font:inherit;padding:9px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg)}
.good{color:var(--good)}.ok{color:var(--ok)}.bad{color:var(--bad)}
.num{font-variant-numeric:tabular-nums}
"""

STUDENT_PAGE = r"""<!doctype html><html lang="ro"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Test rețea laborator</title>
<style>""" + COMMON_CSS + r"""
main{max-width:640px}
.row{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.row input{flex:1;min-width:180px}
#status{font-size:20px;font-weight:600;margin:20px 0 8px}
.bar{height:10px;background:var(--line);border-radius:5px;overflow:hidden;margin:12px 0 20px}
.bar>i{display:block;height:100%;width:0;background:var(--acc);transition:width .3s}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}
.grid .card h3{margin:0 0 4px;font-size:12px;color:var(--mut);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.big{font-size:24px;font-weight:700}
</style></head><body><main>
<h1>Test rețea laborator</h1>
<p class="mut">Lasă pagina deschisă. Testul pornește de la profesor.</p>
<div class="card" style="margin-top:12px">Calculatorul tău: <b class="num" id="myip" style="font-size:20px">…</b></div>
<div id="status">Mă conectez la server…</div>
<div class="mut" id="detail"></div>
<div class="bar"><i id="prog"></i></div>
<div class="grid">
 <div class="card"><h3>Latență</h3><div class="big num" id="r_ping">–</div><div class="mut num" id="r_ping2"></div></div>
 <div class="card"><h3>Încărcare pagină</h3><div class="big num" id="r_page">–</div><div class="mut num" id="r_page2"></div></div>
 <div class="card"><h3>Download</h3><div class="big num" id="r_down">–</div></div>
 <div class="card"><h3>Upload</h3><div class="big num" id="r_up">–</div></div>
</div>
<div class="card" style="margin-top:16px"><h3 style="margin:0 0 6px;font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.04em">Semnal Wi-Fi</h3>
 <div id="wifi" style="font-size:18px;font-weight:600">Scriptul de semnal nu rulează pe acest calculator.</div>
 <p class="mut" style="margin:8px 0 10px">Browserul nu poate citi semnalul Wi-Fi. Descarcă scriptul, deschide-l cu dublu-clic și lasă fereastra deschisă în timpul testului.
 Dacă Windows afișează „Windows protected your PC”, apasă „More info” → „Run anyway”.</p>
 <a href="/test-semnal-wifi.bat" download><button>Descarcă test-semnal-wifi.bat</button></a>
 <details style="margin-top:10px"><summary class="mut">Alternativ, din PowerShell</summary>
 <code id="pscmd" style="display:block;margin-top:6px;padding:8px;border:1px solid var(--line);border-radius:6px;word-break:break-all"></code></details>
</div></main>
<script>
const $=id=>document.getElementById(id);
const tab=Math.random().toString(36).slice(2,12);
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
$("pscmd").textContent=`irm ${location.origin}/wifi.ps1 | iex`;
let offset=0, st=null, curRun=null, abort=null, R={};

const SIM=[[25],[420,35,300,140,60,45,40,30,25,80,75,18,12,10,8,8,6,6,5,5,4,4,3,3,2,2,2,2,1,1]];
const rnd=()=>Math.random().toString(36).slice(2);
const srvNow=()=>Date.now()+offset;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

async function poll(){
  const t0=Date.now();
  try{
    const s=await (await fetch(`/api/state?tab=${tab}`,{cache:"no-store"})).json();
    const t1=Date.now(); offset=s.now-(t0+t1)/2; st=s; $("myip").textContent=s.ip;
    const w=s.wifi;
    $("wifi").innerHTML=!w?"Scriptul de semnal nu rulează pe acest calculator.":w.age>10?`<span class="ok">Scriptul nu mai trimite date (ultima dată acum ${w.age} s).</span>`:
      `<span class="${w.signal>=70?"good":w.signal>=50?"ok":"bad"}">${w.signal??"?"}%</span> · ${esc(w.ssid)} · canal ${w.channel??"?"} ${esc(w.band||"")}`;
    if(s.dup){ if(abort)abort.abort(); status("Pagina e deja deschisă în alt tab pe acest calculator.","Închide acest tab; testul rulează în celălalt."); return }
    if(s.running && s.run!==curRun && srvNow()<s.start_at+s.duration*1000){curRun=s.run;runTest(s)}
    if(!s.running && abort){abort.abort()}
    if(!abort && curRun===null) status("Conectat. Aștept pornirea testului de la profesor.","");
  }catch(e){ if(!abort) status("Nu mă pot conecta la server.","Verifică dacă ești în rețeaua laboratorului.") }
}
setInterval(poll,1000); poll();

function status(a,b){$("status").textContent=a;$("detail").textContent=b||""}
function prog(p){$("prog").style.width=Math.round(p*100)+"%"; R.progress=Math.round(p*100)}
let lastSend=0;
async function send(force){
  if(!force && Date.now()-lastSend<1000) return; lastSend=Date.now();
  try{await fetch("/api/result",{method:"POST",body:JSON.stringify({...R,tab,run:curRun})})}catch(e){}
}
function timeoutFetch(url,opt,ms,ctl){
  const c=new AbortController(); const t=setTimeout(()=>c.abort(),ms);
  const onAb=()=>c.abort(); ctl.signal.addEventListener("abort",onAb);
  return fetch(url,{...opt,signal:c.signal}).finally(()=>{clearTimeout(t);ctl.signal.removeEventListener("abort",onAb)});
}
const avg=a=>a.reduce((x,y)=>x+y,0)/a.length;
const pct=(a,p)=>{const s=[...a].sort((x,y)=>x-y);return s[Math.min(s.length-1,Math.floor(p*s.length))]};
const cls=(v,g,o)=>v<=g?"good":v<=o?"ok":"bad";
const show=(el,v,u,c)=>{$(el).innerHTML=`<span class="${c}">${v}</span> <span class="mut" style="font-size:14px">${u}</span>`};

async function runTest(s){
  abort=new AbortController(); const ctl=abort;
  R={phase:"pornește"}; ["r_ping","r_page","r_down","r_up"].forEach(k=>$(k).textContent="–"); $("r_ping2").textContent=$("r_page2").textContent="";
  try{
    while(srvNow()<s.start_at){status(`Testul pornește în ${Math.ceil((s.start_at-srvNow())/1000)} s…`,"");await sleep(200);if(ctl.signal.aborted)throw 0}
    // 1. latență
    R.phase="latență"; status("Măsor latența…","30 de cereri mici către server"); send(true);
    const pings=[]; let lost=0;
    for(let i=0;i<30&&!ctl.signal.aborted;i++){
      const t=performance.now();
      try{await (await timeoutFetch("/ping?r="+rnd(),{cache:"no-store"},2000,ctl)).text();pings.push(performance.now()-t)}catch(e){if(ctl.signal.aborted)throw 0;lost++}
      prog(i/30*0.1);
    }
    if(pings.length){
      R.ping_avg=+avg(pings).toFixed(1); R.ping_max=+Math.max(...pings).toFixed(1);
      R.ping_jitter=+(pings.length>1?avg(pings.slice(1).map((v,i)=>Math.abs(v-pings[i]))):0).toFixed(1);
    }
    R.loss=+(lost/30*100).toFixed(1);
    show("r_ping",R.ping_avg??"–","ms",cls(R.ping_avg??999,10,50)); $("r_ping2").textContent=`jitter ${R.ping_jitter??"–"} ms · max ${R.ping_max??"–"} ms · pierderi ${R.loss}%`;
    send(true);
    // 2. simulare acces Moodle
    let urls=null;
    if(s.mode==="moodle"){
      const m=await (await fetch("/api/moodle-assets?host="+location.hostname,{cache:"no-store"})).json();
      if(m.error) throw new Error(m.error);
      urls=m.urls;
      try{performance.setResourceTimingBufferSize(20000)}catch(e){}
    }
    R.phase="Moodle"; R.pages=0; R.errors=0; const times=[];
    const end=s.start_at+s.duration*1000;
    while(srvNow()<end && !ctl.signal.aborted){
      status(s.mode==="moodle"?"Încarc pagina reală Moodle, în buclă…":"Simulez încărcarea unor pagini Moodle…",`${R.pages} pagini încărcate`);
      try{ times.push(urls? await loadMoodle(urls,ctl) : await loadSim(ctl)); R.pages++ }
      catch(e){ if(ctl.signal.aborted)throw 0; R.errors++ }
      if(times.length){R.page_avg=Math.round(avg(times));R.page_p95=Math.round(pct(times,.95));R.page_max=Math.round(Math.max(...times))}
      prog(0.1+0.8*Math.min(1,(srvNow()-s.start_at)/(end-s.start_at)));
      if(times.length){show("r_page",(R.page_avg/1000).toFixed(2),"s",cls(R.page_avg,1000,3000));$("r_page2").textContent=`${R.pages} pagini · p95 ${(R.page_p95/1000).toFixed(2)} s · erori ${R.errors}`}
      send();
      if(s.pause>0) await sleep(s.pause*1000);
    }
    // 3. viteză
    R.phase="viteză"; status("Măsor viteza de download și upload…",""); send(true);
    try{
      let t=performance.now(); const b=await (await timeoutFetch("/dl?mb=10&r="+rnd(),{cache:"no-store"},30000,ctl)).arrayBuffer();
      R.down_mbps=+(b.byteLength*8/((performance.now()-t)/1000)/1e6).toFixed(1); show("r_down",R.down_mbps,"Mbit/s",R.down_mbps>=50?"good":R.down_mbps>=15?"ok":"bad");
      prog(.95);
      const up=new Uint8Array(4*1024*1024); for(let i=0;i<up.length;i+=4096)up[i]=Math.random()*256|0;
      t=performance.now(); await (await timeoutFetch("/sim/upload",{method:"POST",body:up},30000,ctl)).text();
      R.up_mbps=+(up.length*8/((performance.now()-t)/1000)/1e6).toFixed(1); show("r_up",R.up_mbps,"Mbit/s",R.up_mbps>=30?"good":R.up_mbps>=10?"ok":"bad");
    }catch(e){if(ctl.signal.aborted)throw 0}
    prog(1); R.phase="gata"; send(true);
    status("Test terminat. Mulțumim!","Rezultatele au fost trimise profesorului.");
  }catch(e){
    R.phase=ctl.signal.aborted?"oprit":"eroare"; send(true);
    status(ctl.signal.aborted?"Testul a fost oprit de profesor.":"Eroare: "+(e&&e.message||e),"");
  }finally{abort=null}
}

// o "pagină": HTML-ul întâi, apoi resursele câte 6 în paralel (ca un browser)
async function pool(items,fn,n=6){let i=0;await Promise.all(Array.from({length:n},async()=>{while(i<items.length){const k=i++;await fn(items[k])}}))}
async function loadSim(ctl){
  const t=performance.now();
  const get=async kb=>{const r=await timeoutFetch(`/sim/asset?kb=${kb}&r=${rnd()}`,{cache:"no-store"},15000,ctl);if(!r.ok)throw new Error(r.status);await r.arrayBuffer()};
  await get(SIM[0][0]); await pool(SIM[1],get);
  return performance.now()-t;
}
async function loadMoodle(urls,ctl){
  const t=performance.now();
  const get=async u=>{await timeoutFetch(u,{mode:"no-cors",cache:"no-store",credentials:"include"},15000,ctl)};
  await get(urls[0]); await pool(urls.slice(1),get);
  // fetch() în mod no-cors se termină la primirea antetelor; așteptăm și sfârșitul descărcării din Resource Timing
  let last=0; const until=performance.now()+15000;
  for(const u of urls){
    let e; while(!(e=performance.getEntriesByName(u).filter(x=>x.startTime>=t-1).pop()) && performance.now()<until) await sleep(20);
    if(!e) throw new Error("timeout"); last=Math.max(last,e.responseEnd);
  }
  performance.clearResourceTimings();
  return last-t;
}
</script></body></html>"""

ADMIN_PAGE = r"""<!doctype html><html lang="ro"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Panou profesor</title>
<style>""" + COMMON_CSS + r"""
header{display:flex;flex-wrap:wrap;gap:16px;align-items:flex-end;justify-content:space-between;margin-bottom:16px}
.url{font-size:26px;font-weight:700;color:var(--acc)}
.controls{display:flex;flex-wrap:wrap;gap:10px;align-items:end;margin-bottom:16px}
.controls label{display:flex;flex-direction:column;font-size:12px;color:var(--mut);gap:4px}
.controls input{width:100px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:16px}
.kpis .card h3{margin:0 0 4px;font-size:12px;color:var(--mut);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.kpis .v{font-size:24px;font-weight:700}
.tw{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:14px;white-space:nowrap}
th,td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:right}
th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}
th{color:var(--mut);font-weight:600;font-size:12px;position:sticky;top:0;background:var(--card)}
td .pill{padding:2px 8px;border-radius:10px;font-size:12px;font-weight:600}
.pill.good{background:var(--goodbg)}.pill.ok{background:var(--okbg)}.pill.bad{background:var(--badbg)}
tr.off td{opacity:.45}
.mini{height:6px;width:60px;background:var(--line);border-radius:3px;overflow:hidden;display:inline-block;vertical-align:middle}
.mini>i{display:block;height:100%;background:var(--acc)}
#verdict{margin-bottom:16px}
</style></head><body><main>
<header><div><h1>Panou profesor</h1><div class="mut">Elevii deschid în browser:</div><div class="url" id="url">…</div></div>
<div class="mut num" id="srv"></div></header>
<div class="card controls">
 <label>Ce testăm<select id="mode"><option value="sim">Simulare Moodle (server de test)</option><option value="moodle">Moodle real (pagina de login)</option></select></label>
 <label>Durată (secunde)<input id="dur" type="number" value="60" min="10" max="600"></label>
 <label>Pauză între pagini (s)<input id="pause" type="number" value="1" min="0" max="30" step="0.5"></label>
 <button id="start">Pornește pe toate</button><button class="sec" id="stop">Oprește</button>
 <button class="sec" id="reset">Șterge deconectații</button><button class="sec" id="csv">Export CSV</button>
</div>
<div class="kpis">
 <div class="card"><h3>Calculatoare online</h3><div class="v num" id="k_on">0</div></div>
 <div class="card"><h3>Latență medie</h3><div class="v num" id="k_ping">–</div></div>
 <div class="card"><h3>Pagină, medie</h3><div class="v num" id="k_page">–</div></div>
 <div class="card"><h3>Pagină, cel mai lent</h3><div class="v num" id="k_worst">–</div></div>
 <div class="card"><h3>Erori</h3><div class="v num" id="k_err">0</div></div>
 <div class="card"><h3>Semnal Wi-Fi mediu</h3><div class="v num" id="k_sig">–</div></div>
 <div class="card"><h3>Trafic server</h3><div class="v num" id="k_tr">–</div></div>
</div>
<div class="card" id="verdict"><span class="mut">Deschide pagina pe calculatoarele elevilor, apoi apasă „Pornește pe toate”.</span></div>
<div class="card tw"><table><thead><tr><th>Calculator (IP)</th><th>Stare</th><th title="acum / minim în timpul testului">Semnal</th><th>Canal</th><th>Access point</th><th>Legătură ↓/↑</th><th>Latență</th><th>Jitter</th><th>Pierderi</th>
<th>Pagini</th><th>Pagină medie</th><th>p95</th><th>Max</th><th>Erori</th><th>Download</th><th>Upload</th></tr></thead><tbody id="rows"></tbody></table></div>
</main>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const pill=(v,u,g,o,low)=>v==null?'<span class="mut">–</span>':`<span class="pill ${low?(v>=g?"good":v>=o?"ok":"bad"):(v<=g?"good":v<=o?"ok":"bad")}">${v}${u}</span>`;
const sec=ms=>ms==null?null:(ms/1000).toFixed(2);
let prev=null;
async function ctl(action){
  await fetch("/api/control",{method:"POST",body:JSON.stringify({action,mode:$("mode").value,duration:+$("dur").value,pause:+$("pause").value})});
  refresh();
}
$("start").onclick=()=>ctl("start"); $("stop").onclick=()=>ctl("stop"); $("reset").onclick=()=>ctl("reset");
$("csv").onclick=()=>location.href="/api/export.csv";
async function refresh(){
  let d; try{d=await (await fetch("/api/clients",{cache:"no-store"})).json()}catch(e){$("srv").textContent="Serverul nu răspunde";return}
  $("url").textContent=d.url;
  const s=d.state, active=d.clients.filter(c=>c.online && !["gata","oprit","eroare","așteaptă"].includes(c.phase));
  const running=s.running && d.now<s.start_at+s.duration*1000+90000 && (d.now<s.start_at+5000 || active.length>0);
  $("start").disabled=running; $("stop").disabled=!running;
  if(prev){const dt=(d.now-prev.now)/1000;$("k_tr").innerHTML=`${((d.stats.bytes-prev.stats.bytes)*8/dt/1e6).toFixed(0)}<span class="mut" style="font-size:14px"> Mbit/s</span>`;
    $("srv").textContent=`${((d.stats.requests-prev.stats.requests)/dt).toFixed(0)} cereri/s · test #${s.run} ${running?"în curs":"oprit"}`}
  prev=d;
  const on=d.clients.filter(c=>c.online), has=k=>d.clients.filter(c=>c[k]!=null);
  $("k_on").textContent=on.length;
  const av=(a,k)=>a.length?a.reduce((x,c)=>x+c[k],0)/a.length:null;
  const ping=av(has("ping_avg"),"ping_avg"), page=av(has("page_avg"),"page_avg");
  const worst=has("page_p95").sort((a,b)=>b.page_p95-a.page_p95)[0];
  $("k_ping").innerHTML=ping==null?"–":`${ping.toFixed(1)}<span class="mut" style="font-size:14px"> ms</span>`;
  $("k_page").innerHTML=page==null?"–":`${sec(page)}<span class="mut" style="font-size:14px"> s</span>`;
  $("k_worst").innerHTML=worst?`${sec(worst.page_p95)}<span class="mut" style="font-size:14px"> s · ${esc(worst.ip)}</span>`:"–";
  $("k_err").textContent=d.clients.reduce((x,c)=>x+(c.errors||0),0);
  const sigs=d.clients.filter(c=>c.wifi_online&&c.signal!=null), sig=av(sigs,"signal");
  $("k_sig").innerHTML=sig==null?"–":`${sig.toFixed(0)}<span class="mut" style="font-size:14px"> % · ${sigs.length} calc.</span>`;
  $("rows").innerHTML=d.clients.map(c=>`<tr class="${c.online||c.wifi_online?"":"off"}"><td class="num"><b>${esc(c.ip)}</b>${c.pc_name?`<div class="mut" style="font-size:12px">${esc(c.pc_name)}</div>`:""}</td>
   <td>${c.online?"":c.phase==="doar semnal"?"":"deconectat · "}${esc(c.phase)} ${c.progress!=null&&c.phase!=="gata"?`<span class="mini"><i style="width:${c.progress}%"></i></span>`:""}</td>
   <td class="num">${c.signal==null?'<span class="mut" title="scriptul de semnal nu rulează">–</span>':pill(c.signal,"%",70,50,1)+(c.sig_min!=null?` <span class="mut">min ${c.sig_min}%</span>`:"")+(c.wifi_online?"":' <span class="mut" title="date vechi">⏸</span>')}</td>
   <td class="num">${c.channel??"–"} <span class="mut">${esc(c.band||"")}</span></td>
   <td class="num mut" title="${esc(c.bssid)}">${c.bssid?esc(c.bssid.slice(-8)):"–"}</td>
   <td class="num">${c.rx_rate!=null?`${Math.round(c.rx_rate)}/${Math.round(c.tx_rate??0)}`:"–"}</td>
   <td class="num">${pill(c.ping_avg," ms",10,50)}</td><td class="num">${pill(c.ping_jitter," ms",5,20)}</td><td class="num">${pill(c.loss,"%",0,2)}</td>
   <td class="num">${c.pages??"–"}</td><td class="num">${pill(sec(c.page_avg)," s",1,3)}</td><td class="num">${pill(sec(c.page_p95)," s",1.5,4)}</td>
   <td class="num">${c.page_max!=null?sec(c.page_max)+" s":"–"}</td><td class="num">${pill(c.errors,"",0,2)}</td>
   <td class="num">${pill(c.down_mbps," Mbit/s",50,15,1)}</td><td class="num">${pill(c.up_mbps," Mbit/s",30,10,1)}</td></tr>`).join("")
   || '<tr><td colspan="16" class="mut" style="text-align:left">Niciun calculator conectat încă.</td></tr>';
  verdict(d.clients, ping, page);
}
function verdict(cs,ping,page){
  if(!cs.some(c=>c.page_avg!=null)) return;
  const p=[], bad=cs.filter(c=>c.page_avg>3000).map(c=>c.ip), lossy=cs.filter(c=>c.loss>0).map(c=>c.ip);
  if(ping>50)p.push(`latența medie e mare (${ping.toFixed(0)} ms) — rețeaua Wi-Fi e aglomerată sau serverul e pe Wi-Fi cu economisire de energie`);
  if(lossy.length)p.push(`se pierd cereri la: ${lossy.map(esc).join(", ")}`);
  if(bad.length)p.push(`pagini lente (peste 3 s) la: ${bad.map(esc).join(", ")}`);
  const errs=cs.filter(c=>c.errors>0).map(c=>`${c.ip} (${c.errors})`);
  if(errs.length)p.push(`cereri eșuate sau expirate la: ${errs.map(esc).join(", ")}`);
  const weak=cs.filter(c=>(c.sig_min??c.signal)!=null && (c.sig_min??c.signal)<50).map(c=>c.ip);
  if(weak.length)p.push(`semnal Wi-Fi slab (sub 50%) la: ${weak.map(esc).join(", ")}`);
  const nosig=cs.filter(c=>c.online&&c.signal==null).length;
  if(nosig)p.push(`${nosig} calculatoare nu rulează scriptul de semnal`);
  $("verdict").innerHTML=p.length?`<b class="ok">Probleme găsite:</b> ${p.join("; ")}.`:
   `<b class="good">Rețeaua face față.</b> Pagina medie se încarcă în ${sec(page)} s, latența medie e ${ping?.toFixed(1)} ms.`;
}
refresh(); setInterval(refresh,1000);
</script></body></html>"""


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True
    ip = lan_ip()
    print(f"Server de test pornit.\n  Elevii deschid:   http://{ip}:{PORT}\n  Panou profesor:   http://127.0.0.1:{PORT}/admin\n(Ctrl+C pentru oprire)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)
