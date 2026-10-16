"""Tiny loopback receiver that saves whatever the browser POSTs to disk.

The export path that matters runs in the page: KaTeX turns each formula into
MathML there, and the server refuses to export un-converted LaTeX. So the only
honest way to get a real Word/PDF file is to let the page build it — and then
the bytes have to travel back to disk somehow. This is that bit: one POST per
file, named by the query string, loopback only.

Usage: python tools/export_receiver.py <directory> [port]
"""
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

FOLDER = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.environ["TEMP"], "qb-export")
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8902
SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


class Handler(BaseHTTPRequestHandler):
    def _headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")

    def do_OPTIONS(self):
        self.send_response(204)
        self._headers()
        self.end_headers()

    def do_POST(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        name = (query.get("name") or ["out.bin"])[0]
        name = "".join(char for char in name if char in SAFE) or "out.bin"
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        target = os.path.join(FOLDER, name)
        os.makedirs(FOLDER, exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(body)
        self.send_response(200)
        self._headers()
        self.end_headers()
        self.wfile.write(b"ok")
        print(f"wrote {target} ({len(body)} bytes)", flush=True)

    def log_message(self, *args):
        pass


os.makedirs(FOLDER, exist_ok=True)
print(f"waiting on http://127.0.0.1:{PORT}/dump?name=<file> -> {FOLDER}", flush=True)
server = HTTPServer(("127.0.0.1", PORT), Handler)
server.serve_forever()
