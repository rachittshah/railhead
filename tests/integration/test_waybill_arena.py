"""The Waybill arena runner on the engine: three stub agents play the short track (horizon cut to 60 days)
through `railhead waybill-run`, in both arms. The enforced run is killed in the middle (the runner and the
agents with SIGKILL, the coordinator stopped) and resumed from the log. Both runs must verify (replay ==
live) and give the contract statistics, the scores and the cost per agent."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from railhead.actionlog import read_log
from railhead.client import agent_socket_path
from railhead.multi import arena as wa
from railhead.multi.gen import gen_track

pytestmark = [pytest.mark.integration, pytest.mark.timeout(1800)]

HORIZON = 60
D = 74


@pytest.fixture(scope="module")
def tasks(tmp_path_factory):
    out = tmp_path_factory.mktemp("wb-arena-tasks")
    specs = gen_track("short", 7, out, rotations=[0], horizon_days=HORIZON)
    return {s.objective["arm"]: out / s.task_id for s in specs}


def scripts(d: Path, run: Path) -> list[str]:
    """A offers B a supply contract that nobody delivers (broken at day 30); B accepts it; C sends a message
    and a payment, and tries the socket of A and the merged log (the sandbox denies both)."""
    waits = []
    for _ in range(HORIZON // 5 + 2):
        waits += ["rh wait days=5", "@sleep 0.4"]
    a = ["rh info", ("rh offer to=B seller=me buyer=B cargo=0 industry=0 quantity=10 window_start=+0d "
                    "deadline=+30d price=1000 penalty_per_unit=10 expires_tick=+20d"), *waits]
    b = ["rh wait days=2 until=offer_received", "rh contracts", "rh accept contract_id=C1", *waits]
    coord = run.resolve() / "coord"
    c = ["rh market", 'rh msg to=A text="hello"', "rh pay to=A amount=1000",
         f"@connect {agent_socket_path(coord, 0)}", f"@read {coord / 'run' / 'actions.jsonl'}", *waits]
    out = []
    for name, lines in (("a", a), ("b", b), ("c", c)):
        p = d / f"{name}.txt"
        p.write_text("\n".join(lines) + "\n")
        out.append(f"stub:{p}")
    return out


def _kill(pid: int, sig: int, group: bool = True) -> None:
    try:
        (os.killpg if group else os.kill)(pid, sig)
    except OSError:
        pass


def check(run: Path, res: dict, arm: str) -> None:
    assert res["verify"]["clean"], res["verify"]
    assert res["verify"]["replay_equals_live"] is True, res["verify"]
    assert res["arm"] == arm and res["run"]["ended"] in ("horizon", "all_submitted")
    recs = [r.raw for r in read_log(run / "coord/run/actions.jsonl")]
    assert max(r.get("end_ticks", r["ticks"]) for r in recs) == recs[0]["ticks"] + HORIZON * D
    con = res["contracts"]
    assert (con["signed"], con["broken"], con["kept"]) == (1, 1, 0), con
    rows = {r["slot"]: r for r in res["companies"]}
    assert rows[3]["agent"] is None and rows[3]["agent_status"] == "absent"
    for r in rows.values():
        assert r["score"] == r["net_worth"] + r["ledger_balance"]
    assert rows[2]["ledger_balance"] == -1000 and rows[0]["contracts"]["broken"] == 1
    if arm == "enforced":  # the seller paid the penalty for the full shortfall (no bond)
        assert rows[0]["ledger_balance"] == 1000 - 10 * 10 + 0
    else:  # cheap talk: only the payment moved money
        assert rows[0]["ledger_balance"] == 1000
    for s in ("s0-stub", "s1-stub", "s2-stub"):
        c = res["cost"]["by_agent"][s]
        assert c["source"] == "otlp" and c["requests"] > 0 and c["cost_usd"] > 0 and c["by_year"]
    assert res["cost"]["usd_per_game_year"] > 0
    log = (run / "agents/s2-stub/agent.log").read_text()
    if res["run"]["isolation"] == "sandbox":
        assert "connect denied" in log and "read denied" in log
    # the merged log never reaches the agents' directories
    assert not list((run / "agents").rglob("actions.jsonl"))


def test_enforced_run_is_killed_resumed_and_verified(tasks, tmp_path):
    run = tmp_path / "run"
    players = scripts(tmp_path, run)
    cmd = [sys.executable, "-m", "railhead.multi.arena", "run", str(tasks["enforced"]), str(run), *players,
           "--turn-s", "120", "--poll", "0.3", "--grace-s", "10", "--max-backoff-s", "1"]
    with open(tmp_path / "runner1.log", "wb") as fh:
        p = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
    start = None
    t_end = time.time() + 600
    while time.time() < t_end:  # kill in the middle of the game, after the contract is signed
        clock = [json.loads(x) for x in (run / "clock.jsonl").read_text().splitlines()] \
            if (run / "clock.jsonl").exists() else []
        if clock:
            start = start or clock[0]["ticks"]
            if clock[-1]["ticks"] >= start + 15 * D:
                break
        assert p.poll() is None, (tmp_path / "runner1.log").read_text()[-3000:]
        time.sleep(0.2)
    else:
        pytest.fail("the game did not reach day 15")
    _kill(p.pid, signal.SIGKILL)
    p.wait(30)
    for pid_file in (run / "agents").glob("*/agent.pid"):
        _kill(int(pid_file.read_text()), signal.SIGKILL)
    coord_pid = int((run / "coord.pid").read_text())
    _kill(coord_pid, signal.SIGTERM, group=False)
    for _ in range(300):
        if not wa._alive(coord_pid):
            break
        time.sleep(0.1)
    killed_at = [json.loads(x) for x in (run / "clock.jsonl").read_text().splitlines()][-1]["ticks"]
    assert killed_at < start + HORIZON * D

    r = subprocess.run([sys.executable, "-m", "railhead.multi.arena", "resume", str(run)], capture_output=True,
                       text=True, timeout=900, check=False)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    res = json.loads((run / "result.json").read_text())
    check(run, res, "enforced")
    m = json.loads((run / "run.json").read_text())
    for s in m["seats"]:
        assert [x["kind"] for x in s["launches"]] == ["launch", "resume"], s
    assert "coordinator up" in r.stdout  # a new coordinator rebuilt the world from the log


def test_cheap_talk_run_through_the_runner(tasks, tmp_path):
    run = tmp_path / "run"
    players = scripts(tmp_path, run)
    res = wa.Runner.create(tasks["cheap_talk"], run, players, turn_s=120, poll_s=0.3, grace_s=10).run()
    check(run, res, "cheap_talk")
