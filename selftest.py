#!/usr/bin/env python3
"""Autotest pentru serverul de test: verifica tot lantul fara 30 de laptopuri.

Porneste un Moodle fals (intarzieri configurabile) si server.py pe porturi libere,
apoi simuleaza N elevi care fac exact ce face browserul: ping, descarcare 2 MB,
incarcare Moodle, trimitere rezultate si raport de semnal WiFi. La final verifica
agregarea din /api/results, exportul CSV si scripturile descarcabile.

Rulare:
    python selftest.py                # 30 elevi, porturi 18090 / 18083
    python selftest.py --clients 8 --verbose

Cod de ieșire 0 = totul a trecut. Orice altceva = vezi mesajele FAIL.
"""
import argparse
import csv
import io
import json
import os
import random
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MOODLE_PAGE = b"""<!doctype html><html lang="ro"><head><meta charset="utf-8">
<title>Moodle fals</title><link rel="stylesheet" href="/theme/styles.css"></head>
<body><h1>Moodle (server fals pentru test)</h1><script src="/lib/javascript.js"></script>
<img src="/pix/logo.png" alt=""></body></html>"""

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" - {detail}" if detail else ""))
    return ok


# --------------------------------------------------------------------------- #
# Moodle fals
# --------------------------------------------------------------------------- #
class MoodleHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    delay = 0.05          # "cat de incarcat" e Moodle-ul; se poate schimba din mers
    requests = 0

    def log_message(self, *a):
        pass

    def do_GET(self):
        type(self).requests += 1
        if type(self).delay:
            time.sleep(type(self).delay)
        path = self.path.split("?")[0]
        if path in ("/theme/styles.css", "/lib/javascript.js", "/pix/logo.png"):
            body = b"/* asset */\n" * 200
            ctype = "text/css" if path.endswith(".css") else "application/octet-stream"
        else:
            body = MOODLE_PAGE
            ctype = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


