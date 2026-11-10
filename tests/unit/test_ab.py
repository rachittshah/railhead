"""A/B harness: plan checks, cell layout and order, the resumable run state machine (fake processes), collect,
and the paired report."""

import json
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from railhead import ab


def _checkout(root, name, tasks=("t1", "t2")):
    co = root / name
    (co / ".venv" / "bin").mkdir(parents=True)
    (co / ".venv" / "bin" / "railhead").write_text("#!/bin/sh\n")
    (co / ".venv" / "bin" / "python").write_text("#!/bin/sh\n")
    for t in tasks:
        (co / "tasks" / t).mkdir(parents=True)
        (co / "tasks" / t / "task.json").write_text(json.dumps({"task_id": t, "horizon_days": 100,
                                                                "briefing": "b", "objective": {}}))
        (co / "tasks" / t / "start.sav").write_bytes(b"sav")
    return co


def _plan(tmp_path, **over):
    a, b = _checkout(tmp_path, "A"), _checkout(tmp_path, "B")
    raw = {"name": "x", "hypothesis": "B builds more rail", "primary_metric": "vehicles_rail",
           "arms": [{"name": "control", "checkout": str(a)}, {"name": "treatment", "checkout": str(b)}],
           "models": ["codex:gpt-6-sol", "claude:claude-haiku-5-5"], "tasks": ["t1", "t2"], "replicates": 1,
           "parallel": 2, "billing": "subscription", "seed": 7}
    raw.update(over)
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(raw))
    return p


def test_plan_loads_and_hash_ignores_formatting(tmp_path):
    p = _plan(tmp_path)
    plan = ab.load_plan(p)
    assert [a.name for a in plan.arms] == ["control", "treatment"] and plan.replicates == 1
    h = plan.hash
    p.write_text(json.dumps(json.loads(p.read_text()), indent=4))
    assert ab.load_plan(p).hash == h


def test_plan_toml_is_accepted(tmp_path):
    _plan(tmp_path)
    t = tmp_path / "plan.toml"
    t.write_text(f'name = "x"\nhypothesis = "h"\nprimary_metric = "net_worth"\nmodels = ["codex:gpt-6-sol"]\n'
                 f'tasks = ["t1"]\nbilling = "api"\n[[arms]]\nname = "a"\ncheckout = "{tmp_path / "A"}"\n'
                 f'[[arms]]\nname = "b"\ncheckout = "{tmp_path / "B"}"\n')
    assert ab.load_plan(t).billing == "api"


def test_plan_rejects_missing_preregistration_unknown_metric_and_missing_tasks(tmp_path):
    p = _plan(tmp_path, hypothesis="", primary_metric="vibes", tasks=["t1", "nope"], billing="")
    with pytest.raises(ValueError) as e:
        ab.load_plan(p)
    msg = str(e.value)
    assert "hypothesis" in msg and "vibes" in msg and "nope" in msg and "billing" in msg


def test_task_map_lets_an_arm_use_a_variant_task(tmp_path):
    p = _plan(tmp_path)
    raw = json.loads(p.read_text())
    _ = _checkout(tmp_path, "C", tasks=("t1-v2", "t2"))
    raw["arms"][1] = {"name": "treatment", "checkout": str(tmp_path / "C"), "task_map": {"t1": "t1-v2"}}
    p.write_text(json.dumps(raw))
    plan = ab.load_plan(p)
    assert plan.arms[1].task_id("t1") == "t1-v2" and plan.arms[1].task_id("t2") == "t2"


def test_cells_layout_and_seeded_order(tmp_path):
    plan = ab.load_plan(_plan(tmp_path, replicates=2))
    cs = ab.cells(plan)
    assert len(cs) == 2 * 2 * 2 * 2
    c = next(c for c in cs if c.model == "codex:gpt-6-sol" and c.rep == 2 and c.arm == "control" and c.task == "t1")
    assert c.id == "control/t1/codex-gpt-6-sol/r2"
    assert c.player_dir(tmp_path / "out") == tmp_path / "out/control/t1/codex-gpt-6-sol/r2/gpt-6-sol"
    o1, o2 = ab.run_order(cs, 7), ab.run_order(list(reversed(cs)), 7)
    assert [x.id for x in o1] == [x.id for x in o2] != sorted(x.id for x in cs)
    assert [x.id for x in ab.run_order(cs, 8)] != [x.id for x in o1]


