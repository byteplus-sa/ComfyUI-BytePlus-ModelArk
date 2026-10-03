#!/usr/bin/env python3
"""
Tiny local server for taking docs screenshots from a running ComfyUI page (see README.md here).

  python3 scripts/capture/capture_server.py        # http://127.0.0.1:8300

  GET  /capture.js               the in-page capture functions
  GET  /workflows/<slug>.json    public/workflows/<slug>.json
  POST /save/<path>              writes the body to docs-site/src/assets/<path>
"""
import http.server
import os
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.dirname(os.path.dirname(HERE))
ASSETS = os.path.join(SITE, "src", "assets")


class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body=b"", kind="text/plain"):
        self.send_response(code)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Content-Type", kind)
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204)

    def do_GET(self):
        path = urllib.parse.unquote(self.path.split("?")[0])
        if path == "/capture.js":
            return self._send(200, open(os.path.join(HERE, "capture.js"), "rb").read(), "application/javascript")
        if path.startswith("/workflows/"):
            name = os.path.basename(path)
            file = os.path.join(SITE, "public", "workflows", name)
            if os.path.isfile(file):
                return self._send(200, open(file, "rb").read(), "application/json")
        self._send(404)

    def do_POST(self):
        path = urllib.parse.unquote(self.path.split("?")[0])
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if path.startswith("/save/"):
            relative = os.path.normpath(path[len("/save/"):]).lstrip("/")
            if relative.startswith(".."):
                return self._send(400)
            target = os.path.join(ASSETS, relative)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as file:
                file.write(body)
            return self._send(200, b"ok")
        self._send(404)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("127.0.0.1", 8300), Handler).serve_forever()
