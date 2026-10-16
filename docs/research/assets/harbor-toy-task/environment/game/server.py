"""Stand-in for a game server sidecar: in-memory state that the agent mutates
and the verifier later reads over the compose network."""

import json
import os
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {"boot_id": uuid.uuid4().hex, "booted_at": time.time(), "pid": os.getpid(), "ticks": 0}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/state":
            self._send(200, STATE)
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path == "/tick":
            STATE["ticks"] += 1
            self._send(200, STATE)
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, *args) -> None:
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 9000), Handler).serve_forever()
