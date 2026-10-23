import os
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

print("LEON container entry", flush=True)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, status, body):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send(503, '{"error":"starting"}')

    def do_POST(self):
        self._send(503, '{"error":"starting"}')

    def do_OPTIONS(self):
        origin = self.headers.get("Origin") or ""
        self.send_response(204)
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
            requested = self.headers.get("Access-Control-Request-Headers") or "content-type"
            self.send_header("Access-Control-Allow-Headers", requested)
            self.send_header("Vary", "Origin")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt, *args):
        print("entry " + (fmt % args), flush=True)


port = int(os.environ.get("PORT", "8080"))
print("LEON entry listening on %s" % port, flush=True)
ThreadingHTTPServer.allow_reuse_address = True
server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()

print("LEON importing application", flush=True)
try:
    import main
except BaseException:
    traceback.print_exc()
    os._exit(1)

print("LEON application imported", flush=True)
server.shutdown()
server.server_close()
print("LEON starting server", flush=True)
import uvicorn
uvicorn.run(main.app, host="0.0.0.0", port=port, log_level="info")
