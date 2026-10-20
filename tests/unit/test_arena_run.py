"""Arena runner: one isolated daemon + one headless agent process per player, traced over OTLP."""

from pathlib import Path

from railhead.arena_run import Player, agent_command, agent_env, parse_players


def test_parse_players_accepts_vendor_model_pairs():
    ps = parse_players(["claude:claude-opus-5-5", "codex:gpt-6-astra"])
    assert ps == [Player("claude", "claude-opus-5-5", "claude-opus-5-5"), Player("codex", "gpt-6-astra", "gpt-6-astra")]


def test_claude_player_runs_headless_without_permission_prompts(tmp_path):
    cmd = agent_command(Player("claude", "claude-sonnet-5-5", "sonnet"), "PLAY", tmp_path)
    assert cmd[:2] == ["claude", "-p"] and "PLAY" in cmd
    assert "--dangerously-skip-permissions" in cmd and cmd[cmd.index("--model") + 1] == "claude-sonnet-5-5"


def test_codex_player_gets_traces_and_no_sandbox_for_the_unix_socket(tmp_path):
    cmd = agent_command(Player("codex", "gpt-6-luna", "luna"), "PLAY", tmp_path)
    assert cmd[:2] == ["codex", "exec"] and cmd[cmd.index("-m") + 1] == "gpt-6-luna"
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd and "--skip-git-repo-check" in cmd
    assert any("otel.exporter" in c for c in cmd)
    # e2e traces: spans and the model's own responses, not only token counts
    assert any("otel.trace_exporter" in c and "/v1/traces" in c for c in cmd)
    assert "otel.log_agent_responses=true" in cmd


def test_agent_env_points_rh_at_its_own_daemon_and_tags_traces(tmp_path):
    env = agent_env(Player("claude", "claude-haiku-5-5", "haiku"), "arena-dev-0100", tmp_path / "state",
                    Path("/r/tasks"), "http://127.0.0.1:4318", base={"PATH": "/usr/bin"})
    assert env["RAILHEAD_TASK"] == "arena-dev-0100"
    assert env["RAILHEAD_STATE"] == str(tmp_path / "state")
    assert env["CLAUDE_CODE_ENABLE_TELEMETRY"] == "1"
    assert "player=haiku" in env["OTEL_RESOURCE_ATTRIBUTES"]
    assert env["OTEL_LOG_TOOL_CONTENT"] == "1"  # full tool input and output in the trace events
    assert env["PATH"].split(":")[0].endswith("/bin")


def test_opencode_player_runs_standalone_auto_approved_and_logged_as_json(tmp_path):
    p = parse_players(["opencode:opencode/big-pickle"])[0]
    assert (p.vendor, p.model, p.label) == ("opencode", "opencode/big-pickle", "big-pickle")
    cmd = agent_command(p, "PLAY", tmp_path)
    assert cmd[:2] == ["opencode", "run"] and cmd[-1] == "PLAY"
    assert "--standalone" in cmd and "--auto" in cmd
    assert cmd[cmd.index("--model") + 1] == "opencode/big-pickle"
    assert cmd[cmd.index("--format") + 1] == "json"


def test_opencode_player_does_not_load_the_operators_personal_config(tmp_path):
    p = parse_players(["opencode:opencode/big-pickle"])[0]
    env = agent_env(p, "arena-dev-0100", tmp_path / "big-pickle" / "state", Path("/r/tasks"),
                    "http://127.0.0.1:4318", base={"PATH": "/usr/bin", "HOME": "/Users/x"})
    assert env["XDG_CONFIG_HOME"] == str(tmp_path / "big-pickle" / ".config")  # an empty config dir per player
    assert env["OPENCODE_DISABLE_CLAUDE_CODE"] == "1"  # no ~/.claude prompt or skills


