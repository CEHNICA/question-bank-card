"""One-shot receiver: the browser POSTs the exported docx here, it lands on disk.

Used to verify Word output end to end. Binds the loopback interface only and
exits after one successful POST.
"""

import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

TARGET = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.environ["TEMP"], "qb-exported.docx")
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8901


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
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        with open(TARGET, "wb") as handle:
            handle.write(body)
        self.send_response(200)
        self._headers()
        self.end_headers()
        self.wfile.write(b"ok")
        print(f"wrote {TARGET} ({len(body)} bytes)", flush=True)
        self.server.shutdown_requested = True

    def log_message(self, *args):
        pass


server = HTTPServer(("127.0.0.1", PORT), Handler)
server.timeout = 180
print(f"waiting on http://127.0.0.1:{PORT}/dump -> {TARGET}", flush=True)
while not getattr(server, "shutdown_requested", False):
    server.handle_request()
print("done", flush=True)
