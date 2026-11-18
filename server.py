#!/usr/bin/env python3
"""
Server HTTP pentru testarea retelei wireless din laboratorul de informatica.

Ruleaza pe laptopul profesorului (acest calculator) si este accesat de cei
~30 de clienti Windows printr-un link in browser, in reteaua locala.

Porneste cu:
    python3 server.py
"""
import csv
import io
import json
import os
import re
import socket
import threading
import time
import urllib.request
import uuid
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
SERVER_VERSION = "1.1"

DEFAULT_CONFIG = {
    "host": "0.0.0.0",
    "port": 8090,
    "moodle_url": "http://192.168.6.113:8083",
    "expected_clients": 30,
    "payload_max_mb": 10,
}

# Variabile de mediu pentru cazurile in care nu vrem sa atingem config.json
# (testare automata, doua instante pe acelasi laptop, alt port pentru o proba).
ENV_OVERRIDES = {
    "LABTEST_HOST": ("host", str),
    "LABTEST_PORT": ("port", int),
    "LABTEST_MOODLE_URL": ("moodle_url", str),
    "LABTEST_EXPECTED_CLIENTS": ("expected_clients", int),
    "LABTEST_PAYLOAD_MB": ("payload_max_mb", int),
    "LABTEST_DATA_DIR": ("data_dir", str),
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
            cfg.update(json.load(f))
    for env_name, (key, cast) in ENV_OVERRIDES.items():
        if env_name in os.environ:
            try:
                cfg[key] = cast(os.environ[env_name])
            except ValueError:
                print(f"Avertisment: {env_name}={os.environ[env_name]!r} nu e valid, il ignor")
    return cfg


CONFIG = load_config()
DATA_DIR = CONFIG.get("data_dir") or os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)


def lan_ip():
    """IP-ul din reteaua locala, folosit pentru adresele afisate elevilor."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("1.1.1.1", 80))  # nu trimite pachete, doar alege interfata
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


LAN_IP = lan_ip()


def _as_number(value, cast=float):
    """Curata valorile venite din browser/script: string gol sau gunoi -> None."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


# Buffer precalculat pentru testul de viteza (evitam sa generam date la fiecare cerere)
_PAYLOAD_BUF = os.urandom(CONFIG["payload_max_mb"] * 1024 * 1024)


def _read_wifi_script():
    """Scriptul PowerShell sursa (ASCII), cu adresa serverului inca necompletata.

    Il fortam pe ASCII: PowerShell 5.1 citeste fisierele fara BOM cu codepage-ul
    local, deci diacriticele ar ajunge stricate la elev. Daca cineva editeaza
    scriptul cu diacritice, preferam sa le inlocuim decat sa pice descarcarea.
    """
    with open(os.path.join(SCRIPTS_DIR, "wifi_signal.ps1"), "r", encoding="utf-8") as f:
        text = f.read()
    return text.encode("ascii", "replace").decode("ascii")


# Preludiu care face dintr-un singur fisier .bat + .ps1 (fara dezarhivare):
# cmd ruleaza liniile de batch, PowerShell primeste restul.
BAT_PRELUDE = (
    "<# :\r\n"
    "@echo off\r\n"
    "title Test semnal WiFi - laborator\r\n"
    'powershell -NoProfile -ExecutionPolicy Bypass -Command "iex (Get-Content -LiteralPath '
    "'%~f0' -Raw)\"\r\n"
    "exit /b\r\n"
    "#>\r\n"
)

ZIP_README = """Test semnal WiFi - laborator informatica

1. Dezarhiveaza tot (click dreapta pe arhiva -> Extract All).
2. Dublu-click pe run_wifi_test.bat.
3. Lasa fereastra deschisa pana apare "Trimis cu succes".
4. Daca Windows sau browserul avertizeaza despre fisier .bat/.ps1, alege
   "Pastreaza" / "More info" -> "Run anyway". Fisierul vine de la profesor,
   din reteaua laboratorului.

Adresa serverului este deja scrisa in script, nu trebuie modificat nimic.
"""


