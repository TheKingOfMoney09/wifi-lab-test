#!/usr/bin/env python3
"""Tester Wi-Fi — aplicație locală pentru testarea conexiunii wireless (Linux).

Pornire:  python3 wifi_tester.py        apoi deschide http://127.0.0.1:8765
Necesită doar Python 3 (stdlib) și utilitarele iw, nmcli, ping.
"""
import glob
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get("PORT", "8765"))
UA = {"User-Agent": "wifi-tester/1.0"}
DOWN_URL = "https://speed.cloudflare.com/__down?bytes={n}"
UP_URL = "https://speed.cloudflare.com/__up"


def run(cmd, timeout=20):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def wifi_iface():
    for path in glob.glob("/sys/class/net/*/wireless"):
        return path.split("/")[4]
    return None


def gateway(iface):
    for line in run(["ip", "route"]).splitlines():
        p = line.split()
        if p[:1] == ["default"] and "via" in p and (iface is None or iface in p):
            return p[p.index("via") + 1]
    return None


def quality(dbm):
    if dbm is None:
        return "necunoscut"
    if dbm >= -50:
        return "excelent"
    if dbm >= -60:
        return "foarte bun"
    if dbm >= -67:
        return "bun"
    if dbm >= -75:
        return "slab"
    return "foarte slab"


def info():
    iface = wifi_iface()
    if not iface:
        return {"error": "Nu am găsit nicio placă wireless."}
    link = run(["iw", "dev", iface, "link"])
    if "Not connected" in link or not link.strip():
        return {"iface": iface, "connected": False}

    def grab(pat, cast=str):
        m = re.search(pat, link)
        return cast(m.group(1)) if m else None

    signal = grab(r"signal:\s*(-?\d+)", int)
    freq = grab(r"freq:\s*([\d.]+)", float)
    devinfo = run(["iw", "dev", iface, "info"])
    ch = re.search(r"channel (\d+).*?width: (\d+) MHz", devinfo)
    ip = re.search(r"inet (\S+)", run(["ip", "-4", "addr", "show", iface]))
    rx = grab(r"rx bitrate:\s*([\d.]+ MBit/s)")
    tx = grab(r"tx bitrate:\s*([\d.]+ MBit/s)")
    std = "Wi-Fi 6" if "HE-" in link else "Wi-Fi 5" if "VHT-" in link else "Wi-Fi 4" if "MCS" in link else ""
    return {
        "iface": iface,
        "connected": True,
        "ssid": grab(r"SSID:\s*(.+)"),
        "bssid": grab(r"Connected to (\S+)"),
        "signal": signal,
        "quality": quality(signal),
        "freq": freq,
        "band": ("6 GHz" if freq and freq > 5925 else "5 GHz" if freq and freq > 4900 else "2.4 GHz") if freq else None,
        "channel": int(ch.group(1)) if ch else None,
        "width": int(ch.group(2)) if ch else None,
        "rx": rx,
        "tx": tx,
        "standard": std,
        "ip": ip.group(1) if ip else None,
        "gateway": gateway(iface),
    }


def ping(host, count=20):
    if not re.fullmatch(r"[\w.:-]+", host or ""):
        return {"error": "Adresă invalidă"}
    out = run(["ping", "-c", str(count), "-i", "0.2", "-W", "2", host], timeout=count + 10)
    loss = re.search(r"([\d.]+)% packet loss", out)
    rtt = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms", out)
    if not loss:
        return {"host": host, "error": "Ping eșuat"}
    res = {"host": host, "loss": float(loss.group(1))}
    if rtt:
        res.update(min=float(rtt.group(1)), avg=float(rtt.group(2)),
                   max=float(rtt.group(3)), jitter=float(rtt.group(4)))
    return res


def dns():
    results = []
    for name in ("google.com", "cloudflare.com", "wikipedia.org"):
        t = time.perf_counter()
        try:
            socket.getaddrinfo(name, 443)
            results.append({"name": name, "ms": round((time.perf_counter() - t) * 1000, 1)})
        except OSError:
            results.append({"name": name, "ms": None})
    ok = [r["ms"] for r in results if r["ms"] is not None]
    return {"results": results, "ok": len(ok) == len(results), "avg": round(sum(ok) / len(ok), 1) if ok else None}


