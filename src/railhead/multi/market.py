"""The contract market of a Waybill world (docs/WAYBILL.md, section 9). No engine: the coordinator and the
replay give it ticks, cargo monitor totals and industry snapshots through callbacks, and both run the same
driver, so the replay makes every system record again and compares it with the log.

* :class:`MarketConfig` comes from ``task.json`` (``objective.market``). It is sealed with the task.
* :class:`Market` owns one :class:`railhead.exchange.Exchange`. Exchange company ids are the engine
  company ids as strings (:func:`ex_id`).
* :class:`MonitorBook` keeps one engine cargo monitor per (company, cargo, industry) that a live contract
  needs, and the monitor totals at each step boundary. Deliveries in ``[start, end]`` are
  ``total(end) - total(max(start, added))``: a monitor counts nothing before it exists.
* :meth:`Market.tick_records` is the driver for one tick: settle what is due, then open the tenders of a
  quarter that starts at this tick. :meth:`Market.next_forced` is the next tick the world must stop at.
* :func:`exchange_wakes` gives the exchange wake reasons of each waiting company at a tick.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from railhead.exchange import Exchange, ExchangeError, TenderConfig
from railhead.exchange.contracts import LIVE_STATUSES
from railhead.exchange.ledger import EXCHANGE
from railhead.multi.log import EXCHANGE_UNTIL, FORCED_REASONS, TICKS_PER_DAY
from railhead.multi.regions import RegionMap, clip, region_of

WAKE_ORDER = ("deadline", "obligation_due", "tender", "offer_received", "message_received")
COMMANDS = ("offer", "counter", "accept", "amend", "withdraw", "tender_bid", "msg", "pay")
QUERIES = ("market", "contracts", "inbox", "ledger", "view")


def ex_id(company: int) -> str:
    return str(int(company))


def eng_id(company: str) -> int:
    return int(company)


@dataclass
class MarketConfig:
    arm: str = "enforced"
    seed: int = 0
    start_balances: dict[int, int] = field(default_factory=dict)  # slot -> pounds on the ledger
    credit_limit: int | None = None
    quarter_days: int = 90
    tender: dict = field(default_factory=dict)  # TenderConfig fields

    @classmethod
    def from_objective(cls, objective: dict) -> MarketConfig | None:
        d = objective.get("market")
        if d is None:
            return None
        cfg = cls(arm=d.get("arm", "enforced"), seed=int(d.get("seed", 0)),
                  start_balances={int(k): int(v) for k, v in (d.get("start_balances") or {}).items()},
                  credit_limit=d.get("credit_limit"), quarter_days=int(d.get("quarter_days", 90)),
                  tender=dict(d.get("tender") or {}))
        TenderConfig(**cfg.tender)  # fail early on a bad config
        if cfg.quarter_days < 1:
            raise ValueError("market.quarter_days must be 1 or more")
        return cfg

    def to_json(self) -> dict:
        d = asdict(self)
        d["start_balances"] = {str(k): v for k, v in sorted(self.start_balances.items())}
        return d


class MonitorBook:
    def __init__(self) -> None:
        self.keys: dict[tuple[int, int, int], dict] = {}  # (company, cargo, industry) -> {id, added}
        self.samples: dict[int, dict[int, int]] = {}  # monitor id -> {tick: total}

    @staticmethod
    def needed(ex: Exchange) -> list[tuple[int, int, int]]:
        out = set()
        for c in ex.contract_map.values():
            if c.status in LIVE_STATUSES and c.kind == "supply":
                for company, _s, _e in ex.delivery_parts(c):
                    if company != EXCHANGE:
                        out.add((eng_id(company), int(c.terms["cargo"]), int(c.terms["industry"])))
        return sorted(out)

    def ensure(self, ex: Exchange, tick: int, add: Callable[[int, int, int], int | None]) -> list[tuple]:
        """Add a monitor for every needed key that has none (idempotent). ``add`` returns the engine
        monitor id, or None if the engine refuses (for example, the industry closed): that key counts 0."""
        new = []
        for key in self.needed(ex):
            if key in self.keys:
                continue
            mid = add(*key)
            self.keys[key] = {"id": mid, "added": tick}
            if mid is not None:
                self.samples[mid] = {tick: 0}
            new.append(key)
        return new

    @property
    def active(self) -> bool:
        return any(v["id"] is not None for v in self.keys.values())

    def sample(self, tick: int, rows: list[dict]) -> None:
        for r in rows:
            mid = int(r["id"])
            if mid in self.samples:
                self.samples[mid][tick] = int(r["total"])

    def _total_at(self, mid: int, t: int) -> int:
        best = None
        for tick, total in self.samples[mid].items():
            if tick <= t and (best is None or tick > best[0]):
                best = (tick, total)
        return 0 if best is None else best[1]

    def deliveries(self, company: str, cargo: int, industry: int, start: int, end: int) -> int:
        k = self.keys.get((eng_id(company), int(cargo), int(industry)))
        if k is None or k["id"] is None:
            return 0
        s = max(start, k["added"])
        return max(0, self._total_at(k["id"], end) - self._total_at(k["id"], s))

    def state(self) -> dict:
        return {"keys": {f"{k[0]}:{k[1]}:{k[2]}": v for k, v in sorted(self.keys.items())},
                "samples": {str(m): dict(sorted(s.items())) for m, s in sorted(self.samples.items())}}


class Market:
    def __init__(self, cfg: MarketConfig, companies: list[dict], start_tick: int, horizon_tick: int | None,
                 regions: RegionMap | None = None) -> None:
        self.cfg = cfg
        self.companies = [dict(c) for c in companies]
        self.slot_of = {int(c["id"]): int(c["slot"]) for c in companies}
        self.start_tick, self.horizon_tick, self.regions = start_tick, horizon_tick, regions
        self.quarter_ticks = cfg.quarter_days * TICKS_PER_DAY
        self.tender_config = TenderConfig(**cfg.tender)
        sb = {ex_id(c["id"]): cfg.start_balances.get(int(c["slot"]), 0) for c in companies}
        self.ex = Exchange([ex_id(c["id"]) for c in sorted(companies, key=lambda c: int(c["slot"]))],
                           arm=cfg.arm, seed=cfg.seed, credit_limit=cfg.credit_limit,
                           tender_config=self.tender_config, start_balances={k: v for k, v in sb.items() if v},
                           tick=start_tick)
        self.book = MonitorBook()

    # ------------------------------------------------------------------ schedule
    def quarter_at(self, tick: int) -> int | None:
        d = tick - self.start_tick
        return d // self.quarter_ticks if d >= 0 and d % self.quarter_ticks == 0 else None

    def tenders_fit(self, tick: int) -> bool:
        t = self.tender_config
        return self.horizon_tick is None or tick + t.bid_ticks + t.window_ticks <= self.horizon_tick

    def next_forced(self, now: int) -> int | None:
        """The first tick after ``now`` at which the world must stop: an exchange deadline (offer expiry,
        window start, bid close, contract deadline) or the start of a quarter whose tenders fit the horizon."""
        cands = [d["tick"] for d in self.ex.deadlines() if d["tick"] > now]
        q = (now - self.start_tick) // self.quarter_ticks + 1
        qt = self.start_tick + q * self.quarter_ticks
        if self.tenders_fit(qt):
            cands.append(qt)
        return min(cands) if cands else None

    # ------------------------------------------------------------------ the driver
    def snapshot(self, industries: list[dict], tick: int) -> list[dict]:
        return industry_snapshot(industries, self.regions, tick)

    def tick_records(self, tick: int, industries: Callable[[], list[dict]]) -> list[dict]:
        """System records for ``tick``: expiries, awards, activations and settlements that are due, then
        the tenders of a quarter that starts at ``tick`` (if they end by the horizon)."""
        out = self.ex.settle_due(tick, self.book.deliveries)
        q = self.quarter_at(tick)
        if q is not None and self.tenders_fit(tick):
            out.append(self.ex.open_tenders(tick, q, self.snapshot(industries(), tick)))
        return out

    def opens_tenders(self, tick: int) -> bool:
        return self.quarter_at(tick) is not None and self.tenders_fit(tick)

    def wakes(self, tick: int, waiters: dict[int, tuple[list[str], int]]) -> dict[int, set[str]]:
        """Market wake reasons of the waiters at ``tick`` (call it before :meth:`tick_records`)."""
        return exchange_wakes(self.ex, waiters, tick, at_quarter=self.opens_tenders(tick))

    def cap(self, now: int, waiters: dict[int, tuple[list[str], int]]) -> int | None:
        """The tick the next step must not pass: one tick when an offer or message condition is already
        true, else :meth:`next_forced`."""
        if pending_now(self.ex, waiters):
            return now + 1
        return self.next_forced(now)

    # ------------------------------------------------------------------ licences
    def licence_rects(self, company: int, tick: int) -> list[list[int]]:
        """The rectangles where ``company`` may build at ``tick`` under its live licences, each clipped to
        the grantor's region at ``tick`` (a licence cannot grant what the grantor does not hold)."""
        if self.regions is None:
            return []
        out = []
        for lc in self.ex.licences_held(ex_id(company), tick):
            grantor = int(lc["grantor"])
            if grantor in self.slot_of:
                out += clip(lc["rect"], self.regions.rects_at(self.slot_of[grantor], tick))
        return out

    # ------------------------------------------------------------------ scores
    def balance(self, company: int) -> int:
        assert self.ex.accounts is not None
        return self.ex.accounts.balance(ex_id(company))

    def state(self) -> dict:
        return {"exchange": self.ex.state(), "monitors": self.book.state()}