def _wifi_script_zip(server_url):
    """Arhiva cu scriptul de semnal, legata la adresa de pe care a fost ceruta."""
    ps1 = _read_wifi_script().replace("__SERVER_URL__", server_url)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("run_wifi_test.bat", "".join([
            "@echo off\r\n",
            "title Test semnal WiFi - laborator\r\n",
            'powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0wifi_signal.ps1"\r\n',
        ]))
        zf.writestr("wifi_signal.ps1", ps1)
        zf.writestr("CITESTE.txt", ZIP_README)
    return buf.getvalue()


def _wifi_script_bat(server_url):
    """Un singur fisier .bat: elevul il descarca si face dublu-click."""
    ps1 = _read_wifi_script().replace("__SERVER_URL__", server_url)
    return (BAT_PRELUDE + ps1.replace("\r\n", "\n").replace("\n", "\r\n")).encode("ascii", "replace")


def _request_origin(handler):
    """Adresa la care elevul vede serverul, exact asa cum trebuie scrisa in script.

    Folosim antetul Host (deci functioneaza si daca IP-ul se schimba), dar nu
    lasam 'localhost' in script: ar trimite datele catre laptopul elevului.
    """
    host = (handler.headers.get("Host") or "").strip()
    host = re.sub(r"[^0-9A-Za-z_.\-:\[\]]", "", host)
    if not host:
        host = f"{LAN_IP}:{CONFIG['port']}"
    hostname = host.split(":")[0].lower()
    if hostname in ("localhost", "127.0.0.1", "::1", "[::1]") or hostname.startswith("127."):
        host = f"{LAN_IP}:{CONFIG['port']}"
    return f"http://{host}"


STATE_LOCK = threading.Lock()
STATE = {
    "session_id": None,
    "state": "idle",  # idle | running | stopped
    "started_at": None,
}
# session_id -> { ip: {...} }  -- cheia e IP-ul laptopului, ca sa nu se poata
# incurca intre testul din browser si scriptul de semnal WiFi
RESULTS = {}
# ip -> {...}  (ultima valoare citita de pe fiecare laptop Windows, dupa IP)
WIFI = {}


def _session_file(session_id):
    return os.path.join(DATA_DIR, f"session_{session_id}.json")