def test_arm_env_drops_other_checkouts_settings(tmp_path):
    plan = ab.load_plan(_plan(tmp_path))
    arm = plan.arms[1]
    arm.env = {"RAILHEAD_X": "1"}
    env = ab.arm_env(arm, base={"PATH": "/usr/bin", "RAILHEAD_TASKS": "/elsewhere", "RAILHEAD_STATE": "/s",
                                "RAILHEAD_OPENTTD": "/eng", "VIRTUAL_ENV": "/v", "PYTHONPATH": "/p"})
    assert env["RAILHEAD_TASKS"] == str(arm.tasks) and "RAILHEAD_STATE" not in env
    assert env["RAILHEAD_OPENTTD"] == "/eng" and "VIRTUAL_ENV" not in env and "PYTHONPATH" not in env
    assert env["PATH"].startswith(str(arm.checkout / ".venv" / "bin")) and env["RAILHEAD_X"] == "1"


# ----------------------------------------------------------------------------------- fake process layer

class FakeProv(ab.Provenance):
    def arm(self, arm):
        return {"own_code": True, "git": {"commit": "c0ffee", "dirty": False, "diff_sha256": "d"},
                "engine_sha256": "e", "prompt_sha256": "p"}

    def harness(self, vendor):
        return {"cli": vendor, "version": "1.0"}


class FakeRunner(ab.Runner):
    """Writes the files a real arena-run would write. ``plan_nw`` gives the verified net worth per cell."""

    def __init__(self, plan, out, nw=lambda c: 1000, fail_launch=(), supervise_status="done"):
        super().__init__(plan, out)
        self.nw, self.fail_launch, self.status = nw, set(fail_launch), supervise_status
        self.calls = []
        self.lock = threading.Lock()
        self.active, self.max_active, self.active_keys = 0, 0, []
        self.alive_pids: set[int] = set()

    def _log(self, *x):
        with self.lock:
            self.calls.append(x)

    def launch(self, cell):
        self._log("launch", cell.id)
        if cell.id in self.fail_launch:
            return 1
        pdir = cell.player_dir(self.out)
        (pdir / "state" / "run").mkdir(parents=True, exist_ok=True)
        (pdir / "agent.pid").write_text("999999")
        return 0

    def resume(self, cell):
        self._log("resume", cell.id)
        return 0

    def supervise(self, cell, on_start=None):
        self._log("supervise", cell.id)
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.active_keys.append(sorted(k for k in getattr(self, "_running", set())))
            self._running = getattr(self, "_running", set()) | {cell.key}
        if on_start:
            on_start(4242)
        time.sleep(0.05)
        pdir = cell.player_dir(self.out)
        (pdir / "state" / "run" / "actions.jsonl").write_text(
            json.dumps({"kind": "header", "ticks": 0, "checksum": {"date": 1, "ticks": 0}}) + "\n"
            + json.dumps({"kind": "meta", "m": "submit", "a": {}, "ticks": 0}, separators=(",", ":")) + "\n")
        with open(cell.dir(self.out) / "supervise.log", "a") as fh:
            fh.write(json.dumps({cell.label: self.status}) + "\n")
        with self.lock:
            self.active -= 1
            self._running.discard(cell.key)
        return 0

    def down(self, cell):
        self._log("down", cell.id)
        return 0

    def verify(self, cell):
        self._log("verify", cell.id)
        (cell.player_dir(self.out) / "verify").mkdir(parents=True, exist_ok=True)
        (cell.player_dir(self.out) / "verify" / "reward.json").write_text(json.dumps(
            {"net_worth": self.nw(cell), "net_worth_horizon": 1, "valid": 1, "reward": 0.5}))
        return 0

    def export(self, cell, dest):
        self._log("export", cell.id)
        dest.write_text(json.dumps({"task": cell.task, "results": [
            {"player": cell.label, "status": "verified", "net_worth": self.nw(cell), "score_k50": 0.5, "valid": 1},
            {"player": "baseline", "status": "certificate", "net_worth": 10}],
            "curves": {cell.label: []}, "fleet_mix": {cell.label: {"road": 1}}}))
        return 0

    def alive(self, pid, needle):
        return pid in self.alive_pids


