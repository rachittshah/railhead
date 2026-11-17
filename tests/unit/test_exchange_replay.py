"""Replay property and conservation under random operation sequences (fixed seeds)."""

import json
import random

import pytest

from railhead.exchange import Exchange, ExchangeError, TenderConfig

CO = ["a", "b", "c", "d"]
WORLD = [
    {"id": i, "type": "x", "produces": [i % 3], "accepts": [(i + 1) % 3], "tile": [i * 7 % 50, i * 13 % 50],
     "region": i % 4}
    for i in range(12)
]


def json_round_trip(records):
    return json.loads(json.dumps(records))


def random_session(seed, arm, steps=400):
    """Drive an exchange with random (often invalid) commands. Uses only a seeded RNG."""
    rng = random.Random(seed)
    ex = Exchange(CO, arm=arm, seed=seed, credit_limit=rng.choice([None, 3000]),
                  tender_config=TenderConfig(per_quarter=2, bid_ticks=30, window_ticks=300))
    tick, quarter, rejected = 0, 0, 0
    delivered = {}

    def deliveries(company, cargo, industry, start, end):
        key = (company, cargo, industry, start, end)
        return delivered.setdefault(key, rng.choice([0, 0, 5, 20, 50, 100, 200]))

    for _ in range(steps):
        tick += rng.choice([0, 0, 1, 5, 20])
        ids = sorted(ex.contract_map) or ["C1"]
        tids = sorted(ex.tenders) or ["T1"]
        me, other = rng.sample(CO, 2)
        terms = {"seller": me, "buyer": other, "cargo": rng.randint(0, 2), "industry": rng.randint(0, 11),
                 "quantity": rng.randint(1, 150), "window_start": tick + rng.randint(-5, 50),
                 "deadline": tick + rng.randint(1, 400), "price": rng.randint(0, 3000),
                 "penalty_per_unit": rng.randint(0, 30), "bond": rng.randint(0, 500)}
        if rng.random() < 0.5:
            terms["seller"], terms["buyer"] = other, me
        terms["window_start"] = max(0, terms["window_start"])
        op = rng.choice(["offer", "offer_public", "counter", "accept", "accept", "withdraw", "amend", "amend",
                         "bid", "msg", "pay", "settle", "tenders"])
        try:
            if op == "offer":
                ex.offer(tick, me, "supply", terms, to=other, expires_tick=tick + rng.randint(1, 100))
            elif op == "offer_public":
                t = dict(terms)
                t["buyer" if t["seller"] == me else "seller"] = None
                ex.offer(tick, me, "supply", t, expires_tick=tick + rng.randint(1, 100))
            elif op == "counter":
                c = ex.contract_map.get(rng.choice(ids))
                t = dict(c.terms) if c else terms
                if c:
                    for f in ("seller", "buyer"):
                        t[f] = t[f] or me
                    t["price"] = rng.randint(0, 3000)
                ex.counter(tick, me, rng.choice(ids), t, expires_tick=tick + rng.randint(1, 100))
            elif op == "accept":
                open_ = [c for c in ex.contract_map.values() if c.status in ("offered", "countered")]
                if open_ and rng.random() < 0.8:          # mostly well-formed: the right party answers
                    c = rng.choice(sorted(open_, key=lambda c: int(c.id[1:])))
                    who = c.responder or rng.choice([x for x in CO if x != c.proposer])
                    ex.accept(tick, who, c.id)
                else:
                    ex.accept(tick, rng.choice(CO), rng.choice(ids))
            elif op == "withdraw":
                ex.withdraw(tick, rng.choice(CO), rng.choice(ids))
            elif op == "amend":
                cid = rng.choice(ids)
                c = ex.contract_map.get(cid)
                if c is not None and c.pending_amendment and rng.random() < 0.7:
                    who = [p for p in (c.terms["seller"], c.terms["buyer"]) if p != c.pending_amendment["by"]]
                    ex.amend(tick, who[0], cid, c.pending_amendment["terms"])
                else:
                    t = dict(c.terms) if c else terms
                    t.update(price=rng.randint(0, 3000), bond=rng.randint(0, 500),
                             deadline=tick + rng.randint(1, 300))
                    ex.amend(tick, t["seller"] if rng.random() < 0.5 else t["buyer"], cid, t)
            elif op == "bid":
                ex.tender_bid(tick, me, rng.choice(tids), rng.randint(0, 20000))
            elif op == "msg":
                ex.msg(tick, me, other, f"note {rng.randint(0, 99)}")
            elif op == "pay":
                ex.pay(tick, me, other, rng.randint(-5, 2000))
            elif op == "settle":
                ex.settle_due(tick, deliveries)
            elif op == "tenders":
                ex.open_tenders(tick, quarter, WORLD)
                quarter += 1
        except ExchangeError:
            rejected += 1
        ex.check_invariants()
    ex.settle_due(tick + 10_000, deliveries)
    ex.check_invariants()
    return ex, rejected


