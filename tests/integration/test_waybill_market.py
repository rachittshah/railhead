"""Waybill Phases 2 and 3 on the engine: three scripted agents in one world with franchise regions and the
contract market, in both arms.

(a) an out-of-region build is refused live (ERR_FRANCHISE), and a forged one is rejected by the replay;
(b) A offers B two supply contracts and B accepts both: A really delivers coal for one (kept) and nothing
    for the other (broken); the settlement amounts follow the formula, the ledger conserves money, and the
    replay gives the same exchange state and the same score for each company;
(c) the same game in cheap_talk moves no money but records the silent outcomes;
(d) a tender opens, gets two sealed bids, is awarded to the lowest bid and settles.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from railhead.actionlog import read_log
from railhead.bridge import BridgeError
from railhead.client import Client, DaemonError, agent_socket_path
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

pytestmark = [pytest.mark.integration, pytest.mark.timeout(1800)]

D = 74
W = 128
HORIZON = 200
Q1, P1, K1 = 20, 10_000, 100      # kept: A delivers coal to the power station
Q2, P2, K2 = 30, 5_000, 50        # broken: nobody delivers to the other industry
START_BAL = {0: 20_000, 1: 50_000}  # unequal starts on the ledger (slot -> pounds)
TENDER = {"per_quarter": 2, "bid_ticks": 3 * D, "window_ticks": 40 * D, "quantity_base": 10,
          "quantity_jitter_pct": 0}


class SockWorld:
    """What RoadOracle needs from a World, over one agent socket (so every build goes through the
    coordinator and its franchise check)."""

    def __init__(self, c: Client) -> None:
        self.c = c

    def call(self, m, **a):
        return self.c.request("query", m=m, a=a)

    def act(self, m, **a):
        try:
            return self.c.request("act", m=m, a=a)
        except DaemonError as e:
            raise BridgeError(e.code, e.message) from None


class Agent:
    def __init__(self, state: Path, slot: int, plan: dict) -> None:
        self.slot, self.plan = slot, plan
        self.c = Client(state)
        self.c.path = agent_socket_path(state, slot)
        self.c.timeout = 900
        self.notes: dict = {}

    def ex(self, m, **a):
        return self.c.request("ex", m=m, a=a)

    def wait(self, **a) -> dict | None:
        try:
            out = self.c.request("wait", **a)
        except DaemonError as e:
            assert e.code == "ERR_HORIZON", e
            return None
        return None if out.get("reason") == "horizon" else out

    def idle(self) -> None:
        while self.wait(days=30) is not None:
            pass

    # ---------------------------------------------------------------- scripts
    def run(self) -> None:
        getattr(self, f"run_{self.slot}")()

    def run_0(self) -> None:  # A: build a coal truck route, offer two contracts, bid on the first tender
        p = self.plan
        self.c.request("act", m="company_loan", a={"amount": 300_000})
        net = RoadOracle(SockWorld(self.c)).deliver(p["mine"], p["ps"], p["coal"], max_trucks=4)
        self.notes["vehicles"] = len(net.vehicles)
        common = {"to": "B", "seller": "me", "buyer": "B", "cargo": p["coal"], "window_start": "+0d"}
        self.notes["c1"] = self.ex("offer", **common, industry=p["ps"]["id"], quantity=Q1, deadline="+120d",
                                   price=P1, penalty_per_unit=K1)["contract"]["contract_id"]
        self.notes["c2"] = self.ex("offer", **common, industry=p["other"], quantity=Q2, deadline="+90d",
                                   price=P2, penalty_per_unit=K2)["contract"]["contract_id"]
        t = self.ex("market")["tenders"][0]
        self.notes["tender"] = t
        self.ex("tender_bid", tender_id=t["tender_id"], price=t["max_price"] // 2)
        self.idle()

    def run_1(self) -> None:  # B: wait for the offers, accept both, bid higher on the tender
        signed = 0
        while signed < 2:
            out = self.wait(days=10, until=["offer_received"])
            assert out is not None
            self.notes.setdefault("wakes", []).append(out["reason"])
            for c in self.ex("contracts"):
                if c["your_turn"]:
                    self.ex("accept", contract_id=c["contract_id"])
                    signed += 1
        t = self.ex("market")["tenders"][0]
        self.ex("tender_bid", tender_id=t["tender_id"], price=t["max_price"])
        self.notes["inbox"] = self.ex("inbox")
        self.idle()

    def run_2(self) -> None:  # C: try to build in A's region; build (dry run) at home; message A
        p = self.plan
        x, y = p["a_tile"]
        try:
            self.c.request("act", m="rail_track", a={"tile": y * W + x, "track": "ne_sw"})
            self.notes["franchise"] = "accepted"
        except DaemonError as e:
            self.notes["franchise"] = (e.code, e.message)
        hx, hy = p["c_tile"]
        try:
            self.c.request("act", m="rail_track", a={"tile": hy * W + hx, "track": "ne_sw", "dry_run": True})
        except DaemonError as e:
            self.notes["home"] = e.code
        self.notes["rh"] = rh_calls(self.c.path)
        self.ex("msg", to="A", text="hello from C")
        self.notes["contracts"] = self.ex("contracts")
        self.idle()


def rh_calls(sock: Path) -> list[tuple[int, str, str]]:
    """`rh` as a model runs it: one market query that works and one command that fails."""
    import contextlib
    import io
    import os

    from railhead import cli

    out = []
    old = os.environ.get("RAILHEAD_SOCKET")
    os.environ["RAILHEAD_SOCKET"] = str(sock)
    try:
        for argv in (["market"], ["accept", "contract_id=C99"], ["tender", "list"]):
            o, e = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
                rc = cli.main(argv)
            out.append((rc, o.getvalue(), e.getvalue()))
    finally:
        if old is None:
            os.environ.pop("RAILHEAD_SOCKET", None)
        else:
            os.environ["RAILHEAD_SOCKET"] = old
    return out


def make_world(tmp: Path):
    tdir = tmp / "task"
    tdir.mkdir()
    with World.create(tmp / "gen", make_config(seed=3), found_company=False) as w:
        companies = build_start(w, tdir / "start.sav", 3, history_days=5)
        coal = next(c["id"] for c in w.call("cargos") if c["label"] == "COAL")
        inds = w.call("industries")
    mine = next(i for i in inds if any(p["cargo"] == coal for p in i["produces"]))
    ps = next(i for i in inds if coal in i["accepts"])
    other = next(i["id"] for i in inds if i["id"] not in (mine["id"], ps["id"]))
    pad = 16
    ay1 = min(W - 30, max(mine["y"], ps["y"]) + pad)
    regions = RegionMap(W, W, {0: [[1, 1, W - 2, ay1]], 1: [[1, ay1 + 1, 63, W - 2]],
                               2: [[64, ay1 + 1, W - 2, W - 2]]})
    plan = {"coal": coal, "mine": mine, "ps": ps, "other": other, "a_tile": (W // 2, ay1 // 2),
            "c_tile": (96, ay1 + 10), "ay1": ay1}
    return tdir, companies, regions, plan


def play(tmp: Path, arm: str) -> dict:
    tdir, companies, regions, plan = make_world(tmp)
    market = MarketConfig(arm=arm, seed=11, start_balances=dict(START_BAL), quarter_days=60, tender=TENDER)
    write_task(tdir, f"waybill-market-{arm}", companies, horizon_days=HORIZON, runoff_days=0, agent_turn_s=900,
               regions=regions, market=market)
    state = tmp / "state"
    coord = Coordinator(tdir, state).start()
    agents = [Agent(state, s, plan) for s in range(3)]
    errors: list[BaseException] = []

    def go(a: Agent) -> None:
        try:
            a.run()
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    try:
        threads = [threading.Thread(target=go, args=(a,), daemon=True) for a in agents]
        for t in threads:
            t.start()
        for t in threads:
            t.join(1500)
        assert not any(t.is_alive() for t in threads), "an agent did not finish"
        assert not errors, errors
        views = {a.slot: a.ex("view") for a in agents}
        with coord.world_lock:
            mk = coord.market
            live = {"ticks": coord.sw.ticks, "net_worth": coord.sw.net_worth(), "exchange": mk.ex.state(),
                    "market": mk.state(),
                    "ledger": {int(c["id"]): mk.balance(int(c["id"])) for c in companies}}
            mk.ex.check_invariants()
    finally:
        coord.close()
    again = Coordinator(tdir, state)  # recovery: the market comes back from the log
    try:
        recovered = again.market.state() if again.market is not None else None
    finally:
        again.close()
    live["recovered"] = recovered
    return {"task": tdir, "log": state / "run" / "actions.jsonl", "companies": companies, "live": live,
            "notes": [a.notes for a in agents], "views": views, "plan": plan, "tmp": tmp, "regions": regions}


@pytest.fixture(scope="module")
def enforced(tmp_path_factory):
    return play(tmp_path_factory.mktemp("wb-enforced"), "enforced")


@pytest.fixture(scope="module")
def cheap(tmp_path_factory):
    return play(tmp_path_factory.mktemp("wb-cheap"), "cheap_talk")


def ex_records(log: Path) -> list[dict]:
    return [r.raw["x"] for r in read_log(log) if r.raw.get("kind") == "ex"]


def settles(log: Path) -> dict[str, dict]:
    return {x["contract_id"]: x for x in ex_records(log) if x["op"] == "settle"}


def ids(g: dict) -> tuple[str, str, str]:
    a, b, c = (str(x["id"]) for x in sorted(g["companies"], key=lambda c: c["slot"]))
    return a, b, c


# ---------------------------------------------------------------------- (a) franchise
def test_out_of_region_build_is_refused_live(enforced):
    note = enforced["notes"][2]["franchise"]
    assert note[0] == "ERR_FRANCHISE" and "outside your franchise region" in note[1]
    cid_c = int(ids(enforced)[2])
    acts = [r.raw for r in read_log(enforced["log"]) if r.raw.get("kind") == "act" and r.raw["company"] == cid_c]
    assert all(a["a"].get("dry_run") for a in acts if a["m"] == "rail_track")  # the refused build is not logged


def test_forged_out_of_region_build_is_rejected_by_the_replay(enforced, tmp_path):
    cid_c = int(ids(enforced)[2])
    x, y = enforced["plan"]["a_tile"]
    lines = enforced["log"].read_text().splitlines()
    forged = []
    for ln in lines:
        r = json.loads(ln)
        if r.get("kind") == "act" and r.get("company") == cid_c and r["m"] == "rail_track":
            r["a"]["tile"] = y * W + x  # same record, but a real build on a tile of A's region
            r["a"].pop("dry_run", None)
        forged.append(json.dumps(r, sort_keys=True))
    bad = tmp_path / "forged.jsonl"
    bad.write_text("\n".join(forged) + "\n")
    with pytest.raises(LogRejected, match="outside your franchise region"):
        replay_multi(enforced["task"] / "start.sav", bad, tmp_path / "w", horizon_days=HORIZON,
                     regions=enforced["regions"], market_cfg=MarketConfig.from_objective(_obj(enforced)))
    out = verify_multi(enforced["task"], enforced["task"] / "start.sav", bad, tmp_path / "v")
    assert out["rejected"] == 1


def _obj(g: dict) -> dict:
    return json.loads((g["task"] / "task.json").read_text())["objective"]


# ---------------------------------------------------------------------- (b) contracts, enforced
def test_kept_and_broken_contracts_settle_by_the_formula(enforced):
    a, b, _c = ids(enforced)
    notes = enforced["notes"][0]
    st = settles(enforced["log"])
    s1, s2 = st[notes["c1"]], st[notes["c2"]]
    assert s1["delivered"] >= Q1 and s1["outcome"] == "kept", s1
    assert s2["delivered"] == 0 and s2["outcome"] == "broken"
    assert "offer_received" in enforced["notes"][1]["wakes"]
    led = enforced["live"]["ledger"]
    ex = enforced["live"]["exchange"]
    tender_refs = set(ex["tenders"]) | {o["id"] for o in ex["obligations"].values() if o["kind"] == "tender_penalty"}
    tender_net = sum((t["amount"] if t["dst"] == a else -t["amount"]) for t in ex["ledger"]["history"]
                     if t["ref"] in tender_refs and a in (t["src"], t["dst"]))
    assert led[int(a)] == START_BAL[0] + P1 - K2 * Q2 + tender_net
    assert led[int(b)] == START_BAL[1] - P1 + K2 * Q2
    assert sum(enforced["live"]["exchange"]["ledger"]["balances"].values()) == 0


def test_replay_gives_the_same_exchange_state_and_scores(enforced, tmp_path):
    cfg = MarketConfig.from_objective(_obj(enforced))
    res = replay_multi(enforced["task"] / "start.sav", enforced["log"], tmp_path / "w", horizon_days=HORIZON,
                       regions=enforced["regions"], market_cfg=cfg)
    assert res.valid, res.divergences[:2]
    assert res.market.ex.state() == enforced["live"]["exchange"]
    assert res.net_worth == enforced["live"]["net_worth"]
    out = verify_multi(enforced["task"], enforced["task"] / "start.sav", enforced["log"], tmp_path / "v")
    assert out["valid"] == 1 and out["rejected"] == 0, out
    for row in out["companies"]:
        cid = row["id"]
        assert row["ledger_balance"] == enforced["live"]["ledger"][cid]
        assert row["score"] == enforced["live"]["net_worth"][cid] + enforced["live"]["ledger"][cid]


def test_delivery_extraction_shares_monitors_with_the_book_without_changing_the_replay(enforced, tmp_path):
    """The H3/H4 delivery extraction (railhead.multi.deliveries) on this game, with its monitors forced onto
    the key of the kept contract (A's coal to the power station, which the market book also monitors) and
    onto 60 more keys: the replay's checkpoints and final checksum equal a plain replay's, the market state
    (exchange and the book's monitor ids and samples) and every score equal the verifier's, and the
    extraction counts at least the delivery that the contract settled."""
    from railhead.multi.deliveries import extract, keyed

    cfg = MarketConfig.from_objective(_obj(enforced))
    plain = replay_multi(enforced["task"] / "start.sav", enforced["log"], tmp_path / "plain", horizon_days=HORIZON,
                         regions=enforced["regions"], market_cfg=cfg)
    out = verify_multi(enforced["task"], enforced["task"] / "start.sav", enforced["log"], tmp_path / "v")
    a, b, c = (int(x) for x in ids(enforced))
    p = enforced["plan"]
    kept = (a, p["coal"], p["ps"]["id"])
    extra = [kept] + [(co, p["coal"], i) for co in (a, b, c) for i in range(20)]
    got = extract(enforced["task"] / "start.sav", enforced["log"], tmp_path / "x", horizon_days=HORIZON,
                  runoff_days=0, regions=enforced["regions"], market_cfg=cfg, require_seal=False,
                  extra_keys=extra)
    assert got["checkpoints"] == plain.checkpoints and got["final_checksum"] == plain.final_checksum
    assert got["market"] == out["market"]
    assert {r["id"]: r["score"] for r in got["replay"]["companies"]} == {r["id"]: r["score"] for r in out["companies"]}
    assert list(kept) in [s["key"] for s in got["monitors"]["shared_with_book"]]  # the collision happened
    assert got["monitors"]["extraction"] >= 40
    settled = settles(enforced["log"])[enforced["notes"][0]["c1"]]["delivered"]
    assert keyed(got["deliveries"]["horizon"])[kept] >= settled >= Q1


def test_forged_delivery_count_is_rejected(enforced, tmp_path):
    lines = enforced["log"].read_text().splitlines()
    out = []
    for ln in lines:
        r = json.loads(ln)
        if r.get("kind") == "ex" and r["x"]["op"] == "settle" and r["x"]["outcome"] == "broken":
            r["x"]["delivered"] = r["x"]["parts"][0][3] = 1  # claim a partial delivery
            r["x"]["outcome"] = "partial"
        out.append(json.dumps(r, sort_keys=True))
    bad = tmp_path / "forged.jsonl"
    bad.write_text("\n".join(out) + "\n")
    with pytest.raises(LogRejected, match="computed"):
        replay_multi(enforced["task"] / "start.sav", bad, tmp_path / "w", horizon_days=HORIZON,
                     regions=enforced["regions"], market_cfg=MarketConfig.from_objective(_obj(enforced)))


def test_agents_see_views_only(enforced):
    vc = enforced["views"][2]
    assert vc["contracts"] == [] and vc["inbox"] == []
    assert enforced["notes"][2]["contracts"] == []
    va = enforced["views"][0]
    assert [m["text"] for m in va["inbox"]] == ["hello from C"]
    assert "settlements" in va and {s["outcome"] for s in va["settlements"]} >= {"kept", "broken"}
    for t in vc["market"]["tenders"]:
        assert set(t) >= {"your_bid"} and "bids" not in t


# ---------------------------------------------------------------------- (c) cheap talk
def test_cheap_talk_moves_no_contract_money_but_records_the_outcomes_and_obligations(cheap, tmp_path):
    a, b, _c = ids(cheap)
    st = settles(cheap["log"])
    notes = cheap["notes"][0]
    assert st[notes["c1"]]["outcome"] == "kept" and st[notes["c2"]]["outcome"] == "broken"
    ex = cheap["live"]["exchange"]
    hist = ex["ledger"]["history"]
    # arm parity: only start balances and tender money (the exchange pays the winner) move; no agent pays
    assert {t["reason"] for t in hist} <= {"start_balance", "tender_escrow", "settle_payment", "settle_refund"}
    assert all(t["ref"] in ex["tenders"] for t in hist if t["reason"] != "start_balance")
    led = cheap["live"]["ledger"]
    tender_in = {c: sum(t["amount"] for t in hist if t["dst"] == c and t["reason"] == "settle_payment")
                 for c in (a, b)}
    assert led[int(a)] == START_BAL[0] + tender_in[a] and led[int(b)] == START_BAL[1] + tender_in[b]
    # what the contracts owe is recorded as obligations: B owes A the price of the kept contract, A owes B the
    # penalty of the broken one; nobody paid, so each is broken after the grace period (or still open)
    obs = {(o["contract_id"], o["kind"]): o for o in ex["obligations"].values()}
    assert obs[(notes["c1"], "supply_payment")]["payer"] == b and obs[(notes["c1"], "supply_payment")]["amount"] == P1
    assert obs[(notes["c2"], "supply_penalty")]["payer"] == a and obs[(notes["c2"], "supply_penalty")]["amount"] == \
        K2 * Q2
    assert {o["status"] for o in obs.values()} <= {"broken", "open"}
    va = cheap["views"][0]
    assert "settlements" not in va
    assert {c["status"] for c in va["contracts"] if c["contract_id"] in (notes["c1"], notes["c2"])} == {"closed"}
    out = verify_multi(cheap["task"], cheap["task"] / "start.sav", cheap["log"], tmp_path / "v")
    assert out["valid"] == 1 and out["rejected"] == 0, out
    assert {r["id"]: r["ledger_balance"] for r in out["companies"]} == led


# ---------------------------------------------------------------------- (d) tenders
def test_tender_opens_is_bid_awarded_and_settled(enforced):
    a, b, _c = ids(enforced)
    recs = ex_records(enforced["log"])
    t = enforced["notes"][0]["tender"]
    tid = t["tender_id"]
    opened = [x for x in recs if x["op"] == "tender_open"]
    assert opened and opened[0]["tenders"][0]["tender_id"] == tid
    bids = [x for x in recs if x["op"] == "tender_bid" and x["tender_id"] == tid]
    assert {x["company"] for x in bids} == {a, b}
    award = next(x for x in recs if x["op"] == "tender_award" and x["tender_id"] == tid)
    assert award["winner"] == a and award["price"] == t["max_price"] // 2
    assert award["tick"] == t["bid_close"]
    s = settles(enforced["log"])[tid]
    assert s["outcome"] in ("kept", "partial", "broken") and s["tick"] == t["deadline"]
    ex = enforced["live"]["exchange"]
    assert ex["tenders"][tid]["status"] == "settled"


def test_a_restarted_coordinator_recovers_the_market(enforced, cheap):
    for g in (enforced, cheap):
        assert g["live"]["recovered"] == g["live"]["market"]


def test_rh_market_commands_end_to_end(enforced):
    (rc1, out1, _), (rc2, _, err2), (rc3, _, err3) = enforced["notes"][2]["rh"]
    assert rc1 == 0 and json.loads(out1)["tenders"] and "region" in json.loads(out1)
    assert rc2 == 1 and err2.startswith("ERR_UNKNOWN_CONTRACT")
    assert rc3 == 2 and "usage: rh tender bid" in err3
