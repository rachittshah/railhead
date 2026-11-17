"""The Waybill exchange facade: contracts, ledger, tenders, messages and the public order book.

Every state change is a serialisable record (a JSON-safe dict). Command methods validate, build a record and
pass it to :meth:`Exchange.apply`, which is the only mutation path. A rejected command raises
:class:`ExchangeError` and leaves no record and no change. ``Exchange.replay(ex.records)`` rebuilds an
identical exchange (compare :meth:`Exchange.state`).

Record fields: ``op``, ``seq`` (0, 1, 2, ... in log order), ``tick`` (never decreases), ``company`` (the
acting company, or None for records the coordinator writes: expire, activate, award, settle, tender_open),
plus the op's own fields (:data:`RECORD_FIELDS`). See docs/WAYBILL-EXCHANGE.md.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterable
from dataclasses import asdict
from typing import Any

from .contracts import (
    ENABLED_KINDS,
    LIVE_STATUSES,
    OPEN_STATUSES,
    PARTY_FIELDS,
    SCHEMAS,
    Contract,
    parties,
    validate_terms,
)
from .errors import ExchangeError
from .ledger import EXCHANGE, Ledger, bond_account, escrow_account, is_int
from .settlement import ARMS, compute_supply_settlement, settle_supply
from .tenders import TICKS_PER_DAY, TenderConfig, auction, generate_tenders

DEFAULT_OFFER_TICKS = 30 * TICKS_PER_DAY
MAX_MESSAGE_CHARS = 4000
MAX_MEMO_CHARS = 200

BASE_FIELDS = frozenset({"op", "seq", "tick", "company"})
RECORD_FIELDS: dict[str, frozenset[str]] = {
    "init": frozenset({"arm", "companies", "seed", "credit_limit", "tender_config"}),
    "offer": frozenset({"contract_id", "kind", "terms", "to", "expires_tick"}),
    "counter": frozenset({"contract_id", "terms", "expires_tick"}),
    "accept": frozenset({"contract_id"}),
    "withdraw": frozenset({"contract_id"}),
    "amend": frozenset({"contract_id", "terms"}),
    "expire": frozenset({"contract_id"}),
    "activate": frozenset({"contract_id"}),
    "settle": frozenset({"contract_id", "delivered", "parts", "outcome"}),
    "tender_open": frozenset({"quarter", "tenders"}),
    "tender_bid": frozenset({"tender_id", "price"}),
    "tender_award": frozenset({"tender_id", "winner", "price"}),
    "msg": frozenset({"to", "text"}),
    "pay": frozenset({"to", "amount", "memo"}),
}
SYSTEM_OPS = frozenset({"init", "expire", "activate", "settle", "tender_open", "tender_award"})
TENDER_FIELDS = frozenset({"tender_id", "quarter", "industry", "cargo", "distance", "source_industry", "region",
                           "quantity", "max_price", "penalty_per_unit", "open_tick", "bid_close", "window_start",
                           "deadline"})

# deliveries_fn(company, cargo, industry, start_tick, end_tick) -> units delivered, both ends inclusive
DeliveriesFn = Callable[[str, int, int, int, int], int]


class Exchange:
    def __init__(self, companies: Iterable[str], *, arm: str = "enforced", seed: int = 0,
                 credit_limit: int | None = None, tender_config: TenderConfig | None = None):
        self._reset()
        cfg = tender_config or TenderConfig()
        self.apply({"op": "init", "seq": 0, "tick": 0, "company": None, "arm": arm, "companies": list(companies),
                    "seed": seed, "credit_limit": credit_limit, "tender_config": asdict(cfg)})

    @classmethod
    def replay(cls, records: Iterable[dict[str, Any]]) -> Exchange:
        ex = cls.__new__(cls)
        ex._reset()
        for r in records:
            ex.apply(r)
        return ex

    def _reset(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.tick = 0
        self.arm = ""
        self.seed = 0
        self.companies: tuple[str, ...] = ()
        self.accounts: Ledger | None = None
        self.tender_config = TenderConfig()
        self.contract_map: dict[str, Contract] = {}
        self.tenders: dict[str, dict[str, Any]] = {}
        self.messages: list[dict[str, Any]] = []
        self.next_contract = 1
        self.next_tender = 1

    # ================================================================================== record application
    def apply(self, record: dict[str, Any]) -> dict[str, Any]:
        """Validate and apply one record. Raises ExchangeError (and changes nothing) if it is not valid now."""
        if not isinstance(record, dict) or record.get("op") not in RECORD_FIELDS:
            raise ExchangeError("bad_record", f"unknown op in {record!r}")
        op = record["op"]
        want = BASE_FIELDS | RECORD_FIELDS[op]
        if set(record) != want:
            raise ExchangeError("bad_record", f"{op}: fields {sorted(record)} != {sorted(want)}")
        if record["seq"] != len(self.records):
            raise ExchangeError("bad_record", f"{op}: seq {record['seq']!r} != {len(self.records)}")
        tick = record["tick"]
        if not is_int(tick) or tick < 0:
            raise ExchangeError("bad_record", f"{op}: tick must be a non-negative int")
        if tick < self.tick:
            raise ExchangeError("time_reversed", f"{op}: tick {tick} < current tick {self.tick}")
        if (op == "init") != (not self.records):
            raise ExchangeError("bad_record", "init must be the first record, and only the first")
        company = record["company"]
        if op in SYSTEM_OPS:
            if company is not None:
                raise ExchangeError("bad_record", f"{op} is a system record: company must be null")
        else:
            self._company(company)
        rec = copy.deepcopy(record)
        getattr(self, f"_op_{op}")(rec)
        self.records.append(rec)
        self.tick = tick
        return copy.deepcopy(rec)

    def _record(self, op: str, tick: int, company: str | None, **fields: Any) -> dict[str, Any]:
        return self.apply({"op": op, "seq": len(self.records), "tick": tick, "company": company, **fields})

    # ---------------------------------------------------------------------------------------------- helpers
    def _company(self, c: Any) -> str:
        if c not in self.companies:
            raise ExchangeError("unknown_company", repr(c))
        return c

    def _contract(self, cid: Any) -> Contract:
        c = self.contract_map.get(cid) if isinstance(cid, str) else None
        if c is None:
            raise ExchangeError("unknown_contract", repr(cid))
        return c

    def _tender(self, tid: Any) -> dict[str, Any]:
        t = self.tenders.get(tid) if isinstance(tid, str) else None
        if t is None:
            raise ExchangeError("unknown_tender", repr(tid))
        return t

    def _need_funds(self, debits: list[tuple[str, int]]) -> None:
        assert self.accounts is not None
        total: dict[str, int] = {}
        for acct, amt in debits:
            total[acct] = total.get(acct, 0) + amt
        for acct, amt in total.items():
            if amt > 0 and not self.accounts.can_debit(acct, amt):
                raise ExchangeError("insufficient_funds", f"{acct} cannot fund {amt} (balance "
                                                          f"{self.accounts.balance(acct)}, limit {self.accounts.credit_limit})")

    def _move(self, src: str, dst: str, amount: int, reason: str, ref: str, tick: int) -> None:
        assert self.accounts is not None
        if amount > 0:
            self.accounts.transfer(src, dst, amount, reason=reason, ref=ref, tick=tick)

    def _check_open(self, c: Contract, tick: int, company: str) -> None:
        if c.status not in OPEN_STATUSES:
            raise ExchangeError("bad_state", f"{c.id} is {c.status}, not open for negotiation")
        if tick >= c.expires_tick:
            raise ExchangeError("offer_expired", f"{c.id} expired at tick {c.expires_tick}")
        if not c.can_respond(company):
            code = "not_your_turn" if company == c.proposer else "not_party"
            raise ExchangeError(code, f"{company} cannot respond to {c.id} now")

    def _check_supply_window(self, terms: dict[str, Any], tick: int) -> None:
        if terms["deadline"] <= tick:
            raise ExchangeError("deadline_passed", f"deadline {terms['deadline']} is not after tick {tick}")

    def _expiry(self, expires_tick: Any, tick: int) -> int:
        if not is_int(expires_tick) or expires_tick <= tick:
            raise ExchangeError("bad_terms", f"expires_tick must be an int after tick {tick}: {expires_tick!r}")
        return expires_tick

    # ---------------------------------------------------------------------------------------------- ops
    def _op_init(self, r: dict[str, Any]) -> None:
        if r["arm"] not in ARMS:
            raise ExchangeError("bad_arm", f"{r['arm']!r} (arms: {', '.join(ARMS)})")
        if not is_int(r["seed"]):
            raise ExchangeError("bad_record", "seed must be an int")
        if not isinstance(r["companies"], list) or not r["companies"]:
            raise ExchangeError("bad_company", "companies must be a non-empty list")
        try:
            cfg = TenderConfig(**r["tender_config"])
        except TypeError as e:
            raise ExchangeError("bad_config", f"tender_config: {e}") from None
        ledger = Ledger(r["companies"], credit_limit=r["credit_limit"])
        self.arm, self.seed, self.tender_config, self.accounts = r["arm"], r["seed"], cfg, ledger
        self.companies = ledger.companies

    def _op_offer(self, r: dict[str, Any]) -> None:
        tick, company, kind, to = r["tick"], r["company"], r["kind"], r["to"]
        if kind not in SCHEMAS:
            raise ExchangeError("unknown_kind", repr(kind))
        if kind not in ENABLED_KINDS:
            raise ExchangeError("kind_not_enabled", f"{kind} contracts are not tradable yet")
        if to is not None:
            self._company(to)
            if to == company:
                raise ExchangeError("bad_terms", "cannot address an offer to yourself")
        terms = validate_terms(kind, r["terms"], open_party=to is None)
        a, b = parties(kind, terms)
        if company not in (a, b):
            raise ExchangeError("not_party", f"the offerer {company} must be one of the parties {a}, {b}")
        other = b if company == a else a
        if other != to:
            raise ExchangeError("bad_terms", "a public offer leaves the other party null; an addressed offer "
                                             f"names it (to={to!r}, other party={other!r})")
        self._check_supply_window(terms, tick)
        expires = self._expiry(r["expires_tick"], tick)
        cid = f"C{self.next_contract}"
        if r["contract_id"] != cid:
            raise ExchangeError("bad_record", f"contract_id {r['contract_id']!r} != next id {cid}")
        r["terms"] = terms
        self.contract_map[cid] = Contract(id=cid, kind=kind, terms=terms, offerer=company, to=to, created_tick=tick,
                                       expires_tick=expires, proposer=company, responder=to,
                                       events=[{"tick": tick, "event": "offer", "company": company}])
        self.next_contract += 1

    def _filled_terms(self, c: Contract, terms: dict[str, Any], company: str) -> dict[str, Any]:
        """Validate counter terms: the parties stay the same, and an open party becomes ``company``."""
        new = validate_terms(c.kind, terms)
        a_f, b_f = PARTY_FIELDS[c.kind]
        want = dict(c.terms)
        for f in (a_f, b_f):
            if want[f] is None:
                want[f] = company
        if (new[a_f], new[b_f]) != (want[a_f], want[b_f]):
            raise ExchangeError("bad_terms", f"the parties cannot change in a counter: {a_f}={want[a_f]}, "
                                             f"{b_f}={want[b_f]}")
        return new

    def _op_counter(self, r: dict[str, Any]) -> None:
        tick, company = r["tick"], r["company"]
        c = self._contract(r["contract_id"])
        self._check_open(c, tick, company)
        terms = self._filled_terms(c, r["terms"], company)
        self._check_supply_window(terms, tick)
        expires = self._expiry(r["expires_tick"], tick)
        r["terms"] = terms
        c.move("countered", tick, "counter", company)
        c.terms, c.expires_tick = terms, expires
        c.responder, c.proposer = c.proposer, company

    def _op_accept(self, r: dict[str, Any]) -> None:
        tick, company = r["tick"], r["company"]
        c = self._contract(r["contract_id"])
        self._check_open(c, tick, company)
        terms = dict(c.terms)
        for f in PARTY_FIELDS[c.kind]:
            if terms[f] is None:
                terms[f] = company
        self._check_supply_window(terms, tick)
        if self.arm == "enforced":
            self._need_funds([(terms["buyer"], terms["price"]), (terms["seller"], terms["bond"])])
        c.move("signed", tick, "accept", company)
        c.terms, c.signed_tick, c.responder = terms, tick, None
        c.terms_history = [{"from_tick": tick, "terms": copy.deepcopy(terms)}]
        if self.arm == "enforced":
            self._move(terms["buyer"], escrow_account(c.id), terms["price"], "escrow", c.id, tick)
            self._move(terms["seller"], bond_account(c.id), terms["bond"], "bond", c.id, tick)
            c.escrow_price, c.escrow_bond = terms["price"], terms["bond"]
        if tick >= terms["window_start"]:
            c.move("active", tick, "activate")

    def _op_withdraw(self, r: dict[str, Any]) -> None:
        tick, company = r["tick"], r["company"]
        c = self._contract(r["contract_id"])
        if c.status not in OPEN_STATUSES:
            raise ExchangeError("bad_state", f"{c.id} is {c.status}; only open offers can be withdrawn")
        if company not in (c.proposer, c.responder, c.offerer):
            raise ExchangeError("not_party", f"{company} is not negotiating {c.id}")
        c.move("withdrawn", tick, "withdraw", company)

    def _op_expire(self, r: dict[str, Any]) -> None:
        c = self._contract(r["contract_id"])
        if c.status not in OPEN_STATUSES or r["tick"] < c.expires_tick:
            raise ExchangeError("bad_state", f"{c.id} ({c.status}, expires {c.expires_tick}) cannot expire now")
        c.move("expired", r["tick"], "expire")

    def _op_activate(self, r: dict[str, Any]) -> None:
        c = self._contract(r["contract_id"])
        if c.status != "signed" or r["tick"] < c.terms["window_start"]:
            raise ExchangeError("bad_state", f"{c.id} ({c.status}) cannot activate at tick {r['tick']}")
        c.move("active", r["tick"], "activate")

    def _op_amend(self, r: dict[str, Any]) -> None:
        tick, company = r["tick"], r["company"]
        c = self._contract(r["contract_id"])
        if c.status not in LIVE_STATUSES:
            raise ExchangeError("bad_state", f"{c.id} is {c.status}; only signed or active contracts can be amended")
        if c.tender_id is not None:
            raise ExchangeError("not_amendable", f"{c.id} is a tender award")
        if company not in parties(c.kind, c.terms):
            raise ExchangeError("not_party", f"{company} is not a party to {c.id}")
        if tick >= c.terms["deadline"]:
            raise ExchangeError("deadline_passed", f"{c.id} deadline {c.terms['deadline']} has passed")
        new = validate_terms(c.kind, r["terms"])
        if parties(c.kind, new) != parties(c.kind, c.terms):
            raise ExchangeError("bad_terms", "an amendment cannot change the parties")
        self._check_supply_window(new, tick)
        r["terms"] = new
        pend = c.pending_amendment
        if pend is None or pend["by"] == company or pend["terms"] != new:
            c.pending_amendment = {"by": company, "tick": tick, "terms": new}
            c.events.append({"tick": tick, "event": "amend_propose", "company": company})
            return
        dp, db = new["price"] - c.escrow_price, new["bond"] - c.escrow_bond
        if self.arm == "enforced":
            self._need_funds([(new["buyer"], max(dp, 0)), (new["seller"], max(db, 0))])
            self._move(new["buyer"], escrow_account(c.id), dp, "escrow_topup", c.id, tick)
            self._move(escrow_account(c.id), new["buyer"], -dp, "escrow_refund", c.id, tick)
            self._move(new["seller"], bond_account(c.id), db, "bond_topup", c.id, tick)
            self._move(bond_account(c.id), new["seller"], -db, "bond_refund", c.id, tick)
            c.escrow_price, c.escrow_bond = new["price"], new["bond"]
        c.terms, c.pending_amendment = new, None
        c.terms_history.append({"from_tick": tick, "terms": copy.deepcopy(new)})
        c.events.append({"tick": tick, "event": "amend_sign", "company": company})
        if c.status == "signed" and tick >= new["window_start"]:
            c.move("active", tick, "activate")

    def _op_settle(self, r: dict[str, Any]) -> None:
        tick = r["tick"]
        c = self._contract(r["contract_id"])
        if c.status not in LIVE_STATUSES:
            raise ExchangeError("bad_state", f"{c.id} is {c.status}; only signed or active contracts settle")
        if tick < c.terms["deadline"]:
            raise ExchangeError("not_due", f"{c.id} deadline {c.terms['deadline']} is after tick {tick}")
        expect = compute_supply_settlement(c.terms, r["delivered"])["outcome"]
        if r["outcome"] != expect:
            raise ExchangeError("record_mismatch", f"{c.id}: outcome {r['outcome']!r} != computed {expect!r}")
        assert self.accounts is not None
        res = settle_supply(self.accounts, c.id, c.terms, r["delivered"], arm=self.arm, tick=tick)
        c.move("settled", tick, "settle")
        c.outcome, c.settlement, c.escrow_price, c.escrow_bond = res["outcome"], res, 0, 0
        c.settlement["parts"] = copy.deepcopy(r["parts"])
        if c.tender_id is not None:
            self.tenders[c.tender_id]["status"] = "settled"

    def _op_tender_open(self, r: dict[str, Any]) -> None:
        if not is_int(r["quarter"]) or not isinstance(r["tenders"], list):
            raise ExchangeError("bad_record", "tender_open needs an int quarter and a list of tenders")
        for i, t in enumerate(r["tenders"]):
            if not isinstance(t, dict) or set(t) != TENDER_FIELDS:
                raise ExchangeError("bad_record", f"tender {i}: fields must be {sorted(TENDER_FIELDS)}")
            if t["tender_id"] != f"T{self.next_tender + i}":
                raise ExchangeError("bad_record", f"tender {i}: id {t['tender_id']!r} out of sequence")
            ints = [t[k] for k in TENDER_FIELDS - {"tender_id"}]
            if not all(is_int(x) for x in ints) or t["quantity"] < 1 or t["deadline"] <= t["window_start"] \
                    or t["bid_close"] < r["tick"] or t["window_start"] < t["bid_close"]:
                raise ExchangeError("bad_record", f"tender {t['tender_id']}: bad numbers")
        for t in r["tenders"]:
            self.tenders[t["tender_id"]] = {**copy.deepcopy(t), "status": "open", "bids": [], "winner": None,
                                            "price": None}
        self.next_tender += len(r["tenders"])

    def _op_tender_bid(self, r: dict[str, Any]) -> None:
        tick, company, price = r["tick"], r["company"], r["price"]
        t = self._tender(r["tender_id"])
        if t["status"] != "open" or tick >= t["bid_close"]:
            raise ExchangeError("tender_closed", f"{t['tender_id']} bids closed at tick {t['bid_close']}")
        if any(b["company"] == company for b in t["bids"]):
            raise ExchangeError("already_bid", f"{company} already bid on {t['tender_id']}")
        if not is_int(price) or not 0 <= price <= t["max_price"]:
            raise ExchangeError("bad_price", f"bid must be an int in [0, {t['max_price']}]: {price!r}")
        t["bids"].append({"company": company, "price": price, "seq": r["seq"], "tick": tick})

    def _op_tender_award(self, r: dict[str, Any]) -> None:
        tick = r["tick"]
        t = self._tender(r["tender_id"])
        if t["status"] != "open" or tick < t["bid_close"]:
            raise ExchangeError("bad_state", f"{t['tender_id']} ({t['status']}) cannot be awarded at tick {tick}")
        best = auction(t["bids"])
        winner, price = (best["company"], best["price"]) if best else (None, None)
        if (r["winner"], r["price"]) != (winner, price):
            raise ExchangeError("record_mismatch", f"{t['tender_id']}: award {r['winner']}/{r['price']} != "
                                                   f"{winner}/{price}")
        if winner is None:
            t["status"] = "unawarded"
            return
        if t["deadline"] <= tick:
            raise ExchangeError("deadline_passed", f"{t['tender_id']} deadline has passed")
        terms = {"seller": winner, "buyer": EXCHANGE, "cargo": t["cargo"], "industry": t["industry"],
                 "quantity": t["quantity"], "window_start": t["window_start"], "deadline": t["deadline"],
                 "price": price, "penalty_per_unit": t["penalty_per_unit"], "bond": 0}
        cid = t["tender_id"]
        c = Contract(id=cid, kind="supply", terms=terms, offerer=EXCHANGE, to=winner, created_tick=tick,
                     expires_tick=tick, proposer=EXCHANGE, responder=winner, tender_id=cid,
                     events=[{"tick": tick, "event": "award", "company": None}])
        c.move("signed", tick, "sign")
        c.signed_tick, c.responder = tick, None
        c.terms_history = [{"from_tick": tick, "terms": copy.deepcopy(terms)}]
        if self.arm == "enforced":
            self._move(EXCHANGE, escrow_account(cid), price, "tender_escrow", cid, tick)
            c.escrow_price = price
        if tick >= terms["window_start"]:
            c.move("active", tick, "activate")
        self.contract_map[cid] = c
        t.update(status="awarded", winner=winner, price=price)

    def _op_msg(self, r: dict[str, Any]) -> None:
        self._company(r["to"])
        if r["to"] == r["company"]:
            raise ExchangeError("bad_message", "cannot message yourself")
        if not isinstance(r["text"], str) or not r["text"] or len(r["text"]) > MAX_MESSAGE_CHARS:
            raise ExchangeError("bad_message", f"text must be 1..{MAX_MESSAGE_CHARS} characters")
        self.messages.append({"seq": r["seq"], "tick": r["tick"], "from": r["company"], "to": r["to"],
                              "text": r["text"]})

    def _op_pay(self, r: dict[str, Any]) -> None:
        self._company(r["to"])
        if not isinstance(r["memo"], str) or len(r["memo"]) > MAX_MEMO_CHARS:
            raise ExchangeError("bad_memo", f"memo must be a string of at most {MAX_MEMO_CHARS} characters")
        assert self.accounts is not None
        self.accounts.pay(r["company"], r["to"], r["amount"], tick=r["tick"], ref=r["memo"])

    # ================================================================================== commands (rh verbs)
    def offer(self, tick: int, company: str, kind: str, terms: dict[str, Any], *, to: str | None = None,
              expires_tick: int | None = None) -> dict[str, Any]:
        """``rh offer``. ``to=None`` posts to the public order book. Returns the record (``contract_id``)."""
        exp = tick + DEFAULT_OFFER_TICKS if expires_tick is None else expires_tick
        return self._record("offer", tick, company, contract_id=f"C{self.next_contract}", kind=kind, terms=terms,
                            to=to, expires_tick=exp)

    def counter(self, tick: int, company: str, contract_id: str, terms: dict[str, Any], *,
                expires_tick: int | None = None) -> dict[str, Any]:
        exp = tick + DEFAULT_OFFER_TICKS if expires_tick is None else expires_tick
        return self._record("counter", tick, company, contract_id=contract_id, terms=terms, expires_tick=exp)

    def accept(self, tick: int, company: str, contract_id: str) -> dict[str, Any]:
        return self._record("accept", tick, company, contract_id=contract_id)

    def withdraw(self, tick: int, company: str, contract_id: str) -> dict[str, Any]:
        return self._record("withdraw", tick, company, contract_id=contract_id)

    def amend(self, tick: int, company: str, contract_id: str, terms: dict[str, Any]) -> dict[str, Any]:
        """Propose new terms; the same terms from the other party sign the amendment."""
        return self._record("amend", tick, company, contract_id=contract_id, terms=terms)

    def tender_bid(self, tick: int, company: str, tender_id: str, price: int) -> dict[str, Any]:
        return self._record("tender_bid", tick, company, tender_id=tender_id, price=price)

    def msg(self, tick: int, company: str, to: str, text: str) -> dict[str, Any]:
        return self._record("msg", tick, company, to=to, text=text)

    def pay(self, tick: int, company: str, to: str, amount: int, memo: str = "") -> dict[str, Any]:
        return self._record("pay", tick, company, to=to, amount=amount, memo=memo)

    # ================================================================================== coordinator drivers
    def open_tenders(self, tick: int, quarter: int, industries: list[dict[str, Any]]) -> dict[str, Any]:
        """Generate this quarter's tenders from an industry snapshot (seeded) and record them."""
        tenders = generate_tenders(industries, seed=self.seed, quarter=quarter, tick=tick,
                                   config=self.tender_config, first_index=self.next_tender)
        return self._record("tender_open", tick, None, quarter=quarter, tenders=tenders)

    def advance(self, tick: int) -> list[dict[str, Any]]:
        """Records for everything due at ``tick``: offer expiries, tender awards, then activations."""
        out = []
        for c in sorted(self.contract_map.values(), key=_id_key):
            if c.status in OPEN_STATUSES and c.expires_tick <= tick:
                out.append(self._record("expire", tick, None, contract_id=c.id))
        for t in sorted(self.tenders.values(), key=lambda t: _num(t["tender_id"])):
            if t["status"] == "open" and t["bid_close"] <= tick:
                best = auction(t["bids"])
                out.append(self._record("tender_award", tick, None, tender_id=t["tender_id"],
                                        winner=best["company"] if best else None,
                                        price=best["price"] if best else None))
        for c in sorted(self.contract_map.values(), key=_id_key):
            if c.status == "signed" and c.terms["window_start"] <= tick:
                out.append(self._record("activate", tick, None, contract_id=c.id))
        return out

    def delivery_parts(self, c: Contract) -> list[list[Any]]:
        """[[company, start, end], ...] whose deliveries count toward ``c``. For a tender award this is the
        holder plus every seller of a supply contract to the holder with the same cargo and industry, from
        that contract's signing tick (subcontracts)."""
        t = c.terms
        parts = {t["seller"]: [t["seller"], t["window_start"], t["deadline"]]}
        if c.tender_id is not None:
            for s in sorted(self.contract_map.values(), key=_id_key):
                if (s.tender_id is None and s.signed_tick is not None and s.terms["buyer"] == t["seller"]
                        and s.terms["cargo"] == t["cargo"] and s.terms["industry"] == t["industry"]
                        and s.signed_tick <= t["deadline"] and s.terms["seller"] != t["seller"]):
                    start = max(t["window_start"], s.signed_tick)
                    seller = s.terms["seller"]
                    if seller not in parts or start < parts[seller][1]:
                        parts[seller] = [seller, start, t["deadline"]]
        return [parts[k] for k in sorted(parts)]

    def settle_due(self, tick: int, deliveries_fn: DeliveriesFn) -> list[dict[str, Any]]:
        """Run :meth:`advance`, then settle every live contract whose deadline is at or before ``tick``.
        ``deliveries_fn`` is called only here; the records carry the counts so replay never needs it."""
        out = self.advance(tick)
        due = [c for c in self.contract_map.values() if c.status in LIVE_STATUSES and c.terms["deadline"] <= tick]
        for c in sorted(due, key=lambda c: (c.terms["deadline"], _id_key(c))):
            parts = []
            for company, start, end in self.delivery_parts(c):
                n = deliveries_fn(company, c.terms["cargo"], c.terms["industry"], start, end)
                if not is_int(n) or n < 0:
                    raise ExchangeError("bad_delivered", f"deliveries_fn returned {n!r} for {company}")
                parts.append([company, start, end, n])
            delivered = sum(p[3] for p in parts)
            outcome = compute_supply_settlement(c.terms, delivered)["outcome"]
            out.append(self._record("settle", tick, None, contract_id=c.id, delivered=delivered, parts=parts,
                                    outcome=outcome))
        return out

    def deadlines(self) -> list[dict[str, Any]]:
        """Pending ticks that need a forced wake-up: [{tick, kind, id, companies}], sorted."""
        out = []
        for c in self.contract_map.values():
            if c.status in OPEN_STATUSES:
                who = [x for x in (c.proposer, c.responder) if x is not None]
                out.append({"tick": c.expires_tick, "kind": "offer_expiry", "id": c.id, "companies": sorted(who)})
            elif c.status in LIVE_STATUSES:
                who = [x for x in parties(c.kind, c.terms) if x != EXCHANGE]
                if c.status == "signed":
                    out.append({"tick": c.terms["window_start"], "kind": "window_start", "id": c.id,
                                "companies": sorted(who)})
                out.append({"tick": c.terms["deadline"], "kind": "deadline", "id": c.id, "companies": sorted(who)})
        for t in self.tenders.values():
            if t["status"] == "open":
                out.append({"tick": t["bid_close"], "kind": "bid_close", "id": t["tender_id"],
                            "companies": list(self.companies)})
        return sorted(out, key=lambda d: (d["tick"], d["kind"], _num(d["id"]), d["id"]))

    # ================================================================================== queries (rh verbs)
    def market(self, company: str) -> dict[str, Any]:
        """``rh market``: the public order book and the tenders open for bids (other bids stay sealed)."""
        self._company(company)
        book = [self._contract_view(c, company) for c in sorted(self.contract_map.values(), key=_id_key)
                if c.status == "offered" and c.to is None and c.expires_tick > self.tick]
        tenders = []
        for t in sorted(self.tenders.values(), key=lambda t: _num(t["tender_id"])):
            if t["status"] == "open":
                v = {k: t[k] for k in sorted(TENDER_FIELDS)}
                mine = [b["price"] for b in t["bids"] if b["company"] == company]
                v["your_bid"] = mine[0] if mine else None
                tenders.append(v)
        return {"tick": self.tick, "order_book": book, "tenders": tenders}

    def contracts(self, company: str) -> list[dict[str, Any]]:
        """``rh contracts``: every contract and offer the company is party to, or may answer."""
        self._company(company)
        return [self._contract_view(c, company) for c in sorted(self.contract_map.values(), key=_id_key)
                if c.is_party(company)]

    def inbox(self, company: str) -> list[dict[str, Any]]:
        self._company(company)
        return [dict(m) for m in self.messages if m["to"] == company]

    def ledger(self, company: str) -> dict[str, Any]:
        """``rh ledger``. Enforced: the public ledger. Cheap talk: own balance and own transfers only."""
        self._company(company)
        assert self.accounts is not None
        s = self.accounts.state()
        if self.arm == "enforced":
            return s
        return {"balances": {company: self.accounts.balance(company)},
                "history": [h for h in s["history"] if company in (h["src"], h["dst"])]}

    def public_view(self, company: str) -> dict[str, Any]:
        """Everything one company may see. Never shows other companies' messages or sealed bids; in
        cheap talk it never shows settlement outcomes (settled contracts read ``closed``)."""
        settlements: list[dict[str, Any]] = []
        awards = []
        if self.arm == "enforced":
            for c in sorted(self.contract_map.values(), key=_id_key):
                if c.status == "settled" and c.settlement is not None:
                    settlements.append({"contract_id": c.id, "seller": c.terms["seller"], "buyer": c.terms["buyer"],
                                        "outcome": c.outcome, "delivered": c.settlement["delivered"],
                                        "quantity": c.terms["quantity"], "tick": c.events[-1]["tick"]})
        for t in sorted(self.tenders.values(), key=lambda t: _num(t["tender_id"])):
            if t["status"] != "open":
                awards.append({"tender_id": t["tender_id"], "winner": t["winner"], "price": t["price"]})
        view = {
            "tick": self.tick, "arm": self.arm, "company": company,
            "market": self.market(company),
            "contracts": self.contracts(company),
            "inbox": self.inbox(company),
            "sent": [dict(m) for m in self.messages if m["from"] == company],
            "ledger": self.ledger(company),
            "tender_awards": awards,
        }
        if self.arm == "enforced":
            view["settlements"] = settlements
        return view

    def _contract_view(self, c: Contract, company: str) -> dict[str, Any]:
        v = {
            "contract_id": c.id, "kind": c.kind, "status": c.status, "terms": copy.deepcopy(c.terms),
            "offerer": c.offerer, "to": c.to, "expires_tick": c.expires_tick, "signed_tick": c.signed_tick,
            "tender_id": c.tender_id, "your_turn": c.status in OPEN_STATUSES and c.can_respond(company),
            "pending_amendment": copy.deepcopy(c.pending_amendment),
            "terms_history": copy.deepcopy(c.terms_history),
        }
        if c.status == "settled":
            if self.arm == "enforced":
                v["outcome"], v["settlement"] = c.outcome, copy.deepcopy(c.settlement)
            else:
                v["status"] = "closed"
        return v

    # ================================================================================== audit
    def check_invariants(self) -> None:
        """Ledger conserved; every escrow account holds exactly what its live contract says."""
        assert self.accounts is not None
        self.accounts.check_conservation()
        for acct, bal in self.accounts.balances().items():
            if acct.startswith("escrow:"):
                cid, _, sub = acct[len("escrow:"):].partition(":")
                c = self.contract_map.get(cid)
                assert c is not None and c.status in LIVE_STATUSES, f"{acct} holds {bal} for a closed contract"
                assert bal == (c.escrow_bond if sub == "bond" else c.escrow_price), f"{acct} != contract escrow"
        if self.arm == "cheap_talk":
            assert all(h.reason == "pay" for h in self.accounts.history), "cheap talk moved non-pay money"

    def state(self) -> dict[str, Any]:
        """Canonical, JSON-safe snapshot. Two exchanges with equal records have equal states."""
        assert self.accounts is not None
        return {
            "tick": self.tick, "arm": self.arm, "seed": self.seed, "companies": list(self.companies),
            "n_records": len(self.records), "next_contract": self.next_contract, "next_tender": self.next_tender,
            "contracts": {k: self.contract_map[k].to_dict() for k in sorted(self.contract_map, key=_num_key)},
            "tenders": {k: copy.deepcopy(self.tenders[k]) for k in sorted(self.tenders, key=_num_key)},
            "messages": copy.deepcopy(self.messages),
            "ledger": self.accounts.state(),
        }


def _num(s: str) -> int:
    return int(s[1:]) if s[1:].isdigit() else -1


def _num_key(s: str) -> tuple[str, int]:
    return (s[:1], _num(s))


def _id_key(c: Contract) -> tuple[str, int]:
    return _num_key(c.id)
