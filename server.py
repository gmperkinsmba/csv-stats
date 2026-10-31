from __future__ import annotations

import json
import os
import secrets
import socket
import socketserver
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from metrics import connect, create_definition, definitions, get_run, import_csv, init_db, overview, run_metric

HOST, PORT = "127.0.0.1", 8324
TOKEN = secrets.token_urlsafe(24)
ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("OUTCOME_METRICS_DATA", Path.home() / ".local" / "share" / "outcome-metrics"))
DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
os.chmod(DATA, 0o700)
DB_PATH = DATA / "outcome_metrics.sqlite3"
if DB_PATH.resolve().is_relative_to(ROOT):
    raise RuntimeError("Runtime database must be outside the application folder")
old_umask = os.umask(0o077)
DB = connect(str(DB_PATH))
os.umask(old_umask)
DB_LOCK = threading.RLock()
init_db(DB)
if DB_PATH.exists():
    os.chmod(DB_PATH, 0o600)


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def server_bind(self):
        # Bypass HTTPServer.server_bind's reverse-DNS lookup for localhost.
        socketserver.TCPServer.server_bind(self)
        self.server_name = "127.0.0.1"
        self.server_port = self.socket.getsockname()[1]

    def handle_error(self, request, client_address):
        return


class Handler(BaseHTTPRequestHandler):
    server_version = "OutcomeMetrics/1.0"

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, fmt, *args):
        # Avoid query strings, imported data, and request tokens in local logs.
        print(f"{self.client_address[0]} {self.command} {self.path.split('?')[0]} {args[1] if len(args)>1 else ''}")

    def _origin_ok(self):
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        origins = {f"http://{item}" for item in hosts}
        origin = self.headers.get("Origin", "")
        return self.headers.get("Host", "") in hosts and (not origin or origin in origins)

    def _send(self, status, payload, content_type="application/json; charset=utf-8"):
        data = payload if isinstance(payload, bytes) else (json.dumps(payload, ensure_ascii=False).encode() if content_type.startswith("application/json") else str(payload).encode())
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def _body(self, maximum=1_000_000):
        try:
            size = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            raise ValueError("Invalid request size")
        if size < 0 or size > maximum:
            raise ValueError("Request body is missing or too large")
        body = self.rfile.read(size)
        if len(body) != size:
            raise ValueError("Incomplete request body")
        return body

    def _json(self, maximum=100_000):
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            raise ValueError("Use application/json")
        value = json.loads(self._body(maximum))
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object")
        return value

    def do_GET(self):
        if not self._origin_ok():
            return self._send(403, {"error": "Host or origin not allowed"})
        route = urlparse(self.path).path
        if route == "/":
            return self._send(200, (ROOT / "static" / "index.html").read_bytes(), "text/html; charset=utf-8")
        if route == "/app.js":
            return self._send(200, (ROOT / "static" / "app.js").read_bytes(), "text/javascript; charset=utf-8")
        if route == "/style.css":
            return self._send(200, (ROOT / "static" / "style.css").read_bytes(), "text/css; charset=utf-8")
        if route == "/api/bootstrap":
            with DB_LOCK:
                return self._send(200, {"token": TOKEN, "overview": overview(DB), "definitions": definitions(DB)})
        if route == "/api/run":
            try:
                run_id = int(self.headers.get("X-Run-Id", "0"))
                with DB_LOCK:
                    return self._send(200, get_run(DB, run_id))
            except (ValueError, TypeError):
                return self._send(400, {"error": "Run was not found"})
        if route == "/api/health":
            return self._send(200, {"status": "ok", "storage": "local"})
        return self._send(404, {"error": "Not found"})

    def do_POST(self):
        if not self._origin_ok():
            return self._send(403, {"error": "Host or origin not allowed"})
        if self.headers.get("X-Local-Action") != TOKEN:
            return self._send(403, {"error": "Request token required"})
        route = urlparse(self.path).path
        try:
            if route == "/api/import":
                payload = self._json(2_100_000)
                csv_text, mapping, file_name = payload.get("csv"), payload.get("mapping"), payload.get("file_name")
                if not isinstance(csv_text, str) or not isinstance(mapping, dict) or not isinstance(file_name, str):
                    raise ValueError("Upload a CSV file and column mapping")
                csv_bytes = csv_text.encode("utf-8")
                if len(csv_bytes) > 1_000_000 or len(mapping) > 5 or len(file_name) > 240:
                    raise ValueError("CSV or mapping exceeds the allowed size")
                if any(not isinstance(k, str) or not isinstance(v, str) for k, v in mapping.items()):
                    raise ValueError("Column mapping must contain text fields")
                with DB_LOCK:
                    result = import_csv(DB, csv_bytes, Path(file_name).name, mapping)
                return self._send(200, result)
            if route == "/api/definitions":
                d = self._json()
                with DB_LOCK:
                    result = create_definition(DB, d.get("name", ""), d.get("denominator_event", ""),
                                               d.get("numerator_event", ""), d.get("observation_days"))
                return self._send(201, result)
            if route == "/api/run":
                d = self._json()
                with DB_LOCK:
                    result = run_metric(DB, d.get("metric_key", "funnel"), d.get("version"),
                                        d.get("cohort_start", ""), d.get("cohort_end", ""), d.get("as_of", ""))
                return self._send(201, result)
            if route == "/api/demo":
                with DB_LOCK:
                    return self._send(200, load_demo(DB))
            raise ValueError("Unknown action")
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return self._send(400, {"error": str(exc)[:200]})
        except Exception:
            return self._send(500, {"error": "The request could not be completed"})