@pytest.mark.parametrize("arm", ["enforced", "cheap_talk"])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_random_sessions_conserve_money_and_replay_identically(seed, arm):
    ex, rejected = random_session(seed, arm)
    statuses = {c.status for c in ex.contract_map.values()}
    assert rejected > 0 and "settled" in statuses           # the session exercised both paths
    if arm == "cheap_talk":
        assert all(h.reason == "pay" for h in ex.accounts.history)
    records = json_round_trip(ex.records)
    again = Exchange.replay(records)
    assert again.state() == ex.state()
    assert json.loads(json.dumps(ex.state())) == ex.state()
    assert again.records == ex.records


def test_random_session_is_deterministic():
    a, _ = random_session(7, "enforced", steps=200)
    b, _ = random_session(7, "enforced", steps=200)
    assert a.records == b.records


def test_outcome_mix_in_random_sessions():
    outcomes = set()
    for seed in range(1, 4):
        ex, _ = random_session(seed, "enforced")
        outcomes |= {c.outcome for c in ex.contract_map.values() if c.outcome}
    assert outcomes == {"kept", "partial", "broken"}


def test_replay_of_a_prefix_matches_the_live_state_at_that_point():
    ex = Exchange(["a", "b"])
    ex.offer(0, "a", "supply", {"seller": "a", "buyer": "b", "cargo": 1, "industry": 2, "quantity": 10,
                                "window_start": 0, "deadline": 100, "price": 50, "penalty_per_unit": 1}, to="b")
    mid = ex.state()
    ex.accept(1, "b", "C1")
    assert Exchange.replay(ex.records[:2]).state() == mid


def test_rejected_command_leaves_no_record_and_no_change():
    ex, _ = random_session(5, "enforced", steps=80)
    before = json.dumps(ex.state(), sort_keys=True)
    for bad in (lambda: ex.accept(ex.tick, "a", "C9999"), lambda: ex.pay(ex.tick, "a", "a", 5),
                lambda: ex.offer(ex.tick, "a", "loan", {}, to="b"), lambda: ex.msg(ex.tick, "a", "b", "")):
        with pytest.raises(ExchangeError):
            bad()
    assert json.dumps(ex.state(), sort_keys=True) == before


@pytest.mark.parametrize("mutate, code", [
    (lambda r: r.update(seq=99), "bad_record"),
    (lambda r: r.update(extra=1), "bad_record"),
    (lambda r: r.pop("company"), "bad_record"),
    (lambda r: r.update(op="nope"), "bad_record"),
    (lambda r: r.update(tick=-1), "bad_record"),
    (lambda r: r.update(amount=10**9 + 0.5), "bad_amount"),
])
def test_forged_or_malformed_records_are_rejected(mutate, code):
    ex = Exchange(["a", "b"])
    ex.pay(5, "a", "b", 10)
    rec = dict(ex.records[1])
    log = [ex.records[0]]
    mutate(rec)
    fresh = Exchange.replay(log)
    with pytest.raises(ExchangeError) as e:
        fresh.apply(rec)
    assert e.value.code == code


def test_init_record_rules():
    ex = Exchange(["a", "b"])
    with pytest.raises(ExchangeError) as e:
        ex.apply({**ex.records[0], "seq": 1})
    assert e.value.code == "bad_record"
    with pytest.raises(ExchangeError) as e:
        Exchange(["a", "b"], arm="honour")
    assert e.value.code == "bad_arm"
    with pytest.raises(ExchangeError) as e:
        Exchange([])
    assert e.value.code == "bad_company"
    with pytest.raises(ExchangeError) as e:
        Exchange.replay([{**ex.records[0], "tender_config": {"nope": 1}}])
    assert e.value.code == "bad_config"
    with pytest.raises(ExchangeError) as e:
        Exchange.replay([{"op": "pay", "seq": 0, "tick": 0, "company": "a", "to": "b", "amount": 1, "memo": ""}])
    assert e.value.code == "bad_record"                 # the first record must be init


def test_system_records_cannot_carry_a_company():
    ex = Exchange(["a", "b"])
    with pytest.raises(ExchangeError) as e:
        ex.apply({"op": "expire", "seq": 1, "tick": 0, "company": "a", "contract_id": "C1"})
    assert e.value.code == "bad_record"