def test_last_session_and_provider_error_come_from_the_json_log(tmp_path):
    import json

    from railhead.arena_run import last_session

    log = tmp_path / "agent.log"
    log.write_text("\n".join(json.dumps(e) for e in [
        {"type": "step_start", "sessionID": "ses_a"},
        {"type": "error", "sessionID": "ses_b", "error": {"type": "provider.internal",
                                                         "message": "Streaming response failed: [503] Upstream error"}},
    ]) + "\n")
    assert last_session(log) == ("ses_b", True)
    log.write_text(json.dumps({"type": "text", "sessionID": "ses_c"}) + "\n")
    assert last_session(log) == ("ses_c", False)


def test_supervisor_resumes_only_after_infrastructure_failures():
    from railhead.arena_run import next_step

    assert next_step(alive=True, submitted=False, provider_error=True, resumes=0, max_resumes=5) == "wait"
    assert next_step(alive=False, submitted=True, provider_error=True, resumes=0, max_resumes=5) == "done"
    assert next_step(alive=False, submitted=False, provider_error=True, resumes=1, max_resumes=5) == "resume"
    assert next_step(alive=False, submitted=False, provider_error=True, resumes=5, max_resumes=5) == "failed"
    # the model stopped on its own: that is its decision, not an outage, so it is never restarted
    assert next_step(alive=False, submitted=False, provider_error=False, resumes=0, max_resumes=5) == "done"


def test_resume_continues_the_same_opencode_session(tmp_path):
    from railhead.arena_run import resume_command

    p = parse_players(["opencode:opencode/big-pickle"])[0]
    cmd = resume_command(p, "ses_b", tmp_path)
    assert cmd[cmd.index("--session") + 1] == "ses_b" and "--standalone" in cmd and "--auto" in cmd
    assert cmd[cmd.index("--model") + 1] == "opencode/big-pickle" and "rh status" in cmd[-1]


def test_error_kinds_separate_outages_quota_and_stalls():
    from railhead.arena_run import error_kind

    assert error_kind({"type": "provider.internal", "message": "[503] Upstream error: Service temporarily overloaded"}) == "outage"
    assert error_kind({"type": "provider.quota", "message": "Rate limit exceeded. Please try again later.", "status": 429}) == "quota"
    assert error_kind({"type": "aborted", "message": "Session interrupted: inactivity"}) == "stall"
    assert error_kind({"type": "tool", "message": "bad command"}) is None


# ---- resume for every vendor: recorded log snippets, no engine, no network ----

import json
import os

# Arena 2, claude-fable-5-1: the five-hour window ran out at 11:31:49 UTC (2026-10-16)
CLAUDE_SID = "44aaef25-7d0c-4014-9899-83dbf58fcd49"
CLAUDE_START = [
    {"type": "system", "subtype": "hook_started", "hook_name": "SessionStart:startup", "session_id": CLAUDE_SID},
    {"type": "system", "subtype": "init", "cwd": "/arena/claude-fable-5-1", "session_id": CLAUDE_SID,
     "model": "claude-fable-5-1", "permissionMode": "bypassPermissions"},
    {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "rh status"}}]}, "session_id": CLAUDE_SID},
]
CLAUDE_LIMIT = [
    {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": 1792161600,
     "rateLimitType": "five_hour", "overageStatus": "rejected", "isUsingOverage": False}, "session_id": CLAUDE_SID},
    {"type": "assistant", "message": {"model": "<synthetic>", "role": "assistant", "content": [
        {"type": "text", "text": "You've hit your session limit · resets 8:10pm (Asia/Calcutta)"}]},
     "session_id": CLAUDE_SID, "error": "rate_limit", "is_api_error_message": True, "api_error_status": 429,
     "api_error": "usage_limit_reached"},
    {"type": "result", "subtype": "success", "is_error": True, "terminal_reason": "api_error", "api_error_status": 429,
     "api_error": "usage_limit_reached", "api_error_params": {"rate_limit_info": {"status": "rejected",
     "resetsAt": 1792161600, "rateLimitType": "five_hour"}},
     "result": "You've hit your session limit · resets 8:10pm (Asia/Calcutta)", "session_id": CLAUDE_SID},
]
CLAUDE_DONE = {"type": "result", "subtype": "success", "is_error": False, "stop_reason": "end_turn",
               "result": "Submitted.", "session_id": CLAUDE_SID}