def industry_snapshot(industries: list[dict], regions: RegionMap | None, tick: int) -> list[dict]:
    """Engine industry rows -> the exchange's industry snapshot (with the region slot of each industry)."""
    out = []
    for i in industries:
        x, y = int(i["x"]), int(i["y"])
        reg = region_of(regions, x, y, tick) if regions is not None else -1 - int(i["id"])
        out.append({"id": int(i["id"]), "type": int(i.get("type", 0)),
                    "produces": sorted({int(p["cargo"]) for p in i.get("produces", [])}),
                    "accepts": sorted({int(c) for c in i.get("accepts", [])}), "tile": [x, y], "region": reg})
    return out


def cross_region_needs(snapshot: list[dict], max_distance: int | None = None) -> dict:
    """How much of the world's freight crosses franchise regions, from an industry snapshot.

    For each (destination industry, cargo it accepts) with a producer of that cargo somewhere (within
    ``max_distance`` tiles, Manhattan, when given): ``local`` if a producer is in the destination's own
    region, else ``cross_only``. A company can build stations only in its own region, so a cross-only
    delivery needs a station in the other company's region: a partner (a supply contract or a tender
    subcontract). ``tender_candidates`` is the pool the quarterly tenders are drawn from (all cross-region).
    Only industries inside a company's region count (region >= 0): a cross-only need can then always be met
    with a licence from the producer's or the destination's region owner."""
    from railhead.exchange.tenders import candidates

    local = cross = 0
    by_region: dict[str, dict[str, int]] = {}
    for dest in snapshot:
        if dest["region"] < 0:
            continue
        for cargo in dest["accepts"]:
            prods = [p for p in snapshot if cargo in p["produces"] and p["id"] != dest["id"] and p["region"] >= 0]
            if max_distance is not None:
                prods = [p for p in prods if abs(p["tile"][0] - dest["tile"][0])
                         + abs(p["tile"][1] - dest["tile"][1]) <= max_distance]
            if not prods:
                continue
            row = by_region.setdefault(str(dest["region"]), {"local": 0, "cross_only": 0})
            if any(p["region"] == dest["region"] for p in prods):
                local += 1
                row["local"] += 1
            else:
                cross += 1
                row["cross_only"] += 1
    counts: dict[str, int] = {}
    for i in snapshot:
        counts[str(i["region"])] = counts.get(str(i["region"]), 0) + 1
    return {"industries": len(snapshot), "industries_by_region": counts, "local": local, "cross_only": cross,
            "by_region": by_region, "tender_candidates": len(candidates(snapshot)),
            "tender_candidates_cross_only": len(candidates(snapshot, max_distance or 0)) if max_distance else None,
            "max_distance": max_distance}


