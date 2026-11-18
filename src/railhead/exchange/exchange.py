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
from .settlement import ARMS, compute_supply_settlement, outcome_for
from .tenders import TICKS_PER_DAY, TenderConfig, auction, generate_tenders

DEFAULT_OFFER_TICKS = 30 * TICKS_PER_DAY
LICENCE_END = "ended"  # the outcome of a licence at its deadline (it has no performance to measure)
INSTALMENT_TICKS = 90 * TICKS_PER_DAY  # a licence fee is paid in quarterly instalments, in arrears
GRACE_TICKS = 30 * TICKS_PER_DAY  # cheap talk: payments tagged to an obligation count until due + grace
MAX_MESSAGE_CHARS = 4000
MAX_MEMO_CHARS = 200

BASE_FIELDS = frozenset({"op", "seq", "tick", "company"})
RECORD_FIELDS: dict[str, frozenset[str]] = {
    "init": frozenset({"arm", "companies", "seed", "credit_limit", "tender_config", "start_balances", "rules",
                       "horizon_tick"}),
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
    "pay": frozenset({"to", "amount", "memo", "contract_id"}),
    "obligation_settle": frozenset({"obligation_id", "collected", "paid", "outcome"}),
}
OPTIONAL_FIELDS = {"pay": frozenset({"contract_id"}), "init": frozenset({"rules", "horizon_tick"})}  # records written before the field existed stay valid
SYSTEM_OPS = frozenset({"init", "expire", "activate", "settle", "tender_open", "tender_award", "obligation_settle"})
TENDER_FIELDS = frozenset({"tender_id", "quarter", "industry", "cargo", "distance", "source_industry", "region",
                           "quantity", "max_price", "penalty_per_unit", "open_tick", "bid_close", "window_start",
                           "deadline"})

# deliveries_fn(company, cargo, industry, start_tick, end_tick) -> units delivered, both ends inclusive
DeliveriesFn = Callable[[str, int, int, int, int], int]


