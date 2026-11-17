"""Waybill on the engine, a short game: a GameScript event wakes only its company, `rh` talks to a
coordinator through RAILHEAD_SOCKET, and a company that submits leaves the others stepping. The replay
must still give every step checksum and the net worth of each company."""

from __future__ import annotations

import threading

import pytest

from railhead import cli
from railhead.actionlog import read_log
from railhead.client import DaemonError, agent_socket_path
from railhead.multi.coordinator import Coordinator
from railhead.multi.gen import build_start, write_task
from railhead.multi.replay import replay_multi
from railhead.multi.verify import verify_multi
from railhead.world import World
from tests.integration.conftest import make_config
from tests.integration.test_waybill import Agent

pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]

HORIZON = 60


@pytest.fixture(scope="module")
def short(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("waybill-short")
    tdir = tmp / "task"
    tdir.mkdir()
    with World.create(tmp / "gen", make_config(), found_company=False) as w:
        companies = build_start(w, tdir / "start.sav", 3, history_days=5)
    write_task(tdir, "waybill-short", companies, horizon_days=HORIZON, agent_turn_s=300)
    state = tmp / "state"
    coord = Coordinator(tdir, state).start()
    out: dict = {"task": tdir, "log": state / "run" / "actions.jsonl", "companies": companies, "state": state}
    errors: list[BaseException] = []

    def first():  # waits for its own buses to reach its stop
        a = Agent(state, 0)
        a.build()
        out["first"] = a.c.request("wait", days=HORIZON, until=["station_first_vehicle"])
        while True:
            try:
                if a.c.request("wait", days=10).get("reason") == "horizon":
                    return
            except DaemonError as e:
                assert e.code == "ERR_HORIZON"
                return

    def quitter():  # one wait, then submit: the world must step on without it
        a = Agent(state, 1)
        a.c.request("wait", days=3)
        out["submit"] = a.c.request("meta", m="submit", a={"confirm": "yes"})

    def plain():
        a = Agent(state, 2)
        a.build()
        out["plain"] = []
        while True:
            try:
                r = a.c.request("wait", days=4, until=["station_first_vehicle"])
            except DaemonError as e:
                assert e.code == "ERR_HORIZON"
                return
            out["plain"].append(r)
            if r.get("reason") == "horizon":
                return

    def guard(fn):
        def run():
            try:
                fn()
            except BaseException as e:  # noqa: BLE001
                errors.append(e)
        return run

    try:
        # rh, as an agent's shell would run it, before the agents start (slot 1 then plays by script)
        mp = pytest.MonkeyPatch()
        mp.setenv("RAILHEAD_SOCKET", str(agent_socket_path(state, 1)))
        try:
            assert cli.main(["status"]) == 0
            assert cli.main(["company", "loan", "amount=200000"]) == 0
        finally:
            mp.undo()
        threads = [threading.Thread(target=guard(f), daemon=True) for f in (first, quitter, plain)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(600)
        assert not any(t.is_alive() for t in threads), "an agent did not finish"
        assert not errors, errors
        with coord.world_lock:
            out["live"] = {"ticks": coord.sw.ticks, "checksum": coord.sw.world.call("checksum"),
                           "net_worth": coord.sw.net_worth()}
    finally:
        coord.close()
    return out


def test_rh_acts_for_the_company_of_its_socket(short):
    acts = [r.raw for r in read_log(short["log"]) if r.kind == "act"]
    first = acts[0]
    assert first["m"] == "company_loan" and first["a"] == {"amount": 200_000}
    assert first["company"] == next(c["id"] for c in short["companies"] if c["slot"] == 1)


def test_an_event_wakes_only_the_company_it_belongs_to(short):
    r = short["first"]
    assert r["reason"] == "station_first_vehicle", r
    assert r["days"] < HORIZON
    # the event names a station of the waiting company: the other companies' events do not wake it
    recs = [x.raw for x in read_log(short["log"])]
    reasons = [(w["company"], w["reason"]) for x in recs if x["kind"] == "step" for w in x["wakers"]]
    c1 = next(c["id"] for c in short["companies"] if c["slot"] == 1)
    assert all(r == "elapsed" for c, r in reasons if c == c1)


def test_the_world_steps_on_after_a_submit_and_replays(short, tmp_path):
    recs = [r.raw for r in read_log(short["log"])]
    k = next(i for i, r in enumerate(recs) if r["kind"] == "meta" and r["m"] == "submit")
    assert sum(1 for r in recs[k:] if r["kind"] == "step") > 3
    assert short["live"]["ticks"] == recs[0]["ticks"] + HORIZON * 74
    res = replay_multi(short["task"] / "start.sav", short["log"], tmp_path / "r", horizon_days=HORIZON)
    assert res.valid and res.final_checksum["h"] == short["live"]["checksum"]["h"]
    assert res.net_worth == short["live"]["net_worth"]
    assert res.state.submitted == {next(c["id"] for c in short["companies"] if c["slot"] == 1)}
    out = verify_multi(short["task"], short["task"] / "start.sav", short["log"], tmp_path / "v")
    assert out["valid"] == 1 and out["rejected"] == 0
    assert {c["id"]: c["net_worth_horizon"] for c in out["companies"]} == short["live"]["net_worth"]


def test_a_submit_moved_to_another_company_is_rejected(short, tmp_path):
    import json

    recs = [r.raw for r in read_log(short["log"])]
    k = next(i for i, r in enumerate(recs) if r["kind"] == "meta" and r["m"] == "submit")
    others = [c["id"] for c in short["companies"] if c["id"] != recs[k]["company"]]
    recs[k] = dict(recs[k], company=others[0])
    forged = tmp_path / "forged.jsonl"
    forged.write_text("".join(json.dumps(r, separators=(",", ":"), sort_keys=True) + "\n" for r in recs))
    out = verify_multi(short["task"], short["task"] / "start.sav", forged, tmp_path / "v")
    assert out["rejected"] == 1


def test_a_changed_start_state_is_tamper(short, tmp_path):
    import shutil

    tdir = tmp_path / "task"
    shutil.copytree(short["task"], tdir)
    (tdir / "start.sav").write_bytes((tdir / "start.sav").read_bytes() + b"x")
    out = verify_multi(tdir, tdir / "start.sav", short["log"], tmp_path / "v")
    assert out["tamper"] == 1
