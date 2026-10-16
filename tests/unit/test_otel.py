"""Zero-dependency OTLP/HTTP receiver: stores every export as JSONL and summarises LLM usage."""

import gzip
import json
import threading
import urllib.request

from railhead.otel import make_server, summarize


def _post(port, path, payload, gz=False):
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if gz:
        body = gzip.compress(body)
        headers["Content-Encoding"] = "gzip"
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, json.loads(r.read() or b"{}")


def _log_export(model, inp, out, cost, session="s1", service="claude-code"):
    attrs = [{"key": k, "value": v} for k, v in [
        ("event.name", {"stringValue": "api_request"}), ("model", {"stringValue": model}),
        ("input_tokens", {"intValue": str(inp)}), ("output_tokens", {"intValue": str(out)}),
        ("cost_usd", {"doubleValue": cost}), ("session.id", {"stringValue": session})]]
    return {"resourceLogs": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": service}}]},
                              "scopeLogs": [{"logRecords": [{"timeUnixNano": "1", "body": {"stringValue": "claude_code.api_request"},
                                                             "attributes": attrs}]}]}]}


def test_receiver_stores_logs_and_traces_and_summarises_tokens(tmp_path):
    srv = make_server(tmp_path, port=0)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        assert _post(port, "/v1/logs", _log_export("claude-opus-5-5", 1000, 200, 0.05))[0] == 200
        assert _post(port, "/v1/logs", _log_export("claude-opus-5-5", 500, 100, 0.02), gz=True)[0] == 200
        assert _post(port, "/v1/logs", _log_export("gpt-5.3-codex", 300, 50, 0.01, service="codex"))[0] == 200
        assert _post(port, "/v1/traces", {"resourceSpans": []})[0] == 200
    finally:
        srv.shutdown()
    lines = (tmp_path / "logs.jsonl").read_text().splitlines()
    assert len(lines) == 3
    assert (tmp_path / "traces.jsonl").exists()
    s = summarize(tmp_path)
    assert s["claude-opus-5-5"]["requests"] == 2
    assert s["claude-opus-5-5"]["input_tokens"] == 1500
    assert s["claude-opus-5-5"]["output_tokens"] == 300
    assert round(s["claude-opus-5-5"]["cost_usd"], 4) == 0.07
    assert s["gpt-5.3-codex"]["requests"] == 1


def test_unknown_path_is_404(tmp_path):
    srv = make_server(tmp_path, port=0)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        try:
            _post(port, "/v1/nope", {})
            raise AssertionError("expected 404")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        srv.shutdown()