class Exchange:
    def __init__(self, companies: Iterable[str], *, arm: str = "enforced", seed: int = 0,
                 credit_limit: int | None = None, tender_config: TenderConfig | None = None,
                 start_balances: dict[str, int] | None = None, tick: int = 0, rules: int = 1,
                 horizon_tick: int | None = None):
        """``start_balances``: money each company gets from the ``exchange`` account at init (unequal
        starts). ``tick``: the tick of the init record (the coordinator uses the start tick).

        ``rules``: 1 (pilot v3 and before) or 2: a tagged payment goes to the oldest unpaid obligation of the
        contract (FIFO), also after its settlement (a late payment); no licence instalment falls due after
        ``horizon_tick`` minus the grace period (the rest folds into the last one in time). A rules-1 init
        record has neither field, so old logs keep their exact records."""
        self._reset()
        cfg = tender_config or TenderConfig()
        extra = {} if rules == 1 else {"rules": rules, "horizon_tick": horizon_tick}
        self.apply({"op": "init", "seq": 0, "tick": tick, "company": None, "arm": arm, "companies": list(companies),
                    "seed": seed, "credit_limit": credit_limit, "tender_config": asdict(cfg),
                    "start_balances": dict(sorted((start_balances or {}).items())), **extra})

    @classmethod
    def replay(cls, records: Iterable[dict[str, Any]]) -> Exchange:
        ex = cls.__new__(cls)
        ex._reset()
        for r in records:
            ex.apply(r)
        return ex

    @classmethod
    def rescore(cls, records: Iterable[dict[str, Any]], horizon_tick: int, end_tick: int | None = None) -> Exchange:
        """An analysis of an existing log under rules 2 (FIFO allocation, late payments, clipped instalments):
        the company records and the contract system records are applied again in order; the logged
        ``obligation_settle`` records are dropped and made again by the rules-2 driver, at the same place in
        the tick (after the contract records of the tick, before the company records). This is not a
        verification: it answers what the obligation outcomes are under the corrected rules."""
        ex = cls.__new__(cls)
        ex._reset()

        def settle_upto(tick: int, inclusive: bool) -> None:
            for o in sorted(ex.obligations.values(), key=lambda o: (o["settle_tick"], _obl_key(o))):
                if o["status"] == "open" and (o["settle_tick"] < tick or (inclusive and o["settle_tick"] == tick)):
                    collected, outcome = ex._obligation_result(o)
                    ex._record("obligation_settle", max(ex.tick, o["settle_tick"]), None, obligation_id=o["id"],
                               collected=collected, paid=o["paid"], outcome=outcome)

        for r in records:
            if r["op"] == "obligation_settle":
                continue
            r = copy.deepcopy(r)
            if r["op"] == "init":
                r.update(rules=2, horizon_tick=horizon_tick)
            else:
                settle_upto(r["tick"], inclusive=r["company"] is not None)
            r["seq"] = len(ex.records)
            ex.apply(r)
        settle_upto(end_tick if end_tick is not None else horizon_tick, inclusive=True)
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
        self.obligations: dict[str, dict[str, Any]] = {}
        self.next_obligation = 1
        self.rules = 1
        self.horizon_tick: int | None = None

    # ================================================================================== record application
    def apply(self, record: dict[str, Any]) -> dict[str, Any]:
        """Validate and apply one record. Raises ExchangeError (and changes nothing) if it is not valid now."""
        if not isinstance(record, dict) or record.get("op") not in RECORD_FIELDS:
            raise ExchangeError("bad_record", f"unknown op in {record!r}")
        op = record["op"]
        want = BASE_FIELDS | RECORD_FIELDS[op]
        if set(record) != want and set(record) | OPTIONAL_FIELDS.get(op, frozenset()) == want:
            record = {**record, **{k: None for k in want - set(record)}}
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

    @staticmethod
    def _award_result(t: dict[str, Any], tick: int) -> tuple[str | None, int | None]:
        """(winner, price) for a tender at ``tick``. An award at or after the tender deadline lapses (no
        winner), so a late ``advance`` never blocks the exchange."""
        best = auction(t["bids"]) if tick < t["deadline"] else None
        return (best["company"], best["price"]) if best else (None, None)

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
        sb = r["start_balances"]
        if not isinstance(sb, dict) or any(k not in ledger.companies or not is_int(v) or v < 0 for k, v in sb.items()):
            raise ExchangeError("bad_config", "start_balances must map companies to non-negative ints")
        for k in sorted(sb):
            if sb[k]:
                ledger.transfer(EXCHANGE, k, sb[k], reason="start_balance", ref="init", tick=r["tick"])
        rules, horizon = r["rules"] or 1, r["horizon_tick"]
        if rules not in (1, 2) or (horizon is not None and not is_int(horizon)):
            raise ExchangeError("bad_config", f"rules must be 1 or 2 and horizon_tick an int: {rules!r}, {horizon!r}")
        self.arm, self.seed, self.tender_config, self.accounts = r["arm"], r["seed"], cfg, ledger
        self.companies = ledger.companies
        self.rules, self.horizon_tick = rules, horizon

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
        licence = c.kind == "licence"
        if self.arm == "enforced" and not licence:  # a licence fee is paid later, in instalments
            self._need_funds([(terms["buyer"], terms["price"]), (terms["seller"], terms["bond"])])
        c.move("signed", tick, "accept", company)
        c.terms, c.signed_tick, c.responder = terms, tick, None
        c.terms_history = [{"from_tick": tick, "terms": copy.deepcopy(terms)}]
        if licence:  # both arms: the fee falls due in quarterly instalments after the right starts
            for due, amount in instalments(terms["fee"], terms["window_start"], terms["deadline"],
                                           self._last_due()):
                self._oblige(c.id, "licence_instalment", terms["grantee"], terms["grantor"], amount, due, tick)
        elif self.arm == "enforced":
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
        if c.kind == "licence":  # the instalments not yet due are replaced by a schedule for the new terms
            fixed = 0
            for o in self._contract_obligations(c.id):
                if o["status"] == "open" and o["due"] > tick:
                    o["status"] = "replaced"
                elif o["status"] != "replaced":
                    fixed += o["amount"]
            rest = max(0, new["fee"] - fixed)
            dues = [d for d in due_ticks(new["window_start"], new["deadline"]) if d > tick]
            for due, amount in clip_schedule(split(rest, dues), self._last_due(), max(tick + 1, new["window_start"])):
                self._oblige(c.id, "licence_instalment", new["grantee"], new["grantor"], amount, due, tick)
            c.terms, c.pending_amendment = new, None
            c.terms_history.append({"from_tick": tick, "terms": copy.deepcopy(new)})
            c.events.append({"tick": tick, "event": "amend_sign", "company": company})
            if c.status == "signed" and tick >= new["window_start"]:
                c.move("active", tick, "activate")
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
        if c.kind == "licence":  # a licence ends: no deliveries, no money
            if (r["delivered"], r["parts"], r["outcome"]) != (0, [], LICENCE_END):
                raise ExchangeError("record_mismatch", f"{c.id}: a licence ends with delivered 0, no parts, "
                                                       f"outcome {LICENCE_END!r}")
            c.move("settled", tick, "settle")
            c.outcome, c.settlement = LICENCE_END, {"outcome": LICENCE_END, "delivered": 0, "parts": []}
            return
        expect = compute_supply_settlement(c.terms, r["delivered"])["outcome"]
        if r["outcome"] != expect:
            raise ExchangeError("record_mismatch", f"{c.id}: outcome {r['outcome']!r} != computed {expect!r}")
        res = self._settle_supply(c, r["delivered"], tick)
        c.move("settled", tick, "settle")
        c.outcome, c.settlement, c.escrow_price, c.escrow_bond = res["outcome"], res, 0, 0
        c.settlement["parts"] = copy.deepcopy(r["parts"])
        if c.tender_id is not None:
            self.tenders[c.tender_id]["status"] = "settled"

    def _settle_supply(self, c: Contract, delivered: int, tick: int) -> dict[str, Any]:
        """Escrowed money moves now (enforced bilateral contracts; tender awards in both arms: the exchange
        pays the winner for the delivered share). What a party still owes becomes an obligation due now:
        the penalty (after the bond), and in cheap talk the buyer's payment. Enforced collects obligations
        at their due tick; cheap talk leaves them to tagged payments (``pay`` with ``contract_id``)."""
        t = c.terms
        res: dict[str, Any] = dict(compute_supply_settlement(t, delivered))
        res.update(contract_id=c.id, arm=self.arm, published=self.arm == "enforced", obligations=[])
        escrowed = c.escrow_price > 0 or c.escrow_bond > 0 or (self.arm == "enforced" and c.tender_id is None)
        if escrowed:
            esc, bnd = escrow_account(c.id), bond_account(c.id)
            self._move(esc, t["seller"], res["seller_payment"], "settle_payment", c.id, tick)
            self._move(esc, t["buyer"], res["buyer_refund"], "settle_refund", c.id, tick)
            self._move(bnd, t["buyer"], res["penalty_from_bond"], "penalty_bond", c.id, tick)
            self._move(bnd, t["seller"], res["bond_returned"], "bond_return", c.id, tick)
            owed_penalty = res["penalty_from_balance"]
        else:  # cheap talk, bilateral: nothing was escrowed; both payments are owed
            owed_penalty = res["penalty"]
            if res["seller_payment"]:
                res["obligations"].append(self._oblige(c.id, "supply_payment", t["buyer"], t["seller"],
                                                       res["seller_payment"], tick, tick))
        if owed_penalty:
            kind = "tender_penalty" if c.tender_id is not None else "supply_penalty"
            res["obligations"].append(self._oblige(c.id, kind, t["seller"], t["buyer"], owed_penalty, tick, tick))
        return res

    # ---------------------------------------------------------------------------------------------- obligations
    def _oblige(self, cid: str, kind: str, payer: str, payee: str, amount: int, due: int, tick: int) -> str:
        oid = f"O{self.next_obligation}"
        self.next_obligation += 1
        settle = due if self.arm == "enforced" else due + GRACE_TICKS
        self.obligations[oid] = {"id": oid, "contract_id": cid, "kind": kind, "payer": payer, "payee": payee,
                                 "amount": amount, "due": due, "settle_tick": settle, "created_tick": tick,
                                 "paid": 0, "paid_by_due": 0, "payments": [], "collected": 0, "status": "open",
                                 "settled_tick": None}
        if self.rules >= 2:
            self.obligations[oid].update(paid_by_settle=0, completed_tick=None)
        return oid

    def _last_due(self) -> int | None:
        """Rules 2: the last tick at which an instalment may fall due (the horizon minus the grace period)."""
        if self.rules < 2 or self.horizon_tick is None:
            return None
        return self.horizon_tick - GRACE_TICKS

    def _contract_obligations(self, cid: str) -> list[dict[str, Any]]:
        return sorted((o for o in self.obligations.values() if o["contract_id"] == cid), key=_obl_key)

    def _obligation_result(self, o: dict[str, Any]) -> tuple[int, str]:
        """(collected, outcome) at the settle tick: enforced collects what is unpaid; cheap talk counts the
        tagged payments."""
        rest = max(0, o["amount"] - o["paid"])
        if self.arm == "enforced":
            return rest, "kept"
        return 0, outcome_for(o["paid"], o["amount"])

    def _op_obligation_settle(self, r: dict[str, Any]) -> None:
        o = self.obligations.get(r["obligation_id"]) if isinstance(r["obligation_id"], str) else None
        if o is None or o["status"] != "open" or r["tick"] < o["settle_tick"]:
            raise ExchangeError("bad_state", f"obligation {r['obligation_id']!r} cannot settle at tick {r['tick']}")
        collected, outcome = self._obligation_result(o)
        if (r["collected"], r["paid"], r["outcome"]) != (collected, o["paid"], outcome):
            raise ExchangeError("record_mismatch", f"{o['id']}: {r['collected']}/{r['paid']}/{r['outcome']} != "
                                                   f"computed {collected}/{o['paid']}/{outcome}")
        assert self.accounts is not None
        if collected:  # forced: the balance may go negative (the recorded debt), as for supply penalties
            self.accounts.transfer(o["payer"], o["payee"], collected, reason="collect", ref=o["id"], tick=r["tick"],
                                   force=True)
        o.update(collected=collected, status=outcome, settled_tick=r["tick"])
        if self.rules >= 2 and collected and o["completed_tick"] is None:
            o["completed_tick"] = r["tick"]

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
        winner, price = self._award_result(t, tick)
        if (r["winner"], r["price"]) != (winner, price):
            raise ExchangeError("record_mismatch", f"{t['tender_id']}: award {r['winner']}/{r['price']} != "
                                                   f"{winner}/{price}")
        if winner is None:
            t["status"] = "unawarded"
            return
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
        self._move(EXCHANGE, escrow_account(cid), price, "tender_escrow", cid, tick)  # both arms: the exchange pays
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
        """A voluntary payment. With ``contract_id`` it counts toward the payer's open obligations of that
        contract to ``to`` (oldest due first; an overpayment counts toward the last one). A tag that names
        no such obligation is refused. ``to`` may be ``exchange`` only with a tag (a tender penalty)."""
        if not isinstance(r["memo"], str) or len(r["memo"]) > MAX_MEMO_CHARS:
            raise ExchangeError("bad_memo", f"memo must be a string of at most {MAX_MEMO_CHARS} characters")
        cid, company, to, tick = r["contract_id"], r["company"], r["to"], r["tick"]
        open_obl: list[dict[str, Any]] = []
        if cid is not None:
            if not isinstance(cid, str):
                raise ExchangeError("bad_tag", f"contract_id must be a string: {cid!r}")
            mine = [o for o in self._contract_obligations(cid) if o["payer"] == company and o["payee"] == to]
            if self.rules >= 2:  # FIFO over everything not yet paid in full, also after its settlement (late)
                open_obl = [o for o in mine if o["status"] != "replaced" and o["paid"] < o["amount"]
                            and not o["collected"]]
            else:
                open_obl = [o for o in mine if o["status"] == "open" and tick < o["settle_tick"]]
            if not open_obl:
                raise ExchangeError("bad_tag", f"{company} owes {to} nothing open under {cid}")
        if to != EXCHANGE:
            self._company(to)
        elif cid is None:
            raise ExchangeError("unknown_company", "a payment to the exchange needs contract_id (a tender penalty)")
        assert self.accounts is not None
        if not is_int(r["amount"]) or r["amount"] <= 0:
            raise ExchangeError("bad_amount", f"amount must be a positive int: {r['amount']!r}")
        if to == EXCHANGE:
            if not self.accounts.can_debit(company, r["amount"]):
                raise ExchangeError("insufficient_funds", f"{company} cannot pay {r['amount']}")
            self.accounts.transfer(company, EXCHANGE, r["amount"], reason="pay", ref=cid or r["memo"], tick=tick)
        else:
            self.accounts.pay(company, to, r["amount"], tick=tick, ref=cid or r["memo"])
        left = r["amount"]
        for i, o in enumerate(open_obl):
            part = left if i == len(open_obl) - 1 else min(left, o["amount"] - o["paid"])
            if part <= 0:
                continue
            o["paid"] += part
            if tick <= o["due"]:
                o["paid_by_due"] += part
            if self.rules >= 2:
                if tick < o["settle_tick"]:
                    o["paid_by_settle"] += part
                if o["completed_tick"] is None and o["paid"] >= o["amount"]:
                    o["completed_tick"] = tick
            o["payments"].append([tick, part])
            left -= part
            if left == 0:
                break

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

    def pay(self, tick: int, company: str, to: str, amount: int, memo: str = "",
            contract_id: str | None = None) -> dict[str, Any]:
        return self._record("pay", tick, company, to=to, amount=amount, memo=memo, contract_id=contract_id)

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
                winner, price = self._award_result(t, tick)
                out.append(self._record("tender_award", tick, None, tender_id=t["tender_id"], winner=winner,
                                        price=price))
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
                if (s.kind == "supply" and s.tender_id is None and s.signed_tick is not None
                        and s.terms["buyer"] == t["seller"]
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
            if c.kind == "licence":
                out.append(self._record("settle", tick, None, contract_id=c.id, delivered=0, parts=[],
                                        outcome=LICENCE_END))
                continue
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
        for o in sorted(self.obligations.values(), key=lambda o: (o["settle_tick"], _obl_key(o))):
            if o["status"] == "open" and o["settle_tick"] <= tick:
                collected, outcome = self._obligation_result(o)
                out.append(self._record("obligation_settle", tick, None, obligation_id=o["id"], collected=collected,
                                        paid=o["paid"], outcome=outcome))
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
        for o in self.obligations.values():
            if o["status"] == "open":
                payer = [o["payer"]] if o["payer"] != EXCHANGE else []
                out.append({"tick": o["due"], "kind": "obligation_due", "id": o["id"], "companies": payer})
                if o["settle_tick"] != o["due"]:
                    out.append({"tick": o["settle_tick"], "kind": "obligation_settle", "id": o["id"],
                                "companies": []})
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

    def licences_held(self, company: str, tick: int) -> list[dict[str, Any]]:
        """The licences that let ``company`` build at ``tick``: signed or active, window_start <= tick <
        deadline. [{contract_id, grantor, rect, window_start, deadline}] in contract order."""
        out = []
        for c in sorted(self.contract_map.values(), key=_id_key):
            t = c.terms
            if (c.kind == "licence" and c.status in LIVE_STATUSES and t["grantee"] == company
                    and t["window_start"] <= tick < t["deadline"]):
                out.append({"contract_id": c.id, "grantor": t["grantor"], "rect": list(t["rect"]),
                            "window_start": t["window_start"], "deadline": t["deadline"]})
        return out

    def contracts(self, company: str) -> list[dict[str, Any]]:
        """``rh contracts``: every contract and offer the company is party to, or may answer (with the
        obligations of the company under each)."""
        self._company(company)
        return [self._contract_view(c, company) for c in sorted(self.contract_map.values(), key=_id_key)
                if c.is_party(company)]

    def obligations_of(self, company: str) -> list[dict[str, Any]]:
        """The obligations that ``company`` owes or is owed: id, contract, kind, payer, payee, amount, due,
        settle tick, paid, status."""
        keys = ("id", "contract_id", "kind", "payer", "payee", "amount", "due", "settle_tick", "paid", "status")
        return [{k: o[k] for k in keys} for o in sorted(self.obligations.values(), key=_obl_key)
                if company in (o["payer"], o["payee"]) and o["status"] != "replaced"]

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
                if c.status == "settled" and c.settlement is not None and c.kind == "supply":
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
            "obligations": self.obligations_of(company),
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
            "your_obligations": [o for o in self.obligations_of(company) if o["contract_id"] == c.id],
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
        if self.arm == "cheap_talk":  # only voluntary payments, start balances and tender money move
            allowed = ("pay", "start_balance", "tender_escrow", "settle_payment", "settle_refund")
            assert all(h.reason in allowed and (h.reason in ("pay", "start_balance") or h.ref in self.tenders)
                       for h in self.accounts.history), "cheap talk moved bilateral contract money"
        for o in self.obligations.values():
            assert o["paid"] == sum(a for _, a in o["payments"]) and o["paid_by_due"] <= o["paid"], o["id"]

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
            "next_obligation": self.next_obligation,
            "obligations": {k: copy.deepcopy(self.obligations[k])
                            for k in sorted(self.obligations, key=lambda k: int(k[1:]))},
        }


