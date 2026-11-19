"""A Waybill campaign on the engine: 2 seeds x 2 arms of the short track (horizon cut to 45 days) with stub
agents, 2 games at the same time on 2 accounts. The campaign process is killed after the first games start
and started again: it adopts the live games and finishes the others. Every game must verify (replay ==
live), the games that overlapped must have had their own engine ports and OTLP sinks, and no game may have
two runners."""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from railhead.multi.gen import rotation_schedule

pytestmark = [pytest.mark.integration, pytest.mark.timeout(1800)]

ROSTER = ["claude:claude-opus-5-5", "codex:gpt-6.1-terra", "claude:claude-opus-5-5", "codex:gpt-6.1-terra"]


def _ports(run: Path) -> set[int]:
    cfg = (run / "coord" / "world" / "openttd.cfg").read_text()
    return {int(v) for k, _, v in (x.partition("=") for x in cfg.splitlines())
            if k.strip() in ("server_port", "server_admin_port")}


def _diagnose(tmp_path: Path, doc: dict) -> str:
    """Why a game is not done: its state entry, the end of its runner log and of its runner events."""
    out = []
    for k, g in (doc.get("games") or {}).items():
        out.append(f"== {k}: " + json.dumps({x: g.get(x) for x in ("status", "exclusion", "ended", "restarts",
                                                                     "start_failures", "pid")}))
        if g.get("status") == "done":
            continue
        for f in (tmp_path / "runs" / "campaign" / "logs" / f"{k}.log", tmp_path / "runs" / k / "events.jsonl",
                  tmp_path / "runs" / k / "coord.log"):
            if f.exists():
                out.append(f"-- {f.name}:\n" + f.read_text(errors="replace")[-2500:])
    for f in ("campaign1.log",):
        out.append(f"-- {f}:\n" + (tmp_path / f).read_text(errors="replace")[-2000:])
    return "\n".join(out)


def test_two_games_at_a_time_on_two_accounts_survive_a_campaign_restart(tmp_path):
    stub = tmp_path / "stub.txt"
    partner = tmp_path / "runs" / "waybill-short-s0002-r0-enforced-h45" / "run.json"
    task = tmp_path / "tasks" / "waybill-short-s0005-r1-cheap_talk-h45" / "task.json"
    stub.write_text("\n".join(["rh info", f"@read {partner}", f"@read {task}", *["rh wait days=5\n@sleep 0.5"] * 12])
                    + "\n")
    (tmp_path / "schedule.json").write_text(json.dumps(rotation_schedule("short", [2, 5], ROSTER)))
    cfg = {"schedule": "schedule.json", "tasks_dir": "tasks", "runs_dir": "runs", "concurrency": 2,
           "reserve_usd": 1, "usd_cap": 10, "stagger_s": 1, "poll_s": 1, "horizon_days": 45,
           "players": {p: f"stub:{stub}" for p in ROSTER},
           "accounts": [{"name": "one", "profile": "default", "usd_cap": 5},
                        {"name": "two", "profile": str(tmp_path / "profile-two"), "usd_cap": 5}],
           "runner": ["--all-slots", "--traces", "off", "--grace-s", "5", "--poll", "0.3"]}
    (tmp_path / "campaign.json").write_text(json.dumps(cfg))
    cmd = [sys.executable, "-m", "railhead.multi.campaign", "run", str(tmp_path / "campaign.json")]
    state = tmp_path / "runs" / "campaign" / "state.json"

    with open(tmp_path / "campaign1.log", "wb") as fh:
        p = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT)
    t_end = time.time() + 600
    while time.time() < t_end:  # kill the campaign once two games run
        doc = json.loads(state.read_text()) if state.exists() else {}
        if sum(g["status"] == "running" for g in (doc.get("games") or {}).values()) == 2:
            break
        assert p.poll() is None, (tmp_path / "campaign1.log").read_text()[-3000:]
        time.sleep(0.5)
    else:
        pytest.fail("two games never ran at the same time")
    p.send_signal(signal.SIGKILL)
    p.wait(30)

    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1500, check=False)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    doc = json.loads(state.read_text())
    games = doc["games"]
    assert len(games) == 4 and all(g["status"] == "done" for g in games.values()), \
        _diagnose(tmp_path, doc) + "\n-- campaign2:\n" + r.stdout[-3000:]
    assert {g["account"] for g in games.values()} == {"one", "two"}
    assert all(g["restarts"] == 0 for g in games.values()), _diagnose(tmp_path, doc)  # adopted, not resumed
    runs = {k: tmp_path / "runs" / k for k in games}
    spans = {}
    for k, run in runs.items():
        res = json.loads((run / "result.json").read_text())
        assert res["verify"]["clean"] and res["verify"]["replay_equals_live"] is True, (k, res["verify"])
        assert res["run"]["ended"] in ("horizon", "all_submitted")
        assert "profile-two" not in (run / "result.json").read_text() and "one" not in json.dumps(res["run"])
        m = json.loads((run / "run.json").read_text())
        for s in m["seats"]:  # adopted, never started twice
            assert [x["kind"] for x in s["launches"]] == ["launch"], (k, s)
        if m["isolation"] == "sandbox":  # no agent reads another game or task of the campaign
            text = (run / "agents" / "s0-stub" / "agent.log").read_text()
            assert text.count("read denied") == 2 and "read ok" not in text, k
        spans[k] = (m["started"], m["started"] + m["elapsed_s"], _ports(run), m["otel_port"])
        assert games[k]["code_sha"] is None or len(games[k]["code_sha"]) == 40
    overlapping = [(a, b) for a in spans for b in spans if a < b
                   and spans[a][0] < spans[b][1] and spans[b][0] < spans[a][1]]
    assert overlapping
    for a, b in overlapping:  # games that ran at the same time had their own engine ports and sinks
        assert not spans[a][2] & spans[b][2] and spans[a][3] != spans[b][3]
    summary = json.loads(r.stdout[r.stdout.index("{\n"):])
    assert summary["games"] == {"done": 4} and summary["stop"] is None
