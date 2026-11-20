"""Deterministic corpus of exchange record sequences for differential testing (railhead.exchange.bend).

Four kinds, each a list of cases (one case = the list of exchange records of one log):

- ``sessions``: random agents drive the real command API (``offer``, ``counter``, ``accept``, ``withdraw``,
  ``amend``, ``tender_bid``, ``msg``, ``pay``) and the coordinator drivers (``open_tenders``, ``advance``,
  ``settle_due``) over random ticks: both arms, rules 1 and 2, credit limits, start balances, public and
  addressed offers, supply and licence, tender subcontracts, tagged and over-payments, unicode text (outside
  the BMP, lone surrogates, control characters, exactly at the length limit) and integers up to ~2^70.
  Rejected commands leave no record; the generator keeps going.
- ``mutations``: each session mutated once at several places (wrong seq, reversed tick, int -> float / bool /
  string / null, dropped / added / renamed fields, unknown ids, wrong award / outcome, overpay, huge and
  negative ints, non-dict records, an unhashable op), the log cut right after the mutated record.
- ``edge``: hand-written records the command API cannot make (init without ``rules``, pay without
  ``contract_id``, odd ``settle.parts``, floats and bools at Python ``==`` sites, NaN / Infinity, floats
  around 2^53). Fixed list: ``seed`` is ignored.
- ``captured``: the record sequences the unit tests feed ``Exchange.apply`` (tests/conftest.py,
  ``RAILHEAD_EX_CAPTURE``), read from a JSONL file with one case (a JSON array of records) per line.
  :func:`run_capture` makes that file by running the exchange unit tests in a subprocess. It is not
  committed: the unit tests' random sessions make it ~360 MB (12k cases, mostly rejected records after long
  accepted prefixes).

Randomness comes only from ``random.Random`` seeded per case (``seed * 1_000_003 + index``), and no choice
iterates a set, so the same seed gives a byte-identical corpus on every run and ``PYTHONHASHSEED``.
Licence windows stay small: accepting a licence lists one instalment per quarter of its window
(``due_ticks``), so a 2^60-tick window would exhaust memory instead of giving an answer.
"""

from __future__ import annotations

import copy
import json
import os
import random
import subprocess
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .bend import REPO, normalize, outcome_label, reference
from .contracts import LIVE_STATUSES, OPEN_STATUSES, PARTY_FIELDS, Contract
from .errors import ExchangeError
from .exchange import (
    GRACE_TICKS,
    INSTALMENT_TICKS,
    MAX_MEMO_CHARS,
    MAX_MESSAGE_CHARS,
    RECORD_FIELDS,
    Exchange,
)
from .ledger import EXCHANGE
from .tenders import TenderConfig

KINDS = ("sessions", "mutations", "edge", "captured")
RECORD_OPS = tuple(sorted(RECORD_FIELDS))
CAPTURE_FILE = "exchange_captured.jsonl"
CAPTURE_TESTS = ("tests/unit/test_exchange_*.py", "tests/unit/test_multi_market.py", "tests/unit/test_waybill_*.py")
HUGE = 2 ** 70
MUTANTS_PER_SESSION = 4

Records = list[Any]

# Company id sets. The last ones sort differently by code point (Python, JSON sort_keys) and by UTF-16 unit.
NAME_SETS: tuple[tuple[str, ...], ...] = (
    ("0", "1", "2", "3"),
    ("a", "b", "c", "d", "e"),
    ("Zed", "alpha", "Beta", "gamma"),
    ("é", "e", "中", "z"),
    ("", "\U0001f600", "￿", "\U00010000"),
)

# Single code points for messages (each counts 1 toward MAX_MESSAGE_CHARS after the JSON round trip).
CHARS = ("a", "Z", "0", " ", "\"", "\\", "/", "\n", "\t", "\r", "\b", "\f", "\x00", "\x01", "\x1f", "\x7f",
         "\x80", "é", " ", "﻿", "￿", "中", "\U0001f600", "\U0010ffff", "\U00010000",
         "\ud800", "\udbff", "\udc00", "\udfff")
WORDS = ("hello", "deal?", "price 5000", "été", "中文", "\U0001f69a\U0001f4e6",
         "line\nbreak", "tab\there", "quote\"s", "back\\slash", "\x00nul", "lone \ud83d high", "lone \ude00 low")


