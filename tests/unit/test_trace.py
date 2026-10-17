"""Per-player end-to-end trace bundles: OTel events and spans + transcript + game actions + verdict, scrubbed."""

import json

from railhead.trace import bundle, scrub


def _kv(d):
    return [{"key": k, "value": {"intValue": str(v)} if isinstance(v, int) else {"stringValue": v}} for k, v in d.items()]


def _logs(player, t, attrs):
    return {"signal": "logs", "body": {"resourceLogs": [{
        "resource": {"attributes": _kv({"player": player, "service.name": "claude-code", "host.name": "my-mac"})},
        "scopeLogs": [{"logRecords": [{"timeUnixNano": str(t), "attributes": _kv(attrs)}]}]}]}}


def _spans(player, t, name):
    return {"signal": "traces", "body": {"resourceSpans": [{
        "resource": {"attributes": _kv({"player": player})},
        "scopeSpans": [{"spans": [{"traceId": "t1", "spanId": f"s{t}", "parentSpanId": "", "name": name,
                                   "startTimeUnixNano": str(t), "endTimeUnixNano": str(t + 2_000_000),
                                   "attributes": _kv({"user.email": "me@example.com", "tool_name": "Bash"})}]}]}]}}


def test_scrub_drops_identity_keys_and_masks_emails_and_home(monkeypatch):
    monkeypatch.setenv("HOME", "/Users/someone")
    out = scrub({"user.email": "a@b.co", "organization.id": "o", "tool_input": "cat /Users/someone/x; mail a@b.co",
                 "nested": [{"user.account_uuid": "u", "ok": 1}], "dir": "~/.claude/projects/-Users-someone-repo"})
    assert out == {"tool_input": "cat ~/x; mail <email>", "nested": [{"ok": 1}], "dir": "~/.claude/projects/-~-repo"}


def test_bundle_splits_by_player_sorts_by_time_and_joins_game_side(tmp_path):
    otel, arena = tmp_path / "traces", tmp_path / "arena"
    otel.mkdir()
    (otel / "logs.jsonl").write_text("\n".join(json.dumps(x) for x in [
        _logs("opus", 20, {"event.name": "tool_result", "tool_name": "Bash", "user.email": "me@example.com"}),
        _logs("opus", 10, {"event.name": "api_request", "model": "claude-opus-5-5", "input_tokens": 100,
                           "output_tokens": 20, "cost_usd": "0.5"}),
        _logs("luna", 5, {"event.name": "codex.user_prompt", "prompt": "play"}),
    ]) + "\n")
    (otel / "traces.jsonl").write_text(json.dumps(_spans("opus", 15, "claude_code.tool")) + "\n")
    for p in ("opus", "luna"):
        (arena / p / "state/run").mkdir(parents=True)
        (arena / p / "state/run/actions.jsonl").write_text('{"kind":"header","ticks":0}\n{"kind":"act","m":"x"}\n')
        (arena / p / "agent.log").write_text('{"type":"system","cwd":"' + str(tmp_path) + '","email":"me@example.com"}\n')
    (arena / "opus/verify").mkdir()
    (arena / "opus/verify/reward.json").write_text('{"reward": 1.0, "valid": 1, "net_worth": 9}')

    index = bundle(arena, otel, tmp_path / "out")

    rows = {r["player"]: r for r in index}
    assert rows["opus"]["llm_events"] == 2 and rows["opus"]["spans"] == 1 and rows["opus"]["game_actions"] == 1
    assert rows["opus"]["input_tokens"] == 100 and rows["opus"]["net_worth"] == 9 and rows["opus"]["valid"] == 1
    assert rows["luna"]["llm_events"] == 1 and rows["luna"]["valid"] is None
    llm = [json.loads(x) for x in (tmp_path / "out/opus/llm.jsonl").read_text().splitlines()]
    assert [e["event"] for e in llm] == ["api_request", "tool_result"]  # time order, not file order
    text = "".join(f.read_text() for f in (tmp_path / "out").rglob("*") if f.is_file())
    assert "me@example.com" not in text and "my-mac" not in text
    assert (tmp_path / "out/opus/actions.jsonl").exists() and (tmp_path / "out/opus/reward.json").exists()
    assert json.loads((tmp_path / "out/index.json").read_text()) == index


def test_visible_messages_keep_what_the_model_showed_and_drop_hidden_reasoning(tmp_path):
    from railhead.trace import visible_messages

    claude = tmp_path / "claude.log"
    claude.write_text("\n".join(json.dumps(x) for x in [
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "hidden"},
                                                      {"type": "text", "text": "Air pays more per tile."}]}},
        {"type": "result", "result": "Final report."},
    ]) + "\n")
    assert visible_messages(claude) == [{"kind": "message", "text": "Air pays more per tile."},
                                        {"kind": "final", "text": "Final report."}]
    codex = tmp_path / "codex.log"
    codex.write_text("exec\nrh status\ncodex\nTrucks first.\nThen planes.\nexec\nrh wait\ncodex\nDone.\ntokens used\n12\n")
    assert visible_messages(codex) == [{"kind": "message", "text": "Trucks first.\nThen planes."},
                                       {"kind": "final", "text": "Done."}]


def test_opencode_usage_summarises_steps_tokens_latency_and_provider_errors(tmp_path):
    from railhead.trace import opencode_usage

    ev = [
        {"type": "step_start", "timestamp": 1000},
        {"type": "tool_use", "timestamp": 1500, "part": {"tool": "shell", "state": {"status": "completed", "time": {"start": 1400, "end": 1500}}}},
        {"type": "step_finish", "timestamp": 4000, "part": {"reason": "tool-calls", "tokens": {"input": 100, "output": 10, "reasoning": 5, "cache": {"read": 50, "write": 0}}}},
        {"type": "step_start", "timestamp": 5000},
        {"type": "step_start", "timestamp": 9000},  # a step that never finished
        {"type": "error", "timestamp": 70000, "error": {"type": "provider.internal", "message": "Streaming response failed: [503] Upstream error from Nvidia: Service temporarily overloaded"}},
    ]
    log = tmp_path / "agent.log"
    log.write_text("garbage line\n" + "\n".join(json.dumps(e) for e in ev) + "\n")
    u = opencode_usage(log)
    assert u["steps"] == 3 and u["finished_steps"] == 1 and u["unfinished_steps"] == 2
    assert u["tool_calls"] == 1 and u["tool_errors"] == 0
    assert u["tokens"] == {"input": 100, "output": 10, "reasoning": 5, "cache_read": 50}
    assert u["step_seconds"] == {"median": 3.0, "max": 3.0}
    assert u["errors"] == [{"type": "provider.internal", "status": 503, "message": "Service temporarily overloaded"}]
    assert u["finish_reasons"] == {"tool-calls": 1}
    assert u["wall_minutes"] == round((70000 - 1000) / 60000, 1) and u["last_event"] == "error"
