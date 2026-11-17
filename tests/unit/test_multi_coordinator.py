"""The Waybill coordinator on a fake engine: lockstep barrier across threads, straggler auto-waits, the
check-then-act race, submit, horizon, and the log it writes always passes the v3 rules. No OpenTTD."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from railhead import client as client_mod
from railhead.actionlog import read_log
from railhead.bridge import BridgeError
from railhead.daemon import OpError
from railhead.multi import coordinator as coord_mod
from railhead.multi.gen import write_task
from railhead.multi.log import TICKS_PER_DAY, LogState
from railhead.multi.world import SharedWorld

D = TICKS_PER_DAY
COMPANIES = [{"id": 3, "name": "Company A", "slot": 0}, {"id": 5, "name": "Company B", "slot": 1},
             {"id": 7, "name": "Company C", "slot": 2}]


class FakeWorld:
    """Enough of railhead.world.World for SharedWorld: a tick clock, per-company money, a checksum."""

    def __init__(self) -> None:
        self.ticks, self.bound = 1000, -1
        self.money = {c["id"]: 100_000 for c in COMPANIES}
        self.digest = 0
        self.admin = SimpleNamespace(welcome=SimpleNamespace(map_x=64, map_y=64))
        self.bridge = SimpleNamespace(drain_events=list)
        self.calls: list[tuple] = []
        self.monitors: list[dict] = []
        self.delivered: dict[tuple, int] = {}  # (company, cargo, industry) -> cumulative deliveries

    def state(self) -> dict:
        d = self.ticks // D
        return {"ticks": self.ticks, "date": d, "ymd": f"1950-{(d // 30) % 12 + 1:02d}-01", "company": self.bound}

    def call(self, method, timeout=60.0, **args):
        return self.call_args(method, args)

    def call_args(self, method, args=None, timeout=60.0):
        args = args or {}
        self.calls.append((method, self.bound))
        if method == "bind_company":
            self.bound = args["company"]
            return {"company": self.bound}
        if method == "checksum":
            return {"h": hash((self.ticks, tuple(sorted(self.money.items())), self.digest)) & 0xFFFFFFFF}
        if method == "companies":
            return [{"id": c, "money": m, "loan": 0} for c, m in self.money.items()]
        if method in ("vehicles", "stations"):
            return []
        if method == "note":
            self.digest = (self.digest * 31 + args["d"]) & 0xFFFFFFFF
            return {}
        if method == "rail_track":
            return {}
        if method == "industries":
            return [{"id": 1, "type": 0, "x": 5, "y": 5, "produces": [{"cargo": 1}], "accepts": []},
                    {"id": 2, "type": 1, "x": 40, "y": 5, "produces": [], "accepts": [1]}]
        if method == "cargos":
            return [{"id": 0, "label": "PASS"}, {"id": 1, "label": "COAL"}]
        if method == "monitor_add":
            self.monitors.append({"id": len(self.monitors), "company": args["company"], "cargo": args["cargo"],
                                  "target": args["target"], "total": 0,
                                  "base": self.delivered.get((args["company"], args["cargo"], args["target"]), 0)})
            return len(self.monitors) - 1
        if method == "monitor_read":
            for m in self.monitors:
                m["total"] = self.delivered.get((m["company"], m["cargo"], m["target"]), 0) - m.get("base", 0)
            return [{k: v for k, v in m.items() if k != "base"} for m in self.monitors]
        if method == "company_loan":
            if args["amount"] > 500_000:
                raise BridgeError("ERR_BAD_ARG", "above the maximum loan")
            self.money[self.bound] += 1
            return {"loan": args["amount"]}
        raise AssertionError(f"unexpected call {method}")

    def step(self, *, ticks=None, days=None, until=None, timeout=None):
        assert ticks and days is None and until is None  # the coordinator steps in exact ticks only
        self.ticks += ticks
        for c in self.money:
            self.money[c] -= ticks  # running costs: every step changes the checksum
        return self.state() | {"reason": "elapsed"}

    def close(self) -> None:
        pass


class FakeCoordinator(coord_mod.Coordinator):
    def _load(self):
        sw = SharedWorld(FakeWorld(), self.companies)
        self._start_ticks, self._start_date = sw.ticks, sw.date
        from railhead.actionlog import ActionLog

        if self.log_path.exists() and self.log_path.stat().st_size > 0:
            raise AssertionError("the fake has no replay")
        sw.attach_log(ActionLog(self.log_path))
        self._start_market(sw)
        return sw, None


@pytest.fixture
def make(tmp_path):
    made = []

    def _make(horizon_days=60, budget=None, chunk_days=7, regions=None, market=None):
        tdir = tmp_path / "task"
        tdir.mkdir(exist_ok=True)
        if regions is not None:
            regions.save(tdir / "regions.json")
        write_task(tdir, "waybill-test", COMPANIES, horizon_days=horizon_days, agent_turn_s=3600, market=market)
        c = FakeCoordinator(tdir, tmp_path / "state", turn_budget_s=budget, chunk_days=chunk_days)
        threads = [threading.Thread(target=c.clock_loop, daemon=True),
                   threading.Thread(target=c.watchdog_loop, kwargs={"period": 0.02}, daemon=True)]
        for t in threads:
            t.start()
        made.append((c, threads))
        return c

    yield _make
    for c, threads in made:
        c.stop.set()
        with c.cv:
            c.cv.notify_all()
        for t in threads:
            t.join(timeout=5)


def check_log(c, horizon_days=60) -> list[dict]:
    recs = [r.raw for r in read_log(c.log_path)]
    st = LogState(recs[0], horizon_tick=recs[0]["ticks"] + horizon_days * D)
    for r in recs[1:]:
        st.check(r)
    return recs


def quiet(fn):
    """A background agent that the test leaves waiting: the coordinator stops it at teardown."""
    def run():
        try:
            fn()
        except OpError as e:
            assert e.code == "ERR_STOPPING"
    return run


def run_agents(scripts, timeout=20.0):
    errs = []

    def wrap(fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    ts = [threading.Thread(target=wrap, args=(s,), daemon=True) for s in scripts]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout)
        assert not t.is_alive(), "an agent hung"
    if errs:
        raise errs[0]


def test_lockstep_waits_of_different_lengths_step_to_each_target(make):
    c = make()
    out: dict[int, list] = {0: [], 1: [], 2: []}

    def agent(slot, days, n):
        def run():
            for _ in range(n):
                c.handle(slot, {"op": "act", "m": "company_loan", "a": {"amount": 10_000}})
                out[slot].append(c.handle(slot, {"op": "wait", "days": days}))
            c.handle(slot, {"op": "meta", "m": "submit", "a": {"confirm": "yes"}})  # else the world stalls
        return run

    run_agents([agent(0, 3, 4), agent(1, 5, 2), agent(2, 2, 6)])
    for slot, days in ((0, 3), (1, 5), (2, 2)):
        assert [r["ticks"] for r in out[slot]] == [1000 + days * D * (k + 1) for k in range(len(out[slot]))]
        assert all(r["reason"] == "elapsed" and r["days"] == days for r in out[slot])
    recs = check_log(c)
    steps = [r for r in recs if r["kind"] == "step"]
    assert [r["end_ticks"] - r["ticks"] for r in steps] == [2 * D, D, D, D, D, 2 * D, D, D, 2 * D]
    assert {r["company"] for r in recs if r["kind"] == "act"} == {3, 5, 7}
    # every act was sent for its own company
    acts = [cl for cl in c.sw.world.calls if cl[0] == "company_loan"]
    assert sorted(b for _, b in acts) == [3] * 4 + [5] * 2 + [7] * 6


def test_no_step_while_one_agent_still_plays(make):
    c = make()
    done = threading.Event()

    def waiter():
        c.handle(0, {"op": "wait", "days": 1})
        done.set()

    t = threading.Thread(target=waiter, daemon=True)
    t.start()
    time.sleep(0.2)
    assert not done.is_set() and c.sw.ticks == 1000
    run_agents([lambda: c.handle(1, {"op": "wait", "days": 1}), lambda: c.handle(2, {"op": "wait", "days": 1})])
    t.join(5)
    assert done.is_set() and c.sw.ticks == 1000 + D


def test_a_query_runs_while_others_wait_and_the_waiters_hold_no_world_lock(make):
    c = make()
    t = threading.Thread(target=quiet(lambda: c.handle(0, {"op": "wait", "days": 1})), daemon=True)
    t.start()
    time.sleep(0.1)
    assert c.world_lock.acquire(timeout=1)  # a blocked wait does not hold the world lock
    c.world_lock.release()
    assert c.handle(1, {"op": "query", "m": "companies"})
    assert c.handle(1, {"op": "info"})["company"] == 5


def test_a_straggler_gets_an_auto_wait_and_the_world_moves_on(make):
    c = make(budget=0.3)
    got = []
    run_agents([lambda: got.append(c.handle(0, {"op": "wait", "days": 1})),
                lambda: got.append(c.handle(1, {"op": "wait", "days": 1}))])  # slot 2 never answers
    assert len(got) == 2
    recs = check_log(c)
    autos = [r for r in recs if r["kind"] == "wait_req" and r.get("auto")]
    assert autos and autos[0]["company"] == 7 and autos[0]["a"] == {"days": 7}


def test_an_act_from_a_company_in_an_auto_wait_blocks_until_the_wait_ends(make):
    c = make(budget=0.2)
    # slots 0 and 1 wait long; slot 2 is slow, gets its auto-wait, then tries to act
    t0 = threading.Thread(target=lambda: c.handle(0, {"op": "wait", "days": 30}), daemon=True)
    t1 = threading.Thread(target=lambda: c.handle(1, {"op": "wait", "days": 30}), daemon=True)
    t0.start()
    t1.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not any(
            r.raw.get("auto") for r in read_log(c.log_path) if r.kind == "wait_req"):
        time.sleep(0.02)
    c.handle(2, {"op": "act", "m": "company_loan", "a": {"amount": 10_000}})
    acts = [r.raw for r in read_log(c.log_path) if r.kind == "act"]
    assert acts[-1]["company"] == 7 and acts[-1]["ticks"] >= 1000 + 7 * D  # after the auto-wait, not inside it
    c.handle(2, {"op": "wait", "days": 30})
    t0.join(5)
    t1.join(5)
    check_log(c)


def test_the_check_is_repeated_under_the_world_lock(make):
    """An auto-wait can start between the turn check and the world lock: the act must then wait for it."""
    c = make()
    real = c._await_turn
    hit = []

    def racy(company):
        real(company)
        if not hit:  # the watchdog fires right here, once
            hit.append(1)
            with c.world_lock:
                c._register(company, {"days": 7}, auto=True)

    c._await_turn = racy
    result = []

    def slow():
        result.append(c.handle(2, {"op": "act", "m": "company_loan", "a": {"amount": 10_000}}))
        c.handle(2, {"op": "wait", "days": 10})

    t = threading.Thread(target=quiet(slow), daemon=True)
    t.start()
    run_agents([lambda: c.handle(0, {"op": "wait", "days": 10}), lambda: c.handle(1, {"op": "wait", "days": 10})])
    t.join(5)
    assert result
    check_log(c)  # an act inside the auto-wait would break the v3 rules


def test_submit_leaves_the_lockstep_and_blocks_more_play(make):
    c = make()
    c.handle(1, {"op": "meta", "m": "submit", "a": {"confirm": "yes"}})
    with pytest.raises(OpError) as e:
        c.handle(1, {"op": "act", "m": "company_loan", "a": {"amount": 10_000}})
    assert e.value.code == "ERR_SUBMITTED"
    run_agents([lambda: c.handle(0, {"op": "wait", "days": 2}), lambda: c.handle(2, {"op": "wait", "days": 2})])
    assert c.sw.ticks == 1000 + 2 * D
    check_log(c)


def test_waits_stop_at_the_horizon_and_play_ends_there(make):
    c = make(horizon_days=5)
    got = []
    run_agents([lambda s=s: got.append(c.handle(s, {"op": "wait", "days": 30})) for s in range(3)])
    assert {r["reason"] for r in got} == {"horizon"} and c.sw.ticks == 1000 + 5 * D
    for op in ({"op": "wait", "days": 1}, {"op": "act", "m": "company_loan", "a": {"amount": 10_000}}):
        with pytest.raises(OpError) as e:
            c.handle(0, op)
        assert e.value.code == "ERR_HORIZON"
    assert c.handle(0, {"op": "info"})["days_left"] == 0
    check_log(c, horizon_days=5)


def test_a_refused_act_is_logged_for_its_company(make):
    c = make()
    with pytest.raises(BridgeError):
        c.handle(1, {"op": "act", "m": "company_loan", "a": {"amount": 990_000}})
    acts = [r.raw for r in read_log(c.log_path) if r.kind == "act"]
    assert acts == [acts[0]] and acts[0]["company"] == 5 and acts[0]["ok"] is False


def test_wait_conditions_cap_steps_at_one_chunk(make):
    c = make(chunk_days=7)
    run_agents([lambda: c.handle(0, {"op": "wait", "days": 20, "until": ["new_month"]}),
                lambda: c.handle(1, {"op": "wait", "days": 20}), lambda: c.handle(2, {"op": "wait", "days": 20})])
    steps = [r for r in check_log(c) if r["kind"] == "step"]
    assert all(r["end_ticks"] - r["ticks"] <= 7 * D for r in steps)
    assert steps[-1]["end_ticks"] <= 1000 + 20 * D


def test_forks_and_unknown_waits_are_refused(make):
    c = make()
    for req, code in (({"op": "fork_create", "name": "f"}, "ERR_NOT_SUPPORTED"),
                      ({"op": "wait", "days": 0}, "ERR_BAD_ARG"),
                      ({"op": "wait", "days": 1, "foo": 1}, "ERR_BAD_ARG"),
                      ({"op": "query", "m": "company_loan"}, "ERR_NOT_A_QUERY")):
        with pytest.raises(OpError) as e:
            c.handle(0, req)
        assert e.value.code == code


# ---------------------------------------------------------------------- rh socket selection
def test_rh_uses_the_agent_socket_and_never_starts_a_daemon(monkeypatch, tmp_path):
    from railhead import cli

    monkeypatch.setenv("RAILHEAD_SOCKET", str(tmp_path / "x.sock"))
    assert client_mod.Client().path == tmp_path / "x.sock"
    assert client_mod.Client(tmp_path).path == client_mod.socket_path(tmp_path)  # explicit state dir wins
    called = []
    monkeypatch.setattr("railhead.daemon.ensure_up", lambda *a, **k: called.append(1))
    assert cli._client().path == tmp_path / "x.sock" and not called
    monkeypatch.delenv("RAILHEAD_SOCKET")
    monkeypatch.setenv("RAILHEAD_STATE", str(tmp_path))
    monkeypatch.setenv("RAILHEAD_SLOT", "2")
    assert client_mod.Client().path == client_mod.agent_socket_path(None, 2)
    monkeypatch.delenv("RAILHEAD_SLOT")
    assert client_mod.env_socket() is None


def test_long_agent_socket_paths_are_shortened(tmp_path):
    deep = tmp_path / ("d" * 120)
    p = client_mod.agent_socket_path(deep, 1)
    assert len(str(p)) <= 104 and p != client_mod.agent_socket_path(deep, 2)
    assert client_mod.control_socket_path(deep) not in (p, client_mod.agent_socket_path(deep, 2))
    assert Path(client_mod.agent_socket_path(tmp_path, 0)).name in ("agent-0.sock",) or len(str(tmp_path)) > 80


def test_franchise_refuses_an_out_of_region_build_and_does_not_log_it(make):
    from railhead.multi.regions import RegionMap

    rm = RegionMap(64, 64, {0: [[0, 0, 20, 63]], 1: [[21, 0, 40, 63]], 2: [[41, 0, 63, 63]]})
    c = make(regions=rm)
    ok = c.handle(0, {"op": "act", "m": "rail_track", "a": {"tile": "5,5", "track": "ne_sw"}})
    assert ok == {}
    with pytest.raises(OpError) as e:
        c.handle(0, {"op": "act", "m": "rail_track", "a": {"tile": "30,5", "track": "ne_sw"}})
    assert e.value.code == "ERR_FRANCHISE" and "30,5" in e.value.msg
    c.handle(1, {"op": "act", "m": "rail_track", "a": {"tile": "30,5", "track": "ne_sw"}})
    c.handle(0, {"op": "act", "m": "company_loan", "a": {"amount": 1000}})  # not a build: no check
    acts = [r for r in check_log(c) if r["kind"] == "act"]
    assert [(r["company"], r["m"]) for r in acts] == [(3, "rail_track"), (5, "rail_track"), (3, "company_loan")]


# ---------------------------------------------------------------------- the market on the fake engine
def market_cfg(arm="enforced"):
    from railhead.multi.market import MarketConfig

    return MarketConfig(arm=arm, seed=4, start_balances={1: 5000}, quarter_days=30,
                        tender={"per_quarter": 1, "bid_ticks": 2 * D, "window_ticks": 10 * D,
                                "quantity_base": 10, "quantity_jitter_pct": 0})


def ex(c, slot, m, **a):
    return c.handle(slot, {"op": "ex", "m": m, "a": a})


def test_market_commands_views_and_errors(make):
    c = make(horizon_days=60, market=market_cfg())
    info = c.handle(1, {"op": "info"})
    assert info["market"]["arm"] == "enforced" and info["market"]["ledger_balance"] == 5000
    r = ex(c, 1, "offer", to="Company A", seller="A", buyer="me", cargo=1, industry=2, quantity=10,
           window_start="+0d", deadline="+20d", price=1000, penalty_per_unit=50)
    cid = r["contract"]["contract_id"]
    assert r["contract"]["terms"]["deadline"] == 1000 + 20 * D and r["contract"]["terms"]["buyer"] == "5"
    with pytest.raises(OpError) as e:
        ex(c, 0, "offer", to="B", seller="me", buyer="B", cargo=9, industry=2, quantity=1, window_start=0,
           deadline="+5d", price=1, penalty_per_unit=0)
    assert e.value.code == "ERR_BAD_ARG" and "cargo 9" in e.value.msg
    with pytest.raises(OpError) as e:
        ex(c, 0, "offer", to="B", seller="me", buyer="B", cargo=1, industry=2, quantity=1, window_start=0,
           deadline="+90d", price=1, penalty_per_unit=0)
    assert "after the end of the game" in e.value.msg
    with pytest.raises(OpError) as e:
        ex(c, 2, "accept", contract_id=cid)
    assert e.value.code == "ERR_NOT_PARTY"
    with pytest.raises(OpError) as e:
        ex(c, 0, "frobnicate")
    assert e.value.code == "ERR_UNKNOWN_OP"
    ex(c, 0, "msg", to="B", text="deal")
    ex(c, 0, "accept", contract_id=cid.lower())
    # views: C sees neither the contract nor the message; B sees its inbox
    assert ex(c, 2, "contracts") == [] and ex(c, 2, "inbox") == []
    assert [m["text"] for m in ex(c, 1, "inbox")] == ["deal"]
    view = ex(c, 1, "view")
    assert set(view) >= {"market", "contracts", "inbox", "ledger"} and "records" not in view
    assert view["ledger"]["balances"]["escrow:" + cid] == 1000
    # history never gives system records or another company's records
    hist = c.handle(2, {"op": "history", "n": 100})
    assert all(h.get("company") == 7 for h in hist if h["kind"] != "step")
    rows = c.handle(1, {"op": "query", "m": "monitor_read", "a": {}})
    assert rows == []  # the monitor is company A's (the seller): B cannot read it


def test_market_settles_at_the_deadline_with_a_forced_wake(make):
    c = make(horizon_days=60, market=market_cfg())
    world = c.sw.world
    r = ex(c, 0, "offer", to="B", seller="me", buyer="B", cargo=1, industry=2, quantity=10, window_start="+0d",
           deadline="+3d", price=1000, penalty_per_unit=50)
    ex(c, 1, "accept", contract_id=r["contract"]["contract_id"])
    world.delivered[(3, 1, 2)] = 4  # A delivers 4 of 10
    out = {}
    def play(s):
        out[s] = c.handle(s, {"op": "wait", "days": 20})
        if s != 2:
            c.handle(s, {"op": "meta", "m": "submit", "a": {"confirm": "yes"}})

    run_agents([lambda s=s: play(s) for s in (0, 1, 2)])
    assert out[0]["reason"] == "deadline" and out[1]["reason"] == "deadline" and out[0]["days"] == 3
    assert out[2]["reason"] == "elapsed"
    recs = check_log(c)
    settle = [r["x"] for r in recs if r["kind"] == "ex" and r["x"]["op"] == "settle"]
    assert settle and settle[0]["delivered"] == 4 and settle[0]["outcome"] == "partial"
    led = ex(c, 0, "ledger")["balances"]
    assert led["3"] == 1000 * 4 // 10 - 50 * 6 and led["5"] == 5000 - 1000 + 600 + 300
    c.market.ex.check_invariants()


def test_offer_received_wakes_a_waiting_company_after_one_tick(make):
    c = make(horizon_days=60, market=market_cfg())
    out = {}
    t1 = threading.Thread(target=lambda: out.__setitem__(1, c.handle(1, {"op": "wait", "days": 25,
                                                                        "until": ["offer_received"]})), daemon=True)
    t1.start()
    time.sleep(0.2)
    ex(c, 0, "offer", to="B", seller="me", buyer="B", cargo=1, industry=2, quantity=10, window_start="+0d",
       deadline="+20d", price=10, penalty_per_unit=0)
    threading.Thread(target=quiet(lambda: c.handle(0, {"op": "wait", "days": 25})), daemon=True).start()
    threading.Thread(target=quiet(lambda: c.handle(2, {"op": "wait", "days": 25})), daemon=True).start()
    t1.join(10)
    assert out[1]["reason"] == "offer_received" and out[1]["ticks"] == 1000 + 1


def test_market_wake_kinds_need_a_market(make):
    c = make()
    with pytest.raises(OpError) as e:
        c.handle(0, {"op": "wait", "days": 3, "until": ["message_received"]})
    assert e.value.code == "ERR_BAD_ARG"
    with pytest.raises(OpError) as e:
        ex(c, 0, "market")
    assert e.value.code == "ERR_NO_MARKET"