def _addressed_to(ex: Exchange, r: dict, me: str) -> bool:
    """True when company record ``r`` asks ``me`` to answer: an offer to me, or a counter or amendment
    from the other party of a contract that I am a party to."""
    if r["company"] == me or r["company"] is None:
        return False
    if r["op"] == "offer":
        return r["to"] == me
    if r["op"] in ("counter", "amend"):
        c = ex.contract_map.get(r["contract_id"])
        return c is not None and c.is_party(me)
    return False


def exchange_wakes(ex: Exchange, waiters: dict[int, tuple[list[str], int]], tick: int,
                   at_quarter: bool) -> dict[int, set[str]]:
    """Exchange wake reasons at ``tick`` (before the system records of ``tick``) for each waiter
    ``company -> (until, exchange record count when the wait started)``.

    * forced: ``deadline`` when a contract of the company reaches its deadline at ``tick``; ``tender``
      for everyone when a quarter's tenders open at ``tick``; ``obligation_due`` for the payer of an
      obligation that falls due at ``tick``;
    * ``until=deadline``: any exchange deadline at ``tick`` that names the company (offer expiry, window
      start, bid close);
    * ``until=offer_received``: an offer, counter or amendment for the company since the wait started;
    * ``until=message_received``: a message to the company since the wait started."""
    dl = [d for d in ex.deadlines() if d["tick"] == tick]
    out: dict[int, set[str]] = {}
    for c, (until, since) in waiters.items():
        me = ex_id(c)
        kinds = {u.partition(":")[0] for u in until}
        rs: set[str] = set()
        if any(d["kind"] == "deadline" and me in d["companies"] for d in dl):
            rs.add("deadline")
        if any(d["kind"] == "obligation_due" and me in d["companies"] for d in dl):
            rs.add("obligation_due")  # forced, both arms: a payment of the company falls due
        if "deadline" in kinds and any(me in d["companies"] for d in dl):
            rs.add("deadline")
        if at_quarter:
            rs.add("tender")
        recent = ex.records[since:]
        if "offer_received" in kinds and any(_addressed_to(ex, r, me) for r in recent):
            rs.add("offer_received")
        if "message_received" in kinds and any(r["op"] == "msg" and r["to"] == me for r in recent):
            rs.add("message_received")
        if rs:
            out[c] = rs
    return out


