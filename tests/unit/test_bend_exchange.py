"""Differential tests of the Bend 2 exchange core (bend/exchange/main.bend) against the Python reference.

The binary is built from the current sources into a temp dir (``RAILHEAD_BEND_EX`` overrides it). Skipped
when neither a binary nor the ``bend`` compiler is available. Sizes scale with ``RAILHEAD_BEND_N`` (cases
per corpus kind, default 150) and the seed with ``RAILHEAD_BEND_SEED`` (default 7), so a long soak is
``RAILHEAD_BEND_N=5000 pytest tests/unit/test_bend_exchange.py``.

Beyond the oracle's corpus kinds (sessions, mutations cut after the mutated record, edge cases), two
generators here keep the log going after a mutation, so values that a check accepts flow into later records:
tender terms are not schema-checked (negative penalties reach settlement and obligations), and Python
``==`` sites see floats and bools that equal the computed int (huge prices around 2^53 and 2^70).
"""

import copy
import os
import random
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from railhead.exchange import Exchange, corpus
from railhead.exchange.bend import BINARY_ENV, build, compare, find_bend, reference

N = int(os.environ.get("RAILHEAD_BEND_N", "150"))
SEED = int(os.environ.get("RAILHEAD_BEND_SEED", "7"))


@pytest.fixture(scope="module")
def binary(tmp_path_factory):
    env = os.environ.get(BINARY_ENV)
    if env:
        return Path(env)
    if find_bend() is None:
        pytest.skip("bend is not installed")
    return build(tmp_path_factory.mktemp("bend") / "waybill-ex")


def _check_all(binary, cases):
    """Compare every case; fail with the first few differences."""
    with ThreadPoolExecutor(max(1, (os.cpu_count() or 2))) as pool:
        results = list(pool.map(lambda recs: compare(recs, binary), cases))
    bad = [(i, r) for i, r in enumerate(results) if not r.match]
    msg = "\n".join(f"case {i} ({r.n_records} records): {r.diff}" for i, r in bad[:5])
    assert not bad, f"{len(bad)} of {len(cases)} cases differ:\n{msg}"
    return results


def test_edge_cases_match(binary):
    _check_all(binary, corpus.edge())


def test_sessions_match(binary):
    _check_all(binary, corpus.sessions(SEED, N))


def test_mutations_match(binary):
    _check_all(binary, corpus.mutations(SEED + 1, N))


# ============================================================================================== continued
def _renumber(records):
    """Make each dict record's seq its position (the mutated record keeps its own)."""
    out = copy.deepcopy(records)
    for i, r in enumerate(out):
        if isinstance(r, dict) and isinstance(r.get("seq"), int) and not isinstance(r.get("seq"), bool):
            r["seq"] = i
    return out


def continued_mutations(seed, n):
    """Each case: a session with one record mutated in place (corpus._mutate) and the rest of the log kept."""
    rng = random.Random(seed)
    out = []
    names = [m for m in corpus.MUTATIONS if m not in ("non_dict", "unhashable_op", "drop_field", "rename_field")]
    while len(out) < n:
        recs = corpus.session(random.Random(rng.randrange(2 ** 32)))
        companies = list(recs[0].get("companies") or [])
        i = rng.randrange(1, len(recs))
        m = corpus._mutate(rng, rng.choice(names), recs[i], recs[i - 1]["tick"], companies)
        if m is None:
            continue
        out.append(recs[:i] + [m] + copy.deepcopy(recs[i + 1:]))
    return out


def _flow_mutant(rng, recs):
    """One value-carrying mutation that later records may still accept."""
    recs = copy.deepcopy(recs)
    opens = [r for r in recs if r["op"] == "tender_open" and r["tenders"]]
    awards = [r for r in recs if r["op"] == "tender_award" and r["price"] is not None]
    obls = [r for r in recs if r["op"] == "obligation_settle"]
    bids = [r for r in recs if r["op"] in ("tender_bid", "msg")]
    pick = rng.randrange(5)
    if pick == 0 and opens:  # tender numbers no check looks at: negative penalties, cargo, industry
        t = rng.choice(rng.choice(opens)["tenders"])
        k = rng.choice(["penalty_per_unit", "penalty_per_unit", "cargo", "industry", "distance", "region"])
        t[k] = rng.choice([-1, -t[k] - 1, -(2 ** 70), 0])
    elif pick == 1 and awards:  # a float or bool price equal to the computed one
        r = rng.choice(awards)
        r["price"] = float(r["price"]) if r["price"] != 1 else True
    elif pick == 2 and obls:
        r = rng.choice(obls)
        k = rng.choice(["collected", "paid"])
        r[k] = float(r[k]) if r[k] not in (0, 1) else bool(r[k])
    elif pick == 3 and bids:  # the stored seq echoes what the record wrote
        r = rng.choice(bids)
        r["seq"] = float(r["seq"]) if r["seq"] != 1 else True
    else:
        r = recs[0]
        r["rules"] = rng.choice([True, 1.0, 2.0, 0, None, "", [], {}, -0.0, False]) if "rules" in r else 2.0
    return recs