CODEX_SID = "01a15876-fb28-76d2-9ba5-132ad579c619"
CODEX_START = f"""WARNING: proceeding, even though we could not create PATH aliases: operation would block
Reading additional input from stdin...
2026-10-20T08:39:15.126060Z ERROR codex_core::session::session: failed to load skill /x/SKILL.md: missing YAML frontmatter
OpenAI Codex v0.163.0
--------
workdir: /arena/gpt-6-astra
model: gpt-6-astra
provider: openai
approval: never
sandbox: danger-full-access
reasoning effort: high
reasoning summaries: auto
session id: {CODEX_SID}
--------
user
You are the owner of a transport company in OpenTTD.
codex
I start with the brief.
exec
/bin/zsh -lc 'rh brief' in /arena/gpt-6-astra
 succeeded in 0ms:
"""


def _jsonl(path, events, mtime=None):
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_claude_session_id_and_usage_limit_come_from_the_stream_json_log(tmp_path):
    from railhead.arena_run import error_kind, last_error, last_session

    log = _jsonl(tmp_path / "agent.log", CLAUDE_START + CLAUDE_LIMIT)
    assert last_session(log) == (CLAUDE_SID, True)
    err = last_error(log)
    assert error_kind(err) == "quota" and err["resets_at"] == 1792161600
    # finished normally, or cut off mid-stream by a reboot: not a provider error
    assert last_session(_jsonl(log, CLAUDE_START + [CLAUDE_DONE])) == (CLAUDE_SID, False)
    assert last_session(_jsonl(log, CLAUDE_START)) == (CLAUDE_SID, False)


def test_claude_reset_time_also_comes_from_the_result_text(tmp_path):
    from railhead.arena_run import error_kind, last_error

    result = {k: v for k, v in CLAUDE_LIMIT[-1].items() if k != "api_error_params"}
    log = _jsonl(tmp_path / "agent.log", CLAUDE_START + [result], mtime=1792150309)  # 11:31:49 UTC
    err = last_error(log)
    assert error_kind(err) == "quota" and err["resets_at"] == 1792161600  # 8:10pm IST that day
    usage = {"type": "result", "is_error": True, "result": "Claude AI usage limit reached", "session_id": CLAUDE_SID}
    err = last_error(_jsonl(log, CLAUDE_START + [usage]))
    assert error_kind(err) == "quota" and err["resets_at"] is None
    outage = {"type": "result", "is_error": True, "api_error_status": 529,
              "result": "API Error: 529 Overloaded", "session_id": CLAUDE_SID}
    assert error_kind(last_error(_jsonl(log, CLAUDE_START + [outage]))) == "outage"


def test_codex_session_id_and_quota_come_from_the_text_log(tmp_path):
    import datetime as dt

    from railhead.arena_run import error_kind, last_error, last_session

    log = tmp_path / "agent.log"
    log.write_text(CODEX_START + "tokens used\n2,97,193\nSubmitted.\n")
    assert last_session(log) == (CODEX_SID, False)
    log.write_text(CODEX_START + "ERROR: You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), "
                   "visit https://chatgpt.com/settings/usage to purchase more credits or try again at Oct 21st, 2026 5:04 PM.\n")
    assert last_session(log) == (CODEX_SID, True)
    err = last_error(log)
    assert error_kind(err) == "quota"
    assert err["resets_at"] == dt.datetime(2026, 10, 21, 17, 4).astimezone().timestamp()  # Codex prints local time
    log.write_text(CODEX_START + "ERROR: exceeded retry limit, last status: 429 Too Many Requests\n")
    assert error_kind(last_error(log)) == "quota" and last_error(log)["resets_at"] is None
    log.write_text(CODEX_START + "ERROR: exceeded retry limit, last status: 500 Internal Server Error\n")
    assert error_kind(last_error(log)) == "outage"
    # the agent went on after the error: the error did not end the run
    log.write_text(CODEX_START + "ERROR: stream disconnected before completion\ncodex\nBack again.\n")
    assert last_session(log) == (CODEX_SID, False)


