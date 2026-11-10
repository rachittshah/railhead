"""A/B harness end to end with a real engine and no model: an A/A test (two arms, the same checkout) played by
the scripted player. The run is killed mid-game and resumed; the report must show exactly zero differences."""

import json
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
RAILHEAD = REPO / ".venv" / "bin" / "railhead"
TASKS = ["tycoon-dev-0010", "tycoon-dev-0070"]  # 64x64 maps, 365-day horizon: fast to play and to verify
SCRIPT = """# A/A scripted player: a fixed sequence of rh commands, no model
rh status
rh company loan amount=100000
rh vehicle start vehicle=99999
rh air airport tile=10,10 type=small dry_run=true
@sleep 8
rh wait days=30
rh wait days=60
rh submit confirm=yes
"""
SLEEP_STEP = 4  # script.progress == 4: the four rh lines are done and the player sleeps


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _railhead(*args, timeout=900):
    return subprocess.run([str(RAILHEAD), *map(str, args)], capture_output=True, text=True, timeout=timeout,
                          stdin=subprocess.DEVNULL, check=False)


def test_aa_experiment_is_resumable_and_reports_exact_zero(tmp_path):
    if not os.environ.get("RAILHEAD_OPENTTD"):
        pytest.skip("set RAILHEAD_OPENTTD")
    script = tmp_path / "aa.rh"
    script.write_text(SCRIPT)
    plan = tmp_path / "plan.json"
    arm = {"checkout": str(REPO), "supervise_args": ["--poll", "1"]}
    plan.write_text(json.dumps({
        "name": "aa-test", "hypothesis": "A/A: identical arms give identical games",
        "primary_metric": "net_worth", "arms": [{"name": "a1", **arm}, {"name": "a2", **arm}],
        "models": [f"script:{script}"], "tasks": TASKS, "replicates": 1, "parallel": 2,
        "billing": "none (scripted player)", "seed": 11}))
    out = tmp_path / "out"
    cells = [out / a / t / "script-aa.rh" / "r1" for a in ("a1", "a2") for t in TASKS]
    try:
        # 1. start the run, wait until two players sleep mid-game, then kill the harness and those players
        first = subprocess.Popen([str(RAILHEAD), "ab", "run", str(plan), "--out", str(out)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        deadline, sleeping = time.time() + 240, []
        while time.time() < deadline and len(sleeping) < 2:
            sleeping = [c for c in cells if (c / "aa.rh" / "script.progress").exists()
                        and (c / "aa.rh" / "script.progress").read_text().strip() == str(SLEEP_STEP)]
            time.sleep(0.5)
        assert len(sleeping) == 2, "two cells should run in parallel and reach the sleep step"
        os.killpg(first.pid, signal.SIGKILL)  # the harness and its supervisors die like in a crash
        first.wait(timeout=30)
        for c in sleeping:
            pid = int((c / "aa.rh" / "agent.pid").read_text())
            os.kill(pid, signal.SIGKILL)
        # 2. run again: the two interrupted cells are resumed, the other two are launched, nothing is rerun
        r = _railhead("ab", "run", plan, "--out", out)
        assert r.returncode == 0, r.stdout + r.stderr
        events = [json.loads(x) for x in (out / "events.jsonl").read_text().splitlines()]
        resumed = sorted(e["cell"] for e in events if e["event"] == "resumed")
        assert resumed == sorted(str(c.relative_to(out)) for c in sleeping)
        assert sum(e["event"] == "started" for e in events) == 4
        for c in cells:
            m = json.loads((c / "manifest.json").read_text())
            assert m["state"] == "finished" and m["exit_state"] == "submitted"
            assert m["resume_count"] == (1 if c in sleeping else 0)
            assert m["arm_info"]["own_code"] and m["arm_info"]["engine_sha256"] and m["arm_info"]["git"]["commit"]
            assert m["task_files"]["start_sav_sha256"] and m["harness"]["cli"].startswith("railhead")
        r = _railhead("ab", "run", plan, "--out", out)  # a third run does nothing
        assert r.returncode == 0
        assert sum(json.loads(x)["event"] == "started" for x in (out / "events.jsonl").read_text().splitlines()) == 4
        # the same script played the same game in both arms
        for t in TASKS:
            logs = [[(x["kind"], x.get("m"), x.get("a"), x.get("ok")) for x in map(json.loads, (
                out / a / t / "script-aa.rh" / "r1" / "aa.rh" / "state/run/actions.jsonl").read_text().splitlines())]
                for a in ("a1", "a2")]
            assert logs[0] == logs[1]
        # 3. collect (each arm's verifier) and report
        r = _railhead("ab", "collect", plan, "--out", out)
        assert r.returncode == 0, r.stdout + r.stderr
        r = _railhead("ab", "report", plan, "--out", out)
        assert r.returncode == 0, r.stdout + r.stderr
        rep = json.loads((out / "report.json").read_text())
        assert rep["arms_run_identical_code"]
        metrics = {m["metric"]: m["pooled"] for m in rep["comparisons"][0]["metrics"]}
        nw = metrics["net_worth"]
        assert nw["n"] == 2 and nw["diff"] == 0 and nw["lo"] == 0 == nw["hi"] and nw["tied"]
        for name, p in metrics.items():
            if p["n"] and not name.startswith("model_"):
                assert p["diff"] == 0 and p["tied"], name
        md = (out / "report.md").read_text()
        assert "TIED on every game metric" in md and "too small to conclude anything" in md
        assert md.index("Hypothesis") < md.index("a2 - a1")
        rows = [r for m in json.loads((out / "exports" / "a1.json").read_text())["maps"] for r in m["results"]
                if r["status"] != "certificate"]
        assert {r["status"] for r in rows} == {"verified"} and all(r["valid"] == 1 for r in rows)
        assert all(r["billing"] == "none (scripted player)" and r["end"] == "submitted" for r in rows)
        assert all(r["behaviour"]["failed_rate"] > 0 for r in rows)  # the scripted failing action is counted
    finally:
        for c in cells:  # leave no daemon, engine, player or sink running
            pdir = c / "aa.rh"
            if (pdir / "state").exists():
                subprocess.run([str(RAILHEAD), "down"], env={**os.environ, "RAILHEAD_STATE": str(pdir / "state")},
                               capture_output=True, timeout=120, check=False)
            if (pdir / "agent.pid").exists():
                pid = int((pdir / "agent.pid").read_text())
                if _alive(pid):
                    os.kill(pid, signal.SIGKILL)
        sink = out / "otel" / "sink.json"
        if sink.exists():
            pid = json.loads(sink.read_text())["pid"]
            if _alive(pid):
                ps = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True,
                                    check=False).stdout
                if "otel serve" in ps:
                    os.kill(pid, signal.SIGTERM)