def instalments(fee: int, window_start: int, deadline: int, last_due: int | None = None) -> list[tuple[int, int]]:
    """The (due tick, amount) of a licence fee: one instalment each quarter after ``window_start`` (in
    arrears) and the last at ``deadline``; equal amounts, the remainder on the last. A fee of 0: none.
    ``last_due`` (rules 2): no instalment after it; see :func:`clip_schedule`."""
    return clip_schedule(split(fee, due_ticks(window_start, deadline)), last_due, window_start)


def clip_schedule(schedule: list[tuple[int, int]], last_due: int | None, floor: int) -> list[tuple[int, int]]:
    """The instalments due at or before ``last_due``, with the amounts of the later ones folded into the last
    of them. If none is in time: the whole amount at ``last_due`` (not before ``floor``)."""
    if last_due is None or not schedule:
        return schedule
    kept = [(d, a) for d, a in schedule if d <= last_due]
    late = sum(a for d, a in schedule if d > last_due)
    if not kept:
        return [(max(floor, last_due), late)]
    if late:
        kept[-1] = (kept[-1][0], kept[-1][1] + late)
    return kept


def due_ticks(window_start: int, deadline: int) -> list[int]:
    """The instalment ticks of a licence: every quarter after ``window_start`` before ``deadline``, then
    ``deadline``."""
    return list(range(window_start + INSTALMENT_TICKS, deadline, INSTALMENT_TICKS)) + [deadline]