def pick_reason(reasons: set[str]) -> str:
    return next(r for r in WAKE_ORDER if r in reasons)


def pending_now(ex: Exchange, waiters: dict[int, tuple[list[str], int]]) -> bool:
    """True when a waiter's offer or message condition is already true: the next step is one tick."""
    for c, (until, since) in waiters.items():
        kinds = {u.partition(":")[0] for u in until}
        me = ex_id(c)
        recent = ex.records[since:]
        if "offer_received" in kinds and any(_addressed_to(ex, r, me) for r in recent):
            return True
        if "message_received" in kinds and any(r["op"] == "msg" and r["to"] == me for r in recent):
            return True
    return False


def check_system_record(logged: dict, expect: dict) -> str | None:
    """Why a logged system record differs from the one the replay's driver made, or None."""
    if logged != expect:
        return f"logged {logged.get('op')} record {logged!r} != computed {expect!r}"
    return None


__all__ = [
    "COMMANDS",
    "EXCHANGE_UNTIL",
    "FORCED_REASONS",
    "QUERIES",
    "ExchangeError",
    "Market",
    "MarketConfig",
    "MonitorBook",
    "check_system_record",
    "eng_id",
    "ex_id",
    "exchange_wakes",
    "pending_now",
    "pick_reason",
]


