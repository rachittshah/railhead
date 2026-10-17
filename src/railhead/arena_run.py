"""Run an arena: several models play the same task first-person, each with its own daemon.

    railhead arena-run <task_id> <out_dir> claude:claude-opus-5-5 codex:gpt-6-astra opencode:opencode/big-pickle ... [--otel URL]
    railhead arena <out_dir>                                  # live scoreboard

Each player gets ``<out_dir>/<label>/{state,agent.log}``. Claude players run ``claude -p`` and Codex
players run ``codex exec``, and opencode players run ``opencode run``; all only see ``rh`` (on PATH,
bound to their own daemon). Claude and Codex export every LLM call over OTLP to ``--otel`` (default the
local ``railhead otel serve`` sink), tagged with ``player=<label>``. opencode writes its events as JSON
to ``agent.log`` and gets an empty configuration directory, so it never loads the operator's setup.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from railhead.engine import REPO_DIR

PROMPT = """You are the owner of a transport company in OpenTTD, and you play the game yourself, in first \
person, through the `rh` command (it is on PATH: just run `rh ...` in the shell). Your goal: make as much \
money as possible and scale your company over the whole mandate. Every form of transport is allowed: road, \
rail (couple wagons with `rh vehicle attach`), air and water.

Start with `rh brief`, `rh help` and `rh status`.

Rules:
- Play ONLY through `rh`. Do not read or modify any files of the Railhead repository and do not run any \
other program against the game. Your score is computed by replaying your own `rh` action log in a clean \
engine, then letting your company run 365 more days without you.
- Time passes only in `rh wait days=N` (optionally `until=...`). The world is frozen while you think. Cover \
the WHOLE mandate: wait in chunks of 60-365 days between management rounds; reinvest profits; expand into \
new routes and new transport modes; sell or fix loss-making vehicles; manage the loan.
- Budget: up to about 600 rh calls. When you are done, run `rh submit confirm=yes`.

At the end, write a short report: your strategy, the final `rh status` numbers, and any problems with rh."""


@dataclass(frozen=True)
class Player:
    vendor: str  # claude | codex | opencode
    model: str
    label: str


def parse_players(specs: list[str]) -> list[Player]:
    out = []
    for s in specs:
        vendor, model = s.split(":", 1)
        if vendor not in ("claude", "codex", "opencode"):
            raise ValueError(f"unknown vendor {vendor!r} in {s!r}")
        out.append(Player(vendor, model, model.split("/")[-1]))
    return out


def agent_command(p: Player, prompt: str, workdir: Path, otel: str = "http://127.0.0.1:4318") -> list[str]:
    if p.vendor == "claude":
        return ["claude", "-p", prompt, "--model", p.model, "--dangerously-skip-permissions",
                "--output-format", "stream-json", "--verbose"]
    if p.vendor == "opencode":  # a private server per player; every step logged as JSON
        return ["opencode", "run", "--standalone", "--model", p.model, "--auto", "--format", "json", prompt]
    return ["codex", "exec", "-m", p.model, "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox",
            "-C", str(workdir), "-c", 'model_reasoning_effort="high"',
            "-c", f'otel.exporter={{otlp-http={{endpoint="{otel}/v1/logs",protocol="json"}}}}',
            "-c", f'otel.trace_exporter={{otlp-http={{endpoint="{otel}/v1/traces",protocol="json"}}}}',
            "-c", "otel.log_user_prompt=true", "-c", "otel.log_agent_responses=true",
            "-c", f'otel.environment="arena-{p.label}"', prompt]


def agent_env(p: Player, task: str, state: Path, tasks_dir: Path, otel: str, base: dict | None = None) -> dict:
    env = dict(os.environ if base is None else base)
    env.update({
        "RAILHEAD_TASK": task, "RAILHEAD_STATE": str(state), "RAILHEAD_TASKS": str(tasks_dir),
        "PATH": str(Path(sys.executable).parent) + ":" + env.get("PATH", ""),
        # Claude Code telemetry (logs/events, metrics, beta traces) -> OTLP/HTTP JSON
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1", "OTEL_LOGS_EXPORTER": "otlp", "OTEL_METRICS_EXPORTER": "otlp",
        "OTEL_TRACES_EXPORTER": "otlp", "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA": "1",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json", "OTEL_EXPORTER_OTLP_ENDPOINT": otel,
        "OTEL_LOG_USER_PROMPTS": "1", "OTEL_LOG_TOOL_DETAILS": "1", "OTEL_LOG_TOOL_CONTENT": "1",
        "OTEL_LOGS_EXPORT_INTERVAL": "2000",
        "OTEL_RESOURCE_ATTRIBUTES": f"player={p.label},arena.task={task}",
    })
    if p.vendor == "opencode":  # isolation: no operator AGENTS.md, config, skills or ~/.claude prompt
        cfg = Path(state).parent / ".config"
        cfg.mkdir(parents=True, exist_ok=True)
        env.update({"XDG_CONFIG_HOME": str(cfg), "OPENCODE_DISABLE_CLAUDE_CODE": "1"})
    return env


RESUME_PROMPT = ("The model provider failed and your session was cut off. Nothing in the game changed while you were "
                 "away. Continue the same mandate with the same rules: run `rh status` and `rh history n=10` to see "
                 "where you are, then keep playing. When you are done, run `rh submit confirm=yes` and write your report.")
PROVIDER_ERROR = re.compile(r"\[(5\d\d|429)\]|overloaded|rate.?limit|timed? ?out", re.IGNORECASE)


def last_session(agent_log: Path) -> tuple[str | None, bool]:
    """(opencode session id of the last event, whether the run ended on a provider error)."""
    session, last = None, None
    for line in Path(agent_log).read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("{"):
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        session, last = e.get("sessionID", session), e
    err = (last or {}).get("error") or {}
    failed = (last or {}).get("type") == "error" and (
        str(err.get("type", "")).startswith("provider") or bool(PROVIDER_ERROR.search(str(err.get("message", "")))))
    return session, failed


def next_step(alive: bool, submitted: bool, provider_error: bool, resumes: int, max_resumes: int) -> str:
    """The harness recovers from infrastructure failures, never from the model's own decisions."""
    if alive:
        return "wait"
    if submitted or not provider_error:
        return "done"
    return "resume" if resumes < max_resumes else "failed"