def _exp(tmp_path, runner_kw=None, **plan_over):
    plan = ab.load_plan(_plan(tmp_path, **plan_over))
    out = tmp_path / "out"
    runner = FakeRunner(plan, out, **(runner_kw or {}))
    return ab.Experiment(plan, out, runner=runner, prov=FakeProv(), poll=0.01), runner


@pytest.fixture(autouse=True)
def _no_sink(monkeypatch):
    monkeypatch.setattr(ab.Experiment, "start_sink", lambda self: setattr(self, "sink", {"info": {"started_by": "test"}}))


def test_run_launches_every_cell_once_then_never_reruns(tmp_path):
    exp, r = _exp(tmp_path)
    res = exp.run()
    assert len(res) == 8 and set(res.values()) == {ab.FINISHED}
    assert sum(1 for c in r.calls if c[0] == "launch") == 8
    assert sum(1 for c in r.calls if c[0] == "down") == 8  # every cell's daemon is stopped
    m = ab.read_manifest(ab.cells(exp.plan)[0], exp.out)
    for k in ("plan_hash", "arm", "task", "model", "vendor", "harness", "arm_info", "task_files", "started_at",
              "ended_at", "exit_state", "resume_count", "billing", "supervisor_pid", "windows"):
        assert k in m, k
    assert m["billing"] == "subscription" and m["exit_state"] == "submitted"
    r.calls.clear()
    exp.run()
    assert not [c for c in r.calls if c[0] in ("launch", "resume", "supervise")]
    ev = [e["event"] for e in exp.events.read()]
    assert ev.count("started") == 8 and ev.count("finished") == 8


def test_parallelism_is_bounded_and_same_task_model_never_overlaps(tmp_path):
    exp, r = _exp(tmp_path, parallel=3)
    exp.run()
    assert r.max_active <= 3
    assert all(len(ks) == len(set(ks)) for ks in r.active_keys)


def test_run_order_follows_the_recorded_seed(tmp_path):
    exp, r = _exp(tmp_path, parallel=1)
    exp.run()
    info = json.loads((exp.out / "run.json").read_text())
    launched = [c[1] for c in r.calls if c[0] == "launch"]
    assert launched == info["order"] and info["seed"] == 7


def test_changed_plan_is_refused_for_the_same_out_dir(tmp_path):
    exp, _ = _exp(tmp_path)
    exp.run()
    raw = json.loads(exp.plan.path.read_text())
    raw["hypothesis"] = "changed my mind"
    exp.plan.path.write_text(json.dumps(raw))
    exp2 = ab.Experiment(ab.load_plan(exp.plan.path), exp.out, runner=FakeRunner(exp.plan, exp.out),
                         prov=FakeProv())
    with pytest.raises(SystemExit):
        exp2.run()


def test_infra_failure_is_marked_and_retried_only_on_request(tmp_path):
    plan_path = _plan(tmp_path)
    plan = ab.load_plan(plan_path)
    bad = ab.cells(plan)[0].id
    out = tmp_path / "out"
    r = FakeRunner(plan, out, fail_launch=[bad])
    exp = ab.Experiment(plan, out, runner=r, prov=FakeProv(), poll=0.01)
    res = exp.run()
    assert res[bad] == ab.FAILED and ab.cell_state(ab.cells(plan)[0], out) == ab.FAILED
    r2 = FakeRunner(plan, out)
    exp2 = ab.Experiment(plan, out, runner=r2, prov=FakeProv(), poll=0.01)
    exp2.run()
    assert not [c for c in r2.calls if c[0] == "launch"]  # failed cell skipped without --retry-failed
    exp2.run(retry_failed=True)
    assert [c[1] for c in r2.calls if c[0] == "launch"] == [bad]
    assert ab.cell_state(ab.cells(plan)[0], out) == ab.FINISHED


def test_provider_failure_from_the_supervisor_is_an_infra_failure(tmp_path):
    exp, _ = _exp(tmp_path, runner_kw={"supervise_status": "failed"})
    res = exp.run()
    assert set(res.values()) == {ab.FAILED}
    assert "max resumes" in ab.read_manifest(ab.cells(exp.plan)[0], exp.out)["exit_state"]