# ---------------------------------------------------------------------- agent commands
SUPPLY_FIELDS = ("seller", "buyer", "cargo", "industry", "quantity", "window_start", "deadline", "price",
                 "penalty_per_unit", "bond")
LICENCE_FIELDS = ("grantor", "grantee", "rect", "window_start", "deadline", "fee")
TERM_FIELDS = tuple(dict.fromkeys((*SUPPLY_FIELDS, *LICENCE_FIELDS)))
PARTY_ARGS = frozenset({"seller", "buyer", "grantor", "grantee"})
TICK_FIELDS = frozenset({"window_start", "deadline", "expires_tick"})
COMMAND_ARGS: dict[str, tuple[str, ...]] = {
    "offer": ("kind", "to", "expires_tick", "terms", *TERM_FIELDS),
    "counter": ("contract_id", "expires_tick", "terms", *TERM_FIELDS),
    "accept": ("contract_id",),
    "amend": ("contract_id", "terms", *TERM_FIELDS),
    "withdraw": ("contract_id",),
    "tender_bid": ("tender_id", "price"),
    "msg": ("to", "text"),
    "pay": ("to", "amount", "memo", "contract_id", "contract"),
}


class CommandError(ValueError):
    def __init__(self, msg: str, code: str = "ERR_BAD_ARG") -> None:
        super().__init__(msg)
        self.code = code


def resolve_company(v: Any, me: str, companies: list[dict], allow_open: bool = False) -> str | None:
    """A company given as its id (3 or "3"), its name ("Company B"), its letter ("B") or "me"."""
    if v is None or (isinstance(v, str) and v.lower() in ("", "any", "null", "none")):
        if allow_open:
            return None
        raise CommandError("a company is required here (an id, a name like 'Company B', or me)")
    if isinstance(v, str) and v.lower() == "me":
        return me
    for c in companies:
        cid, name = int(c["id"]), str(c["name"])
        if (isinstance(v, int) and not isinstance(v, bool) and v == cid) or \
                (isinstance(v, str) and v.strip().lower() in (str(cid), name.lower(), name.split()[-1].lower())):
            return ex_id(cid)
    known = ", ".join(f"{c['id']} ({c['name']})" for c in companies)
    raise CommandError(f"unknown company {v!r}; companies: {known}")


def resolve_tick(v: Any, now: int, what: str) -> int:
    """A tick: an absolute int, or relative to now: "+30d" (days), "+500t" or "+500" (ticks)."""
    if isinstance(v, int) and not isinstance(v, bool):
        return v
    if isinstance(v, str):
        t = v.strip().lower()
        try:
            if t.startswith("+") and t.endswith("d"):
                return now + int(t[1:-1]) * TICKS_PER_DAY
            if t.startswith("+"):
                return now + int(t[1:].rstrip("t"))
            return int(t)
        except ValueError:
            pass
    raise CommandError(f"{what} must be a tick (an integer) or relative to now, e.g. +30d (days) or +500t (ticks); "
                       f"got {v!r}")


def resolve_rect(v: Any) -> list[int]:
    """A rectangle "x0,y0,x1,y1" (or a list of 4 ints): both corners are included."""
    parts = v.replace(" ", "").split(",") if isinstance(v, str) else v
    try:
        rect = [int(x) for x in parts]
    except (TypeError, ValueError):
        rect = []
    if len(rect) != 4:
        raise CommandError(f"rect must be x0,y0,x1,y1 (the two corners of a rectangle), got {v!r}")
    return rect


