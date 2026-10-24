"""Local service the browser extension talks to (127.0.0.1 only; never put it behind Tailscale).

Every request except /v1/health needs the X-Autofill-Token header from
companion.json. Requests that come from web pages (an http/https Origin) or name
another host are refused, so a website open in the browser can't read or change the data.

    python3 autofill_server.py            # port from companion.json (default 8781)
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import autofill_store as store
from autofill_api import dispatch

CONFIG_PATH = Path(os.environ.get("AUTOFILL_CONFIG", Path.home() / ".hermes" / "scholarship-fill" / "companion.json"))
DEFAULT_PORT = 8781
MAX_BODY = 256 * 1024  # a pasted transcript fits easily


def load_config(path: Path = CONFIG_PATH) -> dict:
    if path.exists():
        cfg = json.loads(path.read_text())
    else:
        cfg = {"port": int(os.environ.get("AUTOFILL_PORT") or DEFAULT_PORT), "token": secrets.token_urlsafe(32)}
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # the key is never world-readable
        with os.fdopen(fd, "w") as fh:
            json.dump(cfg, fh, indent=1)
    os.chmod(path, 0o600)
    return cfg


class Handler(BaseHTTPRequestHandler):
    server_version = "ScholarshipAutofill/1"
    token = ""
    port = DEFAULT_PORT
    data_dir: Path | None = None

    def log_message(self, fmt, *args):  # paths only; values never appear in URLs
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

    def _reply(self, status: int, body: dict):
        raw = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _allowed(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        if host not in (f"127.0.0.1:{self.port}", f"localhost:{self.port}"):
            self._reply(HTTPStatus.FORBIDDEN, {"ok": False, "error": "wrong host"})
            return False
        origin = (self.headers.get("Origin") or "").lower()
        if origin.startswith(("http:", "https:")) or origin == "null":
            self._reply(HTTPStatus.FORBIDDEN, {"ok": False, "error": "web pages can't use this"})
            return False
        if self.path.split("?")[0] == "/v1/health":
            return True
        sent = self.headers.get("X-Autofill-Token") or ""
        if not (self.token and hmac.compare_digest(sent.encode(), self.token.encode())):
            self._reply(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "bad token"})
            return False
        return True

    def _body(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY:
            self._reply(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": "too large"})
            return None
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._reply(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "bad json"})
            return None
        if not isinstance(body, dict):
            self._reply(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "expected an object"})
            return None
        return body

    def do_GET(self):
        if not self._allowed():
            return
        self._reply(*dispatch("GET", self.path.split("?")[0], None, self.data_dir))

    def do_POST(self):
        if not self._allowed():
            return
        body = self._body()
        if body is None:
            return
        self._reply(*dispatch("POST", self.path.split("?")[0], body, self.data_dir))

    def do_OPTIONS(self):  # no CORS: a web page's preflight always fails
        self._reply(HTTPStatus.FORBIDDEN, {"ok": False, "error": "no"})


def make_server(port: int, token: str, data_dir: Path | None = None) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"token": token, "port": port, "data_dir": data_dir})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def main():
    cfg = load_config()
    store.load()  # a damaged or foreign data file stops the service here, loudly, not mid-request
    srv = make_server(int(cfg.get("port", DEFAULT_PORT)), cfg["token"])
    print(f"scholarship autofill service on http://127.0.0.1:{srv.server_address[1]}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