def test_codex_same_day_reset_has_no_date(tmp_path):
    import datetime as dt

    from railhead.arena_run import last_error

    noon = dt.datetime(2026, 10, 20, 12, 0).astimezone().timestamp()
    log = tmp_path / "agent.log"
    log.write_text(CODEX_START + "ERROR: You've hit your usage limit. Try again at 5:04 PM.\n")
    os.utime(log, (noon, noon))
    assert last_error(log)["resets_at"] == dt.datetime(2026, 10, 20, 17, 4).astimezone().timestamp()


def test_resume_prompt_covers_a_machine_restart():
    from railhead.arena_run import RESUME_PROMPT

    assert "restart" in RESUME_PROMPT and "usage limit" in RESUME_PROMPT and "rh status" in RESUME_PROMPT


def test_resume_continues_the_same_claude_session(tmp_path):
    from railhead.arena_run import RESUME_PROMPT, resume_command

    cmd = resume_command(Player("claude", "claude-opus-5-5", "claude-opus-5-5"), CLAUDE_SID, tmp_path)
    assert cmd == ["claude", "-p", "--resume", CLAUDE_SID, RESUME_PROMPT, "--model", "claude-opus-5-5",
                   "--dangerously-skip-permissions", "--output-format", "stream-json", "--verbose"]


def test_resume_continues_the_same_codex_session_with_the_launch_flags(tmp_path):
    from railhead.arena_run import RESUME_PROMPT, resume_command

    p = Player("codex", "gpt-6-astra", "gpt-6-astra")
    cmd = resume_command(p, CODEX_SID, tmp_path)
    assert cmd[:3] == ["codex", "exec", "resume"] and cmd[-2:] == [CODEX_SID, RESUME_PROMPT]
    assert cmd[cmd.index("-m") + 1] == "gpt-6-astra"
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd and "--skip-git-repo-check" in cmd
    assert 'model_reasoning_effort="high"' in cmd and "otel.log_agent_responses=true" in cmd
    assert any("otel.trace_exporter" in c for c in cmd) and 'otel.environment="arena-gpt-6-astra"' in cmd
    assert "-C" not in cmd  # `codex exec resume` has no -C: the process starts in the player directory


def test_resume_without_a_session_starts_a_fresh_agent_that_knows_the_rules(tmp_path):
    from railhead.arena_run import PROMPT, RESUME_PROMPT, resume_command

    for spec in ("claude:claude-opus-5-5", "codex:gpt-6-astra", "opencode:opencode/big-pickle"):
        cmd = resume_command(parse_players([spec])[0], None, tmp_path)
        assert "resume" not in cmd[:3] and "--resume" not in cmd and "--session" not in cmd
        assert any(PROMPT in c and RESUME_PROMPT in c for c in cmd)


def test_quota_waits_until_the_reported_reset_else_the_backoff_schedule():
    from railhead.arena_run import BACKOFF, resume_wait

    assert resume_wait({"type": "provider.quota", "resets_at": 1000.0}, 0, now=400.0) == 660.0  # reset + 60 s
    assert resume_wait({"type": "provider.quota", "resets_at": 1000.0}, 0, now=5000.0) == 0.0  # already reset
    assert resume_wait({"type": "provider.quota", "resets_at": None}, 1, now=0.0) == BACKOFF["quota"][1]
    assert resume_wait({"type": "provider.internal", "message": "[503]"}, 0, now=0.0) == BACKOFF["outage"][0]


def _arena(tmp_path, actions: dict[str, str], logs: dict[str, str]):
    for label, text in actions.items():
        (tmp_path / label / "state/run").mkdir(parents=True)
        (tmp_path / label / "state/run/actions.jsonl").write_text(text)
        (tmp_path / label / "agent.log").write_text(logs[label])
        (tmp_path / label / "agent.pid").write_text("999999")  # gone after the reboot
    return tmp_path


