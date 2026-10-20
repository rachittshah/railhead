"""Run an arena: several models play the same task first-person, each with its own daemon.

    railhead arena-run <task_id> <out_dir> claude:claude-opus-5-5 codex:gpt-6-astra opencode:opencode/big-pickle ... [--otel URL]
    railhead arena <out_dir>                                  # live scoreboard
    railhead arena-run --supervise <task_id> <out_dir> <players...>   # resume after provider errors and limits
    railhead arena-run --resume <task_id> <out_dir> <players...>      # resume after a crash or a reboot

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


RESUME_PROMPT = ("Your session was cut off: the model provider failed, a usage limit was reached, or the machine "
                 "restarted. The game was restored from your own `rh` action log, so it is exactly where your last "
                 "recorded action left it, and no game time passed while you were away (an action that was running "
                 "when the session stopped may be missing). Continue the same mandate with the same rules: run "
                 "`rh status` and `rh history n=10` to see where you are, then keep playing. When you are done, run "
                 "`rh submit confirm=yes` and write your report.")
PROVIDER_ERROR = re.compile(r"\[(5\d\d|429)\]|overloaded|rate.?limit|timed? ?out|stream disconnected", re.IGNORECASE)
QUOTA_TEXT = re.compile(r"usage.?limit|session limit|rate.?limit|too many requests|\b429\b|quota", re.IGNORECASE)
BACKOFF = {"outage": (60, 120, 300, 600, 900, 1800), "stall": (30, 120, 300, 600, 900, 1800),
           "quota": (1800, 3600, 3600, 3600, 3600, 3600)}
RESET_MARGIN_S = 60  # reset times are printed to the minute; start a little after the window opens


def error_kind(err: dict) -> str | None:
    """outage (5xx, overload, timeout), quota (429, usage limit, a reported reset time), stall (stream
    inactivity), else None."""
    kind, msg = str(err.get("type", "")), str(err.get("message", ""))
    if (kind == "provider.quota" or err.get("status") == 429 or err.get("resets_at") or "[429]" in msg
            or QUOTA_TEXT.search(msg)):
        return "quota"
    if kind == "aborted" and "inactivity" in msg.lower():
        return "stall"
    status = err.get("status")
    if kind.startswith("provider") or (isinstance(status, int) and status >= 500) or PROVIDER_ERROR.search(msg):
        return "outage"
    return None


_MONTHS = {m: i for i, m in enumerate(("jan feb mar apr may jun jul aug sep oct nov dec").split(), 1)}
# Claude: "resets 8:10pm (Asia/Calcutta)", "resets Oct 21, 5pm (Asia/Calcutta)"; Codex (local time):
# "try again at 5:04 PM.", "try again at Oct 21st, 2026 5:04 PM."
_RESET = re.compile(r"(?:resets|try again at)\s+(?:(?P<mon>[A-Za-z]{3})[a-z]*\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?,?\s+"
                    r"(?:(?P<year>\d{4})\s+)?(?:at\s+)?)?(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>[ap]\.?m\.?)"
                    r"(?:\s*\((?P<tz>[^)]+)\))?", re.IGNORECASE)


def reset_time(text: str, ref: float) -> float | None:
    """The epoch of a reset time written in an error text, read relative to ``ref`` (when the error was written)."""
    import datetime as dt
    from zoneinfo import ZoneInfo

    m = _RESET.search(text or "")
    if not m:
        return None
    try:
        tz = ZoneInfo(m["tz"].strip()) if m["tz"] else None
    except (KeyError, ValueError):
        tz = None
    now = dt.datetime.fromtimestamp(ref, tz) if tz else dt.datetime.fromtimestamp(ref).astimezone()
    hour = int(m["h"]) % 12 + (12 if m["ap"].lower().startswith("p") else 0)
    minute = int(m["m"] or 0)
    if m["mon"] and m["mon"].lower() in _MONTHS:
        at = now.replace(year=int(m["year"] or now.year), month=_MONTHS[m["mon"].lower()], day=int(m["day"]),
                         hour=hour, minute=minute, second=0, microsecond=0)
    else:  # no date: the next time the clock shows this time
        at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if at < now - dt.timedelta(minutes=1):
            at += dt.timedelta(days=1)
    return at.timestamp()


def _claude_error(e: dict, rejected_reset: float | None, ref: float) -> dict:
    """A stream-json ``result`` event with ``is_error`` -> the error shape that error_kind reads."""
    text = str(e.get("result") or "")
    msg = " ".join(str(x) for x in (e.get("api_error"), e.get("terminal_reason"), text) if x)
    info = (e.get("api_error_params") or {}).get("rate_limit_info") or {}
    resets = info.get("resetsAt") or rejected_reset or reset_time(text, ref)
    quota = bool(resets) or e.get("api_error_status") == 429 or bool(QUOTA_TEXT.search(msg))
    return {"type": "provider.quota" if quota else "error", "message": msg or "error",  # error_kind decides
            "status": e.get("api_error_status"), "resets_at": float(resets) if resets else None}


def _codex_error(line: str, ref: float) -> dict:
    """A Codex ``ERROR: ...`` line -> the error shape that error_kind reads."""
    msg = line[len("ERROR:"):].strip()
    status = re.search(r"last status:\s*(\d{3})", msg)
    status = int(status[1]) if status else None
    resets = reset_time(msg, ref) if re.search(r"usage limit", msg, re.IGNORECASE) else None
    quota = bool(resets) or status == 429 or bool(QUOTA_TEXT.search(msg))
    return {"type": "provider.quota" if quota else "error", "message": msg, "status": status,  # error_kind decides
            "resets_at": resets}


def _scan(agent_log: Path) -> tuple[str | None, dict]:
    """(session id, the error that ended the run or {}) for any vendor's agent.log.

    opencode: JSON events with ``sessionID``; the run failed if the last event is ``type: error``.
    Claude: stream-json events with ``session_id``; the run failed if the last ``result`` has ``is_error``.
    Codex: text; ``session id: <uuid>`` in the header, and an ``ERROR:`` line that no agent output follows."""
    path = Path(agent_log)
    if not path.exists():
        return None, {}
    ref = path.stat().st_mtime
    session, err, rejected_reset = None, {}, None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("{"):
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "sessionID" in e or e.get("type") == "error":  # opencode
                session = e.get("sessionID", session)
                err = (e.get("error") or {"type": "error"}) if e.get("type") == "error" else {}
                continue
            session = e.get("session_id", session)  # Claude: every event carries it, the init event first
            if e.get("type") == "rate_limit_event":
                info = e.get("rate_limit_info") or {}
                rejected_reset = info.get("resetsAt") if info.get("status") == "rejected" else None
            elif e.get("type") == "result":
                err = _claude_error(e, rejected_reset, ref) if e.get("is_error") else {}
            elif e.get("type") in ("assistant", "user"):
                err = {}
            continue
        m = re.match(r"session id:\s*(\S+)", line)
        if m:
            session, err = m[1], {}
        elif line.startswith("ERROR:"):
            err = _codex_error(line, ref)
        elif line in ("codex", "exec", "user"):  # the agent went on after the error
            err = {}
    return session, err


def last_session(agent_log: Path) -> tuple[str | None, bool]:
    """(session id of the last run, whether the run ended on a provider error) for opencode, Claude and Codex."""
    session, err = _scan(agent_log)
    return session, bool(err) and error_kind(err) is not None


def last_error(agent_log: Path) -> dict:
    """The provider error that ended the run (``type``, ``message`` and, for Claude and Codex, ``status`` and
    ``resets_at``), or {} if the run did not end on an error."""
    return _scan(agent_log)[1]


def resume_wait(err: dict, resumes: int, now: float | None = None) -> float:
    """Seconds to wait before a resume: until the reported reset for a usage limit, else the BACKOFF schedule."""
    import time

    kind = error_kind(err) or "outage"
    if kind == "quota" and err.get("resets_at"):
        return max(0.0, float(err["resets_at"]) + RESET_MARGIN_S - (time.time() if now is None else now))
    return float(BACKOFF[kind][min(resumes, len(BACKOFF[kind]) - 1)])


def next_step(alive: bool, submitted: bool, provider_error: bool, resumes: int, max_resumes: int) -> str:
    """The harness recovers from infrastructure failures, never from the model's own decisions."""
    if alive:
        return "wait"
    if submitted or not provider_error:
        return "done"
    return "resume" if resumes < max_resumes else "failed"


