#!/usr/bin/env python3
"""Serve rapid-editor static files and proxy /api/* to Omeka S."""
from __future__ import annotations

import http.server
import os
import sys
import urllib.error
import urllib.request

OMEKA_BASE = os.getenv("OMEKA_BASE_URL", "http://localhost:8888")
PORT = int(os.getenv("EDITOR_PORT", "9000"))
STATIC_DIR = os.path.dirname(os.path.abspath(__file__))


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    # ── Proxy /api/* to Omeka ────────────────────────────────────────────

    def _proxy(self):
        target = OMEKA_BASE + self.path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else None

        headers = {}
        ct = self.headers.get("Content-Type")
        if ct:
            headers["Content-Type"] = ct

        req = urllib.request.Request(
            target, data=body, method=self.command, headers=headers,
        )
        try:
            resp = urllib.request.urlopen(req)
            self.send_response(resp.status)
            for key, val in resp.getheaders():
                if key.lower() not in ("transfer-encoding", "connection"):
                    self.send_header(key, val)
            self.end_headers()
            self.wfile.write(resp.read())
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(e.read())

    def _is_api(self):
        return self.path.startswith("/api/") or self.path.startswith("/files/")

    def do_GET(self):
        if self._is_api():
            self._proxy()
        else:
            super().do_GET()

    def do_PATCH(self):
        self._proxy()

    def do_POST(self):
        self._proxy()

    # Quieter logging — show only proxied requests
    def log_message(self, fmt, *args):
        if self._is_api():
            sys.stderr.write(f"  proxy {self.command} {self.path}\n")


if __name__ == "__main__":
    server = http.server.HTTPServer(("", PORT), Handler)
    sys.stderr.write(f"\n  Rapid Editor → http://localhost:{PORT}\n")
    sys.stderr.write(f"  Proxying API → {OMEKA_BASE}\n\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\nStopped.\n")