class _FakeProc:
    def __init__(self, pid, on_start=None):
        self.pid = pid
        self.on_start = on_start

    def poll(self):
        return 0


def test_arena_run_resume_restarts_only_players_that_did_not_submit(tmp_path, monkeypatch):
    from railhead import arena_run, daemon

    out = _arena(tmp_path, {"claude-opus-5-5": '{"m":"wait"}\n', "gpt-6-astra": '{"m":"wait"}\n{"m":"submit"}\n'},
                 {"claude-opus-5-5": "\n".join(json.dumps(e) for e in CLAUDE_START) + "\n", "gpt-6-astra": CODEX_START})
    booted, started = [], []
    monkeypatch.setattr(daemon, "ensure_up", lambda task, state: booted.append((task, Path(state))) or 1)

    def popen(cmd, **kw):
        started.append((cmd, kw))
        return _FakeProc(4242)

    monkeypatch.setattr(arena_run.subprocess, "Popen", popen)
    assert arena_run.main(["--resume", "arena-train-0203", str(out), "claude:claude-opus-5-5", "codex:gpt-6-astra"]) == 0
    pdir = out / "claude-opus-5-5"
    assert booted == [("arena-train-0203", pdir / "state")]  # the daemon replays actions.jsonl on boot
    assert [c for c, _ in started] == [arena_run.resume_command(arena_run.Player("claude", "claude-opus-5-5",
                                                                "claude-opus-5-5"), CLAUDE_SID, pdir)]
    assert started[0][1]["cwd"] == pdir and started[0][1]["env"]["RAILHEAD_STATE"] == str(pdir / "state")
    assert (pdir / "agent.pid").read_text() == "4242"
    assert "=== resume after restart" in (pdir / "agent.log").read_text()
    assert (out / "gpt-6-astra/agent.pid").read_text() == "999999"  # submitted: left alone


def test_supervisor_waits_for_a_claude_usage_limit_then_resumes_the_session(tmp_path, monkeypatch):
    import time

    from railhead import arena_run

    limit = [dict(e) for e in CLAUDE_LIMIT]
    limit[-1]["api_error_params"] = {"rate_limit_info": {"status": "rejected", "resetsAt": time.time() - 120}}
    limit[0]["rate_limit_info"] = dict(limit[0]["rate_limit_info"], resetsAt=time.time() - 120)
    out = _arena(tmp_path, {"claude-opus-5-5": '{"m":"wait"}\n'},
                 {"claude-opus-5-5": "\n".join(json.dumps(e) for e in CLAUDE_START + limit) + "\n"})
    pdir = out / "claude-opus-5-5"
    started = []

    def popen(cmd, stdout=None, **kw):  # the resumed agent plays on and submits
        started.append(cmd)
        stdout.write((json.dumps(CLAUDE_DONE) + "\n").encode())
        with open(pdir / "state/run/actions.jsonl", "a") as f:
            f.write('{"m":"submit"}\n')
        return _FakeProc(4243)

    monkeypatch.setattr(arena_run.subprocess, "Popen", popen)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    status = arena_run.supervise("arena-train-0203", out, parse_players(["claude:claude-opus-5-5"]), poll=0)
    assert status == {"claude-opus-5-5": "done"}
    assert started == [arena_run.resume_command(Player("claude", "claude-opus-5-5", "claude-opus-5-5"), CLAUDE_SID, pdir)]
    assert (pdir / "agent.pid").read_text() == "4243"


def test_an_agent_error_that_is_not_the_providers_never_restarts_the_player(tmp_path):
    from railhead.arena_run import last_session

    log = tmp_path / "agent.log"
    log.write_text(CODEX_START + "ERROR: Invalid request: unsupported parameter\n")
    assert last_session(log) == (CODEX_SID, False)
    too_long = {"type": "result", "is_error": True, "result": "Prompt is too long", "session_id": CLAUDE_SID}
    assert last_session(_jsonl(log, CLAUDE_START + [too_long])) == (CLAUDE_SID, False)
