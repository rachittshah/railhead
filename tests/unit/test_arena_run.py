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