def _save_results(session_id):
    # scriem intai intr-un fisier temporar: o intrerupere de curent in timpul
    # testului nu trebuie sa strice rezultatele deja adunate
    path = _session_file(session_id)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(RESULTS.get(session_id, {}), f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _save_wifi():
    path = os.path.join(DATA_DIR, "wifi_signal.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(WIFI, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _load_existing():
    """Reia semnalul WiFi de la rularea anterioara; un fisier stricat nu opreste serverul."""
    path = os.path.join(DATA_DIR, "wifi_signal.json")
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            WIFI.update(json.load(f))
    except (OSError, json.JSONDecodeError) as e:
        print(f"Avertisment: nu am putut citi {path} ({e}); pornesc cu lista goala.")


_load_existing()

_MOODLE_PROBE = {"at": 0.0, "result": None}


def _probe_reason(exc):
    """Clasifica eroarea de conectare, ca panoul sa spuna ce e de facut."""
    text = f"{type(exc).__name__}: {exc}"
    low = text.lower()
    if "10061" in text or "refused" in low:
        return "refused"        # nimeni nu asculta pe portul ala
    if "timeout" in low or "timed out" in low:
        return "timeout"        # firewall / alt calculator
    if "11001" in text or "getaddrinfo" in low or "name or service not known" in low:
        return "dns"            # nume de host scris gresit
    return "error"


def probe_moodle(url, timeout=3.0):
    """Verifica din chiar laptopul profesorului daca Moodle raspunde (max o data la 5 s)."""
    now = time.time()
    if _MOODLE_PROBE["result"] is not None and now - _MOODLE_PROBE["at"] < 5:
        return _MOODLE_PROBE["result"]
    started = time.perf_counter()
    result = {"url": url}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": f"WifiLabTest/{SERVER_VERSION}"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read(512)
            result.update(ok=True, status=getattr(resp, "status", None))
    except Exception as e:  # noqa: BLE001 - orice eroare inseamna "nu raspunde"
        result.update(ok=False, reason=_probe_reason(e), error=f"{type(e).__name__}: {e}"[:200])
    result["ms"] = round((time.perf_counter() - started) * 1000)
    _MOODLE_PROBE.update(at=now, result=result)
    return result


def config_warnings():
    """Probleme de configurare care fac testul sa para defect fara sa fie."""
    out = []
    url = str(CONFIG.get("moodle_url") or "")
    host = (urlsplit(url).hostname or "").lower()
    if not url.startswith("http"):
        out.append("moodle_url din config.json nu incepe cu http:// - browserul nu poate masura nimic.")
    if host in ("localhost", "127.0.0.1", "::1"):
        out.append("moodle_url arata spre localhost: merge doar pe laptopul profesorului, "
                   "elevii nu au Moodle acolo. Pune IP-ul din reteaua laboratorului.")
    elif host and host != LAN_IP and LAN_IP != "127.0.0.1":
        out.append(f"moodle_url foloseste {host}, dar acest laptop are IP-ul {LAN_IP}. "
                   f"Daca Moodle ruleaza aici, actualizeaza config.json.")
    return out


SAFE_PATH_RE = re.compile(r"^[a-zA-Z0-9_\-./]+$")


class Handler(BaseHTTPRequestHandler):
    server_version = f"WifiLabTest/{SERVER_VERSION}"
    protocol_version = "HTTP/1.1"  # keep-alive: mult mai putine conexiuni TCP noi cu 30 de clienti
    # Fara timeout, un tab inchis brusc lasa thread-ul blocat pe citire la infinit
    # si, cu 30 de elevi, serverul ajunge sa nu mai accepte conexiuni.
    timeout = 30

    def log_message(self, fmt, *args):
        # log mai discret, fara zgomot per-request de ping/payload
        pass

    def log_error(self, fmt, *args):
        # nu umplem consola cu erori de la elevii care inchid pagina in timpul testului
        pass

    # ---------- helpers ----------
    def _write(self, data):
        """Scrie in socket fara sa arunce exceptie daca elevul a inchis pagina."""
        try:
            self.wfile.write(data)
            return True
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            self.close_connection = True
            return False

    def _send_body(self, body, ctype, extra_headers=None, status=HTTPStatus.OK):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        return self._write(body)

    def _send_json(self, obj, status=HTTPStatus.OK, extra_headers=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        headers = {"Access-Control-Allow-Origin": "*"}
        headers.update(extra_headers or {})
        return self._send_body(body, "application/json; charset=utf-8", headers, status)

    def _read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            return {}
        if length <= 0:
            return {}
        if length > 2_000_000:
            # nu citim tot ce a trimis clientul, dar nici nu lasam octetii in
            # socket: ar desincroniza urmatoarea cerere pe aceeasi conexiune
            self.close_connection = True
            return {}
        raw = self.rfile.read(length)
        # Windows PowerShell 5.1 trimite uneori BOM sau alt encoding decat UTF-8;
        # inainte, o decodare esuata insemna cererea aruncata in tacere.
        for encoding in ("utf-8-sig", "utf-8", "cp1252", "utf-16"):
            try:
                return json.loads(raw.decode(encoding))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
        return {}

    def _serve_static(self, rel_path):
        if rel_path == "" or rel_path == "/":
            rel_path = "index.html"
        rel_path = rel_path.lstrip("/")
        if not SAFE_PATH_RE.match(rel_path) or ".." in rel_path:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        full_path = os.path.normpath(os.path.join(STATIC_DIR, rel_path))
        if not full_path.startswith(os.path.normpath(STATIC_DIR)):
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not os.path.isfile(full_path):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
        }.get(os.path.splitext(full_path)[1], "application/octet-stream")
        with open(full_path, "rb") as f:
            body = f.read()
        return self._send_body(body, ctype)

    # ---------- routing ----------
    def do_GET(self):
        parts = urlsplit(self.path)
        path = parts.path
        qs = parse_qs(parts.query)

        if path == "/api/ping":
            return self._send_json({"t": time.time() * 1000})

        if path == "/api/payload":
            try:
                size = int(qs.get("size", ["1000000"])[0])
            except ValueError:
                size = 1000000
            size = max(1, min(size, len(_PAYLOAD_BUF)))
            return self._send_body(_PAYLOAD_BUF[:size], "application/octet-stream")

        if path == "/api/session":
            with STATE_LOCK:
                payload = {
                    "session_id": STATE["session_id"],
                    "state": STATE["state"],
                    "started_at": STATE["started_at"],
                    "moodle_url": CONFIG["moodle_url"],
                    "expected_clients": CONFIG["expected_clients"],
                }
            return self._send_json(payload)

        if path == "/api/results":
            with STATE_LOCK:
                session_id = qs.get("session_id", [None])[0] or STATE["session_id"]
                data = dict(RESULTS.get(session_id, {}))
                wifi = dict(WIFI)
            return self._send_json({"session_id": session_id, "results": data, "wifi": wifi})

        if path == "/api/wifi-signal":
            with STATE_LOCK:
                wifi = dict(WIFI)
            return self._send_json({"wifi": wifi})

        if path == "/api/whoami":
            ip = self.client_address[0]
            with STATE_LOCK:
                match = WIFI.get(ip)
            return self._send_json({"client_id": match["client_id"] if match else None})

        if path == "/api/export.csv":
            return self._export_csv(qs.get("session_id", [None])[0] or STATE["session_id"])

        if path == "/api/health":
            with STATE_LOCK:
                session_id = STATE["session_id"]
                results = len(RESULTS.get(session_id, {})) if session_id else 0
                wifi_count = len(WIFI)
            return self._send_json({
                "ok": True,
                "version": SERVER_VERSION,
                "server": {"host": CONFIG["host"], "port": CONFIG["port"], "lan_ip": LAN_IP},
                "student_url": f"http://{LAN_IP}:{CONFIG['port']}/",
                "data_dir": DATA_DIR,
                "expected_clients": CONFIG["expected_clients"],
                "moodle": probe_moodle(CONFIG["moodle_url"]),
                "warnings": config_warnings(),
                "counts": {"results": results, "wifi_signals": wifi_count},
            })

        if path == "/api/sessions":
            items = []
            try:
                names = os.listdir(DATA_DIR)
            except OSError:
                names = []
            for name in names:
                if not (name.startswith("session_") and name.endswith(".json")):
                    continue
                full = os.path.join(DATA_DIR, name)
                try:
                    with open(full, "r", encoding="utf-8") as f:
                        saved = json.load(f)
                    items.append({
                        "session_id": name[len("session_"):-len(".json")],
                        "clients": len(saved),
                        "saved_at": os.path.getmtime(full) * 1000,
                    })
                except (OSError, json.JSONDecodeError):
                    continue
            items.sort(key=lambda i: i["saved_at"], reverse=True)
            with STATE_LOCK:
                current = STATE["session_id"]
            return self._send_json({"sessions": items, "current": current})

        if path in ("/download/test-wifi.zip", "/download/test-wifi.bat"):
            origin = _request_origin(self)
            if path.endswith(".zip"):
                return self._send_body(
                    _wifi_script_zip(origin + "/api/wifi-signal"),
                    "application/zip",
                    {"Content-Disposition": 'attachment; filename="test-wifi.zip"'},
                )
            return self._send_body(
                _wifi_script_bat(origin + "/api/wifi-signal"),
                "application/octet-stream",
                {"Content-Disposition": 'attachment; filename="test-semnal-wifi.bat"'},
            )

        if path == "/favicon.ico":
            return self._send_body(b"", "image/x-icon", status=HTTPStatus.NO_CONTENT)

        if path.startswith("/static/"):
            return self._serve_static(path[len("/static/"):])

        if path == "/" or path == "/index.html":
            return self._serve_static("index.html")
        if path == "/admin":
            return self._serve_static("admin.html")

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        parts = urlsplit(self.path)
        path = parts.path

        if path == "/api/session/start":
            with STATE_LOCK:
                sid = uuid.uuid4().hex[:8]
                STATE["session_id"] = sid
                STATE["state"] = "running"
                STATE["started_at"] = time.time() * 1000
                RESULTS[sid] = {}
                _save_results(sid)
            return self._send_json({"ok": True, "session_id": sid})

        if path == "/api/session/stop":
            with STATE_LOCK:
                STATE["state"] = "stopped"
            return self._send_json({"ok": True})

        if path == "/api/results":
            body = self._read_json_body()
            ip = self.client_address[0]
            client_id = str(body.get("client_id", "")).strip()[:64] or ip
            with STATE_LOCK:
                session_id = body.get("session_id") or STATE["session_id"]
            if not session_id:
                return self._send_json({"ok": False, "error": "no_active_session"}, HTTPStatus.BAD_REQUEST)
            lat = body.get("latency") if isinstance(body.get("latency"), dict) else {}
            entry = {
                "client_id": client_id,
                "ip": ip,
                "received_at": time.time() * 1000,
                "latency": {
                    "avg": _as_number(lat.get("avg")),
                    "min": _as_number(lat.get("min")),
                    "max": _as_number(lat.get("max")),
                    "p95": _as_number(lat.get("p95")),
                    "jitter": _as_number(lat.get("jitter")),
                    "samples": _as_number(lat.get("samples"), int),
                    "loss_pct": _as_number(lat.get("loss_pct")),
                } if lat else None,
                "throughput_mbps": _as_number(body.get("throughput_mbps")),
                "moodle_load_ms": _as_number(body.get("moodle_load_ms")),
                "moodle_timeout": bool(body.get("moodle_timeout", False)),
                "moodle_error": (str(body.get("moodle_error"))[:120] if body.get("moodle_error") else None),
                "connection": body.get("connection") if isinstance(body.get("connection"), dict) else None,
                "page_visible": body.get("page_visible"),
            }
            with STATE_LOCK:
                RESULTS.setdefault(session_id, {})[ip] = entry
                _save_results(session_id)
            return self._send_json({"ok": True})

        if path == "/api/wifi-signal":
            body = self._read_json_body()
            ip = self.client_address[0]
            client_id = str(body.get("client_id", "")).strip()[:64] or ip
            entry = {
                "client_id": client_id,
                "ip": ip,
                "received_at": time.time() * 1000,
                "ssid": str(body.get("ssid") or "")[:64] or None,
                "bssid": str(body.get("bssid") or "")[:32] or None,
                "state": str(body.get("state") or "")[:32] or None,
                "band": str(body.get("band") or "")[:16] or None,
                # numere, nu string-uri: altfel sumele din panoul profesorului
                # concatenau ("88" + "72" = "8872") in loc sa adune
                "signal_percent": _as_number(body.get("signal_percent"), int),
                "rssi_dbm": _as_number(body.get("rssi_dbm"), int),
                "channel": _as_number(body.get("channel"), int),
                "radio_type": str(body.get("radio_type") or "")[:32] or None,
                "rx_rate_mbps": _as_number(body.get("rx_rate_mbps")),
                "tx_rate_mbps": _as_number(body.get("tx_rate_mbps")),
                "authentication": str(body.get("authentication") or "")[:32] or None,
            }
            with STATE_LOCK:
                WIFI[ip] = entry
                _save_wifi()
            return self._send_json({"ok": True})

        self.send_error(HTTPStatus.NOT_FOUND)

    def _export_csv(self, session_id):
        with STATE_LOCK:
            results = dict(RESULTS.get(session_id, {}))
            wifi = dict(WIFI)

        cols = [
            "ip", "client_id", "latency_avg_ms", "latency_min_ms", "latency_max_ms", "latency_p95_ms",
            "jitter_ms", "latency_samples", "loss_pct",
            "throughput_mbps", "moodle_load_ms", "moodle_timeout", "moodle_error",
            "conn_effective_type", "conn_downlink_mbps", "conn_rtt_ms",
            "wifi_ssid", "wifi_bssid", "wifi_signal_percent", "wifi_rssi_dbm", "wifi_channel",
            "wifi_band", "wifi_radio_type", "wifi_auth", "wifi_rx_rate_mbps", "wifi_tx_rate_mbps",
        ]
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(cols)
        all_ips = sorted(set(results) | set(wifi), key=lambda ip: [int(p) if p.isdigit() else 0 for p in ip.split(".")])
        for ip in all_ips:
            r = results.get(ip, {}) or {}
            lat = r.get("latency") or {}
            conn = r.get("connection") or {}
            w = wifi.get(ip, {}) or {}
            writer.writerow([
                ip,
                w.get("client_id") or r.get("client_id") or ip,
                lat.get("avg"), lat.get("min"), lat.get("max"), lat.get("p95"),
                lat.get("jitter"), lat.get("samples"), lat.get("loss_pct"),
                r.get("throughput_mbps"), r.get("moodle_load_ms"), r.get("moodle_timeout"),
                r.get("moodle_error"),
                conn.get("effectiveType"), conn.get("downlink"), conn.get("rtt"),
                w.get("ssid"), w.get("bssid"), w.get("signal_percent"), w.get("rssi_dbm"),
                w.get("channel"), w.get("band"), w.get("radio_type"), w.get("authentication"),
                w.get("rx_rate_mbps"), w.get("tx_rate_mbps"),
            ])
        # BOM ca Excel sa deschida diacriticele corect
        body = ("\ufeff" + out.getvalue()).encode("utf-8")
        return self._send_body(
            body,
            "text/csv; charset=utf-8",
            {"Content-Disposition": f'attachment; filename="rezultate_{session_id}.csv"'},
        )


class LabHTTPServer(ThreadingHTTPServer):
    # backlogul implicit (5) e mult prea mic pentru 30 de clienti care
    # interogheaza simultan la fiecare 1.5s -> multe conexiuni erau refuzate
    request_queue_size = 128
    daemon_threads = True


def main():
    host, port = CONFIG["host"], CONFIG["port"]
    try:
        server = LabHTTPServer((host, port), Handler)
    except OSError as e:
        print(f"Nu pot porni serverul pe portul {port}: {e}")
        print("Cel mai probabil portul e ocupat de o instanta veche a acestui server.")
        print(f"Opreste-o (Ctrl+C in fereastra ei) sau schimba 'port' in {CONFIG_PATH}.")
        raise SystemExit(1)

    print(f"Server pornit pe portul {port} (versiunea {SERVER_VERSION}).")
    print(f"  Pagina clienti (studenti): http://{LAN_IP}:{port}/")
    print(f"  Panou profesor (admin):    http://{LAN_IP}:{port}/admin")
    print(f"  Moodle configurat la:      {CONFIG['moodle_url']}")
    print(f"  Rezultatele se salveaza in: {DATA_DIR}")
    for warning in config_warnings():
        print(f"  ATENTIE: {warning}")
    moodle = probe_moodle(CONFIG["moodle_url"])
    if moodle.get("ok"):
        print(f"  Moodle raspunde de pe acest laptop ({moodle['ms']} ms).")
    else:
        print(f"  ATENTIE: Moodle nu raspunde de pe acest laptop: {moodle.get('error')}")
        print("           Elevii vor vedea 'Moodle: timeout' pana se rezolva adresa/portul.")
    print("  Daca elevii nu pot deschide pagina, verifica firewall-ul Windows pentru")
    print(f"  portul {port} (vezi TROUBLESHOOTING.md).")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer oprit.")


if __name__ == "__main__":
    main()