def test_interrupted_cell_is_resumed_not_relaunched(tmp_path):
    exp, r = _exp(tmp_path)
    c = ab.cells(exp.plan)[0]
    pdir = c.player_dir(exp.out)
    (pdir / "state" / "run").mkdir(parents=True)
    (pdir / "agent.pid").write_text("123")
    (pdir / "agent.log").write_text("codex output, then the machine rebooted\n")
    ab._write_json(ab.manifest_path(c, exp.out), {"state": ab.RUNNING, "resume_count": 0,
                                                  "windows": [[1.0, None]], "started_at": "t0"})
    exp.run()
    assert ("resume", c.id) in r.calls and ("launch", c.id) not in r.calls
    m = ab.read_manifest(c, exp.out)
    assert m["resume_count"] == 1 and m["state"] == ab.FINISHED and m["started_at"] == "t0"
    assert any(e["event"] == "resumed" and e["cell"] == c.id for e in exp.events.read())


def test_running_agent_is_attached_not_resumed(tmp_path):
    exp, r = _exp(tmp_path)
    c = ab.cells(exp.plan)[0]
    pdir = c.player_dir(exp.out)
    (pdir / "state" / "run").mkdir(parents=True)
    (pdir / "agent.pid").write_text("555")
    r.alive_pids.add(555)
    ab._write_json(ab.manifest_path(c, exp.out), {"state": ab.RUNNING, "windows": [[1.0, None]]})
    exp.run()
    assert ("resume", c.id) not in r.calls and ("supervise", c.id) in r.calls


def test_ended_cleanly_reads_claude_and_script_transcripts(tmp_path):
    log = tmp_path / "agent.log"
    log.write_text(json.dumps({"type": "result", "is_error": False, "session_id": "s"}) + "\n")
    assert ab.ended_cleanly("claude", log)
    log.write_text(json.dumps({"type": "assistant", "session_id": "s"}) + "\n")
    assert not ab.ended_cleanly("claude", log)
    log.write_text("$ rh status\n=== script finished ===\n")
    assert ab.ended_cleanly("script", log) and not ab.ended_cleanly("codex", log)


def test_collect_verifies_with_each_arm_and_writes_exports_with_provenance(tmp_path):
    exp, r = _exp(tmp_path, runner_kw={"nw": lambda c: 2000 if c.arm == "treatment" else 1000})
    exp.run()
    out = exp.collect()
    assert sum(1 for c in r.calls if c[0] == "verify") == 8
    doc = json.loads((exp.out / "exports" / "treatment.json").read_text())
    assert [m["task"] for m in doc["maps"]] == ["t1", "t2"]
    row = next(x for x in doc["maps"][0]["results"] if x["status"] == "verified")
    assert row["billing"] == "subscription" and row["arm"] == "treatment" and row["plan_hash"] == exp.plan.hash
    assert "behaviour" in row and row["end"] == "submitted"
    assert sum(1 for x in doc["maps"][0]["results"] if x["player"] == "baseline") == 1
    r.calls.clear()
    exp.collect()
    assert not [c for c in r.calls if c[0] == "verify"]  # a verified game is not verified again
    assert set(out["exports"]) == {"control", "treatment"}


# ------------------------------------------------------------------------------------------------- report

def test_report_direction_is_treatment_minus_control_and_small_n_is_stated(tmp_path):
    exp, _ = _exp(tmp_path, runner_kw={"nw": lambda c: 2000 if c.arm == "treatment" else 1000},
                  primary_metric="net_worth")
    exp.run()
    exp.collect()
    rep = ab.build_report(exp.plan, exp.out, boot=200)
    nw = rep["comparisons"][0]["metrics"][0]
    assert nw["metric"] == "net_worth" and rep["comparisons"][0]["b"] == "treatment"
    p = nw["pooled"]
    assert p["n"] == 2 and p["diff"] == 1000 and p["lo"] == p["hi"] == 1000 and not p["tied"]
    assert p["wins_b"] == 2 and p["sign_p"] == 0.5
    md = ab.render_markdown(rep)
    assert md.index("Hypothesis") < md.index("treatment - control")
    assert "too small to conclude anything" in md


def test_report_aa_identical_results_are_exactly_zero_and_tied(tmp_path):
    exp, _ = _exp(tmp_path)
    exp.run()
    exp.collect()
    rep = ab.build_report(exp.plan, exp.out, boot=200)
    assert rep["arms_run_identical_code"]
    for m in rep["comparisons"][0]["metrics"]:
        if m["pooled"]["n"] and not m["metric"].startswith("model_"):
            assert m["pooled"]["diff"] == 0 and m["pooled"]["tied"], m["metric"]
    assert "TIED on every game metric" in ab.render_markdown(rep)