def resume_command(p: Player, session: str, workdir: Path) -> list[str]:
    return ["opencode", "run", "--standalone", "--session", session, "--model", p.model, "--auto", "--format", "json",
            RESUME_PROMPT]


def supervise(task: str, out_dir: Path, players: list[Player], max_resumes: int = 6,
              backoff: tuple[int, ...] = (60, 120, 300, 600, 900, 1800), poll: int = 30) -> dict[str, str]:
    """Watch opencode players; after a provider failure, resume the same session with backoff."""
    import time

    out_dir = Path(out_dir).resolve()
    tasks_dir = Path(os.environ.get("RAILHEAD_TASKS", REPO_DIR / "tasks")).resolve()
    resumes = {p.label: 0 for p in players}
    status = {p.label: "wait" for p in players}
    while any(v in ("wait", "resume") for v in status.values()):
        for p in players:
            if status[p.label] in ("done", "failed"):
                continue
            pdir = out_dir / p.label
            pid = int((pdir / "agent.pid").read_text())
            try:
                os.kill(pid, 0)
                alive = True
            except OSError:
                alive = False
            submitted = '"m":"submit"' in (pdir / "state/run/actions.jsonl").read_text()
            session, failed = last_session(pdir / "agent.log")
            step = next_step(alive, submitted, failed, resumes[p.label], max_resumes)
            if step == "resume" and session:
                time.sleep(backoff[min(resumes[p.label], len(backoff) - 1)])
                resumes[p.label] += 1
                with open(pdir / "agent.log", "ab") as log:  # the child keeps its own copy of the descriptor
                    log.write(f"\n=== resume {resumes[p.label]} after provider error ===\n".encode())
                    log.flush()
                    proc = subprocess.Popen(resume_command(p, session, pdir), cwd=pdir, stdin=subprocess.DEVNULL,
                                            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                                            env=agent_env(p, task, pdir / "state", tasks_dir, "http://127.0.0.1:4318"))
                (pdir / "agent.pid").write_text(str(proc.pid))
                print(json.dumps({"player": p.label, "resume": resumes[p.label], "session": session}), flush=True)
                step = "wait"
            elif step in ("done", "failed") and status[p.label] != step:
                print(json.dumps({"player": p.label, "status": step, "resumes": resumes[p.label]}), flush=True)
            status[p.label] = step
        time.sleep(poll)
    return status


def launch(task: str, out_dir: Path, players: list[Player], otel: str = "http://127.0.0.1:4318") -> list[int]:
    from railhead.daemon import ensure_up

    out_dir = Path(out_dir).resolve()
    tasks_dir = Path(os.environ.get("RAILHEAD_TASKS", REPO_DIR / "tasks")).resolve()
    pids = []
    for p in players:
        pdir = out_dir / p.label
        state = pdir / "state"
        state.mkdir(parents=True, exist_ok=True)
        os.environ["RAILHEAD_TASKS"] = str(tasks_dir)
        ensure_up(task, state)
        log = open(pdir / "agent.log", "ab")
        proc = subprocess.Popen(agent_command(p, PROMPT, pdir, otel), cwd=pdir, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT,
                                env=agent_env(p, task, state, tasks_dir, otel), start_new_session=True)
        (pdir / "agent.pid").write_text(str(proc.pid))
        pids.append(proc.pid)
    return pids


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--supervise":  # railhead arena-run --supervise <task> <out_dir> opencode:<model> ...
        print(json.dumps(supervise(argv[1], Path(argv[2]), parse_players(argv[3:]))))
        return 0
    otel = "http://127.0.0.1:4318"
    if "--otel" in argv:
        i = argv.index("--otel")
        otel = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    task, out, specs = argv[0], Path(argv[1]), argv[2:]
    pids = launch(task, out, parse_players(specs), otel)
    print({"launched": len(pids), "pids": pids})
    return 0