def flow_mutations(seed, n):
    rng = random.Random(seed)
    return [_flow_mutant(rng, corpus.session(random.Random(rng.randrange(2 ** 32)))) for _ in range(n)]


def test_continued_mutations_match(binary):
    _check_all(binary, continued_mutations(SEED + 2, N))


def test_flow_mutations_match(binary):
    results = _check_all(binary, flow_mutations(SEED + 3, N))
    assert any('"ok"' in r.expected for r in results)  # some mutants are accepted and reach the state


# ============================================================================================== hand-written
HUGE = 2 ** 70
_init, _rec = corpus._init, corpus._rec


def _tender_log(arm, ppu, price, delivered, max_price=10 ** 22, quantity=10):
    """init, a tender bid by a, its award, settlement with ``delivered`` and the obligation settlements, as
    the drivers write them; a record Python rejects is kept as the last one (it is the case under test)."""
    from railhead.exchange import Exchange
    from railhead.exchange.errors import ExchangeError
    from railhead.exchange.tenders import TenderConfig

    ex = Exchange(["a", "b"], arm=arm, tender_config=TenderConfig(per_quarter=1, bid_ticks=10, window_ticks=100))
    attempted = [copy.deepcopy(ex.records[0])]
    apply = ex.apply

    def hook(r):
        attempted.append(copy.deepcopy(r))
        return apply(r)

    ex.apply = hook
    try:
        ex._record("tender_open", 0, None, quarter=0,
                   tenders=[corpus._tender("T1", 0, max_price, quantity=quantity, ppu=ppu)])
        ex.tender_bid(1, "a", "T1", price)
        ex.settle_due(10, lambda *a: delivered)
        ex.settle_due(110, lambda *a: delivered)
        ex.settle_due(110 + 2220, lambda *a: delivered)
    except ExchangeError:
        pass
    return attempted


@pytest.mark.parametrize("arm", ["enforced", "cheap_talk"])
@pytest.mark.parametrize("ppu,price,delivered", [(-5, 7, 0), (-5, 0, 0), (-5, 7, 4), (5, 0, 3), (-HUGE, HUGE, 1),
                                                 (3, HUGE, 9), (0, 2 ** 53 + 1, 10)])
def test_tender_terms_flow(binary, arm, ppu, price, delivered):
    recs = _tender_log(arm, ppu, price, delivered)
    _check_all(binary, [recs])


def test_python_equality_sites(binary):
    cases = []
    for price in (HUGE, 2 ** 53, 2 ** 53 + 1, 2 ** 53 + 2, 3):
        recs = _tender_log("enforced", 1, price, 10)
        award = next((j for j, r in enumerate(recs) if r["op"] == "tender_award"), None)
        if award is None:
            continue
        for v in (float(price), float(price) + 2.0 ** 20, price + 1, True if price == 1 else float(price) * 2):
            c = copy.deepcopy(recs[:award + 1])
            c[award]["price"] = v
            cases.append(c)
    # seq: bool and float that equal the position, stored as written in a message
    base = [_init()]
    for seq in (1.0, True, 1, -0.0, 1.5):
        cases.append(base + [_rec("msg", seq, 1, "a", to="b", text="hi")])
    cases.append([_init(seq=-0.0), _rec("msg", True, 1, "a", to="b", text="x"),
                  _rec("msg", 2.0, 2, "b", to="a", text="y")])
    _check_all(binary, cases)
    assert reference(cases[-1]).startswith('{"ok"')


def _huge_penalty_log(digits: int) -> list[dict]:
    """A cheap-talk supply contract broken outright, with quantity and penalty per unit of ``digits`` digits:
    the penalty obligation has about twice as many digits (independent review, 2026-11-20)."""
    ex = Exchange(["a", "b"], arm="cheap_talk")
    big = 10 ** digits
    terms = {"seller": "a", "buyer": "b", "cargo": 1, "industry": 1, "quantity": big, "window_start": 1,
             "deadline": 10, "price": 0, "penalty_per_unit": big}
    ex.offer(0, "a", "supply", terms, to="b")
    ex.accept(0, "b", "C1")
    ex._record("settle", 10, None, contract_id="C1", delivered=0, parts=[], outcome="broken")
    return ex.records


@pytest.mark.parametrize("digits", [20, 2200])
def test_integers_beyond_python_str_limit(binary, digits):
    """A computed amount past CPython's 4,300-digit int/str guard is printed on both sides, not an error."""
    recs = _huge_penalty_log(digits)
    assert reference(recs).startswith('{"ok":')
    _check_all(binary, [recs])