# ============================================================================================== sessions
def _run(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> bool:
    """Call one command; a rejected command (ExchangeError) is fine and leaves no record."""
    try:
        fn(*args, **kwargs)
    except ExchangeError:
        return False
    return True


def _text(rng: random.Random) -> Any:
    r = rng.random()
    if r < 0.04:
        return "".join(rng.choice(CHARS) for _ in range(MAX_MESSAGE_CHARS))      # exactly at the limit
    if r < 0.06:
        return "".join(rng.choice(CHARS) for _ in range(MAX_MESSAGE_CHARS + 1))  # one over (rejected)
    if r < 0.08:
        return rng.choice(["", None, 7])                                         # rejected
    if r < 0.3:
        return "".join(rng.choice(CHARS) for _ in range(rng.randint(1, 40)))
    return " ".join(rng.choice(WORDS) for _ in range(rng.randint(1, 6)))


def _money(rng: random.Random, lo: int = 0, hi: int = 50_000) -> int:
    r = rng.random()
    if r < 0.08:
        return HUGE + rng.randint(-1000, 1000)
    if r < 0.12:
        return rng.randint(2 ** 53 - 5, 2 ** 64 + 5)
    if r < 0.2:
        return lo
    return rng.randint(lo, hi)


def _world(rng: random.Random) -> list[dict[str, Any]]:
    n = rng.randint(4, 12)
    return [{"id": i, "type": rng.randint(0, 9), "produces": sorted({rng.randint(0, 3) for _ in range(rng.randint(0, 2))}),
             "accepts": sorted({rng.randint(0, 3) for _ in range(rng.randint(1, 2))}),
             "tile": [rng.randint(0, 255), rng.randint(0, 255)], "region": rng.randint(0, 3)} for i in range(n)]


def _supply_terms(rng: random.Random, seller: Any, buyer: Any, tick: int) -> dict[str, Any]:
    ws = max(0, tick + rng.randint(-100, 600))
    t = {"seller": seller, "buyer": buyer, "cargo": rng.randint(0, 3), "industry": rng.randint(0, 11),
         "quantity": rng.choice([1, rng.randint(1, 300), rng.randint(1, 300), HUGE]),
         "window_start": ws, "deadline": ws + rng.choice([rng.randint(1, 3000), rng.randint(1, 300), 0]),
         "price": _money(rng), "penalty_per_unit": _money(rng, 0, 400)}
    if rng.random() < 0.6:
        t["bond"] = _money(rng, 0, 5000)
    return t


def _licence_terms(rng: random.Random, grantor: Any, grantee: Any, tick: int) -> dict[str, Any]:
    ws = max(0, tick + rng.randint(-100, 3000))
    x0, y0 = rng.randint(0, 200), rng.randint(0, 200)
    return {"grantor": grantor, "grantee": grantee,
            "rect": [x0, y0, x0 + rng.randint(-2, 50), y0 + rng.randint(0, 50)],
            "window_start": ws, "deadline": ws + rng.choice([rng.randint(1, 6 * INSTALMENT_TICKS),
                                                              rng.randint(1, INSTALMENT_TICKS), 0]),
            "fee": rng.choice([0, rng.randint(1, 9), rng.randint(1, 100_000), HUGE + rng.randint(0, 99)])}


def _tweak(rng: random.Random, terms: dict[str, Any], tick: int) -> dict[str, Any]:
    """New terms for a counter or an amendment: some numbers changed, the parties kept."""
    t = copy.deepcopy(terms)
    for k in sorted(t):
        if rng.random() < 0.35 and k in ("price", "bond", "fee", "penalty_per_unit"):
            t[k] = rng.choice([max(0, t[k] + rng.randint(-500, 500)), t[k] * 2, 0, _money(rng)])
        elif rng.random() < 0.2 and k == "deadline":
            t[k] = max(tick + 1, t[k] + rng.randint(-200, 2000))
        elif rng.random() < 0.15 and k == "quantity":
            t[k] = max(1, t[k] + rng.randint(-50, 50))
    if rng.random() < 0.1:
        t["deadline"] = tick  # rejected: deadline passed
    return t


def _filled(c: Contract, company: str) -> dict[str, Any]:
    t = copy.deepcopy(c.terms)
    for f in PARTY_FIELDS[c.kind]:
        if t[f] is None:
            t[f] = company
    return t


def _responder(rng: random.Random, ex: Exchange, c: Contract) -> str:
    """Usually a company that may respond; sometimes anyone (not_your_turn, not_party)."""
    if rng.random() < 0.15:
        return rng.choice(ex.companies)
    if c.responder is not None:
        return c.responder
    others = [x for x in ex.companies if x != c.proposer]
    return rng.choice(others) if others else c.proposer


def _act(rng: random.Random, ex: Exchange, tick: int) -> None:
    """One random company command at ``tick``."""
    me = rng.choice(ex.companies)
    others = [c for c in ex.companies if c != me] or [me]
    other = rng.choice(others)
    contracts = [ex.contract_map[k] for k in sorted(ex.contract_map)]
    open_c = [c for c in contracts if c.status in OPEN_STATUSES]
    live = [c for c in contracts if c.status in LIVE_STATUSES and c.tender_id is None]
    tenders = [ex.tenders[k] for k in sorted(ex.tenders)]
    what = rng.choices(("offer", "licence", "sub", "counter", "accept", "withdraw", "amend", "bid", "msg", "pay",
                        "tagpay", "junk"), weights=(14, 7, 4, 8, 14, 3, 7, 8, 5, 4, 9, 2))[0]
    expires = rng.choice([None, None, tick + rng.randint(1, 800), tick])
    if what in ("offer", "licence"):
        public = rng.random() < 0.35
        sell = rng.random() < 0.5
        if what == "offer":
            a, b = (me, other) if sell else (other, me)
            terms = _supply_terms(rng, a, b, tick)
            if public:
                terms["buyer" if sell else "seller"] = None
        else:
            a, b = (me, other) if sell else (other, me)
            terms = _licence_terms(rng, a, b, tick)
            if public:
                terms["grantee" if sell else "grantor"] = None
        kind = "supply" if what == "offer" else "licence"
        _run(ex.offer, tick, me, kind, terms, to=None if public else other, expires_tick=expires)
    elif what == "sub":  # a subcontract to a tender holder (counts toward the award's deliveries)
        awarded = [t for t in tenders if t["status"] == "awarded" and t["winner"] != me]
        if awarded:
            t = rng.choice(awarded)
            terms = _supply_terms(rng, me, t["winner"], tick)
            terms.update(cargo=t["cargo"], industry=t["industry"])
            _run(ex.offer, tick, me, "supply", terms, to=t["winner"], expires_tick=expires)
            c = ex.contract_map.get(f"C{ex.next_contract - 1}")
            if c is not None and c.status == "offered" and rng.random() < 0.8:
                _run(ex.accept, tick, t["winner"], c.id)
    elif what == "counter" and open_c:
        c = rng.choice(open_c)
        who = _responder(rng, ex, c)
        _run(ex.counter, tick, who, c.id, _tweak(rng, _filled(c, who), tick), expires_tick=expires)
    elif what == "accept" and open_c:
        c = rng.choice(open_c)
        _run(ex.accept, tick, _responder(rng, ex, c), c.id)
    elif what == "withdraw" and open_c:
        c = rng.choice(open_c)
        who = rng.choice([c.proposer, c.offerer, rng.choice(ex.companies)])
        _run(ex.withdraw, tick, who, c.id)
    elif what == "amend" and live:
        c = rng.choice(live)
        ps = [p for p in (c.terms[f] for f in PARTY_FIELDS[c.kind]) if p in ex.companies]
        if not ps:
            return
        p = rng.choice(ps)
        new = _tweak(rng, c.terms, tick)
        if _run(ex.amend, tick, p, c.id, new) and rng.random() < 0.7:
            q = rng.choice([x for x in ps if x != p] or [p])
            same = new if rng.random() < 0.85 else _tweak(rng, new, tick)
            _run(ex.amend, tick, q, c.id, copy.deepcopy(same))
    elif what == "bid" and tenders:
        live_t = [t for t in tenders if t["status"] == "open" and tick < t["bid_close"]]
        t = rng.choice(live_t if live_t and rng.random() < 0.85 else tenders)
        price = rng.choice([rng.randint(0, t["max_price"]), rng.randint(0, t["max_price"]), t["max_price"],
                            0, t["max_price"] + 1, -1])
        _run(ex.tender_bid, tick, me, t["tender_id"], price)
    elif what == "msg":
        _run(ex.msg, tick, me, rng.choice([other, other, me]), _text(rng))
    elif what == "pay":
        memo = rng.choice(["", "thanks", "x" * MAX_MEMO_CHARS, "y" * (MAX_MEMO_CHARS + 1), "\U0001f4b0 é"])
        _run(ex.pay, tick, me, rng.choice([other, other, me]), rng.choice([1, _money(rng, 1), -5, 0]), memo)
    elif what == "tagpay":
        mine = [o for o in (ex.obligations[k] for k in sorted(ex.obligations, key=lambda k: int(k[1:])))
                if o["payer"] in ex.companies]
        if not mine:
            return
        o = rng.choice(mine)
        rest = max(1, o["amount"] - o["paid"])
        amount = rng.choice([rest, max(1, rest // 2), rest + rng.randint(1, 1000), 1, o["amount"] * 3])
        to = o["payee"] if rng.random() < 0.9 else rng.choice([*ex.companies, EXCHANGE])
        _run(ex.pay, tick, o["payer"], to, amount, rng.choice(["", "instalment"]), o["contract_id"])
    elif what == "junk":  # commands the exchange refuses
        cid = rng.choice(["C999", "T999", 5, None])
        _run(rng.choice([ex.accept, ex.withdraw]), tick, me, cid)
        _run(ex.pay, tick, me, EXCHANGE, 10, "", None)


def _deliveries(rng: random.Random) -> Callable[[str, int, int, int, int], int]:
    def fn(company: str, cargo: int, industry: int, start: int, end: int) -> int:
        return rng.choice([0, 0, rng.randint(1, 50), rng.randint(1, 400), 10 ** 6, HUGE])
    return fn


def session(rng: random.Random) -> Records:
    """One random session through the command API; returns the exchange's records."""
    return session_run(rng)[0]


def session_run(rng: random.Random) -> tuple[Records, str | None]:
    """(records, quirk) of one random session. ``quirk`` is the repr of a non-ExchangeError exception that
    ended the session early (the live exchange may then be half-changed, so the session stops), else None."""
    names = list(rng.choice(NAME_SETS))
    rng.shuffle(names)
    companies = names[:rng.randint(2, len(names))]
    rules = rng.choice([1, 2])
    start = rng.choice([0, 0, rng.randint(1, 5000)])
    horizon = start + rng.randint(GRACE_TICKS // 2, 12 * INSTALMENT_TICKS) if rules == 2 else None
    credit = rng.choice([None, None, 0, rng.randint(1, 20_000), HUGE])
    balances = {c: rng.choice([0, rng.randint(1, 100_000), HUGE]) for c in companies if rng.random() < 0.6}
    cfg = TenderConfig(per_quarter=rng.randint(0, 3), quantity_base=rng.randint(1, 300),
                       quantity_per_tile=rng.randint(0, 2), quantity_jitter_pct=rng.randint(0, 50),
                       payout_base_per_unit=rng.choice([40, rng.randint(0, 100), HUGE]),
                       payout_per_unit_per_tile=rng.randint(0, 3), penalty_bp=rng.choice([0, 5000, 20000]),
                       bid_ticks=rng.randint(0, 400), window_ticks=rng.randint(1, 4000),
                       reach_tiles=rng.choice([0, 0, 100]))
    ex = Exchange(companies, arm=rng.choice(["enforced", "cheap_talk"]), seed=rng.randint(0, 2 ** 31),
                  credit_limit=credit, tender_config=cfg, start_balances=balances, tick=start, rules=rules,
                  horizon_tick=horizon)
    world = _world(rng)
    deliveries = _deliveries(rng)
    tick, quarter = start, 0
    quirk = None
    try:
        for _ in range(rng.randint(15, 120)):
            tick += rng.choice([0, 0, 1, 5, 30, 200, 700, 2500])
            if rng.random() < 0.1:
                _run(ex.open_tenders, tick, quarter, world)
                quarter += 1
            for _ in range(rng.randint(0, 4)):
                _act(rng, ex, tick)
            r = rng.random()
            if r < 0.6:
                _run(ex.settle_due, tick, deliveries)
            elif r < 0.75:
                _run(ex.advance, tick)
        if rng.random() < 0.6:  # play to the end: every deadline and the horizon passed
            ends = [c.terms["deadline"] for c in ex.contract_map.values()] + [horizon or 0, tick]
            ends += [t["deadline"] for t in ex.tenders.values()]
            for t_end in sorted({max(ends), max(ends) + GRACE_TICKS + 1}):
                _run(ex.settle_due, t_end, deliveries)
    except Exception as e:  # noqa: BLE001 -- not an ExchangeError: the live exchange may be half-changed; stop here
        quirk = repr(e)
    return copy.deepcopy(ex.records), quirk


def sessions(seed: int, n: int) -> list[Records]:
    return [session(random.Random(seed * 1_000_003 + i)) for i in range(n)]


# ============================================================================================== mutations
def _int_paths(x: Any, path: tuple[Any, ...] = ()) -> list[tuple[tuple[Any, ...], int]]:
    """(path, value) of every int (not bool) inside ``x``, in sorted-key order."""
    if isinstance(x, bool):
        return []
    if isinstance(x, int):
        return [(path, x)]
    if isinstance(x, dict):
        return [p for k in sorted(x) for p in _int_paths(x[k], (*path, k))]
    if isinstance(x, list):
        return [p for i, v in enumerate(x) for p in _int_paths(v, (*path, i))]
    return []


def _str_paths(x: Any, path: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    if isinstance(x, str):
        return [path] if path else []
    if isinstance(x, dict):
        return [p for k in sorted(x) for p in _str_paths(x[k], (*path, k))]
    if isinstance(x, list):
        return [p for i, v in enumerate(x) for p in _str_paths(v, (*path, i))]
    return []


def _set(x: Any, path: tuple[Any, ...], value: Any) -> None:
    for p in path[:-1]:
        x = x[p]
    x[path[-1]] = value


def _is_licence_window(rec: dict[str, Any], path: tuple[Any, ...]) -> bool:
    """A licence window field: made huge it would list ~2^60 instalments (see the module docstring)."""
    terms = rec.get("terms")
    return (len(path) == 2 and path[0] == "terms" and path[1] in ("window_start", "deadline")
            and isinstance(terms, dict) and "rect" in terms)


def _pick_int(rng: random.Random, rec: dict[str, Any], safe: bool = False) -> tuple[Any, ...] | None:
    paths = [p for p, _ in _int_paths(rec) if not (safe and _is_licence_window(rec, p))]
    return rng.choice(paths) if paths else None


def _get(x: Any, path: tuple[Any, ...]) -> Any:
    for p in path:
        x = x[p]
    return x


def _mutate(rng: random.Random, name: str, rec: Any, prev_tick: int, companies: list[str]) -> Any:
    """One in-place mutant of one record (the record mutation ``name``), or None if it does not apply."""
    if name == "non_dict":
        return rng.choice([[rec], list(rec.items()) if isinstance(rec, dict) else 0, 0, "init", None, 1.5])
    if not isinstance(rec, dict):
        return None
    r = copy.deepcopy(rec)
    keys = sorted(r)
    if name == "drop_field":
        del r[rng.choice(keys)]
    elif name == "add_field":
        r[rng.choice(["extra", "contract_id", "tender_id", "rules", "kind", "x"])] = rng.choice([1, None, "C1"])
        if r == rec:
            return None
    elif name == "rename_field":
        k = rng.choice(keys)
        r[k + rng.choice(["_", "s", "X"])] = r.pop(k)
    elif name == "seq":
        r["seq"] = rng.choice([rec["seq"] + 1, rec["seq"] - 1, float(rec["seq"]), str(rec["seq"]), None,
                               rec["seq"] + 0.5, True, False, -1, 2 ** 64])
    elif name == "tick_reversed":
        if not prev_tick:
            r["tick"] = -1
        else:
            r["tick"] = prev_tick - rng.randint(1, max(1, prev_tick))
    elif name in ("int_to_float", "int_to_float_lossy", "int_to_bool", "int_to_str", "huge", "negative"):
        p = _pick_int(rng, r, safe=name == "huge")
        if p is None:
            return None
        v = _get(r, p)
        new = {"int_to_float": float(v),
               "int_to_float_lossy": v + 0.5 if abs(v) < 2 ** 52 else float(v) * 3,
               "int_to_bool": v == 1 if v in (0, 1) else rng.choice([True, False]),
               "int_to_str": str(v), "huge": HUGE + v if v >= 0 else -HUGE,
               "negative": -v - 1}[name]
        _set(r, p, new)
    elif name == "null":
        paths = [(k,) for k in keys] + [p for p, _ in _int_paths(r)] + _str_paths(r)
        _set(r, rng.choice(paths), None)
    elif name == "empty_string":
        paths = _str_paths(r)
        if not paths:
            return None
        _set(r, rng.choice(paths), "")
    elif name == "unknown_id":
        k = next((k for k in ("contract_id", "tender_id", "obligation_id", "to") if k in r), "company")
        r[k] = rng.choice(["C999", "T999", "O999", "nobody", "exchange", "escrow:C1", "C0", "c1"])
    elif name == "other_company":
        k = rng.choice([k for k in ("company", "to") if k in r] or ["company"])
        r[k] = rng.choice([*companies, EXCHANGE, None])
    elif name == "wrong_award":
        if r.get("op") != "tender_award":
            return None
        if rng.random() < 0.5:
            r["winner"] = rng.choice([*companies, None])
        else:
            r["price"] = rng.choice([0, 1, None]) if r["price"] is None else r["price"] + rng.choice([-1, 1])
    elif name == "wrong_outcome":
        if r.get("op") not in ("settle", "obligation_settle"):
            return None
        num = "delivered" if r["op"] == "settle" else rng.choice(["collected", "paid"])
        k = rng.choice(["outcome", "outcome", num, "parts" if r["op"] == "settle" else num])
        if k == "outcome":
            r[k] = rng.choice(["kept", "partial", "broken", "ended"])
        elif k == "parts":
            r[k] = [] if r[k] else [["x", 0, 0, 1]]
        else:
            r[k] = r[k] + rng.choice([-1, 1, 1000]) if isinstance(r[k], int) else 0
        if r == rec:
            return None
    elif name == "overpay":
        if r.get("op") != "pay":
            return None
        r["amount"] = rng.choice([r["amount"] * 10, r["amount"] + HUGE, 10 ** 30])
    elif name == "out_of_window":
        r["tick"] = (rec["tick"] if isinstance(rec.get("tick"), int) else 0) + rng.choice([1, 100, 7000, 60_000])
    elif name == "unhashable_op":
        r["op"] = rng.choice([[rec.get("op")], {"op": rec.get("op")}])
    return r


# Record mutations change one record in place. Move mutations put a valid record at another position of the
# log with ``seq`` and ``tick`` made consistent there, so the record passes the envelope checks and reaches
# the op's own rules (already_bid, offer_expired, not_due, tender_closed, ...).
MUTATIONS = ("drop_field", "add_field", "rename_field", "seq", "tick_reversed", "int_to_float", "int_to_float_lossy",
             "int_to_bool", "int_to_str", "null", "unknown_id", "other_company", "wrong_award", "wrong_outcome",
             "overpay", "out_of_window", "huge", "negative", "empty_string", "non_dict", "unhashable_op")
MOVES = ("duplicate", "later", "earlier")
_WEIGHTS = {"seq": 1, "drop_field": 1, "add_field": 1, "rename_field": 1, "non_dict": 1, "unhashable_op": 1,
            "null": 2, "duplicate": 6, "later": 8, "earlier": 8, "other_company": 6, "out_of_window": 4,
            "wrong_award": 4, "wrong_outcome": 5, "overpay": 4}
_ALL = MUTATIONS + MOVES


def _moved(rec: Any, seq: int, tick: Any) -> Any:
    if not isinstance(rec, dict):
        return rec
    return {**copy.deepcopy(rec), "seq": seq, "tick": tick}


def mutants_of(records: Records, rng: random.Random, k: int = MUTANTS_PER_SESSION) -> list[Records]:
    """Up to ``k`` mutants of one valid log, each cut right after its mutated record. Positions favour the
    interesting records (system records and later records) but every position is possible."""
    if not records:
        return []
    companies = list(records[0].get("companies") or []) if isinstance(records[0], dict) else []
    out: list[Records] = []
    rare = [j for j, r in enumerate(records) if r.get("op") in ("settle", "obligation_settle", "tender_award", "amend",
                                                               "pay", "tender_open", "tender_bid", "accept")]
    for _ in range(k * 4):
        if len(out) >= k:
            break
        name = rng.choices(_ALL, weights=[_WEIGHTS.get(m, 2) for m in _ALL])[0]
        i = rng.choice(rare) if rare and rng.random() < 0.6 else rng.randrange(len(records))
        if name == "duplicate":  # the same record again, right after itself
            case = copy.deepcopy(records[:i + 1]) + [_moved(records[i], i + 1, records[i]["tick"])]
        elif name == "later":  # the record again at a later point of the log (at that point's tick)
            j = rng.randint(i + 1, len(records))
            case = copy.deepcopy(records[:j]) + [_moved(records[i], j, records[j - 1]["tick"])]
        elif name == "earlier":  # a record before its time (at the earlier point's tick)
            if i == 0:
                continue
            j = rng.randint(1, i)
            case = copy.deepcopy(records[:j]) + [_moved(records[i], j, records[j - 1]["tick"])]
        else:
            prev = records[i - 1]["tick"] if i else 0
            m = _mutate(rng, name, records[i], prev, companies)
            if m is None:
                continue
            case = copy.deepcopy(records[:i]) + [m]
        out.append(case)
    return out


def mutations(seed: int, n: int) -> list[Records]:
    """``n`` mutants of the sessions of ``seed`` (session i is the same as ``sessions(seed, i + 1)[i]``)."""
    out: list[Records] = []
    i = 0
    while len(out) < n:
        rng = random.Random(seed * 1_000_003 + i)
        base = session(rng)
        out += mutants_of(base, random.Random(seed * 1_000_003 + i + 7_777_777_777))[:n - len(out)]
        i += 1
        if i > 10 * n + 10:  # cannot happen with non-empty sessions; a guard against an endless loop
            break
    return out


# ============================================================================================== edge cases
def _base(arm: str = "enforced", rules: int = 1, credit: int | None = None, **sb: int) -> Exchange:
    return Exchange(["a", "b", "c"], arm=arm, rules=rules, horizon_tick=50_000 if rules == 2 else None,
                    credit_limit=credit, start_balances=sb,
                    tender_config=TenderConfig(per_quarter=1, bid_ticks=10, window_ticks=100))


def _init(**over: Any) -> dict[str, Any]:
    r = copy.deepcopy(_base().records[0])
    for k, v in over.items():
        if v is _DROP:
            r.pop(k, None)
        else:
            r[k] = v
    return r


_DROP = object()


def _rec(op: str, seq: int, tick: int, company: Any, **fields: Any) -> dict[str, Any]:
    return {"op": op, "seq": seq, "tick": tick, "company": company, **fields}


def _tender(tid: str, tick: int, max_price: int, quantity: int = 10, ppu: int = 5) -> dict[str, Any]:
    return {"tender_id": tid, "quarter": 0, "industry": 1, "cargo": 2, "distance": 3, "source_industry": 4,
            "region": 0, "quantity": quantity, "max_price": max_price, "penalty_per_unit": ppu, "open_tick": tick,
            "bid_close": tick + 10, "window_start": tick + 10, "deadline": tick + 110}


def _swap_last(records: Records, **fields: Any) -> Records:
    out = copy.deepcopy(records)
    out[-1].update(fields)
    return out


def _supply_settled(arm: str, price: int, quantity: int, delivered: int, ppu: int = 3, bond: int = 0,
                    pay: int | None = None, rules: int = 1) -> Records:
    """Records up to (and including) the settle of one a->b supply contract (b buys)."""
    ex = _base(arm=arm, rules=rules, b=price + 10 if arm == "enforced" else 0, a=bond)
    ex.offer(0, "a", "supply", {"seller": "a", "buyer": "b", "cargo": 1, "industry": 2, "quantity": quantity,
                                "window_start": 0, "deadline": 100, "price": price, "penalty_per_unit": ppu,
                                "bond": bond}, to="b")
    ex.accept(1, "b", "C1")
    ex.settle_due(100, lambda *a: delivered)
    if pay is not None:
        oid = min(ex.obligations, key=lambda k: int(k[1:])) if ex.obligations else None
        if oid is not None:
            o = ex.obligations[oid]
            ex.pay(101, o["payer"], o["payee"], pay, "", "C1")
    return copy.deepcopy(ex.records)


def edge_cases() -> list[Records]:
    """Hand-written cases (fixed order)."""
    base = _init()
    cases: list[Records] = [[]]
    # ---- init: optional fields, rules truthiness, bad configs
    cases += [[_init(rules=_DROP, horizon_tick=_DROP)], [_init(rules=_DROP, horizon_tick=10)],
              [_init(horizon_tick=_DROP, rules=2)], [_init(rules=2, horizon_tick=None)]]
    for v in (0, False, None, "", [], {}, 0.0, -0.0, True, 1.0, 2.0, 2, 3, "2", 1.5, float("nan"), [2]):
        cases.append([_init(rules=v, horizon_tick=40_000)])
    for v in (40_000.0, True, "1", -5, HUGE):
        cases.append([_init(rules=2, horizon_tick=v)])
    for v in (None, [], "x", 5, {"per_quarter": 2}, {"per_quarter": True}, {"per_quarter": -1}, {"nope": 1},
              {"window_ticks": 0}, {"bid_ticks": 1.0}, {"penalty_bp": HUGE}):
        cases.append([_init(tender_config=v)])
    for v in (None, True, 0, -1, 1.0, HUGE, "5"):
        cases.append([_init(credit_limit=v)])
    for v in ({"a": True}, {"a": 1.0}, {"a": -1}, {"z": 5}, {"a": 0}, {"a": HUGE, "b": 2 ** 64}, [], None):
        cases.append([_init(start_balances=v)])
    for v in ([], ["a"], ["a", "a"], ["a:b"], ["exchange"], [""], [1], "ab", None, ["\U0001f600", ""],
              ["b", "a", "B", "é"]):
        cases.append([_init(companies=v)])
    for v in ("cheap", None, 1, ["enforced"]):
        cases.append([_init(arm=v)])
    for v in (1.0, True, None, "0", -1, HUGE):
        cases.append([_init(seed=v)])
    cases += [[_init(seq=0.0)], [_init(seq=False)], [_init(seq=-0.0)], [_init(seq=float("nan"))], [_init(seq=1)],
              [_init(tick=0.0)], [_init(tick=-1)], [_init(tick=float("inf"))], [_init(company="a")],
              [_init(op="Init")], [base, base], [{"op": "msg"}]]
    cases += [[v] for v in (None, 0, 1.5, "init", [base], True, float("nan"), float("-inf"))]
    cases += [[base, v] for v in (None, [], {}, {"op": None}, {"op": ["msg"]}, {"op": {"x": 1}})]

    # ---- seq and tick type swaps that are echoed into state (messages, tender bids)
    msg = _rec("msg", 1, 5, "a", to="b", text="hi")
    for s in (1.0, True, 1.5, "1", -0.0, None):
        cases.append([base, {**msg, "seq": s}])
    for t in (5.0, True, -0.0, 0.0):
        cases.append([base, {**msg, "tick": t}])
    cases.append([base, {**msg, "company": "b", "to": "b"}])
    long_ok = "\U0001f600" * MAX_MESSAGE_CHARS
    cases += [[base, {**msg, "text": long_ok}], [base, {**msg, "text": long_ok + "x"}],
              [base, {**msg, "text": "\ud83d" * MAX_MESSAGE_CHARS}], [base, {**msg, "text": "😀\udfff\x00"}],
              [base, {**msg, "text": "a" * MAX_MESSAGE_CHARS}], [base, {**msg, "text": ""}],
              [base, {**msg, "text": ["hi"]}], [base, {**msg, "to": "exchange"}]]

    # ---- tenders: seq/price/winner type swaps at the == sites, floats around 2^53 and 1e22 / 1e23
    big = 10 ** 23
    topen = _rec("tender_open", 1, 0, None, quarter=0, tenders=[_tender("T1", 0, big), _tender("T2", 0, 50)])
    bids = [topen, _rec("tender_bid", 2, 1, "a", tender_id="T1", price=10 ** 22),
            _rec("tender_bid", 3, 2, "b", tender_id="T2", price=1), _rec("tender_bid", 4, 2, "c", tender_id="T2", price=0)]
    pre = [base, *bids]
    award1 = _rec("tender_award", 5, 10, None, tender_id="T1", winner="a", price=10 ** 22)
    for p in (10 ** 22, 1e22, 1e23, 9999999999999999583119736832, float(10 ** 22) + 2 ** 20, True, "x"):
        cases.append([*pre, {**award1, "price": p}])
    award2 = _rec("tender_award", 5, 10, None, tender_id="T2", winner="c", price=0)
    for p in (0, 0.0, -0.0, False, None, 1, float("nan")):
        cases.append([*pre, {**award2, "price": p}])
    for w in ("b", None, 0, "C"):
        cases.append([*pre, {**award2, "winner": w}])
    cases.append([base, {**bids[1], "seq": 1}])
    cases.append([base, topen, {**bids[1], "seq": 2.0}])
    cases.append([base, topen, {**bids[1], "seq": 2, "price": 1e22}])
    cases.append([base, topen, {**bids[1], "seq": 2, "price": True}])
    late = _rec("tender_award", 5, 200, None, tender_id="T2", winner=None, price=None)
    cases += [[*pre, late], [*pre, {**late, "winner": "c", "price": 0}]]
    tbad = copy.deepcopy(topen)
    tbad["tenders"][0]["quantity"] = 0
    cases += [[base, tbad], [base, {**topen, "tenders": [{**_tender("T2", 0, 5)}]}],
              [base, {**topen, "quarter": 0.0}], [base, {**topen, "tenders": "T1"}], [base, {**topen, "tenders": []}]]
    t53 = _rec("tender_open", 1, 0, None, quarter=0, tenders=[_tender("T1", 0, 2 ** 53 + 2)])
    pre53 = [base, t53, _rec("tender_bid", 2, 1, "a", tender_id="T1", price=2 ** 53 + 1)]
    for p in (2 ** 53 + 1, float(2 ** 53), 9007199254740993.0, 9.007199254740992e15):
        cases.append([*pre53, _rec("tender_award", 3, 10, None, tender_id="T1", winner="a", price=p)])

    # ---- supply settle: delivered type swaps, odd parts (echoed), outcome strings
    sup = _supply_settled("enforced", price=1000, quantity=10, delivered=4)
    parts_variants = ([], [[["a", 0, 100, 4]]], [1.5, 1e16, 1e+16, -0.0, 1e-7, 123456789.123456789],
                      ["x", None, True, False], [float("nan"), float("inf"), float("-inf")], {"z": 1, "a": [2], "é": 3},
                      [[[[[]]]]], [HUGE, -HUGE, 2 ** 64], "parts", None, [{"b": 1, "B": 2, "\U0001f600": 3, "￿": 4}])
    for pv in parts_variants:
        cases.append(_swap_last(sup, parts=pv))
    for d in (4.0, True, "4", -1, None, 4.5):
        cases.append(_swap_last(sup, delivered=d))
    for oc in ("kept", "broken", "Partial", None):
        cases.append(_swap_last(sup, outcome=oc))
    cases.append(_supply_settled("enforced", price=HUGE, quantity=3, delivered=1, ppu=HUGE, bond=7))
    cases.append(_supply_settled("cheap_talk", price=HUGE, quantity=HUGE, delivered=HUGE - 1, ppu=HUGE))

    # ---- obligation_settle: collected / paid at the == site; 1e22 and 2^53 floats
    ct = _supply_settled("cheap_talk", price=10 ** 22, quantity=5, delivered=5, pay=10 ** 22)
    ex = Exchange.replay(ct)
    oid = min(ex.obligations, key=lambda k: int(k[1:]))
    o = ex.obligations[oid]
    osettle = _rec("obligation_settle", len(ct), o["settle_tick"], None, obligation_id=oid, collected=0,
                   paid=o["paid"], outcome="kept")
    for paid in (10 ** 22, 1e22, 1e23, float(10 ** 22) + 2 ** 21, True, None):
        cases.append([*ct, {**osettle, "paid": paid}])
    for col in (0, 0.0, False, -0.0, None, 1):
        cases.append([*ct, {**osettle, "collected": col}])
    for oc in ("partial", "broken", "late"):
        cases.append([*ct, {**osettle, "outcome": oc}])
    cases.append([*ct, {**osettle, "obligation_id": 1}])
    cases.append([*ct, {**osettle, "tick": o["settle_tick"] - 1}])
    ct53 = _supply_settled("cheap_talk", price=2 ** 53 + 2, quantity=5, delivered=5, pay=2 ** 53 + 1)
    ex53 = Exchange.replay(ct53)
    o53 = ex53.obligations[min(ex53.obligations, key=lambda k: int(k[1:]))]
    os53 = _rec("obligation_settle", len(ct53), o53["settle_tick"], None, obligation_id=o53["id"], collected=0,
                paid=o53["paid"], outcome="partial")
    for paid in (2 ** 53 + 1, float(2 ** 53), float(2 ** 53 + 2), 9007199254740993.0):
        cases.append([*ct53, {**os53, "paid": paid}])
    en = _supply_settled("enforced", price=100, quantity=10, delivered=0, ppu=50, bond=10)
    exe = Exchange.replay(en)
    if exe.obligations:
        oe = exe.obligations[min(exe.obligations, key=lambda k: int(k[1:]))]
        ose = _rec("obligation_settle", len(en), oe["settle_tick"], None, obligation_id=oe["id"],
                   collected=oe["amount"], paid=0, outcome="kept")
        for col in (oe["amount"], float(oe["amount"]), oe["amount"] + 1):
            cases.append([*en, {**ose, "collected": col}])

    # ---- licence: settle at the == site, amendments, rules 2 clipping
    lic = _base(arm="cheap_talk", rules=2)
    lic.offer(0, "a", "licence", {"grantor": "a", "grantee": None, "rect": [0, 0, 5, 5], "window_start": 10,
                                  "deadline": 10 + 4 * INSTALMENT_TICKS + 5, "fee": 1001}, to=None)
    lic.accept(1, "b", "C1")
    lrecs = copy.deepcopy(lic.records)
    lset = _rec("settle", len(lrecs), 10 + 4 * INSTALMENT_TICKS + 5, None, contract_id="C1", delivered=0, parts=[],
                outcome="ended")
    for d in (0, 0.0, False, -0.0, 1, None):
        cases.append([*lrecs, {**lset, "delivered": d}])
    for pv in ([], [[]], None, ()):
        cases.append([*lrecs, {**lset, "parts": pv}])
    cases.append([*lrecs, {**lset, "outcome": "kept"}])
    lam = copy.deepcopy(lic)
    new = dict(lam.contract_map["C1"].terms, fee=5003, deadline=10 + 2 * INSTALMENT_TICKS)
    lam.amend(INSTALMENT_TICKS + 20, "a", "C1", new)
    lam.amend(INSTALMENT_TICKS + 20, "b", "C1", new)
    cases.append(copy.deepcopy(lam.records))

    # ---- pay: optional contract_id, self payment, tags, memo limits, payments to the exchange
    pay = _rec("pay", 1, 3, "a", to="b", amount=5, memo="")
    cases += [[base, pay], [base, {k: v for k, v in pay.items()}], [base, {**pay, "contract_id": None}],
              [base, {**pay, "to": "a"}], [base, {**pay, "to": "exchange"}],
              [base, {**pay, "to": "exchange", "contract_id": "T1"}], [base, {**pay, "contract_id": 5}],
              [base, {**pay, "contract_id": "C1"}], [base, {**pay, "amount": 5.0}], [base, {**pay, "amount": True}],
              [base, {**pay, "amount": 0}], [base, {**pay, "memo": "m" * MAX_MEMO_CHARS}],
              [base, {**pay, "memo": "m" * (MAX_MEMO_CHARS + 1)}], [base, {**pay, "memo": None}],
              [base, {**pay, "memo": "\U0001f600" * MAX_MEMO_CHARS}], [base, {**pay, "to": None}]]
    lim = _base(credit=0)
    cases.append(copy.deepcopy(lim.records) + [_rec("pay", 1, 0, "a", to="b", amount=1, memo="x")])
    cases.append(copy.deepcopy(lim.records) + [_rec("pay", 1, 0, "a", to="b", amount=1, memo="x", contract_id=None)])

    # ---- offers with odd terms (bond missing, open parties, unhashable kind)
    off = _rec("offer", 1, 0, "a", contract_id="C1", kind="supply", to="b", expires_tick=50,
               terms={"seller": "a", "buyer": "b", "cargo": 1, "industry": 2, "quantity": 3, "window_start": 0,
                      "deadline": 10, "price": 7, "penalty_per_unit": 1})
    cases += [[base, off], [base, {**off, "kind": ["supply"]}], [base, {**off, "kind": "loan"}],
              [base, {**off, "kind": "lease"}], [base, {**off, "kind": None}], [base, {**off, "contract_id": "C2"}],
              [base, {**off, "expires_tick": 0}], [base, {**off, "expires_tick": 50.0}], [base, {**off, "to": "a"}],
              [base, {**off, "terms": {**off["terms"], "bond": True}}], [base, {**off, "terms": None}],
              [base, {**off, "terms": {**off["terms"], "quantity": 0}}],
              [base, {**off, "terms": {**off["terms"], "seller": "b"}}],
              [base, {**off, "to": None, "terms": {**off["terms"], "buyer": None}}],
              [base, {**off, "to": None}], [base, {**off, "terms": {**off["terms"], "deadline": 0}}],
              [base, {**off, "terms": {**off["terms"], "extra": 1}}]]

    # ---- one valid prefix per op rule, then the record that breaks it (the codes the API refuses early)
    tex = _base(b=100)
    tex.apply(_rec("tender_open", 1, 0, None, quarter=0, tenders=[_tender("T1", 0, 50)]))
    tex.tender_bid(1, "a", "T1", 20)
    trecs = copy.deepcopy(tex.records)
    cases += [[*trecs, _rec("tender_bid", 3, 2, "a", tender_id="T1", price=10)],    # already_bid
              [*trecs, _rec("tender_bid", 3, 10, "b", tender_id="T1", price=10)],   # tender_closed
              [*trecs, _rec("tender_bid", 3, 2, "b", tender_id="T1", price=51)],    # bad_price
              [*trecs, _rec("tender_award", 3, 9, None, tender_id="T1", winner="a", price=20)]]  # bad_state
    tex.advance(10)
    award_terms = dict(tex.contract_map["T1"].terms)
    cases += [copy.deepcopy(tex.records) + [_rec("amend", 4, 11, "a", contract_id="T1", terms=award_terms)],
              copy.deepcopy(tex.records) + [_rec("settle", 4, 50, None, contract_id="T1", delivered=10, parts=[],
                                                 outcome="kept")],                  # not_due
              copy.deepcopy(tex.records) + [_rec("pay", 4, 11, "a", to="exchange", amount=5, memo="",
                                                 contract_id="T1")]]                # bad_tag
    nex = _base(credit=0, b=5)
    nex.offer(0, "a", "supply", copy.deepcopy(off["terms"]), to="b", expires_tick=50)
    nrecs = copy.deepcopy(nex.records)
    acc = _rec("accept", 2, 1, "b", contract_id="C1")
    cases += [[*nrecs, acc],                                                         # insufficient_funds (7 > 5)
              [*nrecs, {**acc, "company": "a"}],                                     # not_your_turn
              [*nrecs, {**acc, "company": "c"}],                                     # not_party
              [*nrecs, {**acc, "tick": 50}],                                         # offer_expired
              [*nrecs, {**acc, "tick": 10}],                                         # deadline_passed
              [*nrecs, _rec("withdraw", 2, 1, "c", contract_id="C1")],               # not_party
              [*nrecs, _rec("expire", 2, 49, None, contract_id="C1")],               # bad_state
              [*nrecs, _rec("activate", 2, 1, None, contract_id="C1")],              # bad_state
              [*nrecs, {**acc, "tick": 0, "seq": 2}, {**acc, "seq": 3, "tick": 0}],  # time ok, then bad_state
              [*nrecs, _rec("msg", 2, 0, "a", to="b", text="x"), _rec("msg", 3, 0, "a", to="b", text="y")]]
    cases.append([*nrecs[:1], {**nrecs[1], "tick": 0}, _rec("msg", 2, 0, "a", to="b", text="late")])
    cases.append([_init(tick=10), _rec("msg", 1, 9, "a", to="b", text="x")])         # time_reversed
    cases.append([*_supply_settled("enforced", price=10, quantity=2, delivered=1)[:3]]
                 + [_rec("settle", 3, 100, None, contract_id="C1", delivered=-1, parts=[], outcome="broken")])
    return cases


def edge(n: int | None = None) -> list[Records]:
    cases = edge_cases()
    return cases if n is None else cases[:n]


# ============================================================================================== captured
def run_capture(out_dir: Path, tests: tuple[str, ...] = CAPTURE_TESTS, timeout: float = 900.0) -> Path:
    """Run the exchange unit tests with ``RAILHEAD_EX_CAPTURE=out_dir`` (tests/conftest.py) in a subprocess
    and return the capture file. Needs the repository checkout (``tests/``)."""
    if not (REPO / "tests" / "conftest.py").is_file():
        raise FileNotFoundError(f"{REPO}/tests/conftest.py: capture needs the repository checkout")
    files = sorted(str(p.relative_to(REPO)) for pat in tests for p in REPO.glob(pat)
                   if p.name != "test_exchange_bend.py")  # not this oracle's own tests (it would recurse)
    env = {**os.environ, "RAILHEAD_EX_CAPTURE": str(Path(out_dir).resolve())}
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "--no-header", *files], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=timeout, check=False)
    out = Path(out_dir) / CAPTURE_FILE
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(f"capture run failed ({proc.returncode}): {proc.stdout[-2000:]}{proc.stderr[-2000:]}")
    return out


def captured(n: int | None = None, path: Path | None = None) -> list[Records]:
    """The captured unit-test cases (one JSON array of records per line), first ``n``. ``path`` is the
    capture file or its directory."""
    if path is None:
        raise FileNotFoundError("captured cases need a capture file (corpus.run_capture or tests/conftest.py)")
    p = Path(path)
    if p.is_dir():
        p = p / CAPTURE_FILE
    cases = []
    with open(p, encoding="ascii") as fh:
        for ln in fh:
            if ln.strip():
                cases.append(json.loads(ln))
                if n is not None and len(cases) >= n:
                    break
    return cases


# ============================================================================================== API
def generate(kind: str, seed: int, n: int, *, captured_path: Path | None = None) -> list[Records]:
    """``n`` cases of ``kind`` (``edge`` and ``captured`` are fixed lists: at most ``n`` of them)."""
    if kind == "sessions":
        return sessions(seed, n)
    if kind == "mutations":
        return mutations(seed, n)
    if kind == "edge":
        return edge(n)
    if kind == "captured":
        return captured(n, captured_path)
    raise ValueError(f"unknown corpus kind {kind!r} (kinds: {', '.join(KINDS)})")


def op_name(r: Any) -> str:
    op = r.get("op") if isinstance(r, dict) else None
    return op if isinstance(op, str) else f"<{type(op).__name__ if isinstance(r, dict) else type(r).__name__}>"


def coverage(cases: list[Records]) -> dict[str, Any]:
    """Histograms of reference outcomes (``ok`` / error code) and of record ops, plus arms and rules."""
    outcomes: Counter[str] = Counter()
    ops: Counter[str] = Counter()
    arms: Counter[str] = Counter()
    rules: Counter[str] = Counter()
    for recs in cases:
        outcomes[outcome_label(reference(recs))] += 1
        ops.update(op_name(r) for r in recs)
        first = recs[0] if recs and isinstance(recs[0], dict) else {}
        arms[str(first.get("arm"))] += 1
        rules[str(first.get("rules", 1))] += 1
    return {"cases": len(cases), "outcomes": dict(sorted(outcomes.items())), "ops": dict(sorted(ops.items())),
            "arms": dict(sorted(arms.items())), "rules": dict(sorted(rules.items()))}


def write_corpus(out: Path, kind: str, seed: int, count: int, *, captured_dir: Path | None = None) -> dict[str, Any]:
    """Write ``case-XXXXX.jsonl`` (the Bend input) and ``case-XXXXX.expected`` (the reference line) for
    ``kind`` (or every kind for ``all``). Returns the coverage report per kind. ``captured_dir``: the capture
    file or its directory; without it the capture runs now (:func:`run_capture`, into ``out/capture``)."""
    out.mkdir(parents=True, exist_ok=True)
    kinds = KINDS if kind == "all" else (kind,)
    report: dict[str, Any] = {}
    i = 0
    for k in kinds:
        cap = captured_dir
        if k == "captured" and cap is None:
            cap = run_capture(out / "capture")
        cases = generate(k, seed, count, captured_path=cap)
        for recs in cases:
            stem = out / f"case-{i:05d}"
            stem.with_suffix(".jsonl").write_text(normalize(recs), encoding="ascii")
            stem.with_suffix(".expected").write_text(reference(recs) + "\n", encoding="ascii")
            i += 1
        report[k] = coverage(cases)
    return report
