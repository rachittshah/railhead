"""Licences on the engine: a delivery that needs two companies.

The coal mine is in A's region and the power station in B's. Without a licence, B cannot build at the mine
(ERR_FRANCHISE) and A cannot build at the power station, and no company can use another's station: no
delivery is possible. A grants B a licence for its region; B builds the whole truck route (stop at the
mine in A's region, road across the line, stop at the power station in its own region) and sells A the
coal under a supply contract. After the licence ends, B's route keeps running but B can build nothing new
in A's region. The replay gives the same world, exchange state and scores; a forged licence is rejected.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from railhead.actionlog import read_log
from railhead.client import DaemonError
from railhead.multi.coordinator import Coordinator
from railhead.multi.gen import build_start, write_task
from railhead.multi.log import LogRejected
from railhead.multi.market import MarketConfig
from railhead.multi.regions import RegionMap
from railhead.multi.replay import replay_multi
from railhead.multi.verify import verify_multi
from railhead.oracle.road import RoadOracle
from railhead.world import World
from tests.integration.conftest import make_config
from tests.integration.test_waybill_market import Agent as MarketAgent
from tests.integration.test_waybill_market import SockWorld

pytestmark = [pytest.mark.integration, pytest.mark.timeout(1800)]

W = 128
HORIZON = 170
FEE, Q, PRICE, PPU = 30_000, 20, 8_000, 100
LIC_DAYS, SUPPLY_DAYS = 120, 100  # the fee: 15k due at day 90, 15k at day 120 (quarterly, in arrears)


def split(mine: dict, ps: dict) -> tuple[RegionMap, dict]:
    """Two regions with the line half way between the mine (A) and the power station (B)."""
    dx, dy = ps["x"] - mine["x"], ps["y"] - mine["y"]
    if abs(dx) >= abs(dy):
        cut = (mine["x"] + ps["x"]) // 2
        lo, hi = [[1, 1, cut, W - 2]], [[cut + 1, 1, W - 2, W - 2]]
        far = (cut + 1 + 2, ps["y"]) if dx > 0 else (cut - 2, ps["y"])
    else:
        cut = (mine["y"] + ps["y"]) // 2
        lo, hi = [[1, 1, W - 2, cut]], [[1, cut + 1, W - 2, W - 2]]
        far = (ps["x"], cut + 3) if dy > 0 else (ps["x"], cut - 2)
    a, b = (lo, hi) if (dx if abs(dx) >= abs(dy) else dy) > 0 else (hi, lo)
    return RegionMap(W, W, {0: a, 1: b}), {"a_rect": a[0], "b_rect": b[0], "far": far}


class Agent(MarketAgent):
    def run_0(self) -> None:  # A: grant B a licence for its region, wait for B's offer, accept it
        p = self.plan
        r = p["a_rect"]
        self.notes["lic"] = self.ex("offer", kind="licence", to="B", grantor="me", grantee="B",
                                    rect=f"{r[0]},{r[1]},{r[2]},{r[3]}", window_start="+0d",
                                    deadline=f"+{LIC_DAYS}d", fee=FEE)["contract"]["contract_id"]
        while True:
            out = self.wait(days=5, until=["offer_received"])
            assert out is not None
            mine = [c for c in self.ex("contracts") if c["your_turn"] and c["kind"] == "supply"]
            if mine:
                self.ex("accept", contract_id=mine[0]["contract_id"])
                break
        self.idle()

    def run_1(self) -> None:  # B: refused at the mine; accept the licence; build the route; sell A the coal
        p = self.plan
        x, y = p["mine"]["x"], p["mine"]["y"]
        try:
            self.c.request("act", m="road_line", a={"from_tile": y * W + x - 2, "to_tile": y * W + x + 2})
            self.notes["before"] = "accepted"
        except DaemonError as e:
            self.notes["before"] = e.code
        while True:
            lic = [c for c in self.ex("contracts") if c["your_turn"] and c["kind"] == "licence"]
            if lic:
                break
            assert self.wait(days=1, until=["offer_received"]) is not None
        self.ex("accept", contract_id=lic[0]["contract_id"])
        self.c.request("act", m="company_loan", a={"amount": 300_000})
        net = RoadOracle(SockWorld(self.c)).deliver(p["mine"], p["ps"], p["coal"], max_trucks=4)
        self.notes["vehicles"] = len(net.vehicles)
        self.notes["stations_before"] = len(self.c.request("query", m="stations", a={}))
        self.notes["supply"] = self.ex("offer", to="A", seller="me", buyer="A", cargo=p["coal"],
                                       industry=p["ps"]["id"], quantity=Q, window_start="+0d",
                                       deadline=f"+{SUPPLY_DAYS}d", price=PRICE,
                                       penalty_per_unit=PPU)["contract"]["contract_id"]
        while True:  # cheap talk: pay the first instalment when it falls due, not the second
            out = self.wait(days=7)  # 90 and 120 are not multiples of 7: the due ticks fall inside waits
            if out is None or self.c.request("info")["days_left"] <= HORIZON - LIC_DAYS - 5:
                break
            if out["reason"] == "obligation_due" and p["arm"] == "cheap_talk" and "paid" not in self.notes:
                lic_id = next(c["contract_id"] for c in self.ex("contracts") if c["kind"] == "licence")
                self.notes["paid"] = self.ex("pay", to="A", amount=FEE // 2, contract=lic_id)
            self.notes.setdefault("wakes", []).append(out["reason"])
        fx, fy = p["mine"]["x"], p["mine"]["y"]  # after the licence: nothing new in A's region
        try:
            self.c.request("act", m="road_line", a={"from_tile": (fy + 3) * W + fx - 3,
                                                    "to_tile": (fy + 3) * W + fx + 3})
            self.notes["after"] = "accepted"
        except DaemonError as e:
            self.notes["after"] = e.code
        self.notes["stations_after"] = len(self.c.request("query", m="stations", a={}))
        self.idle()


@pytest.fixture(scope="module", params=["enforced", "cheap_talk"])
def game(tmp_path_factory, request):
    arm = request.param
    tmp = tmp_path_factory.mktemp(f"wb-licence-{arm}")
    tdir = tmp / "task"
    tdir.mkdir()
    with World.create(tmp / "gen", make_config(seed=3), found_company=False) as w:
        companies = build_start(w, tdir / "start.sav", 2, history_days=5)
        coal = next(c["id"] for c in w.call("cargos") if c["label"] == "COAL")
        inds = w.call("industries")
    mine = next(i for i in inds if any(p["cargo"] == coal for p in i["produces"]))
    ps = next(i for i in inds if coal in i["accepts"])
    regions, extra = split(mine, ps)
    plan = {"coal": coal, "mine": mine, "ps": ps, "arm": arm, **extra}
    market = MarketConfig(arm=arm, seed=5, start_balances={1: 50_000}, quarter_days=90,
                          tender={"per_quarter": 0})
    write_task(tdir, "waybill-licence", companies, horizon_days=HORIZON, runoff_days=0, agent_turn_s=900,
               regions=regions, market=market)
    state = tmp / "state"
    coord = Coordinator(tdir, state).start()
    agents = [Agent(state, s, plan) for s in range(2)]
    errors: list[BaseException] = []

    def go(a):
        try:
            a.run()
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    try:
        ts = [threading.Thread(target=go, args=(a,), daemon=True) for a in agents]
        for t in ts:
            t.start()
        for t in ts:
            t.join(1500)
        assert not any(t.is_alive() for t in ts), "an agent did not finish"
        assert not errors, errors
        with coord.world_lock:
            mk = coord.market
            live = {"ticks": coord.sw.ticks, "net_worth": coord.sw.net_worth(), "exchange": mk.ex.state(),
                    "checksum": coord.sw.world.call("checksum"),
                    "ledger": {int(c["id"]): mk.balance(int(c["id"])) for c in companies}}
    finally:
        coord.close()
    return {"task": tdir, "log": state / "run" / "actions.jsonl", "companies": companies, "live": live,
            "notes": [a.notes for a in agents], "plan": plan, "regions": regions, "arm": arm}


def _ids(g):
    a, b = sorted(g["companies"], key=lambda c: c["slot"])
    return int(a["id"]), int(b["id"])


def test_without_a_licence_b_cannot_build_at_the_mine_and_after_it_nothing_new(game):
    nb = game["notes"][1]
    assert nb["before"] == "ERR_FRANCHISE"
    assert nb["after"] == "ERR_FRANCHISE"
    assert nb["vehicles"] >= 1 and nb["stations_after"] == nb["stations_before"] >= 2  # the route stays
    assert "obligation_due" in nb["wakes"]  # B is woken when an instalment falls due (both arms)


def test_b_built_in_as_region_under_the_licence_and_delivered(game):
    a, b = _ids(game)
    rm = game["regions"]
    acts = [r.raw for r in read_log(game["log"]) if r.kind == "act" and r.raw["company"] == b and r.raw["ok"]]
    builds = [r for r in acts if r["m"] in ("road_stop", "road_line", "road_depot")]
    in_a = [r for r in builds if any(not rm.contains(1, r["ticks"], *xy) for xy in _tiles(r))]
    assert in_a, "B built nothing in A's region"
    ex = game["live"]["exchange"]
    lic = ex["contracts"][game["notes"][0]["lic"]]
    sup = ex["contracts"][game["notes"][1]["supply"]]
    assert lic["kind"] == "licence" and lic["outcome"] == "ended" and lic["signed_tick"] is not None
    assert all(r["ticks"] < lic["terms"]["deadline"] for r in in_a)
    assert sup["outcome"] == "kept" and sup["settlement"]["delivered"] >= Q, sup["settlement"]
    led = game["live"]["ledger"]
    obs = sorted((o for o in ex["obligations"].values() if o["contract_id"] == game["notes"][0]["lic"]),
                 key=lambda o: o["due"])
    assert [(o["payer"], o["payee"], o["amount"]) for o in obs] == [(str(b), str(a), FEE // 2)] * 2
    assert obs[0]["due"] == lic["terms"]["window_start"] + 90 * 74 and obs[1]["due"] == lic["terms"]["deadline"]
    if game["arm"] == "enforced":  # instalments collected at their due ticks; the price paid from escrow
        assert [o["status"] for o in obs] == ["kept", "kept"] and all(o["collected"] for o in obs)
        assert led[a] == FEE - PRICE and led[b] == 50_000 - FEE + PRICE
        assert "obligation_due" in game["notes"][1]["wakes"]
    else:  # B paid the first instalment on time and not the second; A never paid B the price
        assert [(o["status"], o["paid"], o["paid_by_due"]) for o in obs] == [("kept", FEE // 2, FEE // 2),
                                                                            ("broken", 0, 0)]
        pay = next(o for o in ex["obligations"].values() if o["kind"] == "supply_payment")
        assert (pay["payer"], pay["amount"], pay["status"]) == (str(a), PRICE, "broken")
        assert led[a] == FEE // 2 and led[b] == 50_000 - FEE // 2


def _tiles(r):
    a = r["a"]
    out = []
    for k in ("tile", "from_tile", "to_tile", "front_tile"):
        if k in a:
            out.append((a[k] % W, a[k] // W))
    return out


def test_replay_equals_live_and_the_verifier_scores_it(game, tmp_path):
    cfg = MarketConfig.from_objective(json.loads((game["task"] / "task.json").read_text())["objective"])
    res = replay_multi(game["task"] / "start.sav", game["log"], tmp_path / "w", horizon_days=HORIZON,
                       regions=game["regions"], market_cfg=cfg)
    assert res.valid, res.divergences[:2]
    assert res.final_checksum["h"] == game["live"]["checksum"]["h"]
    assert res.net_worth == game["live"]["net_worth"] and res.market.ex.state() == game["live"]["exchange"]
    out = verify_multi(game["task"], game["task"] / "start.sav", game["log"], tmp_path / "v")
    assert out["valid"] == 1 and out["rejected"] == 0, out


def test_a_forged_licence_is_rejected(game, tmp_path):
    cfg = MarketConfig.from_objective(json.loads((game["task"] / "task.json").read_text())["objective"])
    out = []
    for ln in Path(game["log"]).read_text().splitlines():
        r = json.loads(ln)
        if r.get("kind") == "ex" and r["x"].get("op") == "offer" and r["x"].get("kind") == "licence":
            r["x"]["terms"]["rect"] = [1, 1, 2, 2]  # the licence no longer covers B's builds
        out.append(json.dumps(r, sort_keys=True))
    bad = tmp_path / "forged.jsonl"
    bad.write_text("\n".join(out) + "\n")
    with pytest.raises(LogRejected, match="outside your franchise region"):
        replay_multi(game["task"] / "start.sav", bad, tmp_path / "w", horizon_days=HORIZON,
                     regions=game["regions"], market_cfg=cfg)
    assert verify_multi(game["task"], game["task"] / "start.sav", bad, tmp_path / "v")["rejected"] == 1
