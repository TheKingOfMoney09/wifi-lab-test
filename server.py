#!/usr/bin/env python3
"""
Server HTTP pentru testarea retelei wireless din laboratorul de informatica.

Ruleaza pe laptopul profesorului (acest calculator) si este accesat de cei
~30 de clienti Windows printr-un link in browser, in reteaua locala.

Porneste cu:
    python3 server.py
"""
import io
import json
import os
import re
import statistics
import threading
import time
import uuid
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")
DATA_DIR = os.path.join(BASE_DIR, "data")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

DEFAULT_CONFIG = {
    "host": "0.0.0.0",
    "port": 8090,
    "moodle_url": "http://192.168.6.113:8083",
    "expected_clients": 30,
    "payload_max_mb": 10,
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


CONFIG = load_config()
os.makedirs(DATA_DIR, exist_ok=True)

# Buffer precalculat pentru testul de viteza (evitam sa generam date la fiecare cerere)
_PAYLOAD_BUF = os.urandom(CONFIG["payload_max_mb"] * 1024 * 1024)


def _build_wifi_script_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in ("run_wifi_test.bat", "wifi_signal.ps1"):
            zf.write(os.path.join(SCRIPTS_DIR, name), arcname=name)
    return buf.getvalue()


_WIFI_SCRIPT_ZIP = _build_wifi_script_zip()

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
    with open(_session_file(session_id), "w", encoding="utf-8") as f:
        json.dump(RESULTS.get(session_id, {}), f, ensure_ascii=False, indent=2)


def _save_wifi():
    with open(os.path.join(DATA_DIR, "wifi_signal.json"), "w", encoding="utf-8") as f:
        json.dump(WIFI, f, ensure_ascii=False, indent=2)


def _load_existing():
    if os.path.exists(os.path.join(DATA_DIR, "wifi_signal.json")):
        with open(os.path.join(DATA_DIR, "wifi_signal.json"), "r", encoding="utf-8") as f:
            WIFI.update(json.load(f))


_load_existing()

SAFE_PATH_RE = re.compile(r"^[a-zA-Z0-9_\-./]+$")


class Handler(BaseHTTPRequestHandler):
    server_version = "WifiLabTest/1.0"
    protocol_version = "HTTP/1.1"  # keep-alive: mult mai putine conexiuni TCP noi cu 30 de clienti

    def log_message(self, fmt, *args):
        # log mai discret, fara zgomot per-request de ping/payload
        pass

    # ---------- helpers ----------
    def _send_json(self, obj, status=HTTPStatus.OK, extra_headers=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
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
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

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
            body = _PAYLOAD_BUF[:size]
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/session":
            with STATE_LOCK:
                return self._send_json({
                    "session_id": STATE["session_id"],
                    "state": STATE["state"],
                    "started_at": STATE["started_at"],
                    "moodle_url": CONFIG["moodle_url"],
                    "expected_clients": CONFIG["expected_clients"],
                })

        if path == "/api/results":
            session_id = qs.get("session_id", [None])[0] or STATE["session_id"]
            with STATE_LOCK:
                data = dict(RESULTS.get(session_id, {}))
            return self._send_json({"session_id": session_id, "results": data, "wifi": dict(WIFI)})

        if path == "/api/wifi-signal":
            with STATE_LOCK:
                return self._send_json({"wifi": dict(WIFI)})

        if path == "/api/whoami":
            ip = self.client_address[0]
            with STATE_LOCK:
                match = WIFI.get(ip)
            return self._send_json({"client_id": match["client_id"] if match else None})

        if path == "/api/export.csv":
            return self._export_csv(qs.get("session_id", [None])[0] or STATE["session_id"])

        if path == "/download/test-wifi.zip":
            body = _WIFI_SCRIPT_ZIP
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", 'attachment; filename="test-wifi.zip"')
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

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
            session_id = body.get("session_id") or STATE["session_id"]
            if not session_id:
                return self._send_json({"ok": False, "error": "no_active_session"}, HTTPStatus.BAD_REQUEST)
            entry = {
                "client_id": client_id,
                "ip": ip,
                "received_at": time.time() * 1000,
                "latency": body.get("latency"),
                "throughput_mbps": body.get("throughput_mbps"),
                "moodle_load_ms": body.get("moodle_load_ms"),
                "moodle_timeout": body.get("moodle_timeout", False),
                "connection": body.get("connection"),
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
                "ssid": body.get("ssid"),
                "bssid": body.get("bssid"),
                "signal_percent": body.get("signal_percent"),
                "channel": body.get("channel"),
                "radio_type": body.get("radio_type"),
                "rx_rate_mbps": body.get("rx_rate_mbps"),
                "tx_rate_mbps": body.get("tx_rate_mbps"),
                "authentication": body.get("authentication"),
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
            "ip", "client_id", "latency_avg_ms", "latency_min_ms", "latency_max_ms", "jitter_ms",
            "throughput_mbps", "moodle_load_ms", "moodle_timeout",
            "conn_effective_type", "conn_downlink_mbps", "conn_rtt_ms",
            "wifi_ssid", "wifi_signal_percent", "wifi_channel", "wifi_radio_type",
            "wifi_rx_rate_mbps", "wifi_tx_rate_mbps",
        ]
        lines = [",".join(cols)]
        all_ips = sorted(set(results.keys()) | set(wifi.keys()))
        for ip in all_ips:
            r = results.get(ip, {})
            lat = r.get("latency") or {}
            conn = r.get("connection") or {}
            w = wifi.get(ip, {})
            label = w.get("client_id") or r.get("client_id") or ip
            row = [
                ip, label,
                lat.get("avg", ""), lat.get("min", ""), lat.get("max", ""), lat.get("jitter", ""),
                r.get("throughput_mbps", ""), r.get("moodle_load_ms", ""), r.get("moodle_timeout", ""),
                conn.get("effectiveType", ""), conn.get("downlink", ""), conn.get("rtt", ""),
                w.get("ssid", ""), w.get("signal_percent", ""), w.get("channel", ""), w.get("radio_type", ""),
                w.get("rx_rate_mbps", ""), w.get("tx_rate_mbps", ""),
            ]
            lines.append(",".join(str(v) if v is not None else "" for v in row))
        body = ("\n".join(lines) + "\n").encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="rezultate_{session_id}.csv"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class LabHTTPServer(ThreadingHTTPServer):
    # backlogul implicit (5) e mult prea mic pentru 30 de clienti care
    # interogheaza simultan la fiecare 1.5s -> multe conexiuni erau refuzate
    request_queue_size = 128
    daemon_threads = True


def main():
    host, port = CONFIG["host"], CONFIG["port"]
    server = LabHTTPServer((host, port), Handler)
    print(f"Server pornit pe http://{host}:{port}")
    print(f"  Pagina clienti (studenti): http://<IP-ul-acestui-laptop>:{port}/")
    print(f"  Panou profesor (admin):    http://<IP-ul-acestui-laptop>:{port}/admin")
    print(f"  Moodle configurat la:      {CONFIG['moodle_url']}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer oprit.")


if __name__ == "__main__":
    main()