def resume_command(p: Player, session: str | None, workdir: Path, otel: str = "http://127.0.0.1:4318") -> list[str]:
    """Continue the same session. With no session id, start a fresh agent that gets the rules and the resume note."""
    if not session:
        return agent_command(p, PROMPT + "\n\n" + RESUME_PROMPT, workdir, otel)
    if p.vendor == "claude":
        return ["claude", "-p", "--resume", session, RESUME_PROMPT, "--model", p.model,
                "--dangerously-skip-permissions", "--output-format", "stream-json", "--verbose"]
    if p.vendor == "codex":  # the flags of agent_command that `codex exec resume` accepts (it has no -C)
        fresh = agent_command(p, RESUME_PROMPT, workdir, otel)
        i = fresh.index("-C")
        return ["codex", "exec", "resume"] + fresh[2:i] + fresh[i + 2:-1] + [session, RESUME_PROMPT]
    return ["opencode", "run", "--standalone", "--session", session, "--model", p.model, "--auto", "--format", "json",
            RESUME_PROMPT]


def _submitted(pdir: Path) -> bool:
    log = pdir / "state/run/actions.jsonl"
    return log.exists() and '"m":"submit"' in log.read_text(encoding="utf-8", errors="replace")


def _start(p: Player, task: str, pdir: Path, cmd: list[str], marker: str, tasks_dir: Path,
           otel: str) -> subprocess.Popen:
    with open(pdir / "agent.log", "ab") as log:  # the child keeps its own copy of the descriptor
        log.write(f"\n=== {marker} ===\n".encode())
        log.flush()
        proc = subprocess.Popen(cmd, cwd=pdir, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True, env=agent_env(p, task, pdir / "state", tasks_dir, otel))
    (pdir / "agent.pid").write_text(str(proc.pid))
    return proc