def test_compare_pools_models_within_task_and_bootstraps_tasks():
    a = {("t1", "m1"): {"x": 1.0}, ("t1", "m2"): {"x": 3.0}, ("t2", "m1"): {"x": 0.0}, ("t3", "m1"): {"x": 5.0}}
    b = {("t1", "m1"): {"x": 2.0}, ("t1", "m2"): {"x": 6.0}, ("t2", "m1"): {"x": 0.0}}
    out = ab.compare(a, b, "x", 500, np.random.default_rng(0))
    p = out["pooled"]
    assert p["n"] == 2 and p["n_units"] == 3 and p["task_diffs"] == [2.0, 0.0]  # t1: mean(1, 3); t3 unpaired
    assert p["sign_p"] == 1.0 and p["n_nonzero"] == 1
    assert out["per_model"]["m2"]["n"] == 1 and out["per_model"]["m2"]["diff"] == 3.0


def test_small_n_note():
    assert "too small" in ab.small_n_note(3) and "0.25" in ab.small_n_note(3)
    assert "too small" not in ab.small_n_note(8)
    assert "nothing can be compared" in ab.small_n_note(0)


def test_collect_reverifies_a_crashed_verifier_and_keeps_the_old_verdict(tmp_path):
    exp, r = _exp(tmp_path)
    exp.run()
    c = ab.cells(exp.plan)[0]
    vdir = c.player_dir(exp.out) / "verify"
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "reward.json").write_text(json.dumps({"reward": 0.0, "valid": 0, "error": 1}))
    exp.collect()
    assert [x[1] for x in r.calls if x[0] == "verify"].count(c.id) == 1
    assert json.loads((vdir / "reward.json").read_text())["valid"] == 1
    assert len(list(vdir.glob("reward.error-*.json"))) == 1


def test_secondary_metrics_are_checked_and_listed_after_the_primary(tmp_path):
    with pytest.raises(ValueError):
        ab.load_plan(_plan(tmp_path / "a", secondary_metrics=["nope"]))
    exp, _ = _exp(tmp_path / "b", runner_kw={"nw": lambda c: 5}, secondary_metrics=["net_worth"])
    exp.run()
    exp.collect()
    rep = ab.build_report(exp.plan, exp.out, boot=50)
    assert [m["metric"] for m in rep["comparisons"][0]["metrics"][:2]] == ["vehicles_rail", "net_worth"]
    assert rep["categorical"]["end"]["control"] == {"submitted": 4}
    assert "Secondary metrics: `net_worth`" in ab.render_markdown(rep)


def test_report_warns_when_an_arm_changed_code_during_the_run(tmp_path):
    exp, _ = _exp(tmp_path)
    exp.run()
    exp.collect()
    c = ab.cells(exp.plan)[0]
    m = ab.read_manifest(c, exp.out)
    m["arm_info"]["git"]["diff_sha256"] = "other"
    ab._write_json(ab.manifest_path(c, exp.out), m)
    rep = ab.build_report(exp.plan, exp.out, boot=50)
    assert any("code changed during the run" in w for w in rep["task_warnings"])
    assert not rep["arms_run_identical_code"]


def test_git_state_saves_one_patch_per_dirty_tree_and_never_overwrites(tmp_path):
    import subprocess

    repo = tmp_path / "r"
    repo.mkdir()
    for cmd in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True)
    (repo / "a.txt").write_text("1\n")
    subprocess.run(["git", "-C", str(repo), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "c"], check=True)
    clean = ab.git_state(repo, tmp_path / "code", "x")
    assert clean["dirty"] is False and "patch" not in clean
    (repo / "a.txt").write_text("2\n")
    (repo / "new.txt").write_text("n\n")
    d = ab.git_state(repo, tmp_path / "code", "x")
    assert d["dirty"] and d["untracked_files"] == 1 and d["diff_sha256"] != clean["diff_sha256"]
    patch = Path(d["patch"])
    assert "+2" in patch.read_text()
    assert "new.txt" in json.loads(patch.with_suffix(".untracked.json").read_text())["files"]
    patch.write_text("kept")
    ab.git_state(repo, tmp_path / "code", "x")
    assert patch.read_text() == "kept"