def split(total: int, dues: list[int]) -> list[tuple[int, int]]:
    """``total`` over ``dues`` in equal integer parts, the remainder on the last; no zero parts."""
    if total <= 0 or not dues:
        return []
    each = total // len(dues)
    parts = [(d, each) for d in dues[:-1]] + [(dues[-1], total - each * (len(dues) - 1))]
    return [(d, a) for d, a in parts if a > 0]


def payment_timing(o: dict[str, Any]) -> tuple[str, float | None]:
    """The payment-timing outcome of an obligation at the end of play, and the days late:

    - ``on_time``: paid (or collected) in full before its settle tick (cheap talk: by the due tick plus the
      30-day grace period; enforced: collected at the due tick);
    - ``late``: paid in full after that, by the end of play (days late = from the due tick to the payment
      that completed it);
    - ``partial``: some paid by the end of play; ``unpaid``: nothing.
    Rules-1 obligations have no completion tick: their payments are read from ``payments``."""
    amount = o["amount"]
    total = o["paid"] + o["collected"]
    if total >= amount:
        done = o.get("completed_tick")
        if done is None:  # rules 1: replay the payments
            acc, done = o["collected"], o.get("settled_tick") if o["collected"] else None
            for t, a in o["payments"]:
                acc += a
                if acc >= amount:
                    done = t if done is None else min(done, t)
                    break
        if done is not None and (done < o["settle_tick"] or (o["collected"] and done == o["settle_tick"])):
            return "on_time", None
        return "late", round(max(0, (done if done is not None else o["settle_tick"]) - o["due"]) / TICKS_PER_DAY, 1)
    return ("partial" if total > 0 else "unpaid"), None


def _obl_key(o: dict[str, Any]) -> tuple[int, int]:
    return (o["due"], int(o["id"][1:]))


def _num(s: str) -> int:
    return int(s[1:]) if s[1:].isdigit() else -1


def _num_key(s: str) -> tuple[str, int]:
    return (s[:1], _num(s))


def _id_key(c: Contract) -> tuple[str, int]:
    return _num_key(c.id)