def load_demo(db):
    from datetime import date, timedelta
    today = date.today()
    stamp = today.isoformat()
    start = (today - timedelta(days=35)).isoformat()
    cohort = today - timedelta(days=28)
    # Same synthetic funnel is imported twice to expose duplicate behavior.
    rows = [
        ("000101", "000041", "visit", cohort - timedelta(days=3)),
        ("000102", "000041", "apply", cohort - timedelta(days=1)),
        ("000103", "000042", "visit", cohort - timedelta(days=5)),
        ("000104", "000043", "visit", today - timedelta(days=2)),
        ("000105", "000044", "visit", cohort - timedelta(days=6)),
        ("000106", "000044", "visit", cohort - timedelta(days=5)),
    ]
    csv_text = "event_id,entity_id,event_type,occurred_at,source\n" + "".join(f"{i},{e},{t},{d.isoformat()},synthetic-demo\n" for i,e,t,d in rows)
    bytes_csv = csv_text.encode()
    first = import_csv(db, bytes_csv, "synthetic-funnel.csv")
    second = import_csv(db, bytes_csv, "synthetic-funnel.csv")
    defs = definitions(db)
    if not defs:
        create_definition(db, "Visit to application", "visit", "apply", 14)
        defs = definitions(db)
    d = defs[0]
    initial = run_metric(db, d["metric_key"], d["version"], start, stamp, stamp)
    late = f"event_id,entity_id,event_type,occurred_at,source\n000107,000042,apply,{(today-timedelta(days=23)).isoformat()},synthetic-demo\n".encode()
    late_import = import_csv(db, late, "synthetic-late-outcome.csv")
    revised = run_metric(db, d["metric_key"], d["version"], start, stamp, stamp)
    return {"first_import": first, "repeat_import": second, "late_import": late_import,
            "initial_run_id": initial["id"], "revised_run_id": revised["id"],
            "definition_version": d["version"], "initial": initial, "revised": revised}


def check_loopback():
    infos = socket.getaddrinfo(HOST, PORT, type=socket.SOCK_STREAM)
    if not all(info[4][0] in {"127.0.0.1", "::1"} for info in infos):
        raise RuntimeError("Loopback binding check failed")


def make_server(host=HOST, port=PORT):
    if host != "127.0.0.1":
        raise ValueError("Outcome Metrics binds to 127.0.0.1 only")
    return LocalServer((host, int(port)), Handler)


if __name__ == "__main__":
    check_loopback()
    server = make_server()
    print(f"Outcome Metrics listening at http://{HOST}:{server.server_port}")
    server.serve_forever()