def _int_arg(a: dict, k: str) -> int:
    v = a[k]
    if isinstance(v, str) and v.strip().lstrip("-").isdigit():
        v = int(v)
    if not isinstance(v, int) or isinstance(v, bool):
        raise CommandError(f"{k} must be an integer, got {v!r}")
    return v


def parse_command(m: str, a: dict, *, me: str, now: int, companies: list[dict],
                  base_terms: dict | None = None) -> dict:
    """Keyword arguments for ``Exchange.<m>(tick, me, **kwargs)`` from loose agent input. Terms come as
    ``terms={...}`` or as top-level fields (seller=me buyer=B cargo=1 ...). For counter and amend, the
    fields that are not given keep their current value (``base_terms``)."""
    if m not in COMMAND_ARGS:
        raise CommandError(f"unknown market command {m!r}; commands: {', '.join(COMMANDS)}")
    unknown = set(a) - set(COMMAND_ARGS[m])
    if unknown:
        raise CommandError(f"{m}: unknown argument(s) {sorted(unknown)} (arguments: {', '.join(COMMAND_ARGS[m])})")
    out: dict[str, Any] = {}
    if m in ("offer", "counter", "amend"):
        terms = dict(base_terms or {})
        given = dict(a.get("terms") or {})
        if not isinstance(given, dict):
            raise CommandError("terms must be an object")
        given.update({k: a[k] for k in TERM_FIELDS if k in a})
        for k, v in given.items():
            if k in PARTY_ARGS:
                terms[k] = resolve_company(v, me, companies, allow_open=m == "offer")
            elif k in TICK_FIELDS:
                terms[k] = resolve_tick(v, now, k)
            elif k == "rect":
                terms[k] = resolve_rect(v)
            elif k in TERM_FIELDS:
                terms[k] = _int_arg(given, k)
            else:
                terms[k] = v
        if m == "offer" and not given:
            raise CommandError("offer needs terms: seller, buyer, cargo, industry, quantity, window_start, deadline, "
                               "price, penalty_per_unit [bond]; or kind=licence grantor, grantee, rect, "
                               "window_start, deadline, fee")
        out["terms"] = terms
    if m == "offer":
        out["kind"] = a.get("kind", "supply")
        out["to"] = resolve_company(a.get("to"), me, companies, allow_open=True)
    if m in ("offer", "counter") and a.get("expires_tick") is not None:
        out["expires_tick"] = resolve_tick(a["expires_tick"], now, "expires_tick")
    for k in ("contract_id", "tender_id"):
        if k in COMMAND_ARGS[m] and m != "pay":  # pay: an optional tag
            if not isinstance(a.get(k), str) or not a[k]:
                raise CommandError(f"{m} needs {k} (for example {'C1' if k == 'contract_id' else 'T1'})")
            out[k] = a[k].strip().upper()
    if m == "tender_bid":
        if "price" not in a:
            raise CommandError("tender bid needs price (your total price for the whole quantity)")
        out["price"] = _int_arg(a, "price")
    if m == "msg":
        out["to"] = resolve_company(a.get("to"), me, companies)
    if m == "pay":
        tag = a.get("contract_id", a.get("contract"))
        if tag is not None:
            if not isinstance(tag, str) or not tag.strip():
                raise CommandError("contract must be a contract id (for example C3 or T2)")
            out["contract_id"] = tag.strip().upper()
        to = a.get("to")
        out["to"] = "exchange" if isinstance(to, str) and to.strip().lower() == "exchange" and tag is not None \
            else resolve_company(to, me, companies)
    if m == "msg":
        if not isinstance(a.get("text"), str):
            raise CommandError("msg needs text=\"...\"")
        out["text"] = a["text"]
    if m == "pay":
        if "amount" not in a:
            raise CommandError("pay needs amount (pounds, a positive integer)")
        out["amount"] = _int_arg(a, "amount")
        out["memo"] = str(a.get("memo", ""))
    return out
