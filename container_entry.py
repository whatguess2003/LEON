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
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self._send(200, '{"status":"healthy","leon_initialized":false}')
            return
        self._send(503, '{"error":"starting"}')

    def do_POST(self):
        self._send(503, '{"error":"starting"}')

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
    threading.Event().wait()

print("LEON application imported", flush=True)
server.shutdown()
server.server_close()
print("LEON starting server", flush=True)
import uvicorn
uvicorn.run(main.app, host="0.0.0.0", port=port, log_level="info")
