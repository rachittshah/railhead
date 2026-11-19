"""The Waybill campaign scheduler (railhead.multi.campaign) with a fake launcher and fake tasks: no engine,
no harness, no login."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from railhead.multi import arena as wa
from railhead.multi import campaign as wc
from railhead.multi.gen import SeedRejected, rotation_schedule

ROSTER = ["claude:claude-opus-5-5", "codex:gpt-6.1-terra", "claude:claude-opus-5-5", "codex:gpt-6.1-terra"]


class Proc:
    n = 1000

    def __init__(self) -> None:
        Proc.n += 1
        self.pid, self.code = Proc.n, None

    def poll(self):
        return self.code


class World:
    """Fake tasks, a fake launcher and a clock; ``finish`` writes what a runner writes at the end."""

    def __init__(self, tmp: Path, reject=(), **cfg) -> None:
        self.tmp, self.reject, self.t = tmp, set(reject), 1000.0
        self.launched: list[tuple[list[str], dict, Proc]] = []
        self.generated: list[tuple[int, list[int]]] = []
        (tmp / "schedule.json").write_text(json.dumps(rotation_schedule("short", [2, 5, 6, 7], ROSTER)))
        doc = {"schedule": "schedule.json", "tasks_dir": "tasks", "runs_dir": "runs", "concurrency": 2,
               "reserve_usd": 20, "stagger_s": 0, "poll_s": 0,
               "accounts": [{"name": "main", "profile": "default", "usd_cap": 250},
                            {"name": "second", "profile": str(tmp / "p-second"), "usd_cap": 400},
                            {"name": "third", "profile": str(tmp / "p-third"), "usd_cap": 300}],
               "runner": ["--all-slots", "--turn-s", "300"]} | cfg
        (tmp / "campaign.json").write_text(json.dumps(doc))
        self.cfg = wc.load_config(tmp / "campaign.json")

    def campaign(self, alive=lambda pid, rd: False) -> wc.Campaign:
        return wc.Campaign(self.cfg, launcher=self.launch, gen_fn=self.gen, alive_fn=alive, now=lambda: self.t)

    def launch(self, cmd, log, env):
        p = Proc()
        self.launched.append((cmd, env, p))
        return p

    def gen(self, track, seed, out, rotations, arms, horizon_days):
        self.generated.append((seed, rotations))
        if seed in self.reject:
            raise SeedRejected(seed, {"cross_only": 1}, 5)
        for r in rotations:
            for arm in arms:
                d = Path(out) / f"waybill-{track}-s{seed:04d}-r{r}-{arm}"
                d.mkdir(parents=True, exist_ok=True)
                (d / "task.json").write_text(json.dumps({"objective": {"seed": seed, "rotation": r, "arm": arm,
                                                                       "track": track}}))

    def start_run(self, key: str, profile: str | None) -> Path:
        rd = self.cfg["runs_dir"] / key
        rd.mkdir(parents=True, exist_ok=True)
        seats = [{"slot": i, "vendor": p.split(":")[0], "model": p.split(":")[1], "label": f"s{i}"}
                 for i, p in enumerate(ROSTER)]
        (rd / "run.json").write_text(json.dumps({"task_id": key, "task_dir": str(self.cfg["tasks_dir"] / key),
                                                 "arm": key.rsplit("-", 1)[1], "seats": seats,
                                                 "claude_profile": profile}))
        return rd

    def finish(self, key: str, usd: float, ended: str = "horizon", clean: bool = True,
               model: str = "claude-opus-5-5", profile: str | None = None) -> None:
        rd = self.cfg["runs_dir"] / key
        if not (rd / "run.json").exists():
            self.start_run(key, profile)
        comps = [{"slot": i, "model_used": model if p.startswith("claude") else p.split(":")[1]}
                 for i, p in enumerate(ROSTER)]
        (rd / "result.json").write_text(json.dumps({
            "verify": {"clean": clean, "replay_equals_live": True}, "run": {"ended": ended, "runner_submits": 0},
            "companies": comps, "cost": {"usd_reported": usd}}))
        for cmd, _, p in self.launched:
            if str(rd) in cmd:
                p.code = 0


def keys(c: wc.Campaign, status: str | None = None) -> list[str]:
    return [k for _, _, k in c.games() if status is None or c.game(k)["status"] == status]


def test_games_go_to_the_account_with_the_most_headroom_one_game_per_account(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "operator-token")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/elsewhere")
    w = World(tmp_path, concurrency=3)
    c = w.campaign()
    s = c.step()
    assert s["running"] == 3 and not s["finished"]
    assert [c.game(k)["account"] for k in keys(c, "running")] == ["second", "third", "main"]
    first = keys(c)[0]
    assert first == "waybill-short-s0002-r0-enforced" and keys(c)[1] == "waybill-short-s0002-r0-cheap_talk"
    cmd, env, _ = w.launched[0]
    assert cmd[3:6] == ["run", str(w.cfg["tasks_dir"] / first), str(w.cfg["runs_dir"] / first)]
    assert cmd[6:10] == ROSTER and cmd[cmd.index("--claude-profile") + 1] == str(tmp_path / "p-second")
    assert "--all-slots" in cmd
    third = w.launched[2][0]
    assert third[third.index("--claude-profile") + 1] == "default"
    assert cmd[cmd.index("--sandbox-deny") + 1] == f"{w.cfg['runs_dir']},{w.cfg['tasks_dir']}"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env and "CLAUDE_CONFIG_DIR" not in env
    g = c.game(first)
    assert g["code_sha"] is None or len(g["code_sha"]) == 40
    st = json.loads(w.cfg["state"].read_text())
    assert st["games"][first]["account"] == "second"
    assert "operator-token" not in w.cfg["state"].read_text()


def test_spend_comes_from_result_json_and_an_exhausted_pool_stops_new_games(tmp_path):
    w = World(tmp_path, concurrency=1, accounts=[{"name": "a", "profile": "default", "usd_cap": 45},
                                                 {"name": "b", "profile": str(tmp_path / "b"), "usd_cap": 30}])
    c = w.campaign()
    c.step()
    k1 = keys(c, "running")[0]
    assert c.game(k1)["account"] == "a"
    w.finish(k1, 17.5)
    s = c.step()  # a: 45 - 17.5 = 27.5 left; b: 30 -> b
    assert c.game(k1)["status"] == "done" and c.game(k1)["usd"] == 17.5
    k2 = keys(c, "running")[0]
    assert c.game(k2)["account"] == "b"
    w.finish(k2, 12.0)
    c.step()  # a 27.5, b 18 -> a
    k3 = keys(c, "running")[0]
    assert c.game(k3)["account"] == "a"
    w.finish(k3, 10.0)
    s = c.step()  # a 17.5, b 18: neither fits the reserve of 20
    assert s["stop"] == "accounts exhausted" and s["finished"] and keys(c, "running") == []
    led = c.ledger()
    assert led["a"]["spent"] == 27.5 and led["b"]["spent"] == 12.0


def test_the_global_cap_admits_a_seed_only_when_both_arms_fit(tmp_path):
    w = World(tmp_path, concurrency=4, usd_cap=70)
    c = w.campaign()
    s = c.step()
    # seed 2: 0 + 2 x 20 <= 70 and 20 + 20 <= 70; seed 5 would need 40 + 40 > 70
    assert len(keys(c, "running")) == 2 and s["stop"] == "global cap"
    assert {c.game(k)["seed"] for k in keys(c, "running")} == {2}


def test_an_excluded_game_replaces_both_arms_with_the_next_accepted_seed(tmp_path):
    w = World(tmp_path, concurrency=2, reject=[8])
    c = w.campaign()
    c.step()
    enf, cheap = keys(c, "running")
    w.finish(enf, 30.0, ended="usd_cap")  # prereg 7: an infrastructure end
    c.step()
    assert c.game(enf)["status"] == "excluded" and c.game(enf)["exclusion"] == ["ended by usd_cap"]
    assert c.game(cheap)["status"] == "running"  # the other arm is not stopped
    rep = [p for p in c.state["pairs"] if p["replaces"] == 2]
    assert rep == [{"seed": 9, "rotation": 0, "replaces": 2, "status": "active"}]  # 8 was rejected
    assert c.state["rejected_seeds"] == [8] and (9, [0]) in w.generated
    w.finish(cheap, 14.0)
    c.step()
    assert c.game(cheap)["status"] == "replaced" and c.ledger()["third"]["spent"] == 14.0
    # the replacement plays the same rotation and seats
    assert "waybill-short-s0009-r0-enforced" in keys(c)
    for k in keys(c, "running"):  # and the other excluded/done games are never started again
        assert c.game(k)["seed"] != 2


def test_a_pending_partner_arm_is_dropped_and_a_wrong_model_excludes(tmp_path):
    w = World(tmp_path, concurrency=1)
    c = w.campaign()
    c.step()
    k = keys(c, "running")[0]
    w.finish(k, 14.0, model="claude-sonnet-5-5")
    c.step()
    assert c.game(k)["status"] == "excluded" and "instead of" in c.game(k)["exclusion"][0]
    assert c.game("waybill-short-s0002-r0-cheap_talk")["status"] == "dropped"


def test_a_restarted_campaign_adopts_live_runners_resumes_dead_ones_and_skips_finished(tmp_path):
    w = World(tmp_path, concurrency=3)
    c = w.campaign()
    c.step()
    a, b, d = keys(c, "running")
    w.finish(a, 14.0)
    w.start_run(b, str(tmp_path / "p-third"))
    w.start_run(d, "default")
    n = len(w.launched)
    live = {c.game(d)["pid"]}
    c2 = w.campaign(alive=lambda pid, rd: pid in live)  # a new process: no Popen objects
    c2.step()
    new = w.launched[n:]
    resumed = [x[0] for x in new if "resume" in x[0]]
    assert resumed == [[*resumed[0][:3], "resume", str(w.cfg["runs_dir"] / b)]]
    assert c2.game(a)["status"] == "done" and c2.game(b)["restarts"] == 1 and c2.game(d)["status"] == "running"
    assert not any(str(w.cfg["runs_dir"] / d) in x[0] for x in new)  # never a second runner on a live game


def test_a_lost_state_file_is_rebuilt_from_the_run_directories(tmp_path):
    w = World(tmp_path, concurrency=2)
    w.gen("short", 2, w.cfg["tasks_dir"], [0], ("enforced", "cheap_talk"), None)
    w.finish("waybill-short-s0002-r0-enforced", 9.0, profile=str(tmp_path / "p-second"))
    w.start_run("waybill-short-s0002-r0-cheap_talk", "default")
    c = w.campaign()
    c.step()
    assert c.game("waybill-short-s0002-r0-enforced")["account"] == "second"
    assert c.ledger()["second"]["spent"] == 9.0
    assert c.game("waybill-short-s0002-r0-cheap_talk")["account"] == "main"
    assert c.game("waybill-short-s0002-r0-cheap_talk")["restarts"] == 1


def test_a_game_without_a_result_after_its_resumes_fails_and_its_seed_is_replaced(tmp_path):
    w = World(tmp_path, concurrency=1, max_restarts=2)
    c = w.campaign()
    c.step()
    k = keys(c, "running")[0]
    w.start_run(k, "default")
    for _ in range(4):
        for _, _, p in w.launched:
            p.code = 1
        c.step()
    assert c.game(k)["status"] == "failed" and c.game(k)["restarts"] == 2
    assert any(p["replaces"] == 2 for p in c.state["pairs"])


def test_players_follow_the_seats_and_can_be_replaced_for_tests(tmp_path):
    w = World(tmp_path, players={"codex:gpt-6.1-terra": "stub:/x"})
    game = wc.schedule_game(w.cfg, 1)
    assert wc.players(w.cfg, game) == ["claude:claude-opus-5-5", "stub:/x", "claude:claude-opus-5-5", "stub:/x"]


def test_the_config_refuses_a_runner_profile_and_duplicate_logins(tmp_path):
    with pytest.raises(ValueError, match="set by the campaign"):
        World(tmp_path, runner=["--claude-profile", "x"])
    with pytest.raises(ValueError, match="same name or profile"):
        World(tmp_path, accounts=[{"name": "a", "profile": "default", "usd_cap": 1},
                                  {"name": "b", "profile": "~/.claude", "usd_cap": 1}])


def test_the_codex_weekly_window_comes_from_the_agents_session_files(tmp_path):
    f = tmp_path / "agents/s1-codex/codex-home/sessions/2026/11/19/rollout.jsonl"
    f.parent.mkdir(parents=True)
    rl = {"primary": {"used_percent": 11.0, "window_minutes": 10080}, "secondary": None}
    f.write_text("\n".join(json.dumps({"type": "event_msg", "payload": {"type": "token_count", "rate_limits": r}})
                           for r in (rl, rl | {"primary": {"used_percent": 12.5, "window_minutes": 10080}})) + "\n")
    assert wc.codex_weekly_used(tmp_path) == 12.5
    assert wc.codex_weekly_used(tmp_path / "none") is None


def test_preflight_finds_a_login_twice_and_prints_no_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(wa.sys, "platform", "darwin")
    monkeypatch.setenv("HOME", str(tmp_path))
    for d, uid in (("p1", "u1"), ("p2", "u1")):
        (tmp_path / d).mkdir()
        (tmp_path / d / ".claude.json").write_text(json.dumps({"oauthAccount": {
            "accountUuid": uid, "organizationUuid": "o", "emailAddress": "x@example.org",
            "hasExtraUsageEnabled": True}}))

    def run(cmd, **kw):
        rec = {"accessToken": "secret-token", "refreshToken": "secret-refresh", "expiresAt": 9e12,
               "rateLimitTier": "max_5x"}
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"claudeAiOauth": rec}), "")

    monkeypatch.setattr(wa.subprocess, "run", run)
    accts = [wc.Account("one", str(tmp_path / "p1"), 10), wc.Account("two", str(tmp_path / "p2"), 10)]
    rows = wc.preflight(accts)
    assert rows[1]["same_account_as"] == "one" and rows[0]["login"] and rows[0]["extra_usage"] is True
    text = json.dumps(rows)
    assert "secret" not in text and "example.org" not in text and "u1" not in text


def test_the_campaign_writes_nothing_into_the_run_directories(tmp_path):
    w = World(tmp_path, concurrency=2)
    c = w.campaign()
    c.step()
    k = keys(c, "running")[0]
    w.finish(k, 5.0)
    before = {p: p.read_bytes() for p in (w.cfg["runs_dir"] / k).rglob("*") if p.is_file()}
    c.step()
    after = {p: p.read_bytes() for p in (w.cfg["runs_dir"] / k).rglob("*") if p.is_file()}
    assert before == after and "second" not in (w.cfg["runs_dir"] / k / "result.json").read_text()
