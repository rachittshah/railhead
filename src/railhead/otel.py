"""The lightest possible OpenTelemetry sink: an OTLP/HTTP (JSON) receiver in the standard library.

Point any OTLP exporter at it (Claude Code, Codex, Harbor agents, OpenLLMetry...) with
``OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318`` and ``OTEL_EXPORTER_OTLP_PROTOCOL=http/json``.
Every export is appended, untouched, to ``<dir>/{traces,logs,metrics}.jsonl`` (one line per export).

    railhead otel serve [--port 4318] [--dir traces]
    railhead otel summary [--dir traces]        # LLM requests, tokens and cost per model
"""

from __future__ import annotations

import gzip
import json
import threading
import time
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

SIGNALS = {"/v1/traces": "traces", "/v1/logs": "logs", "/v1/metrics": "metrics"}


def make_server(out_dir: Path, port: int = 4318, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:  # quiet
            pass

        def do_POST(self) -> None:  # noqa: N802 - http.server API
            signal = SIGNALS.get(self.path.split("?")[0])
            if signal is None:
                self.send_response(404)
                self.end_headers()
                return
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if self.headers.get("Content-Encoding", "").lower() == "gzip":
                body = gzip.decompress(body)
            ctype = self.headers.get("Content-Type", "")
            if "json" in ctype:
                try:
                    payload: Any = json.loads(body or b"{}")
                except json.JSONDecodeError:
                    payload = {"_raw": body.decode("utf-8", "replace")}
            else:  # protobuf: keep the bytes (decode later with opentelemetry-proto if ever needed)
                payload = {"_protobuf_hex": body.hex()}
            line = json.dumps({"received": time.time(), "signal": signal, "body": payload}, separators=(",", ":"))
            with lock, open(out_dir / f"{signal}.jsonl", "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            out = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    return ThreadingHTTPServer((host, port), Handler)


def _attr_value(v: dict) -> Any:
    for k in ("stringValue", "boolValue", "doubleValue"):
        if k in v:
            return v[k]
    if "intValue" in v:
        return int(v["intValue"])
    return v


def iter_log_records(out_dir: Path):
    path = Path(out_dir) / "logs.jsonl"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        body = json.loads(line).get("body", {})
        for rl in body.get("resourceLogs", []):
            res = {a["key"]: _attr_value(a["value"]) for a in rl.get("resource", {}).get("attributes", [])}
            for sl in rl.get("scopeLogs", []):
                for rec in sl.get("logRecords", []):
                    attrs = {a["key"]: _attr_value(a["value"]) for a in rec.get("attributes", [])}
                    yield res, attrs, rec


def summarize(out_dir: Path) -> dict[str, dict]:
    """Per-model totals over every API-request event (Claude Code `api_request`, Codex `*.api_request`...)."""
    agg: dict[str, dict] = defaultdict(lambda: {"requests": 0, "input_tokens": 0, "output_tokens": 0,
                                                "cache_read_tokens": 0, "cost_usd": 0.0, "sessions": set()})
    for res, attrs, rec in iter_log_records(out_dir):
        name = str(attrs.get("event.name") or rec.get("body", {}).get("stringValue", ""))
        if "api_request" not in name and "response" not in name:
            continue
        model = str(attrs.get("model") or attrs.get("gen_ai.request.model") or res.get("service.name", "?"))
        a = agg[model]
        a["requests"] += 1
        a["input_tokens"] += int(attrs.get("input_tokens") or attrs.get("gen_ai.usage.input_tokens") or 0)
        a["output_tokens"] += int(attrs.get("output_tokens") or attrs.get("gen_ai.usage.output_tokens") or 0)
        a["cache_read_tokens"] += int(attrs.get("cache_read_tokens") or 0)
        a["cost_usd"] += float(attrs.get("cost_usd") or 0.0)
        if attrs.get("session.id"):
            a["sessions"].add(attrs["session.id"])
    return {m: {**v, "sessions": len(v["sessions"])} for m, v in agg.items()}


def main(argv: list[str]) -> int:
    sub = argv[0] if argv else "serve"
    d = Path(argv[argv.index("--dir") + 1]) if "--dir" in argv else Path("traces")
    if sub == "serve":
        port = int(argv[argv.index("--port") + 1]) if "--port" in argv else 4318
        srv = make_server(d, port=port)
        print(json.dumps({"otlp_http": f"http://127.0.0.1:{srv.server_address[1]}", "dir": str(d.resolve())}),
              flush=True)
        srv.serve_forever()
        return 0
    if sub == "summary":
        print(json.dumps(summarize(d), indent=1))
        return 0
    print(__doc__)
    return 2