def download(max_seconds=8.0):
    req = urllib.request.Request(DOWN_URL.format(n=200_000_000), headers=UA)
    total, start = 0, time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            while time.perf_counter() - start < max_seconds:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                total += len(chunk)
    except OSError as e:
        return {"error": str(e)}
    secs = time.perf_counter() - start
    return {"mbps": round(total * 8 / secs / 1e6, 1), "mb": round(total / 1e6, 1), "seconds": round(secs, 1)}


def upload(size=25_000_000):
    data = os.urandom(size)
    req = urllib.request.Request(UP_URL, data=data, method="POST",
                                 headers={**UA, "Content-Type": "application/octet-stream"})
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
    except OSError as e:
        return {"error": str(e)}
    secs = time.perf_counter() - start
    return {"mbps": round(size * 8 / secs / 1e6, 1), "mb": round(size / 1e6, 1), "seconds": round(secs, 1)}


def scan():
    out = run(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,CHAN,FREQ,SECURITY", "dev", "wifi", "list"])
    nets = []
    for line in out.splitlines():
        parts = re.split(r"(?<!\\):", line)
        if len(parts) < 6:
            continue
        nets.append({
            "inuse": parts[0] == "*",
            "ssid": parts[1].replace("\\:", ":") or "(ascunsă)",
            "signal": int(parts[2] or 0),
            "channel": parts[3],
            "freq": parts[4],
            "security": parts[5] or "deschisă",
        })
    return {"networks": sorted(nets, key=lambda n: -n["signal"])}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        routes = {
            "/api/info": info,
            "/api/ping": lambda: ping(q.get("host", [""])[0]),
            "/api/dns": dns,
            "/api/download": download,
            "/api/upload": upload,
            "/api/scan": scan,
        }
        if u.path == "/":
            self.send(PAGE, "text/html")
        elif u.path in routes:
            self.send(json.dumps(routes[u.path]()))
        else:
            self.send_error(404)


PAGE = r"""<!doctype html>
<html lang="ro"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tester Wi-Fi</title>
<style>
:root{--bg:#f5f6f8;--card:#fff;--fg:#1b1e24;--mut:#6b7280;--line:#e5e7eb;--acc:#2563eb;
--good:#16a34a;--ok:#ca8a04;--bad:#dc2626}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#181b21;--fg:#e8eaee;--mut:#9aa1ad;
--line:#2a2e36;--acc:#60a5fa;--good:#4ade80;--ok:#facc15;--bad:#f87171}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}
main{max-width:980px;margin:0 auto;padding:24px 16px 48px}
header{display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between;margin-bottom:20px}
h1{font-size:22px;margin:0}
button{background:var(--acc);color:#fff;border:0;border-radius:8px;padding:10px 18px;font:inherit;font-weight:600;cursor:pointer}
button.sec{background:transparent;color:var(--acc);border:1px solid var(--line)}
button:disabled{opacity:.5;cursor:default}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
.card h2{font-size:13px;font-weight:600;color:var(--mut);margin:0 0 8px;text-transform:uppercase;letter-spacing:.04em}
.big{font-size:28px;font-weight:700;font-variant-numeric:tabular-nums}
.unit{font-size:14px;color:var(--mut);font-weight:500;margin-left:4px}
.sub{color:var(--mut);font-size:13px;margin-top:4px}
.good{color:var(--good)}.ok{color:var(--ok)}.bad{color:var(--bad)}
.wide{grid-column:1/-1}
canvas{width:100%;height:120px;display:block}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line)}
th{color:var(--mut);font-weight:600}
.bar{height:6px;border-radius:3px;background:var(--line);overflow:hidden;min-width:60px}
.bar>i{display:block;height:100%;background:var(--acc)}
.spin::after{content:"";display:inline-block;width:14px;height:14px;margin-left:8px;border:2px solid var(--mut);
border-top-color:transparent;border-radius:50%;animation:s 1s linear infinite;vertical-align:-2px}
@keyframes s{to{transform:rotate(360deg)}}
#verdict{font-size:16px}
.tablewrap{overflow-x:auto}
</style></head><body><main>
<header><h1>Tester Wi-Fi</h1>
<div><button id="run">Pornește testul</button> <button class="sec" id="scanBtn">Scanează rețele</button></div></header>

<div class="grid">
 <div class="card wide"><h2>Conexiune</h2><div id="conn" class="sub">Se încarcă…</div></div>
 <div class="card"><h2>Semnal</h2><div class="big" id="sig">–</div><div class="sub" id="sigq"></div></div>
 <div class="card"><h2>Ping router</h2><div class="big" id="pgw">–</div><div class="sub" id="pgws"></div></div>
 <div class="card"><h2>Ping internet</h2><div class="big" id="pnet">–</div><div class="sub" id="pnets"></div></div>
 <div class="card"><h2>DNS</h2><div class="big" id="dns">–</div><div class="sub" id="dnss"></div></div>
 <div class="card"><h2>Download</h2><div class="big" id="down">–</div><div class="sub" id="downs"></div></div>
 <div class="card"><h2>Upload</h2><div class="big" id="up">–</div><div class="sub" id="ups"></div></div>
 <div class="card wide"><h2>Concluzie</h2><div id="verdict" class="sub">Apasă „Pornește testul”.</div></div>
 <div class="card wide"><h2>Semnal în timp real (dBm)</h2><canvas id="chart"></canvas></div>
 <div class="card wide" id="scanCard" hidden><h2>Rețele din jur</h2><div class="tablewrap"><table id="scan"></table></div></div>
</div></main>
<script>
const $=id=>document.getElementById(id);
const api=p=>fetch(p).then(r=>r.json());
const cls=(v,g,o)=>v<=g?"good":v<=o?"ok":"bad";
const big=(el,val,unit,c)=>{$(el).innerHTML=`<span class="${c||""}">${val}</span><span class="unit">${unit}</span>`};
const busy=el=>{$(el).innerHTML='<span class="spin sub">în curs</span>';};
let gw=null, results={};

async function loadInfo(){
  const i=await api("/api/info");
  if(i.error){$("conn").textContent=i.error;return i}
  if(!i.connected){$("conn").textContent=`Placa ${i.iface} nu este conectată.`;return i}
  gw=i.gateway;
  $("conn").innerHTML=`<b>${esc(i.ssid)}</b> · ${i.band} · canal ${i.channel??"?"} (${i.width??"?"} MHz) · ${i.standard}
   · viteză legătură ${i.rx??"?"} ↓ / ${i.tx??"?"} ↑<br>Interfață ${i.iface} · IP ${i.ip??"?"} · router ${i.gateway??"?"} · BSSID ${i.bssid}`;
  const c=i.signal>=-60?"good":i.signal>=-70?"ok":"bad";
  big("sig",i.signal,"dBm",c); $("sigq").textContent="Calitate: "+i.quality;
  return i;
}
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

function showPing(el,r){
  if(r.error||r.avg==null){$(el).innerHTML='<span class="bad">eșuat</span>';return}
  big(el,r.avg.toFixed(1),"ms",cls(r.avg,el=="pgw"?5:30,el=="pgw"?20:80));
  $(el+"s").textContent=`pierderi ${r.loss}% · jitter ${r.jitter.toFixed(1)} ms · max ${r.max.toFixed(0)} ms`;
}

async function runAll(){
  $("run").disabled=true; results={};
  try{
    const i=await loadInfo(); results.info=i;
    busy("pgw"); results.pgw=gw?await api("/api/ping?host="+gw):{error:1}; showPing("pgw",results.pgw);
    busy("pnet"); results.pnet=await api("/api/ping?host=1.1.1.1"); showPing("pnet",results.pnet);
    busy("dns"); const d=results.dns=await api("/api/dns");
    if(d.ok){big("dns",d.avg,"ms",cls(d.avg,50,150));$("dnss").textContent=d.results.map(x=>`${x.name} ${x.ms} ms`).join(" · ")}
    else $("dns").innerHTML='<span class="bad">eșuat</span>';
    busy("down"); const dn=results.down=await api("/api/download");
    if(dn.error)$("down").innerHTML='<span class="bad">eșuat</span>';
    else{big("down",dn.mbps,"Mbit/s",dn.mbps>=100?"good":dn.mbps>=25?"ok":"bad");$("downs").textContent=`${dn.mb} MB în ${dn.seconds} s`}
    busy("up"); const up=results.up=await api("/api/upload");
    if(up.error)$("up").innerHTML='<span class="bad">eșuat</span>';
    else{big("up",up.mbps,"Mbit/s",up.mbps>=50?"good":up.mbps>=10?"ok":"bad");$("ups").textContent=`${up.mb} MB în ${up.seconds} s`}
    verdict();
  }finally{$("run").disabled=false}
}

function verdict(){
  const r=results, p=[];
  if(!r.info?.connected){$("verdict").textContent="Nu ești conectat la o rețea Wi-Fi.";return}
  if(r.info.signal<-70)p.push("semnalul e slab — apropie-te de router sau mută routerul");
  if(r.info.band==="2.4 GHz")p.push("ești pe 2.4 GHz; dacă routerul are 5 GHz, folosește-l pentru viteză mai mare");
  if(r.pgw?.loss>0)p.push(`se pierd pachete până la router (${r.pgw.loss}%) — interferențe sau semnal instabil`);
  if(r.pgw?.avg>20)p.push("latența până la router e mare pentru o rețea locală");
  if(r.pnet?.error||r.pnet?.loss==100)p.push("nu ajungi la internet — problema e la router sau la furnizor");
  if(!r.dns?.ok)p.push("DNS-ul nu funcționează");
  if(r.down?.mbps<25)p.push("viteza de download e mică");
  $("verdict").innerHTML=p.length?"<b class='ok'>Atenție:</b> "+p.join("; ")+".":"<b class='good'>Conexiunea arată foarte bine.</b> Semnal bun, fără pierderi de pachete, internet rapid.";
}

async function doScan(){
  $("scanBtn").disabled=true; $("scanCard").hidden=false; $("scan").innerHTML='<tr><td class="spin sub">Se scanează</td></tr>';
  const s=await api("/api/scan");
  $("scan").innerHTML="<tr><th>Rețea</th><th>Semnal</th><th></th><th>Canal</th><th>Frecvență</th><th>Securitate</th></tr>"+
   s.networks.map(n=>`<tr><td>${n.inuse?"● ":""}${esc(n.ssid)}</td><td>${n.signal}%</td><td><div class="bar"><i style="width:${n.signal}%"></i></div></td>
   <td>${esc(n.channel)}</td><td>${esc(n.freq)}</td><td>${esc(n.security)}</td></tr>`).join("");
  $("scanBtn").disabled=false;
}

// grafic semnal live
const pts=[];
function draw(){
  const cv=$("chart"),dpr=devicePixelRatio||1,w=cv.clientWidth,h=cv.clientHeight;
  cv.width=w*dpr;cv.height=h*dpr;const g=cv.getContext("2d");g.scale(dpr,dpr);
  const css=getComputedStyle(document.documentElement),lo=-90,hi=-30,y=v=>h-(v-lo)/(hi-lo)*h;
  g.strokeStyle=css.getPropertyValue("--line");g.fillStyle=css.getPropertyValue("--mut");g.font="11px system-ui";
  for(const v of [-80,-70,-60,-50,-40]){g.beginPath();g.moveTo(30,y(v));g.lineTo(w,y(v));g.stroke();g.fillText(v,0,y(v)+4)}
  if(pts.length<2)return;
  g.strokeStyle=css.getPropertyValue("--acc");g.lineWidth=2;g.beginPath();
  pts.forEach((v,k)=>{const x=30+k*(w-30)/59;k?g.lineTo(x,y(v)):g.moveTo(x,y(v))});g.stroke();
}
async function tick(){
  try{const i=await api("/api/info");if(i.signal!=null){pts.push(i.signal);if(pts.length>60)pts.shift();
    if(!$("run").disabled){const c=i.signal>=-60?"good":i.signal>=-70?"ok":"bad";big("sig",i.signal,"dBm",c);$("sigq").textContent="Calitate: "+i.quality}}}catch(e){}
  draw();
}
$("run").onclick=runAll; $("scanBtn").onclick=doScan; addEventListener("resize",draw);
loadInfo(); tick(); setInterval(tick,2000);
</script></body></html>"""


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Tester Wi-Fi pornit: http://127.0.0.1:{PORT}  (Ctrl+C pentru oprire)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)