def _agent_alive(p: Player, pdir: Path) -> bool:
    """The agent of the last launch still runs. A pid from before a reboot can belong to another program now,
    so the command name must match too."""
    try:
        pid = int((pdir / "agent.pid").read_text())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    out = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout
    return p.vendor in out


def resume(task: str, out_dir: Path, players: list[Player], otel: str = "http://127.0.0.1:4318") -> dict[str, object]:
    """After a crash, a reboot or a usage limit: for each player that did not submit, boot its daemon (which
    replays actions.jsonl) and continue its agent session. Returns {label: new pid | "submitted" | "running"}."""
    from railhead import daemon

    out_dir = Path(out_dir).resolve()
    tasks_dir = Path(os.environ.get("RAILHEAD_TASKS", REPO_DIR / "tasks")).resolve()
    os.environ["RAILHEAD_TASKS"] = str(tasks_dir)
    result: dict[str, object] = {}
    for p in players:
        pdir = out_dir / p.label
        if _submitted(pdir):
            result[p.label] = "submitted"
            continue
        if _agent_alive(p, pdir):
            result[p.label] = "running"
            continue
        (pdir / "state").mkdir(parents=True, exist_ok=True)
        daemon.ensure_up(task, pdir / "state")
        session, _ = last_session(pdir / "agent.log")
        proc = _start(p, task, pdir, resume_command(p, session, pdir, otel),
                      f"resume after restart (session {session or 'none: fresh start'})", tasks_dir, otel)
        result[p.label] = proc.pid
        print(json.dumps({"player": p.label, "resume": "restart", "session": session, "pid": proc.pid}), flush=True)
    return result


def supervise(task: str, out_dir: Path, players: list[Player], max_resumes: int = 6, poll: int = 30) -> dict[str, str]:
    """Watch the players; after an infrastructure failure, resume the same session after a wait that depends on
    the failure (outage, stall, or quota: until the reported reset time if there is one). Never blocks one
    player on another's wait, and reaps the processes it starts, so a dead agent never looks alive."""
    import time

    out_dir = Path(out_dir).resolve()
    tasks_dir = Path(os.environ.get("RAILHEAD_TASKS", REPO_DIR / "tasks")).resolve()
    otel = "http://127.0.0.1:4318"
    resumes = {p.label: 0 for p in players}
    status = {p.label: "wait" for p in players}
    due: dict[str, float] = {}
    procs: dict[str, subprocess.Popen] = {}

    def alive(label: str, pid: int) -> bool:
        if label in procs:
            return procs[label].poll() is None  # poll() also reaps the child
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    while any(v != "done" and v != "failed" for v in status.values()):
        for p in players:
            if status[p.label] in ("done", "failed"):
                continue
            pdir = out_dir / p.label
            if p.label in due:
                if time.time() < due[p.label]:
                    continue
                session, _ = last_session(pdir / "agent.log")
                resumes[p.label] += 1
                procs[p.label] = _start(p, task, pdir, resume_command(p, session, pdir, otel),
                                        f"resume {resumes[p.label]} after provider error", tasks_dir, otel)
                del due[p.label]
                print(json.dumps({"player": p.label, "resume": resumes[p.label], "session": session}), flush=True)
                continue
            session, failed = last_session(pdir / "agent.log")
            step = next_step(alive(p.label, int((pdir / "agent.pid").read_text())), _submitted(pdir), failed,
                             resumes[p.label], max_resumes)
            if step == "resume":
                err = last_error(pdir / "agent.log")
                kind = error_kind(err) or "outage"
                wait_s = resume_wait(err, resumes[p.label])
                due[p.label] = time.time() + wait_s
                print(json.dumps({"player": p.label, "error": kind, "resume_in_s": round(wait_s),
                                  "resets_at": err.get("resets_at")}), flush=True)
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
    if argv and argv[0] == "--supervise":  # railhead arena-run --supervise <task> <out_dir> <vendor:model> ...
        print(json.dumps(supervise(argv[1], Path(argv[2]), parse_players(argv[3:]))))
        return 0
    otel = "http://127.0.0.1:4318"
    if "--otel" in argv:
        i = argv.index("--otel")
        otel = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    if argv and argv[0] == "--resume":  # railhead arena-run --resume <task> <out_dir> <vendor:model> ...
        print(json.dumps(resume(argv[1], Path(argv[2]), parse_players(argv[3:]), otel)))
        return 0
    task, out, specs = argv[0], Path(argv[1]), argv[2:]
    pids = launch(task, out, parse_players(specs), otel)
    print({"launched": len(pids), "pids": pids})
    return 0
