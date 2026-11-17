"""Waybill gate (Phase 1): three scripted agents play one shared world for one game year through their
own coordinator sockets, with waits of different lengths. The replay of the merged v3 log must give every
step checksum, the final checksum and the net worth of each company that the live game had."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from railhead.actionlog import read_log
from railhead.actions import to_gs
from railhead.client import Client, DaemonError, agent_socket_path
from railhead.multi.coordinator import Coordinator
from railhead.multi.gen import build_start, write_task
from railhead.multi.replay import replay_multi
from railhead.multi.verify import verify_multi
from railhead.world import World
from tests.integration.conftest import make_config

pytestmark = [pytest.mark.integration, pytest.mark.timeout(1500)]

HORIZON = 365
W = 128
BUILDABLE = set(".t^f")
WAIT_DAYS = {0: 5, 1: 11, 2: 17}  # different lengths: the barrier steps to the earliest target


class Agent:
    """A deterministic scripted player that uses only the socket of its own company, like `rh`."""

    def __init__(self, state: Path, slot: int) -> None:
        self.slot = slot
        self.c = Client(state)
        self.c.path = agent_socket_path(state, slot)
        self.c.timeout = 600
        self.log: list[str] = []

    def q(self, m, **a):
        return self.c.request("query", m=m, a=a)

    def act(self, name, **args):
        m, a = to_gs(name, args, map_size_x=W)
        try:
            return self.c.request("act", m=m, a=a)
        except DaemonError as e:
            self.log.append(f"{name}: {e.code}")
            return None

    def site(self, w: int, h: int) -> tuple[int, int]:
        """A flat, buildable w x h site; each slot searches its own band of rows."""
        kind, height = [""] * W, [""] * W
        for y0 in range(0, W, 32):
            for r in self.q("tiles", x=0, y=y0, w=W, h=32):
                kind[r["y"]], height[r["y"]] = r["kind"], r["height"]
        band = W // 3
        for y in range(max(2, self.slot * band), min(W - h - 2, (self.slot + 1) * band)):
            for x in range(2, W - w - 2):
                z = height[y][x]
                if all(set(kind[y + dy][x:x + w]) <= BUILDABLE and height[y + dy][x:x + w] == z * w
                       for dy in range(h)) and all(
                        self.q("tile", tile=(y + dy) * W + x + dx, radius=0)["slope"] == 0
                        for dy in range(h) for dx in range(w)):
                    return x, y
        raise AssertionError(f"slot {self.slot}: no flat site")

    def build(self) -> None:
        self.act("company.loan", amount=300_000)
        x, y = self.site(10, 3)
        at = lambda a, b: f"{a},{b}"
        self.act("road.line", from_tile=at(x, y + 1), to_tile=at(x + 9, y + 1))
        self.act("road.depot", tile=at(x + 4, y), front_tile=at(x + 4, y + 1))
        self.act("road.stop", tile=at(x + 1, y + 2), front_tile=at(x + 1, y + 1))
        self.act("road.stop", tile=at(x + 8, y + 2), front_tile=at(x + 8, y + 1), join_station=-1)
        pas = next(c["id"] for c in self.q("cargos") if c["label"] == "PASS")
        eng = next(e["id"] for e in self.q("engines", vtype="road", cargo=pas) if e["carries"])
        for _ in range(1 + self.slot):
            v = self.act("vehicle.buy", depot_tile=at(x + 4, y), engine=eng)
            if v is None:
                continue
            self.act("order.append", vehicle=v["vehicle"], tile=at(x + 1, y + 2))
            self.act("order.append", vehicle=v["vehicle"], tile=at(x + 8, y + 2))
            self.act("vehicle.start", vehicle=v["vehicle"])

    def run(self) -> None:
        self.build()
        n = 0
        until = ["new_month"] if self.slot == 2 else None  # exercises the chunked wake conditions
        while True:
            n += 1
            if n % 6 == 0:  # an act between waits now and then: interest changes with the loan
                self.act("company.loan", amount=300_000 - 10_000 * (n // 6 % 3))
            try:
                req = {"days": WAIT_DAYS[self.slot]} | ({"until": until} if until else {})
                out = self.c.request("wait", **req)
            except DaemonError as e:
                assert e.code == "ERR_HORIZON", e
                return
            if out.get("reason") == "horizon":
                return


@pytest.fixture(scope="module")
def game(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("waybill")
    tdir = tmp / "task"
    tdir.mkdir()
    with World.create(tmp / "gen", make_config(), found_company=False) as w:
        companies = build_start(w, tdir / "start.sav", 3, history_days=5)
    write_task(tdir, "waybill-gate", companies, horizon_days=HORIZON, runoff_days=0, agent_turn_s=300)
    state = tmp / "state"
    coord = Coordinator(tdir, state).start()
    agents = [Agent(state, s) for s in range(3)]
    errors: list[BaseException] = []

    def play(a: Agent) -> None:
        try:
            a.run()
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    try:
        threads = [threading.Thread(target=play, args=(a,), daemon=True) for a in agents]
        for t in threads:
            t.start()
        for t in threads:
            t.join(900)
        assert not any(t.is_alive() for t in threads), "an agent did not finish"
        assert not errors, errors
        owned = {a.slot: (agents[a.slot].q("vehicles"), agents[a.slot].q("stations")) for a in agents}
        with coord.world_lock:
            live = {"ticks": coord.sw.ticks, "checksum": coord.sw.world.call("checksum"),
                    "net_worth": coord.sw.net_worth()}
    finally:
        coord.close()
    return {"task": tdir, "log": state / "run" / "actions.jsonl", "companies": companies, "live": live,
            "owned": owned, "tmp": tmp}


def test_every_company_played_in_the_shared_world(game):
    recs = [r.raw for r in read_log(game["log"])]
    assert recs[0]["v"] == 3 and [c["slot"] for c in recs[0]["companies"]] == [0, 1, 2]
    for slot, (vehicles, stations) in game["owned"].items():
        assert len(vehicles) == 1 + slot and len(stations) >= 1, f"slot {slot} owns {vehicles} {stations}"
    ids = {c["id"] for c in game["companies"]}
    assert {r["company"] for r in recs if r["kind"] == "act" and r["ok"]} == ids
    assert game["live"]["ticks"] == recs[0]["ticks"] + HORIZON * 74
    nws = game["live"]["net_worth"]
    assert len(set(nws.values())) == 3, nws  # the companies really differ


def test_replay_reproduces_every_step_and_each_company_net_worth(game, tmp_path):
    recs = [r.raw for r in read_log(game["log"])]
    steps = [r for r in recs if r["kind"] == "step"]
    res = replay_multi(game["task"] / "start.sav", game["log"], tmp_path / "r", horizon_days=HORIZON)
    assert res.valid and res.divergences == []
    assert res.steps == len(steps) > 30
    assert len([cp for cp in res.checkpoints if cp[0] == "step"]) == len(steps)
    assert res.end_ticks == game["live"]["ticks"]
    assert res.final_checksum["h"] == game["live"]["checksum"]["h"]
    assert res.net_worth == game["live"]["net_worth"]
    assert res.state.waiting == {}  # every waiter woke at the horizon
    assert any(r["kind"] == "wait_req" and r["a"].get("until") for r in recs)


def test_verify_reports_net_worth_per_company(game, tmp_path):
    out = verify_multi(game["task"], game["task"] / "start.sav", game["log"], tmp_path / "v")
    assert out["valid"] == 1 and out["rejected"] == 0 and out["tamper"] == 0
    got = {c["id"]: c["net_worth_horizon"] for c in out["companies"]}
    assert got == game["live"]["net_worth"]
    assert [c["slot"] for c in out["companies"]] == [0, 1, 2]


def test_an_act_moved_to_another_company_is_rejected(game, tmp_path):
    recs = [r.raw for r in read_log(game["log"])]
    ids = [c["id"] for c in game["companies"]]
    k = next(i for i, r in enumerate(recs) if r["kind"] == "act" and r["company"] == ids[1]
             and r["m"] == "vehicle_buy" and r["ok"])
    recs[k] = dict(recs[k], company=ids[0])
    forged = tmp_path / "forged.jsonl"
    forged.write_text("".join(json.dumps(r, separators=(",", ":"), sort_keys=True) + "\n" for r in recs))
    out = verify_multi(game["task"], game["task"] / "start.sav", forged, tmp_path / "v")
    assert out["rejected"] == 1


def test_a_restarted_coordinator_recovers_the_world_and_the_barrier(game, tmp_path):
    from railhead.multi.log import LogState

    recs = [r.raw for r in read_log(game["log"])]
    # cut the log in the middle of the game, just after a wait request: some companies wait, some do not
    expect = LogState(recs[0])
    for k, r in enumerate(recs[1:], 1):
        expect.check(r)
        if k > len(recs) // 2 and r["kind"] == "wait_req" and 0 < len(expect.waiting) < 3:
            break
    cut = recs[:k + 1]
    state = tmp_path / "state"
    (state / "run").mkdir(parents=True)
    (state / "run" / "actions.jsonl").write_text(
        "".join(json.dumps(r, separators=(",", ":"), sort_keys=True) + "\n" for r in cut))
    coord = Coordinator(game["task"], state)
    try:
        assert coord.sw.ticks == expect.now
        assert {c: w.target for c, w in coord.barrier.waiting.items()} == \
            {c: w["target"] for c, w in expect.waiting.items()}
        assert 0 < len(coord.barrier.waiting) < 3
        c = next(x["id"] for x in game["companies"] if x["id"] not in expect.waiting)
        slot = next(x["slot"] for x in game["companies"] if x["id"] == c)
        assert coord.handle(slot, {"op": "info"})["company"] == c
        coord.handle(slot, {"op": "act", "m": "company_loan", "a": {"amount": 300_000}})
        last = [r.raw for r in read_log(state / "run" / "actions.jsonl")][-1]
        assert last["kind"] == "act" and last["company"] == c and last["seq"] == k + 1
    finally:
        coord.close()