class FakeMoodle(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# --------------------------------------------------------------------------- #
# utilitare HTTP
# --------------------------------------------------------------------------- #
def http_json(url, payload=None, timeout=15, headers=None):
    data = None
    hdrs = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def http_get(url, timeout=15, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), dict(r.headers)


def wait_for(url, seconds=15):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            return http_json(url, timeout=2)
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            time.sleep(0.2)
    raise RuntimeError(f"nu a raspuns: {url}")


def free_port(start):
    for port in range(start, start + 200):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("nu am gasit un port liber")


# --------------------------------------------------------------------------- #
# un elev simulat
# --------------------------------------------------------------------------- #
def simulate_student(index, base, moodle_url, session_id):
    label = f"PC-{index:02d}"
    out = {"client_id": label, "ok": False}

    # 1. polling ca browserul
    try:
        http_json(base + "/api/session", timeout=5)
    except Exception as e:  # noqa: BLE001
        out["error"] = f"session: {e}"
        return out

    # 2. latenta: o cerere de incalzire + 15 masurate (ca in client.js)
    times = []
    try:
        http_get(base + "/api/ping?warmup=1", timeout=5)
        for _ in range(15):
            t0 = time.perf_counter()
            http_get(base + "/api/ping?r=" + os.urandom(3).hex(), timeout=5)
            times.append((time.perf_counter() - t0) * 1000)
    except Exception as e:  # noqa: BLE001
        out["error"] = f"ping: {e}"
        return out
    times_sorted = sorted(times)
    latency = {
        "avg": round(sum(times) / len(times), 1),
        "min": round(min(times), 1),
        "max": round(max(times), 1),
        "p95": round(times_sorted[min(len(times_sorted) - 1, int(0.95 * len(times_sorted)))], 1),
        "jitter": round((sum((b - a) ** 2 for a, b in zip(times, times[1:])) / max(1, len(times) - 1)) ** 0.5, 1),
        "samples": len(times),
        "loss_pct": 0.0,
    }

    # 3. viteza: 2 MB, cititi integral
    try:
        t0 = time.perf_counter()
        body, _ = http_get(base + "/api/payload?size=" + str(2 * 1024 * 1024), timeout=30)
        secs = time.perf_counter() - t0
        throughput = round(len(body) * 8 / 1e6 / secs, 2)
    except Exception as e:  # noqa: BLE001
        out["error"] = f"payload: {e}"
        return out

    # 4. Moodle, ca un fetch no-cors din pagina elevului
    moodle = {"ms": None, "timeout": False, "error": None}
    try:
        t0 = time.perf_counter()
        http_get(moodle_url + ("&" if "?" in moodle_url else "?") + "_=" + str(int(time.time() * 1000)), timeout=15)
        moodle["ms"] = round((time.perf_counter() - t0) * 1000)
    except Exception as e:  # noqa: BLE001
        moodle["timeout"] = True
        moodle["error"] = type(e).__name__

    # 5. trimitere rezultate
    try:
        http_json(base + "/api/results", {
            "client_id": label,
            "session_id": session_id,
            "latency": latency,
            "throughput_mbps": throughput,
            "moodle_load_ms": moodle["ms"],
            "moodle_timeout": moodle["timeout"],
            "moodle_error": moodle["error"],
            "connection": {"effectiveType": "4g", "downlink": 10, "rtt": 50},
            "page_visible": True,
        })
    except Exception as e:  # noqa: BLE001
        out["error"] = f"results: {e}"
        return out

    # 6. raport de semnal WiFi (ce trimite scriptul PowerShell)
    try:
        http_json(base + "/api/wifi-signal", {
            "client_id": label,
            "ssid": "LAB-WIFI",
            "bssid": "84:d8:1b:dd:f6:%02x" % index,
            "state": "connected",
            "band": "5 GHz" if index % 2 else "2.4 GHz",
            "channel": 36 if index % 2 else 6,
            "radio_type": "802.11ac",
            "authentication": "WPA2-Personal",
            "signal_percent": random.randint(35, 95),
            "rssi_dbm": -random.randint(40, 80),
            "rx_rate_mbps": 866.7,
            "tx_rate_mbps": 780.0,
        })
    except Exception as e:  # noqa: BLE001
        out["error"] = f"wifi: {e}"
        return out

    out.update(ok=True, latency=latency, throughput=throughput, moodle=moodle)
    return out


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clients", type=int, default=30)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    moodle_port = free_port(18083)
    server_port = free_port(18090)
    data_dir = os.path.join(BASE_DIR, "data", "selftest")
    os.makedirs(data_dir, exist_ok=True)
    for name in os.listdir(data_dir):
        if name.startswith("session_") or name == "wifi_signal.json":
            os.remove(os.path.join(data_dir, name))

    moodle = FakeMoodle(("127.0.0.1", moodle_port), MoodleHandler)
    threading.Thread(target=moodle.serve_forever, daemon=True).start()
    moodle_url = f"http://127.0.0.1:{moodle_port}"
    print(f"Moodle fals pe {moodle_url}, {args.clients} elevi simulati\n")

    env = dict(os.environ)
    env.update({
        "LABTEST_PORT": str(server_port),
        "LABTEST_HOST": "0.0.0.0",
        "LABTEST_MOODLE_URL": moodle_url,
        "LABTEST_EXPECTED_CLIENTS": str(args.clients),
        "LABTEST_DATA_DIR": data_dir,
        "PYTHONIOENCODING": "utf-8",
    })
    log_path = os.path.join(data_dir, "server.log")
    log_file = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "server.py"], cwd=BASE_DIR, env=env,
                            stdout=log_file, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{server_port}"

    try:
        try:
            health = wait_for(base + "/api/health", 20)
        except RuntimeError as e:
            print(f"Serverul nu a pornit: {e}\n--- {log_path} ---")
            print(open(log_path, encoding="utf-8").read())
            return 1

        check("serverul raspunde la /api/health", health.get("ok") is True)
        check("health vede Moodle ca disponibil", health["moodle"].get("ok") is True,
              f"{health['moodle'].get('ms')} ms")
        check("health avertizeaza cand moodle_url e localhost",
              any("localhost" in w for w in health["warnings"]),
              "corect: in acest test Moodle e pe 127.0.0.1")

        # scriptul descarcat trebuie sa contina adresa reala, nu placeholder
        bat, _ = http_get(base + "/download/test-wifi.bat",
                          headers={"Host": f"127.0.0.1:{server_port}"})
        bat_text = bat.decode("ascii", "replace")
        check("scriptul .bat se descarca", len(bat) > 2000, f"{len(bat)} bytes")
        check("scriptul .bat contine adresa serverului", "/api/wifi-signal" in bat_text)
        check("scriptul .bat nu mai are placeholder", "__SERVER_URL__" not in bat_text)
        check("scriptul .bat incepe cu preludiul de batch", bat_text.startswith("<# :\r\n@echo off"))
        try:
            bat.decode("ascii")
            ascii_ok = True
        except UnicodeDecodeError:
            ascii_ok = False
        check("scriptul .bat e ASCII curat (PowerShell 5.1 il citeste corect)", ascii_ok)

        zip_bytes, _ = http_get(base + "/download/test-wifi.zip")
        import zipfile
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = set(zf.namelist())
            ps1 = zf.read("wifi_signal.ps1").decode("ascii")
        check("arhiva contine fisierele asteptate",
              {"wifi_signal.ps1", "run_wifi_test.bat", "CITESTE.txt"} <= names, str(sorted(names)))
        check("wifi_signal.ps1 din arhiva are adresa serverului", "/api/wifi-signal" in ps1)

        # fara sesiune activa, rezultatele trebuie refuzate curat
        try:
            http_json(base + "/api/results", {"client_id": "X"})
            check("POST /api/results fara sesiune e refuzat", False, "a acceptat")
        except urllib.error.HTTPError as e:
            check("POST /api/results fara sesiune e refuzat", e.code == 400, f"HTTP {e.code}")

        session = http_json(base + "/api/session/start", {})
        sid = session["session_id"]
        check("pornirea sesiunii returneaza un id", bool(sid), sid)

        t0 = time.time()
        with ThreadPoolExecutor(max_workers=args.clients) as pool:
            outcomes = list(pool.map(
                lambda i: simulate_student(i + 1, base, moodle_url, sid), range(args.clients)))
        elapsed = time.time() - t0
        failed = [o for o in outcomes if not o["ok"]]
        check(f"{args.clients} elevi simulati simultan, fara erori", not failed,
              f"{len(failed)} esecuri; {elapsed:.2f}s total"
              + (f"; primul: {failed[0]['error']}" if failed else ""))
        check("fiecare elev a primit confirmare de la /api/results",
              all(o["ok"] for o in outcomes))

        data = http_json(base + f"/api/results?session_id={sid}")
        got = data.get("results", {})
        # Rezultatele se grupeaza dupa IP: toti cei 30 de clienti simulati vin
        # din 127.0.0.1, deci in tabel ramane o singura linie (ultimul care a
        # scris). In laborator fiecare laptop are IP-ul lui.
        check("agregarea nu pierde sau amesteca inregistrarile", len(got) == 1,
              f"{len(got)} linie pentru 30 de clienti de pe acelasi IP")
        check("raportul de semnal e agregat la fel", len(data.get("wifi", {})) == 1,
              f"{len(data.get('wifi', {}))} rapoarte")

        # Un al doilea client, de pe alt IP (LAN), trebuie sa apara ca linie noua.
        lan = f"http://{health['server']['lan_ip']}:{server_port}"
        if health["server"]["lan_ip"] not in ("127.0.0.1", "0.0.0.0"):
            try:
                second = simulate_student(77, lan, moodle_url, sid)
                data2 = http_json(base + f"/api/results?session_id={sid}")
                check("clientii de pe IP-uri diferite apar separat",
                      second["ok"] and len(data2["results"]) == 2,
                      f"{len(data2['results'])} linii")
                data = data2
            except Exception as e:  # noqa: BLE001
                check("clientii de pe IP-uri diferite apar separat", False, str(e)[:80])

        sample = next(iter(got.values()), {})
        check("latenta e numar, nu string", isinstance(sample.get("latency", {}).get("avg"), (int, float)))
        wifi_sample = next(iter(data.get("wifi", {}).values()), {})
        check("semnalul WiFi e numar, nu string",
              isinstance(wifi_sample.get("signal_percent"), int), str(wifi_sample.get("signal_percent")))
        check("p95 si numarul de esantioane sunt salvate",
              sample.get("latency", {}).get("p95") is not None
              and sample.get("latency", {}).get("samples") == 15)

        csv_bytes, headers = http_get(base + f"/api/export.csv?session_id={sid}")
        rows = list(csv.reader(io.StringIO(csv_bytes.decode("utf-8-sig"))))
        check("CSV-ul are cate un rand per IP", len(rows) == len(data["results"]) + 1,
              f"{len(rows)} linii")
        check("CSV-ul are coloanele noi (p95, rssi, banda)",
              {"latency_p95_ms", "wifi_rssi_dbm", "wifi_band"} <= set(rows[0]))

        sessions = http_json(base + "/api/sessions")
        check("sesiunea apare in lista de sesiuni salvate",
              any(s["session_id"] == sid for s in sessions["sessions"]))

        # Moodle cade: health trebuie sa spuna asta, fara sa blocheze serverul
        MoodleHandler.delay = 0
        moodle.shutdown()
        moodle.server_close()
        time.sleep(5.5)  # cache-ul de health e 5 s
        health = http_json(base + "/api/health")
        check("health semnaleaza Moodle cazut", health["moodle"].get("ok") is False,
              str(health["moodle"].get("error"))[:80])

        outcomes = list(simulate_student(99, base, moodle_url, sid) for _ in range(1))
        check("elevul raporteaza eroare Moodle in loc sa blocheze",
              outcomes[0]["ok"] and outcomes[0]["moodle"]["timeout"] is True)

        check("serverul e inca viu dupa toate astea",
              http_json(base + "/api/session").get("session_id") == sid)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_file.close()

    log_text = open(log_path, encoding="utf-8").read()
    tracebacks = log_text.count("Traceback (most recent call last)")
    check("logul serverului nu are traceback-uri", tracebacks == 0, f"{tracebacks} gasite")
    if tracebacks:
        print(log_text[-2000:])

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} verificari trecute")
    if passed != len(RESULTS):
        print(f"Log server: {log_path}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
