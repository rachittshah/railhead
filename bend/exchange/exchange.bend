# exchange.bend: record validation and application of the Waybill exchange,
# the port of src/railhead/exchange/exchange.py (Exchange.apply, every _op_*,
# state, check_invariants) and tenders.auction / TenderConfig validation.
#
# API (import ./exchange.bend as E):
#   type Ex is Data          the exchange (Exchange after _reset(), then records applied)
#   E.empty() -> Ex          Exchange.__new__ + _reset()
#   E.apply(Ex, J.Json) -> Result<&2, &2, String, Ex>
#       Exchange.apply(record): Fail{code} with the ExchangeError code Python raises first,
#       or "internal" where Python raises another exception (an unhashable op or kind)
#   E.check_invariants(Ex) -> Bool     Exchange.check_invariants() passes (False before init)
#   E.state_json(Ex) -> J.Json         Exchange.state()
#   E.replay(List<&2, J.Json>) -> J.Json
#       railhead.exchange.bend.reference_result: {"ok": state} or {"error": code, "seq": i}
#       (i = index of the failing record; "invariant" at seq = number of records)
#
# Python semantics kept on purpose (docs/WAYBILL-BEND.md sections 3, 8, 9):
#   - check order is Python's, statement by statement; one check block that raises a
#     single code (validate_terms, TenderConfig, a tender_open tender) is one Bool;
#   - `==` sites follow C.py_eq_* (seq, contract_id, tender_id, award, settle outcome,
#     licence settle, obligation_settle, rules); `rules = r["rules"] or 1`;
#   - field sets: a record may omit OPTIONAL_FIELDS (read as null; nothing stored);
#   - echoed input: settle.parts, the seq of bids and messages (a float/bool seq that
#     passed `==` is stored as written); computed values are ints;
#   - truthiness where Python tests a value (`if collected:`), `> 0` where it compares;
#   - tender terms are not schema-checked, so no sign is assumed downstream of a tender.
#
# Pure: no IO, no @unsafe; recursion is structural over lists (and Nat fuel in rules.bend).

import Base
import ./int.bend as I
import ./json.bend as J
import ./ledger.bend as L
import ./rules.bend as R
import ./contracts.bend as C
import ./settlement.bend as S

# State records
# =============
# Tender: a tender_open tender (src: its 14 fields as recorded) plus Python's
# status / bids / winner / price. Obl: an obligation dict (r2: made under rules 2,
# which adds paid_by_settle and completed_tick). Ex: the Exchange attributes the
# replay needs (records are not kept: only their count reaches the state).

type Bid is Data:
  Bid{company: String, price: I.Int, seq: J.Json, tick: I.Int}

type Tender is Data:
  Tender{id: String, src: J.Json, status: String, bids: List<&2, Bid>, winner: Maybe<&2, String>, price: Maybe<&2, I.Int>}

def t_id(x: Tender) -> String:
  match x:
    case Tender{id, src, status, bids, winner, price}:
      id

def t_src(x: Tender) -> J.Json:
  match x:
    case Tender{id, src, status, bids, winner, price}:
      src

def t_status(x: Tender) -> String:
  match x:
    case Tender{id, src, status, bids, winner, price}:
      status

def t_bids(x: Tender) -> List<&2, Bid>:
  match x:
    case Tender{id, src, status, bids, winner, price}:
      bids

def t_winner(x: Tender) -> Maybe<&2, String>:
  match x:
    case Tender{id, src, status, bids, winner, price}:
      winner

def t_price(x: Tender) -> Maybe<&2, I.Int>:
  match x:
    case Tender{id, src, status, bids, winner, price}:
      price

def t_set_status(x: Tender, v: String) -> Tender:
  match x:
    case Tender{id, src, _, bids, winner, price}:
      Tender{id, src, v, bids, winner, price}

def t_set_bids(x: Tender, v: List<&2, Bid>) -> Tender:
  match x:
    case Tender{id, src, status, _, winner, price}:
      Tender{id, src, status, v, winner, price}

def t_set_winner(x: Tender, v: Maybe<&2, String>) -> Tender:
  match x:
    case Tender{id, src, status, bids, _, price}:
      Tender{id, src, status, bids, v, price}

def t_set_price(x: Tender, v: Maybe<&2, I.Int>) -> Tender:
  match x:
    case Tender{id, src, status, bids, winner, _}:
      Tender{id, src, status, bids, winner, v}

type Obl is Data:
  Obl{id: String, num: I.Int, cid: String, kind: String, payer: String, payee: String, amount: I.Int, due: I.Int, settle: I.Int, created: I.Int, paid: I.Int, paid_by_due: I.Int, payments: List<&2, J.Json>, collected: I.Int, status: String, settled: Maybe<&2, I.Int>, r2: Bool, paid_by_settle: I.Int, completed: Maybe<&2, I.Int>}

def o_id(x: Obl) -> String:
  match x:
    case Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, paid, paid_by_due, payments, collected, status, settled, r2, paid_by_settle, completed}:
      id

def o_num(x: Obl) -> I.Int:
  match x:
    case Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, paid, paid_by_due, payments, collected, status, settled, r2, paid_by_settle, completed}:
      num

def o_cid(x: Obl) -> String:
  match x:
    case Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, paid, paid_by_due, payments, collected, status, settled, r2, paid_by_settle, completed}:
      cid

def o_kind(x: Obl) -> String:
  match x:
    case Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, paid, paid_by_due, payments, collected, status, settled, r2, paid_by_settle, completed}:
      kind

def o_payer(x: Obl) -> String:
  match x:
    case Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, paid, paid_by_due, payments, collected, status, settled, r2, paid_by_settle, completed}:
      payer

def o_payee(x: Obl) -> String:
  match x:
    case Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, paid, paid_by_due, payments, collected, status, settled, r2, paid_by_settle, completed}:
      payee

def o_amount(x: Obl) -> I.Int:
  match x:
    case Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, paid, paid_by_due, payments, collected, status, settled, r2, paid_by_settle, completed}:
      amount

def o_due(x: Obl) -> I.Int:
  match x:
    case Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, paid, paid_by_due, payments, collected, status, settled, r2, paid_by_settle, completed}:
      due

def o_settle(x: Obl) -> I.Int:
  match x:
    case Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, paid, paid_by_due, payments, collected, status, settled, r2, paid_by_settle, completed}:
      settle

def o_created(x: Obl) -> I.Int:
  match x:
    case Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, paid, paid_by_due, payments, collected, status, settled, r2, paid_by_settle, completed}:
      created

def o_paid(x: Obl) -> I.Int:
  match x:
    case Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, paid, paid_by_due, payments, collected, status, settled, r2, paid_by_settle, completed}:
      paid

def o_paid_by_due(x: Obl) -> I.Int:
  match x:
    case Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, paid, paid_by_due, payments, collected, status, settled, r2, paid_by_settle, completed}:
      paid_by_due

def o_payments(x: Obl) -> List<&2, J.Json>:
  match x:
    case Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, paid, paid_by_due, payments, collected, status, settled, r2, paid_by_settle, completed}:
      payments

def o_collected(x: Obl) -> I.Int:
  match x:
    case Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, paid, paid_by_due, payments, collected, status, settled, r2, paid_by_settle, completed}:
      collected

def o_status(x: Obl) -> String:
  match x:
    case Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, paid, paid_by_due, payments, collected, status, settled, r2, paid_by_settle, completed}:
      status

def o_settled(x: Obl) -> Maybe<&2, I.Int>:
  match x:
    case Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, paid, paid_by_due, payments, collected, status, settled, r2, paid_by_settle, completed}:
      settled

def o_r2(x: Obl) -> Bool:
  match x:
    case Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, paid, paid_by_due, payments, collected, status, settled, r2, paid_by_settle, completed}:
      r2

def o_paid_by_settle(x: Obl) -> I.Int:
  match x:
    case Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, paid, paid_by_due, payments, collected, status, settled, r2, paid_by_settle, completed}:
      paid_by_settle

def o_completed(x: Obl) -> Maybe<&2, I.Int>:
  match x:
    case Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, paid, paid_by_due, payments, collected, status, settled, r2, paid_by_settle, completed}:
      completed

def o_set_paid(x: Obl, v: I.Int) -> Obl:
  match x:
    case Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, _, paid_by_due, payments, collected, status, settled, r2, paid_by_settle, completed}:
      Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, v, paid_by_due, payments, collected, status, settled, r2, paid_by_settle, completed}

def o_set_paid_by_due(x: Obl, v: I.Int) -> Obl:
  match x:
    case Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, paid, _, payments, collected, status, settled, r2, paid_by_settle, completed}:
      Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, paid, v, payments, collected, status, settled, r2, paid_by_settle, completed}

def o_set_payments(x: Obl, v: List<&2, J.Json>) -> Obl:
  match x:
    case Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, paid, paid_by_due, _, collected, status, settled, r2, paid_by_settle, completed}:
      Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, paid, paid_by_due, v, collected, status, settled, r2, paid_by_settle, completed}

def o_set_collected(x: Obl, v: I.Int) -> Obl:
  match x:
    case Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, paid, paid_by_due, payments, _, status, settled, r2, paid_by_settle, completed}:
      Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, paid, paid_by_due, payments, v, status, settled, r2, paid_by_settle, completed}

def o_set_status(x: Obl, v: String) -> Obl:
  match x:
    case Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, paid, paid_by_due, payments, collected, _, settled, r2, paid_by_settle, completed}:
      Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, paid, paid_by_due, payments, collected, v, settled, r2, paid_by_settle, completed}

def o_set_settled(x: Obl, v: Maybe<&2, I.Int>) -> Obl:
  match x:
    case Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, paid, paid_by_due, payments, collected, status, _, r2, paid_by_settle, completed}:
      Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, paid, paid_by_due, payments, collected, status, v, r2, paid_by_settle, completed}

def o_set_paid_by_settle(x: Obl, v: I.Int) -> Obl:
  match x:
    case Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, paid, paid_by_due, payments, collected, status, settled, r2, _, completed}:
      Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, paid, paid_by_due, payments, collected, status, settled, r2, v, completed}

def o_set_completed(x: Obl, v: Maybe<&2, I.Int>) -> Obl:
  match x:
    case Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, paid, paid_by_due, payments, collected, status, settled, r2, paid_by_settle, _}:
      Obl{id, num, cid, kind, payer, payee, amount, due, settle, created, paid, paid_by_due, payments, collected, status, settled, r2, paid_by_settle, v}

type Ex is Data:
  Ex{n: I.Int, tick: I.Int, arm: String, seed: I.Int, companies: List<&2, String>, ledger: L.Ledger, contracts: List<&2, C.Contract>, tenders: List<&2, Tender>, messages: List<&2, J.Json>, next_c: I.Int, next_t: I.Int, obls: List<&2, Obl>, next_o: I.Int, rules2: Bool, horizon: Maybe<&2, I.Int>}

def x_n(x: Ex) -> I.Int:
  match x:
    case Ex{n, tick, arm, seed, companies, ledger, contracts, tenders, messages, next_c, next_t, obls, next_o, rules2, horizon}:
      n

def x_tick(x: Ex) -> I.Int:
  match x:
    case Ex{n, tick, arm, seed, companies, ledger, contracts, tenders, messages, next_c, next_t, obls, next_o, rules2, horizon}:
      tick

def x_arm(x: Ex) -> String:
  match x:
    case Ex{n, tick, arm, seed, companies, ledger, contracts, tenders, messages, next_c, next_t, obls, next_o, rules2, horizon}:
      arm

def x_seed(x: Ex) -> I.Int:
  match x:
    case Ex{n, tick, arm, seed, companies, ledger, contracts, tenders, messages, next_c, next_t, obls, next_o, rules2, horizon}:
      seed

def x_companies(x: Ex) -> List<&2, String>:
  match x:
    case Ex{n, tick, arm, seed, companies, ledger, contracts, tenders, messages, next_c, next_t, obls, next_o, rules2, horizon}:
      companies

def x_ledger(x: Ex) -> L.Ledger:
  match x:
    case Ex{n, tick, arm, seed, companies, ledger, contracts, tenders, messages, next_c, next_t, obls, next_o, rules2, horizon}:
      ledger

def x_contracts(x: Ex) -> List<&2, C.Contract>:
  match x:
    case Ex{n, tick, arm, seed, companies, ledger, contracts, tenders, messages, next_c, next_t, obls, next_o, rules2, horizon}:
      contracts

def x_tenders(x: Ex) -> List<&2, Tender>:
  match x:
    case Ex{n, tick, arm, seed, companies, ledger, contracts, tenders, messages, next_c, next_t, obls, next_o, rules2, horizon}:
      tenders

def x_messages(x: Ex) -> List<&2, J.Json>:
  match x:
    case Ex{n, tick, arm, seed, companies, ledger, contracts, tenders, messages, next_c, next_t, obls, next_o, rules2, horizon}:
      messages

def x_next_c(x: Ex) -> I.Int:
  match x:
    case Ex{n, tick, arm, seed, companies, ledger, contracts, tenders, messages, next_c, next_t, obls, next_o, rules2, horizon}:
      next_c

def x_next_t(x: Ex) -> I.Int:
  match x:
    case Ex{n, tick, arm, seed, companies, ledger, contracts, tenders, messages, next_c, next_t, obls, next_o, rules2, horizon}:
      next_t

def x_obls(x: Ex) -> List<&2, Obl>:
  match x:
    case Ex{n, tick, arm, seed, companies, ledger, contracts, tenders, messages, next_c, next_t, obls, next_o, rules2, horizon}:
      obls

def x_next_o(x: Ex) -> I.Int:
  match x:
    case Ex{n, tick, arm, seed, companies, ledger, contracts, tenders, messages, next_c, next_t, obls, next_o, rules2, horizon}:
      next_o

def x_rules2(x: Ex) -> Bool:
  match x:
    case Ex{n, tick, arm, seed, companies, ledger, contracts, tenders, messages, next_c, next_t, obls, next_o, rules2, horizon}:
      rules2

def x_horizon(x: Ex) -> Maybe<&2, I.Int>:
  match x:
    case Ex{n, tick, arm, seed, companies, ledger, contracts, tenders, messages, next_c, next_t, obls, next_o, rules2, horizon}:
      horizon

def x_set_n(x: Ex, v: I.Int) -> Ex:
  match x:
    case Ex{_, tick, arm, seed, companies, ledger, contracts, tenders, messages, next_c, next_t, obls, next_o, rules2, horizon}:
      Ex{v, tick, arm, seed, companies, ledger, contracts, tenders, messages, next_c, next_t, obls, next_o, rules2, horizon}

def x_set_tick(x: Ex, v: I.Int) -> Ex:
  match x:
    case Ex{n, _, arm, seed, companies, ledger, contracts, tenders, messages, next_c, next_t, obls, next_o, rules2, horizon}:
      Ex{n, v, arm, seed, companies, ledger, contracts, tenders, messages, next_c, next_t, obls, next_o, rules2, horizon}

def x_set_arm(x: Ex, v: String) -> Ex:
  match x:
    case Ex{n, tick, _, seed, companies, ledger, contracts, tenders, messages, next_c, next_t, obls, next_o, rules2, horizon}:
      Ex{n, tick, v, seed, companies, ledger, contracts, tenders, messages, next_c, next_t, obls, next_o, rules2, horizon}

def x_set_seed(x: Ex, v: I.Int) -> Ex:
  match x:
    case Ex{n, tick, arm, _, companies, ledger, contracts, tenders, messages, next_c, next_t, obls, next_o, rules2, horizon}:
      Ex{n, tick, arm, v, companies, ledger, contracts, tenders, messages, next_c, next_t, obls, next_o, rules2, horizon}

def x_set_companies(x: Ex, v: List<&2, String>) -> Ex:
  match x:
    case Ex{n, tick, arm, seed, _, ledger, contracts, tenders, messages, next_c, next_t, obls, next_o, rules2, horizon}:
      Ex{n, tick, arm, seed, v, ledger, contracts, tenders, messages, next_c, next_t, obls, next_o, rules2, horizon}

def x_set_ledger(x: Ex, v: L.Ledger) -> Ex:
  match x:
    case Ex{n, tick, arm, seed, companies, _, contracts, tenders, messages, next_c, next_t, obls, next_o, rules2, horizon}:
      Ex{n, tick, arm, seed, companies, v, contracts, tenders, messages, next_c, next_t, obls, next_o, rules2, horizon}

def x_set_contracts(x: Ex, v: List<&2, C.Contract>) -> Ex:
  match x:
    case Ex{n, tick, arm, seed, companies, ledger, _, tenders, messages, next_c, next_t, obls, next_o, rules2, horizon}:
      Ex{n, tick, arm, seed, companies, ledger, v, tenders, messages, next_c, next_t, obls, next_o, rules2, horizon}

def x_set_tenders(x: Ex, v: List<&2, Tender>) -> Ex:
  match x:
    case Ex{n, tick, arm, seed, companies, ledger, contracts, _, messages, next_c, next_t, obls, next_o, rules2, horizon}:
      Ex{n, tick, arm, seed, companies, ledger, contracts, v, messages, next_c, next_t, obls, next_o, rules2, horizon}

def x_set_messages(x: Ex, v: List<&2, J.Json>) -> Ex:
  match x:
    case Ex{n, tick, arm, seed, companies, ledger, contracts, tenders, _, next_c, next_t, obls, next_o, rules2, horizon}:
      Ex{n, tick, arm, seed, companies, ledger, contracts, tenders, v, next_c, next_t, obls, next_o, rules2, horizon}

def x_set_next_c(x: Ex, v: I.Int) -> Ex:
  match x:
    case Ex{n, tick, arm, seed, companies, ledger, contracts, tenders, messages, _, next_t, obls, next_o, rules2, horizon}:
      Ex{n, tick, arm, seed, companies, ledger, contracts, tenders, messages, v, next_t, obls, next_o, rules2, horizon}

def x_set_next_t(x: Ex, v: I.Int) -> Ex:
  match x:
    case Ex{n, tick, arm, seed, companies, ledger, contracts, tenders, messages, next_c, _, obls, next_o, rules2, horizon}:
      Ex{n, tick, arm, seed, companies, ledger, contracts, tenders, messages, next_c, v, obls, next_o, rules2, horizon}

def x_set_obls(x: Ex, v: List<&2, Obl>) -> Ex:
  match x:
    case Ex{n, tick, arm, seed, companies, ledger, contracts, tenders, messages, next_c, next_t, _, next_o, rules2, horizon}:
      Ex{n, tick, arm, seed, companies, ledger, contracts, tenders, messages, next_c, next_t, v, next_o, rules2, horizon}

def x_set_next_o(x: Ex, v: I.Int) -> Ex:
  match x:
    case Ex{n, tick, arm, seed, companies, ledger, contracts, tenders, messages, next_c, next_t, obls, _, rules2, horizon}:
      Ex{n, tick, arm, seed, companies, ledger, contracts, tenders, messages, next_c, next_t, obls, v, rules2, horizon}

def x_set_rules2(x: Ex, v: Bool) -> Ex:
  match x:
    case Ex{n, tick, arm, seed, companies, ledger, contracts, tenders, messages, next_c, next_t, obls, next_o, _, horizon}:
      Ex{n, tick, arm, seed, companies, ledger, contracts, tenders, messages, next_c, next_t, obls, next_o, v, horizon}

def x_set_horizon(x: Ex, v: Maybe<&2, I.Int>) -> Ex:
  match x:
    case Ex{n, tick, arm, seed, companies, ledger, contracts, tenders, messages, next_c, next_t, obls, next_o, rules2, _}:
      Ex{n, tick, arm, seed, companies, ledger, contracts, tenders, messages, next_c, next_t, obls, next_o, rules2, v}

# Constants and small helpers
# ===========================

def GRACE() -> I.Int:
  I.from_u32(2220)

def fst(-A: Data, -B: Data, p: +Sigma<A, _ => B>) -> A:
  (a, b) = p
  a

def snd(-A: Data, -B: Data, p: +Sigma<A, _ => B>) -> B:
  (a, b) = p
  b

def b_company(b: Bid) -> String:
  match b:
    case Bid{c, p, s, t}:
      c

def b_price(b: Bid) -> I.Int:
  match b:
    case Bid{c, p, s, t}:
      p

def b_tick(b: Bid) -> I.Int:
  match b:
    case Bid{c, p, s, t}:
      t

def b_json(b: Bid) -> J.Json:
  match b:
    case Bid{c, p, s, t}:
      J.obj([("company", J.JStr{c}), ("price", C.jint(p)), ("seq", s), ("tick", C.jint(t))])

def bool_eq(a: Bool, b: Bool) -> Bool:
  Bool.not(Bool.xor(a, b))

def nonzero(+a: I.Int) -> Bool:
  Bool.not(I.is_zero(a))

def to_opt(j: J.Json) -> Maybe<&2, String>:
  match j:
    case J.JStr{s}:
      Some{s}
    case _:
      None{}

def empty() -> Ex:
  Ex{I.zero(), I.zero(), "", I.zero(), [], L.Ledger{[], None{}, [], [], I.zero()}, [], [], [], I.one(), I.one(), [], I.one(), False{}, None{}}

def enforced(+ex: Ex) -> Bool:
  String.eq(x_arm(ex), "enforced")

# Exchange._last_due: rules 2, the horizon minus the grace period
def last_due.go(r2: Bool, h: Maybe<&2, I.Int>) -> Maybe<&2, I.Int>:
  match r2:
    case False{}:
      None{}
    case True{}:
      match h:
        case None{}:
          None{}
        case Some{v}:
          Some{I.sub(v, GRACE())}

def last_due(+ex: Ex) -> Maybe<&2, I.Int>:
  last_due.go(x_rules2(ex), x_horizon(ex))

# rules >= 2 and horizon_tick is not None and tick >= horizon_tick
def at_end.go(r2: Bool, h: Maybe<&2, I.Int>, +tick: I.Int) -> Bool:
  match r2:
    case False{}:
      False{}
    case True{}:
      match h:
        case None{}:
          False{}
        case Some{v}:
          I.is_ge(tick, v)

def at_end(+ex: Ex, +tick: I.Int) -> Bool:
  at_end.go(x_rules2(ex), x_horizon(ex), tick)

# Lookups
# =======

def find_contract.if(c: C.Contract, rest: Maybe<&2, C.Contract>, hit: Bool) -> Maybe<&2, C.Contract>:
  match hit:
    case True{}:
      Some{c}
    case False{}:
      rest

def find_contract(cs: List<&2, C.Contract>, +id: String) -> Maybe<&2, C.Contract>:
  match cs:
    case Nil{}:
      None{}
    case Con{+c, t}:
      find_contract.if(c, find_contract(t, id), String.eq(C.c_id(c), id))

def put_contract.go(cs: List<&2, C.Contract>, +c: C.Contract) -> List<&2, C.Contract>:
  match cs:
    case Nil{}:
      Nil{}
    case Con{+h, t}:
      Con{Bool.pick(C.Contract, String.eq(C.c_id(h), C.c_id(c)), c, h), put_contract.go(t, c)}

# contract_map[c.id] = c, for a contract already in the map
def put_contract(+ex: Ex, +c: C.Contract) -> Ex:
  x_set_contracts(ex, put_contract.go(x_contracts(ex), c))

def add_contract(+ex: Ex, c: C.Contract) -> Ex:
  x_set_contracts(ex, List.append(&2, C.Contract, x_contracts(ex), [c]))

def got_contract(m: Maybe<&2, C.Contract>) -> Result<&2, &2, String, C.Contract>:
  match m:
    case None{}:
      Fail{"unknown_contract"}
    case Some{c}:
      Done{c}

# Exchange._contract
def get_contract(+ex: Ex, cid: J.Json) -> Result<&2, &2, String, C.Contract>:
  match cid:
    case J.JStr{s}:
      got_contract(find_contract(x_contracts(ex), s))
    case _:
      Fail{"unknown_contract"}

def find_tender.if(t: Tender, rest: Maybe<&2, Tender>, hit: Bool) -> Maybe<&2, Tender>:
  match hit:
    case True{}:
      Some{t}
    case False{}:
      rest

def find_tender(ts: List<&2, Tender>, +id: String) -> Maybe<&2, Tender>:
  match ts:
    case Nil{}:
      None{}
    case Con{+t, r}:
      find_tender.if(t, find_tender(r, id), String.eq(t_id(t), id))

def got_tender(m: Maybe<&2, Tender>) -> Result<&2, &2, String, Tender>:
  match m:
    case None{}:
      Fail{"unknown_tender"}
    case Some{t}:
      Done{t}

# Exchange._tender
def get_tender(+ex: Ex, tid: J.Json) -> Result<&2, &2, String, Tender>:
  match tid:
    case J.JStr{s}:
      got_tender(find_tender(x_tenders(ex), s))
    case _:
      Fail{"unknown_tender"}

def put_tender.go(ts: List<&2, Tender>, +t: Tender) -> List<&2, Tender>:
  match ts:
    case Nil{}:
      Nil{}
    case Con{+h, r}:
      Con{Bool.pick(Tender, String.eq(t_id(h), t_id(t)), t, h), put_tender.go(r, t)}

def put_tender(+ex: Ex, +t: Tender) -> Ex:
  x_set_tenders(ex, put_tender.go(x_tenders(ex), t))

def tender_ids(ts: List<&2, Tender>) -> List<&2, String>:
  match ts:
    case Nil{}:
      Nil{}
    case Con{t, r}:
      Con{t_id(t), tender_ids(r)}

def tender_int(+t: Tender, +k: String) -> I.Int:
  C.int_of(C.fld(k, t_src(t)))

def mark_tender.found(m: Maybe<&2, Tender>, +ex: Ex, status: String) -> Ex:
  match m:
    case None{}:
      ex
    case Some{t}:
      put_tender(ex, t_set_status(t, status))

def mark_tender.go(m: Maybe<&2, String>, +ex: Ex, +status: String) -> Ex:
  match m:
    case None{}:
      ex
    case Some{+tid}:
      mark_tender.found(find_tender(x_tenders(ex), tid), ex, status)

def company.if(ok: Bool, s: String) -> Result<&2, &2, String, String>:
  match ok:
    case True{}:
      Done{s}
    case False{}:
      Fail{"unknown_company"}

# Exchange._company
def company(+ex: Ex, c: J.Json) -> Result<&2, &2, String, String>:
  match c:
    case J.JStr{+s}:
      company.if(C.str_in(s, x_companies(ex)), s)
    case _:
      Fail{"unknown_company"}

# Ledger
# ======

def moved(r: Result<&2, &2, String, L.Moved>, ex: Ex) -> Result<&2, &2, String, Ex>:
  match r:
    case Fail{e}:
      Fail{e}
    case Done{m}:
      match m:
        case L.Moved{l, t}:
          Done{x_set_ledger(ex, l)}

def transfer(+ex: Ex, +src: String, +dst: String, +amount: I.Int, reason: String, ref: String, tick: I.Int, force: Bool) -> Result<&2, &2, String, Ex>:
  moved(L.transfer(x_ledger(ex), src, dst, amount, reason, ref, tick, force), ex)

def move_if(pos: Bool, +ex: Ex, +src: String, +dst: String, +amount: I.Int, reason: String, ref: String, tick: I.Int) -> Result<&2, &2, String, Ex>:
  match pos:
    case True{}:
      transfer(ex, src, dst, amount, reason, ref, tick, False{})
    case False{}:
      Done{ex}

# Exchange._move: only a positive amount moves
def move(+ex: Ex, +src: String, +dst: String, +amount: I.Int, reason: String, ref: String, tick: I.Int) -> Result<&2, &2, String, Ex>:
  move_if(I.is_pos(amount), ex, src, dst, amount, reason, ref, tick)

def fund_ok(+l: L.Ledger, +acct: String, +amt: I.Int) -> Bool:
  Bool.or(Bool.not(I.is_pos(amt)), L.can_debit(l, acct, amt))

def need_funds.go(same: Bool, +l: L.Ledger, +a1: String, +x1: I.Int, +a2: String, +x2: I.Int) -> Bool:
  match same:
    case True{}:
      fund_ok(l, a1, I.add(x1, x2))
    case False{}:
      Bool.and(fund_ok(l, a1, x1), fund_ok(l, a2, x2))

# Exchange._need_funds([(a1, x1), (a2, x2)]): per account, the summed positive debit
def need_funds(+ex: Ex, +a1: String, +x1: I.Int, +a2: String, +x2: I.Int) -> Result<&2, &2, String, Unit>:
  C.need(need_funds.go(String.eq(a1, a2), x_ledger(ex), a1, x1, a2, x2), "insufficient_funds")

# Obligations
# ===========

def obl_r2(r2: Bool, +o: Obl) -> List<&2, +Sigma<String, _ => J.Json>>:
  match r2:
    case False{}:
      []
    case True{}:
      [("paid_by_settle", C.jint(o_paid_by_settle(o))), ("completed_tick", C.joint(o_completed(o)))]

def obl_json(+o: Obl) -> J.Json:
  J.obj(List.append(&2, +Sigma<String, _ => J.Json>, [
    ("id", J.JStr{o_id(o)}),
    ("contract_id", J.JStr{o_cid(o)}),
    ("kind", J.JStr{o_kind(o)}),
    ("payer", J.JStr{o_payer(o)}),
    ("payee", J.JStr{o_payee(o)}),
    ("amount", C.jint(o_amount(o))),
    ("due", C.jint(o_due(o))),
    ("settle_tick", C.jint(o_settle(o))),
    ("created_tick", C.jint(o_created(o))),
    ("paid", C.jint(o_paid(o))),
    ("paid_by_due", C.jint(o_paid_by_due(o))),
    ("payments", J.JArr{o_payments(o)}),
    ("collected", C.jint(o_collected(o))),
    ("status", J.JStr{o_status(o)}),
    ("settled_tick", C.joint(o_settled(o)))], obl_r2(o_r2(o), o)))

def settle_tick(enf: Bool, +due: I.Int) -> I.Int:
  match enf:
    case True{}:
      due
    case False{}:
      I.add(due, GRACE())

# Exchange._oblige: the new obligation's id beside the exchange
def oblige(+ex: Ex, cid: String, kind: String, payer: String, payee: String, amount: I.Int, +due: I.Int, tick: I.Int) -> +Sigma<Ex, _ => String>:
  +num = x_next_o(ex)
  +id = "O" ++ I.show(num)
  +o = {Obl{id, num, cid, kind, payer, payee, amount, due, settle_tick(enforced(ex), due), tick, I.zero(), I.zero(), [], I.zero(), "open", None{}, x_rules2(ex), I.zero(), None{}} : Obl}
  (x_set_next_o(x_set_obls(ex, List.append(&2, Obl, x_obls(ex), [o])), I.add(num, I.one())), id)

def oblige_ex(+ex: Ex, cid: String, kind: String, payer: String, payee: String, amount: I.Int, +due: I.Int, tick: I.Int) -> Ex:
  fst(Ex, String, oblige(ex, cid, kind, payer, payee, amount, due, tick))

# one obligation per instalment (due, amount)
def oblige_all(ds: List<&2, R.Due>, +ex: Ex, +cid: String, +payer: String, +payee: String, +tick: I.Int) -> Ex:
  match ds:
    case Nil{}:
      ex
    case Con{d, t}:
      match d:
        case R.Due{due, amount}:
          oblige_all(t, oblige_ex(ex, cid, "licence_instalment", payer, payee, amount, due, tick), cid, payer, payee, tick)

def obl_le(+a: Obl, +b: Obl) -> Bool:
  Bool.or(I.is_lt(o_due(a), o_due(b)), Bool.and(I.is_eq(o_due(a), o_due(b)), I.is_le(o_num(a), o_num(b))))

def obl_ins.if(o: Obl, h: Obl, t: List<&2, Obl>, rest: List<&2, Obl>, before: Bool) -> List<&2, Obl>:
  match before:
    case True{}:
      Con{o, Con{h, t}}
    case False{}:
      Con{h, rest}

def obl_ins(+o: Obl, xs: List<&2, Obl>) -> List<&2, Obl>:
  match xs:
    case Nil{}:
      [o]
    case Con{+h, +t}:
      obl_ins.if(o, h, t, obl_ins(o, t), obl_le(o, h))

# sorted by _obl_key = (due, numeric id)
def obl_sort(xs: List<&2, Obl>) -> List<&2, Obl>:
  match xs:
    case Nil{}:
      Nil{}
    case Con{h, t}:
      obl_ins(h, obl_sort(t))

def obls_of.if(hit: Bool, o: Obl, rest: List<&2, Obl>) -> List<&2, Obl>:
  match hit:
    case True{}:
      Con{o, rest}
    case False{}:
      rest

def obls_of(xs: List<&2, Obl>, +cid: String) -> List<&2, Obl>:
  match xs:
    case Nil{}:
      Nil{}
    case Con{+o, t}:
      obls_of.if(String.eq(o_cid(o), cid), o, obls_of(t, cid))

# Exchange._contract_obligations(cid)
def contract_obls(+ex: Ex, +cid: String) -> List<&2, Obl>:
  obl_sort(obls_of(x_obls(ex), cid))

def find_obl.if(o: Obl, rest: Maybe<&2, Obl>, hit: Bool) -> Maybe<&2, Obl>:
  match hit:
    case True{}:
      Some{o}
    case False{}:
      rest

def find_obl(xs: List<&2, Obl>, +id: String) -> Maybe<&2, Obl>:
  match xs:
    case Nil{}:
      None{}
    case Con{+o, t}:
      find_obl.if(o, find_obl(t, id), String.eq(o_id(o), id))

def pick_obl(m: Maybe<&2, Obl>, o: Obl) -> Obl:
  match m:
    case None{}:
      o
    case Some{n}:
      n

def put_obls.go(xs: List<&2, Obl>, +upd: List<&2, Obl>) -> List<&2, Obl>:
  match xs:
    case Nil{}:
      Nil{}
    case Con{+o, t}:
      Con{pick_obl(find_obl(upd, o_id(o)), o), put_obls.go(t, upd)}

# write the obligations in upd back by id
def put_obls(+ex: Ex, +upd: List<&2, Obl>) -> Ex:
  x_set_obls(ex, put_obls.go(x_obls(ex), upd))

def put_obl(+ex: Ex, o: Obl) -> Ex:
  put_obls(ex, [o])

def pay_amount.go(xs: List<&2, J.Json>) -> I.Int:
  match xs:
    case Con{a, Con{b, Nil{}}}:
      C.int_of(b)
    case _:
      I.zero()

def pay_amount(p: J.Json) -> I.Int:
  match p:
    case J.JArr{xs}:
      pay_amount.go(xs)
    case _:
      I.zero()

def sum_payments(ps: List<&2, J.Json>) -> I.Int:
  match ps:
    case Nil{}:
      I.zero()
    case Con{p, t}:
      I.add(pay_amount(p), sum_payments(t))

# Exchange._obligation_result: (collected, outcome)
def obl_result(enf: Bool, +o: Obl) -> +Sigma<I.Int, _ => String>:
  match enf:
    case True{}:
      (I.max(I.zero(), I.sub(o_amount(o), o_paid(o))), "kept")
    case False{}:
      (I.zero(), R.outcome_name(R.outcome_for(o_paid(o), o_amount(o))))

# Init (_op_init, TenderConfig, Ledger)
# =====================================

def cfg_names() -> List<&2, String>:
  ["per_quarter", "quantity_base", "quantity_per_tile", "quantity_jitter_pct", "payout_base_per_unit", "payout_per_unit_per_tile", "penalty_bp", "bid_ticks", "window_ticks", "reach_tiles"]

def cfg_all(kvs: List<&2, +Sigma<String, _ => J.Json>>) -> Bool:
  match kvs:
    case Nil{}:
      True{}
    case Con{(+k, v), t}:
      Bool.and(Bool.and(C.str_in(k, cfg_names()), C.int_ge(v, I.from_bool(String.eq(k, "window_ticks")))), cfg_all(t))

# TenderConfig(**cfg) succeeds: a dict of known fields, each a non-negative int,
# window_ticks >= 1 (missing fields take the defaults, which pass)
def cfg_ok(j: J.Json) -> Bool:
  match j:
    case J.JObj{kvs}:
      cfg_all(kvs)
    case _:
      False{}

def credit_ok(+j: J.Json) -> Bool:
  Bool.or(C.is_null(j), C.int_ge(j, I.zero()))

def credit_of(j: J.Json) -> Maybe<&2, I.Int>:
  match j:
    case J.JInt{t}:
      Some{C.int_of(J.JInt{t})}
    case _:
      None{}

def all_str(xs: List<&2, J.Json>) -> Bool:
  match xs:
    case Nil{}:
      True{}
    case Con{h, t}:
      Bool.and(C.is_str(h), all_str(t))

def strs(xs: List<&2, J.Json>) -> List<&2, String>:
  match xs:
    case Nil{}:
      Nil{}
    case Con{h, t}:
      Con{C.str_of(h), strs(t)}

def items(j: J.Json) -> List<&2, J.Json>:
  match j:
    case J.JArr{xs}:
      xs
    case _:
      Nil{}

def sb_all(kvs: List<&2, +Sigma<String, _ => J.Json>>, +cs: List<&2, String>) -> Bool:
  match kvs:
    case Nil{}:
      True{}
    case Con{(k, v), t}:
      Bool.and(Bool.and(C.str_in(k, cs), C.int_ge(v, I.zero())), sb_all(t, cs))

def sb_ok(j: J.Json, +cs: List<&2, String>) -> Bool:
  match j:
    case J.JObj{kvs}:
      sb_all(kvs, cs)
    case _:
      False{}

def kv_key(kv: +Sigma<String, _ => J.Json>) -> String:
  (k, v) = kv
  k

def kv_ins.if(kv: +Sigma<String, _ => J.Json>, h: +Sigma<String, _ => J.Json>, t: List<&2, +Sigma<String, _ => J.Json>>, rest: List<&2, +Sigma<String, _ => J.Json>>, before: Bool) -> List<&2, +Sigma<String, _ => J.Json>>:
  match before:
    case True{}:
      Con{kv, Con{h, t}}
    case False{}:
      Con{h, rest}

def kv_ins(+kv: +Sigma<String, _ => J.Json>, xs: List<&2, +Sigma<String, _ => J.Json>>) -> List<&2, +Sigma<String, _ => J.Json>>:
  match xs:
    case Nil{}:
      [kv]
    case Con{+h, +t}:
      kv_ins.if(kv, h, t, kv_ins(kv, t), String.is_le(kv_key(kv), kv_key(h)))

def kv_sort(xs: List<&2, +Sigma<String, _ => J.Json>>) -> List<&2, +Sigma<String, _ => J.Json>>:
  match xs:
    case Nil{}:
      Nil{}
    case Con{h, t}:
      kv_ins(h, kv_sort(t))

def kvs_of(j: J.Json) -> List<&2, +Sigma<String, _ => J.Json>>:
  match j:
    case J.JObj{kvs}:
      kvs
    case _:
      Nil{}

def led_moved(r: Result<&2, &2, String, L.Moved>) -> Result<&2, &2, String, L.Ledger>:
  match r:
    case Fail{e}:
      Fail{e}
    case Done{m}:
      match m:
        case L.Moved{l, t}:
          Done{l}

def sb_step.go(nz: Bool, l: L.Ledger, +k: String, +v: I.Int, tick: I.Int) -> Result<&2, &2, String, L.Ledger>:
  match nz:
    case False{}:
      Done{l}
    case True{}:
      led_moved(L.transfer(l, "exchange", k, v, "start_balance", "init", tick, False{}))

def sb_step(acc: Result<&2, &2, String, L.Ledger>, +k: String, +v: I.Int, tick: I.Int) -> Result<&2, &2, String, L.Ledger>:
  match acc:
    case Fail{e}:
      Fail{e}
    case Done{l}:
      sb_step.go(nonzero(v), l, k, v, tick)

# for k in sorted(sb): if sb[k]: ledger.transfer(EXCHANGE, k, sb[k], ...)
def sb_go(kvs: List<&2, +Sigma<String, _ => J.Json>>, acc: Result<&2, &2, String, L.Ledger>, +tick: I.Int) -> Result<&2, &2, String, L.Ledger>:
  match kvs:
    case Nil{}:
      acc
    case Con{(k, v), t}:
      sb_go(t, sb_step(acc, k, C.int_of(v), tick), tick)

def rules_of(+rv: J.Json) -> J.Json:
  Bool.pick(J.Json, C.truthy(rv), rv, J.JInt{"1"})

def horizon_of(j: J.Json) -> Maybe<&2, I.Int>:
  match j:
    case J.JInt{t}:
      Some{C.int_of(J.JInt{t})}
    case _:
      None{}

def op_init(+ex: Ex, +r: J.Json, +tick: I.Int) -> Result<&2, &2, String, Ex>:
  do Result<&2, &2, String, Ex>:
    +arm : J.Json = C.fld("arm", r)
    Unit <- C.need(Bool.or(C.py_eq_str(arm, "enforced"), C.py_eq_str(arm, "cheap_talk")), "bad_arm")
    +seed : J.Json = C.fld("seed", r)
    Unit <- C.need(C.is_int(seed), "bad_record")
    +cs : J.Json = C.fld("companies", r)
    Unit <- C.need(Bool.and(C.is_list(cs), C.truthy(cs)), "bad_company")
    Unit <- C.need(cfg_ok(C.fld("tender_config", r)), "bad_config")
    +cr : J.Json = C.fld("credit_limit", r)
    Unit <- C.need(credit_ok(cr), "bad_amount")
    Unit <- C.need(all_str(items(cs)), "bad_company")
    +led : L.Ledger <- L.new(strs(items(cs)), credit_of(cr))
    +sb : J.Json = C.fld("start_balances", r)
    Unit <- C.need(sb_ok(sb, L.companies(led)), "bad_config")
    +led2 : L.Ledger <- sb_go(kv_sort(kvs_of(sb)), Done{led}, tick)
    +rules : J.Json = rules_of(C.fld("rules", r))
    +hz : J.Json = C.fld("horizon_tick", r)
    Unit <- C.need(Bool.and(Bool.or(C.py_eq_int(rules, I.one()), C.py_eq_int(rules, I.from_u32(2))), Bool.or(C.is_null(hz), C.is_int(hz))), "bad_config")
    +e1 : Ex = x_set_ledger(x_set_seed(x_set_arm(ex, C.str_of(arm)), C.int_of(seed)), led2)
    +e2 : Ex = x_set_companies(e1, L.companies(led2))
    return x_set_horizon(x_set_rules2(e2, C.py_eq_int(rules, I.from_u32(2))), horizon_of(hz))

# Offers and counters
# ===================

def kind_of.if(ok: Bool, s: String) -> Result<&2, &2, String, String>:
  match ok:
    case True{}:
      Done{s}
    case False{}:
      Fail{"unknown_kind"}

def kind_of(j: J.Json) -> Result<&2, &2, String, String>:
  match j:
    case J.JStr{+s}:
      kind_of.if(C.str_in(s, ["supply", "licence", "loan"]), s)
    case J.JArr{xs}:
      Fail{"internal"}
    case J.JObj{kvs}:
      Fail{"internal"}
    case _:
      Fail{"unknown_kind"}

def check_to.go(isnull: Bool, +ex: Ex, to: J.Json, +co: String) -> Result<&2, &2, String, Unit>:
  match isnull:
    case True{}:
      Done{Unit{}}
    case False{}:
      do Result<&2, &2, String, Unit>:
        +t : String <- company(ex, to)
        C.need(Bool.not(String.eq(t, co)), "bad_terms")

# offer: `if to is not None: self._company(to); if to == company: bad_terms`
def check_to(+ex: Ex, +to: J.Json, +co: String) -> Result<&2, &2, String, Unit>:
  check_to.go(C.is_null(to), ex, to, co)

# Exchange._expiry
def expiry(+e: J.Json, +tick: I.Int) -> Result<&2, &2, String, I.Int>:
  do Result<&2, &2, String, I.Int>:
    Unit <- C.need(Bool.and(C.is_int(e), I.is_gt(C.int_of(e), tick)), "bad_terms")
    return C.int_of(e)

# Exchange._check_supply_window
def check_window(+terms: J.Json, +tick: I.Int) -> Result<&2, &2, String, Unit>:
  C.need(I.is_gt(C.terms_int(terms, "deadline"), tick), "deadline_passed")

def op_offer(+ex: Ex, +r: J.Json, +tick: I.Int, +co: String) -> Result<&2, &2, String, Ex>:
  do Result<&2, &2, String, Ex>:
    +kind : String <- kind_of(C.fld("kind", r))
    Unit <- C.need(Bool.not(String.eq(kind, "loan")), "kind_not_enabled")
    +to : J.Json = C.fld("to", r)
    Unit <- check_to(ex, to, co)
    +terms : J.Json <- C.validate_terms(kind, C.fld("terms", r), C.is_null(to))
    +a : J.Json = C.fld(C.party_a(kind), terms)
    +b : J.Json = C.fld(C.party_b(kind), terms)
    Unit <- C.need(Bool.or(C.py_eq_str(a, co), C.py_eq_str(b, co)), "not_party")
    +other : J.Json = Bool.pick(J.Json, C.py_eq_str(a, co), b, a)
    Unit <- C.need(C.same_party(other, to), "bad_terms")
    Unit <- check_window(terms, tick)
    +exp : I.Int <- expiry(C.fld("expires_tick", r), tick)
    +cid : String = "C" ++ I.show(x_next_c(ex))
    Unit <- C.need(C.py_eq_str(C.fld("contract_id", r), cid), "bad_record")
    +c : C.Contract = C.new_contract(cid, kind, terms, co, to_opt(to), tick, exp, co, to_opt(to), None{}, [C.event(tick, "offer", J.JStr{co})])
    return x_set_next_c(add_contract(ex, c), I.add(x_next_c(ex), I.one()))

# Exchange._check_open
def check_open(+c: C.Contract, +tick: I.Int, +co: String) -> Result<&2, &2, String, Unit>:
  do Result<&2, &2, String, Unit>:
    Unit <- C.need(C.is_open(C.c_status(c)), "bad_state")
    Unit <- C.need(I.is_lt(tick, C.c_expires(c)), "offer_expired")
    C.need(C.can_respond(c, co), Bool.pick(String, String.eq(co, C.c_proposer(c)), "not_your_turn", "not_party"))

def fill(+j: J.Json, +co: String) -> J.Json:
  Bool.pick(J.Json, C.is_null(j), J.JStr{co}, j)

# Exchange._filled_terms
def filled_terms(+c: C.Contract, +terms: J.Json, +co: String) -> Result<&2, &2, String, J.Json>:
  do Result<&2, &2, String, J.Json>:
    +new : J.Json <- C.validate_terms(C.c_kind(c), terms, False{})
    +af : String = C.party_a(C.c_kind(c))
    +bf : String = C.party_b(C.c_kind(c))
    +old : J.Json = C.c_terms(c)
    Unit <- C.need(Bool.and(C.same_party(C.fld(af, new), fill(C.fld(af, old), co)), C.same_party(C.fld(bf, new), fill(C.fld(bf, old), co))), "bad_terms")
    return new

def op_counter(+ex: Ex, +r: J.Json, +tick: I.Int, +co: String) -> Result<&2, &2, String, Ex>:
  do Result<&2, &2, String, Ex>:
    +c : C.Contract <- get_contract(ex, C.fld("contract_id", r))
    Unit <- check_open(c, tick, co)
    +terms : J.Json <- filled_terms(c, C.fld("terms", r), co)
    Unit <- check_window(terms, tick)
    +exp : I.Int <- expiry(C.fld("expires_tick", r), tick)
    +c2 : C.Contract <- C.move(c, R.Countered{}, tick, "counter", J.JStr{co})
    +c3 : C.Contract = C.c_set_expires(C.c_set_terms(c2, terms), exp)
    return put_contract(ex, C.c_set_proposer(C.c_set_responder(c3, Some{C.c_proposer(c)}), co))

# Accept, withdraw, expire, activate
# =================================

# the terms with each null party field set to co
def fill_parties(+terms: J.Json, +kind: String, +co: String) -> J.Json:
  +af = C.party_a(kind)
  +bf = C.party_b(kind)
  +t1 = C.jset(terms, af, fill(C.fld(af, terms), co))
  C.jset(t1, bf, fill(C.fld(bf, t1), co))

def history_entry(tick: I.Int, terms: J.Json) -> J.Json:
  J.obj([("from_tick", C.jint(tick)), ("terms", terms)])

def accept_funds(go: Bool, +ex: Ex, +terms: J.Json) -> Result<&2, &2, String, Unit>:
  match go:
    case False{}:
      Done{Unit{}}
    case True{}:
      need_funds(ex, C.str_of(C.fld("buyer", terms)), C.terms_int(terms, "price"), C.str_of(C.fld("seller", terms)), C.terms_int(terms, "bond"))

# a move to active when cond holds (event "activate", company null)
def activate_if(cond: Bool, +c: C.Contract, +tick: I.Int) -> Result<&2, &2, String, C.Contract>:
  match cond:
    case True{}:
      C.move(c, R.Active{}, tick, "activate", J.JNull{})
    case False{}:
      Done{c}

def accept_escrow(enf: Bool, +ex: Ex, +c: C.Contract, +terms: J.Json, +tick: I.Int) -> Result<&2, &2, String, +Sigma<Ex, _ => C.Contract>>:
  match enf:
    case False{}:
      Done{(ex, c)}
    case True{}:
      do Result<&2, &2, String, +Sigma<Ex, _ => C.Contract>>:
        +cid : String = C.c_id(c)
        +buyer : String = C.str_of(C.fld("buyer", terms))
        +seller : String = C.str_of(C.fld("seller", terms))
        +e1 : Ex <- move(ex, buyer, L.escrow_account(cid), C.terms_int(terms, "price"), "escrow", cid, tick)
        +e2 : Ex <- move(e1, seller, L.bond_account(cid), C.terms_int(terms, "bond"), "bond", cid, tick)
        return (e2, C.c_set_esc_bond(C.c_set_esc_price(c, C.terms_int(terms, "price")), C.terms_int(terms, "bond")))

def accept_money(licence: Bool, +ex: Ex, +c: C.Contract, +terms: J.Json, +tick: I.Int) -> Result<&2, &2, String, +Sigma<Ex, _ => C.Contract>>:
  match licence:
    case True{}:
      +sched = R.instalments(C.terms_int(terms, "fee"), C.terms_int(terms, "window_start"), C.terms_int(terms, "deadline"), last_due(ex))
      Done{(oblige_all(sched, ex, C.c_id(c), C.str_of(C.fld("grantee", terms)), C.str_of(C.fld("grantor", terms)), tick), c)}
    case False{}:
      accept_escrow(enforced(ex), ex, c, terms, tick)

def op_accept(+ex: Ex, +r: J.Json, +tick: I.Int, +co: String) -> Result<&2, &2, String, Ex>:
  do Result<&2, &2, String, Ex>:
    +c : C.Contract <- get_contract(ex, C.fld("contract_id", r))
    Unit <- check_open(c, tick, co)
    +terms : J.Json = fill_parties(C.c_terms(c), C.c_kind(c), co)
    Unit <- check_window(terms, tick)
    +licence : Bool = C.is_licence(C.c_kind(c))
    Unit <- accept_funds(Bool.and(enforced(ex), Bool.not(licence)), ex, terms)
    +c2 : C.Contract <- C.move(c, R.Signed{}, tick, "accept", J.JStr{co})
    +c3 : C.Contract = C.c_set_history(C.c_set_responder(C.c_set_signed(C.c_set_terms(c2, terms), Some{tick}), None{}), [history_entry(tick, terms)])
    +p : +Sigma<Ex, _ => C.Contract> <- accept_money(licence, ex, c3, terms, tick)
    +c5 : C.Contract <- activate_if(I.is_ge(tick, C.terms_int(terms, "window_start")), snd(Ex, C.Contract, p), tick)
    return put_contract(fst(Ex, C.Contract, p), c5)

def op_withdraw(+ex: Ex, +r: J.Json, +tick: I.Int, +co: String) -> Result<&2, &2, String, Ex>:
  do Result<&2, &2, String, Ex>:
    +c : C.Contract <- get_contract(ex, C.fld("contract_id", r))
    Unit <- C.need(C.is_open(C.c_status(c)), "bad_state")
    Unit <- C.need(Bool.or(String.eq(co, C.c_proposer(c)), Bool.or(C.ostr_eq(C.c_responder(c), co), String.eq(co, C.c_offerer(c)))), "not_party")
    +c2 : C.Contract <- C.move(c, R.Withdrawn{}, tick, "withdraw", J.JStr{co})
    return put_contract(ex, c2)

def op_expire(+ex: Ex, +r: J.Json, +tick: I.Int) -> Result<&2, &2, String, Ex>:
  do Result<&2, &2, String, Ex>:
    +c : C.Contract <- get_contract(ex, C.fld("contract_id", r))
    Unit <- C.need(Bool.and(C.is_open(C.c_status(c)), I.is_ge(tick, C.c_expires(c))), "bad_state")
    +c2 : C.Contract <- C.move(c, R.Expired{}, tick, "expire", J.JNull{})
    return put_contract(ex, c2)

def is_signed(s: R.Status) -> Bool:
  match s:
    case R.Signed{}:
      True{}
    case _:
      False{}

def op_activate(+ex: Ex, +r: J.Json, +tick: I.Int) -> Result<&2, &2, String, Ex>:
  do Result<&2, &2, String, Ex>:
    +c : C.Contract <- get_contract(ex, C.fld("contract_id", r))
    Unit <- C.need(Bool.and(is_signed(C.c_status(c)), I.is_ge(tick, C.terms_int(C.c_terms(c), "window_start"))), "bad_state")
    +c2 : C.Contract <- C.move(c, R.Active{}, tick, "activate", J.JNull{})
    return put_contract(ex, c2)

# Amend
# =====

def is_propose(m: Maybe<&2, J.Json>, +co: String, +new: J.Json) -> Bool:
  match m:
    case None{}:
      True{}
    case Some{+p}:
      Bool.or(C.py_eq_str(C.fld("by", p), co), Bool.not(String.eq(J.show(C.fld("terms", p)), J.show(new))))

# the licence obligations not yet due are replaced: (updated obligations, fixed)
def replace_obls(xs: List<&2, Obl>, +tick: I.Int, +fixed: I.Int) -> +Sigma<List<&2, Obl>, _ => I.Int>:
  match xs:
    case Nil{}:
      (Nil{}, fixed)
    case Con{+o, t}:
      +isopen = String.eq(o_status(o), "open")
      +later = I.is_gt(o_due(o), tick)
      +repl = Bool.and(isopen, later)
      +add = Bool.pick(I.Int, repl, o_paid(o), Bool.pick(I.Int, String.eq(o_status(o), "replaced"), I.zero(), o_amount(o)))
      +rf = replace_obls(t, tick, I.add(fixed, add))
      (Con{Bool.pick(Obl, repl, o_set_status(o, "replaced"), o), fst(List<&2, Obl>, I.Int, rf)}, snd(List<&2, Obl>, I.Int, rf))

def ints_after.if(keep: Bool, d: I.Int, rest: List<&2, I.Int>) -> List<&2, I.Int>:
  match keep:
    case True{}:
      Con{d, rest}
    case False{}:
      rest

def ints_after(xs: List<&2, I.Int>, +tick: I.Int) -> List<&2, I.Int>:
  match xs:
    case Nil{}:
      Nil{}
    case Con{+d, t}:
      ints_after.if(I.is_gt(d, tick), d, ints_after(t, tick))

def amend_sign(+c: C.Contract, +new: J.Json, +tick: I.Int, +co: String) -> Result<&2, &2, String, C.Contract>:
  +c2 = C.c_set_pending(C.c_set_terms(c, new), None{})
  +c3 = C.c_set_history(c2, List.append(&2, J.Json, C.c_history(c2), [history_entry(tick, new)]))
  +c4 = C.c_set_events(c3, List.append(&2, J.Json, C.c_events(c3), [C.event(tick, "amend_sign", J.JStr{co})]))
  activate_if(Bool.and(is_signed(C.c_status(c4)), I.is_ge(tick, C.terms_int(new, "window_start"))), c4, tick)

def amend_licence(+ex: Ex, +c: C.Contract, +new: J.Json, +tick: I.Int, +co: String) -> Result<&2, &2, String, Ex>:
  do Result<&2, &2, String, Ex>:
    +cid : String = C.c_id(c)
    +rf : +Sigma<List<&2, Obl>, _ => I.Int> = replace_obls(contract_obls(ex, cid), tick, I.zero())
    +e1 : Ex = put_obls(ex, fst(List<&2, Obl>, I.Int, rf))
    +rest : I.Int = I.max(I.zero(), I.sub(C.terms_int(new, "fee"), snd(List<&2, Obl>, I.Int, rf)))
    +dues : List<&2, I.Int> = ints_after(R.due_ticks(C.terms_int(new, "window_start"), C.terms_int(new, "deadline")), tick)
    +sched : List<&2, R.Due> = R.clip_schedule(R.split(rest, dues), last_due(e1), I.max(I.add(tick, I.one()), C.terms_int(new, "window_start")))
    +e2 : Ex = oblige_all(sched, e1, cid, C.str_of(C.fld("grantee", new)), C.str_of(C.fld("grantor", new)), tick)
    +c2 : C.Contract <- amend_sign(c, new, tick, co)
    return put_contract(e2, c2)

def amend_money(enf: Bool, +ex: Ex, +cid: String, +new: J.Json, +dp: I.Int, +db: I.Int, +tick: I.Int) -> Result<&2, &2, String, Ex>:
  match enf:
    case False{}:
      Done{ex}
    case True{}:
      do Result<&2, &2, String, Ex>:
        +buyer : String = C.str_of(C.fld("buyer", new))
        +seller : String = C.str_of(C.fld("seller", new))
        Unit <- need_funds(ex, buyer, I.max(dp, I.zero()), seller, I.max(db, I.zero()))
        +e1 : Ex <- move(ex, buyer, L.escrow_account(cid), dp, "escrow_topup", cid, tick)
        +e2 : Ex <- move(e1, L.escrow_account(cid), buyer, I.neg(dp), "escrow_refund", cid, tick)
        +e3 : Ex <- move(e2, seller, L.bond_account(cid), db, "bond_topup", cid, tick)
        move(e3, L.bond_account(cid), seller, I.neg(db), "bond_refund", cid, tick)

def amend_supply(+ex: Ex, +c: C.Contract, +new: J.Json, +tick: I.Int, +co: String) -> Result<&2, &2, String, Ex>:
  do Result<&2, &2, String, Ex>:
    +dp : I.Int = I.sub(C.terms_int(new, "price"), C.c_esc_price(c))
    +db : I.Int = I.sub(C.terms_int(new, "bond"), C.c_esc_bond(c))
    +enf : Bool = enforced(ex)
    +e1 : Ex <- amend_money(enf, ex, C.c_id(c), new, dp, db, tick)
    +c1 : C.Contract = Bool.pick(C.Contract, enf, C.c_set_esc_bond(C.c_set_esc_price(c, C.terms_int(new, "price")), C.terms_int(new, "bond")), c)
    +c2 : C.Contract <- amend_sign(c1, new, tick, co)
    return put_contract(e1, c2)

def amend_go(propose: Bool, +ex: Ex, +c: C.Contract, +new: J.Json, +tick: I.Int, +co: String) -> Result<&2, &2, String, Ex>:
  match propose:
    case True{}:
      +pend = J.obj([("by", J.JStr{co}), ("tick", C.jint(tick)), ("terms", new)])
      +c2 = C.c_set_pending(c, Some{pend})
      Done{put_contract(ex, C.c_set_events(c2, List.append(&2, J.Json, C.c_events(c2), [C.event(tick, "amend_propose", J.JStr{co})])))}
    case False{}:
      Bool.pick(Result<&2, &2, String, Ex>, C.is_licence(C.c_kind(c)), amend_licence(ex, c, new, tick, co), amend_supply(ex, c, new, tick, co))

def is_none_str(m: Maybe<&2, String>) -> Bool:
  match m:
    case None{}:
      True{}
    case Some{s}:
      False{}

def op_amend(+ex: Ex, +r: J.Json, +tick: I.Int, +co: String) -> Result<&2, &2, String, Ex>:
  do Result<&2, &2, String, Ex>:
    +c : C.Contract <- get_contract(ex, C.fld("contract_id", r))
    Unit <- C.need(C.is_live(C.c_status(c)), "bad_state")
    Unit <- C.need(is_none_str(C.c_tender(c)), "not_amendable")
    +af : String = C.party_a(C.c_kind(c))
    +bf : String = C.party_b(C.c_kind(c))
    +old : J.Json = C.c_terms(c)
    Unit <- C.need(Bool.or(C.py_eq_str(C.fld(af, old), co), C.py_eq_str(C.fld(bf, old), co)), "not_party")
    Unit <- C.need(I.is_lt(tick, C.terms_int(old, "deadline")), "deadline_passed")
    +new : J.Json <- C.validate_terms(C.c_kind(c), C.fld("terms", r), False{})
    Unit <- C.need(Bool.and(C.same_party(C.fld(af, new), C.fld(af, old)), C.same_party(C.fld(bf, new), C.fld(bf, old))), "bad_terms")
    Unit <- check_window(new, tick)
    amend_go(is_propose(C.c_pending(c), co, new), ex, c, new, tick, co)

# Settle
# ======

def jarr_empty(j: J.Json) -> Bool:
  match j:
    case J.JArr{xs}:
      List.is_empty(&2, J.Json, xs)
    case _:
      False{}

def settle_licence(+ex: Ex, +c: C.Contract, +r: J.Json, +tick: I.Int) -> Result<&2, &2, String, Ex>:
  do Result<&2, &2, String, Ex>:
    Unit <- C.need(Bool.and(C.py_eq_int(C.fld("delivered", r), I.zero()), Bool.and(jarr_empty(C.fld("parts", r)), C.py_eq_str(C.fld("outcome", r), "ended"))), "record_mismatch")
    +c2 : C.Contract <- C.move(c, R.Settled{}, tick, "settle", J.JNull{})
    +st : J.Json = J.obj([("outcome", J.JStr{"ended"}), ("delivered", J.JInt{"0"}), ("parts", J.JArr{[]})])
    return put_contract(ex, C.c_set_settlement(C.c_set_outcome(c2, Some{"ended"}), Some{st}))

def opt_oblige(go: Bool, +ex: Ex, cid: String, kind: String, payer: String, payee: String, +amount: I.Int, +tick: I.Int, ids: List<&2, String>) -> +Sigma<Ex, _ => List<&2, String>>:
  match go:
    case False{}:
      (ex, ids)
    case True{}:
      +p = oblige(ex, cid, kind, payer, payee, amount, tick, tick)
      (fst(Ex, String, p), List.append(&2, String, ids, [snd(Ex, String, p)]))

def settle_escrowed(+ex: Ex, +c: C.Contract, +s: S.Settle, +tick: I.Int) -> Result<&2, &2, String, Ex>:
  do Result<&2, &2, String, Ex>:
    +cid : String = C.c_id(c)
    +terms : J.Json = C.c_terms(c)
    +seller : String = C.str_of(C.fld("seller", terms))
    +buyer : String = C.str_of(C.fld("buyer", terms))
    +esc : String = L.escrow_account(cid)
    +bnd : String = L.bond_account(cid)
    +e1 : Ex <- move(ex, esc, seller, S.s_seller_payment(s), "settle_payment", cid, tick)
    +e2 : Ex <- move(e1, esc, buyer, S.s_buyer_refund(s), "settle_refund", cid, tick)
    +e3 : Ex <- move(e2, bnd, buyer, S.s_from_bond(s), "penalty_bond", cid, tick)
    move(e3, bnd, seller, S.s_bond_returned(s), "bond_return", cid, tick)

def settle_penalty(+ex: Ex, +c: C.Contract, +owed: I.Int, +tick: I.Int, ids: List<&2, String>) -> +Sigma<Ex, _ => List<&2, String>>:
  +terms = C.c_terms(c)
  +kind = Bool.pick(String, is_none_str(C.c_tender(c)), "supply_penalty", "tender_penalty")
  opt_oblige(nonzero(owed), ex, C.c_id(c), kind, C.str_of(C.fld("seller", terms)), C.str_of(C.fld("buyer", terms)), owed, tick, ids)

# Exchange._settle_supply: (exchange, ids of the obligations it made)
def settle_money(escrowed: Bool, +ex: Ex, +c: C.Contract, +s: S.Settle, +tick: I.Int) -> Result<&2, &2, String, +Sigma<Ex, _ => List<&2, String>>>:
  match escrowed:
    case True{}:
      do Result<&2, &2, String, +Sigma<Ex, _ => List<&2, String>>>:
        +e1 : Ex <- settle_escrowed(ex, c, s, tick)
        return settle_penalty(e1, c, S.s_from_balance(s), tick, [])
    case False{}:
      +terms = C.c_terms(c)
      +p = opt_oblige(nonzero(S.s_seller_payment(s)), ex, C.c_id(c), "supply_payment", C.str_of(C.fld("buyer", terms)), C.str_of(C.fld("seller", terms)), S.s_seller_payment(s), tick, [])
      Done{settle_penalty(fst(Ex, List<&2, String>, p), c, S.s_penalty(s), tick, snd(Ex, List<&2, String>, p))}

def jstrs(xs: List<&2, String>) -> List<&2, J.Json>:
  match xs:
    case Nil{}:
      Nil{}
    case Con{h, t}:
      Con{J.JStr{h}, jstrs(t)}

def settle_supply(+ex: Ex, +c: C.Contract, +r: J.Json, +tick: I.Int) -> Result<&2, &2, String, Ex>:
  do Result<&2, &2, String, Ex>:
    +s : S.Settle <- S.compute(C.c_terms(c), C.fld("delivered", r))
    Unit <- C.need(C.py_eq_str(C.fld("outcome", r), S.s_outcome(s)), "record_mismatch")
    +enf : Bool = enforced(ex)
    +escrowed : Bool = Bool.or(I.is_pos(C.c_esc_price(c)), Bool.or(I.is_pos(C.c_esc_bond(c)), Bool.and(enf, is_none_str(C.c_tender(c)))))
    +p : +Sigma<Ex, _ => List<&2, String>> <- settle_money(escrowed, ex, c, s, tick)
    +e1 : Ex = fst(Ex, List<&2, String>, p)
    +ids : List<&2, String> = snd(Ex, List<&2, String>, p)
    +c2 : C.Contract <- C.move(c, R.Settled{}, tick, "settle", J.JNull{})
    +st : J.Json = J.obj(List.append(&2, +Sigma<String, _ => J.Json>, S.fields(s), [("contract_id", J.JStr{C.c_id(c)}), ("arm", J.JStr{x_arm(ex)}), ("published", J.JBool{enf}), ("obligations", J.JArr{jstrs(ids)}), ("parts", C.fld("parts", r))]))
    +c3 : C.Contract = C.c_set_esc_bond(C.c_set_esc_price(C.c_set_settlement(C.c_set_outcome(c2, Some{S.s_outcome(s)}), Some{st}), I.zero()), I.zero())
    return mark_tender.go(C.c_tender(c), put_contract(e1, c3), "settled")

def op_settle(+ex: Ex, +r: J.Json, +tick: I.Int) -> Result<&2, &2, String, Ex>:
  do Result<&2, &2, String, Ex>:
    +c : C.Contract <- get_contract(ex, C.fld("contract_id", r))
    Unit <- C.need(C.is_live(C.c_status(c)), "bad_state")
    Unit <- C.need(I.is_ge(tick, C.terms_int(C.c_terms(c), "deadline")), "not_due")
    Bool.pick(Result<&2, &2, String, Ex>, C.is_licence(C.c_kind(c)), settle_licence(ex, c, r, tick), settle_supply(ex, c, r, tick))

# Obligation settle
# =================

def obl_found(m: Maybe<&2, Obl>, +tick: I.Int, end: Bool) -> Result<&2, &2, String, Obl>:
  match m:
    case None{}:
      Fail{"bad_state"}
    case Some{+o}:
      do Result<&2, &2, String, Obl>:
        Unit <- C.need(Bool.and(String.eq(o_status(o), "open"), Bool.or(I.is_ge(tick, o_settle(o)), end)), "bad_state")
        return o

def obl_lookup(+ex: Ex, j: J.Json) -> Maybe<&2, Obl>:
  match j:
    case J.JStr{s}:
      find_obl(x_obls(ex), s)
    case _:
      None{}

def collect_if(go: Bool, +ex: Ex, +o: Obl, +amount: I.Int, +tick: I.Int) -> Result<&2, &2, String, Ex>:
  match go:
    case False{}:
      Done{ex}
    case True{}:
      transfer(ex, o_payer(o), o_payee(o), amount, "collect", o_id(o), tick, True{})

def completed_if(go: Bool, +o: Obl, +tick: I.Int) -> Obl:
  match go:
    case False{}:
      o
    case True{}:
      o_set_completed(o, Some{tick})

def is_none_int(m: Maybe<&2, I.Int>) -> Bool:
  match m:
    case None{}:
      True{}
    case Some{s}:
      False{}

def op_obl_settle(+ex: Ex, +r: J.Json, +tick: I.Int) -> Result<&2, &2, String, Ex>:
  do Result<&2, &2, String, Ex>:
    +o : Obl <- obl_found(obl_lookup(ex, C.fld("obligation_id", r)), tick, at_end(ex, tick))
    +res : +Sigma<I.Int, _ => String> = obl_result(enforced(ex), o)
    +collected : I.Int = fst(I.Int, String, res)
    +outcome : String = snd(I.Int, String, res)
    Unit <- C.need(Bool.and(C.py_eq_int(C.fld("collected", r), collected), Bool.and(C.py_eq_int(C.fld("paid", r), o_paid(o)), C.py_eq_str(C.fld("outcome", r), outcome))), "record_mismatch")
    +e1 : Ex <- collect_if(nonzero(collected), ex, o, collected, tick)
    +o2 : Obl = o_set_settled(o_set_status(o_set_collected(o, collected), outcome), Some{tick})
    return put_obl(e1, completed_if(Bool.and(x_rules2(ex), Bool.and(nonzero(collected), is_none_int(o_completed(o2)))), o2, tick))

# Tenders
# =======

def tender_fields() -> List<&2, String>:
  ["tender_id", "quarter", "industry", "cargo", "distance", "source_industry", "region", "quantity", "max_price", "penalty_per_unit", "open_tick", "bid_close", "window_start", "deadline"]

def tender_int_fields() -> List<&2, String>:
  ["quarter", "industry", "cargo", "distance", "source_industry", "region", "quantity", "max_price", "penalty_per_unit", "open_tick", "bid_close", "window_start", "deadline"]

def keys_known(ks: List<&2, String>, +want: List<&2, String>) -> Bool:
  match ks:
    case Nil{}:
      True{}
    case Con{k, t}:
      Bool.and(C.str_in(k, want), keys_known(t, want))

def all_ints(ks: List<&2, String>, +t: J.Json) -> Bool:
  match ks:
    case Nil{}:
      True{}
    case Con{k, rest}:
      Bool.and(C.is_int(C.fld(k, t)), all_ints(rest, t))

def tender_nums_ok(+t: J.Json, +tick: I.Int) -> Bool:
  +q = C.terms_int(t, "quantity")
  +bc = C.terms_int(t, "bid_close")
  +ws = C.terms_int(t, "window_start")
  +dl = C.terms_int(t, "deadline")
  Bool.and(I.is_ge(q, I.one()), Bool.and(I.is_gt(dl, ws), Bool.and(I.is_ge(bc, tick), I.is_ge(ws, bc))))

# one tender of a tender_open record (every failure is bad_record)
def tender_ok(+t: J.Json, +i: I.Int, +tick: I.Int) -> Bool:
  +fieldsok = Bool.and(C.is_dict(t), Bool.and(C.all_present(tender_fields(), t), keys_known(J.keys(t), tender_fields())))
  +idok = C.py_eq_str(C.fld("tender_id", t), "T" ++ I.show(i))
  Bool.and(fieldsok, Bool.and(idok, Bool.and(all_ints(tender_int_fields(), t), tender_nums_ok(t, tick))))

def tenders_ok(xs: List<&2, J.Json>, +i: I.Int, +tick: I.Int) -> Bool:
  match xs:
    case Nil{}:
      True{}
    case Con{t, rest}:
      Bool.and(tender_ok(t, i, tick), tenders_ok(rest, I.add(i, I.one()), tick))

def new_tenders(xs: List<&2, J.Json>) -> List<&2, Tender>:
  match xs:
    case Nil{}:
      Nil{}
    case Con{+t, rest}:
      Con{Tender{C.str_of(C.fld("tender_id", t)), t, "open", [], None{}, None{}}, new_tenders(rest)}

def len_int(xs: List<&2, J.Json>) -> I.Int:
  match xs:
    case Nil{}:
      I.zero()
    case Con{h, t}:
      I.add(I.one(), len_int(t))

def op_tender_open(+ex: Ex, +r: J.Json, +tick: I.Int) -> Result<&2, &2, String, Ex>:
  do Result<&2, &2, String, Ex>:
    +ts : J.Json = C.fld("tenders", r)
    Unit <- C.need(Bool.and(C.is_int(C.fld("quarter", r)), C.is_list(ts)), "bad_record")
    Unit <- C.need(tenders_ok(items(ts), x_next_t(ex), tick), "bad_record")
    +e1 : Ex = x_set_tenders(ex, List.append(&2, Tender, x_tenders(ex), new_tenders(items(ts))))
    return x_set_next_t(e1, I.add(x_next_t(ex), len_int(items(ts))))

def has_bid(bs: List<&2, Bid>, +co: String) -> Bool:
  match bs:
    case Nil{}:
      False{}
    case Con{b, t}:
      Bool.or(String.eq(b_company(b), co), has_bid(t, co))

def op_tender_bid(+ex: Ex, +r: J.Json, +tick: I.Int, +co: String) -> Result<&2, &2, String, Ex>:
  do Result<&2, &2, String, Ex>:
    +t : Tender <- get_tender(ex, C.fld("tender_id", r))
    Unit <- C.need(Bool.and(String.eq(t_status(t), "open"), I.is_lt(tick, tender_int(t, "bid_close"))), "tender_closed")
    Unit <- C.need(Bool.not(has_bid(t_bids(t), co)), "already_bid")
    +p : J.Json = C.fld("price", r)
    Unit <- C.need(Bool.and(C.is_int(p), Bool.and(I.is_ge(C.int_of(p), I.zero()), I.is_le(C.int_of(p), tender_int(t, "max_price")))), "bad_price")
    +b : Bid = Bid{co, C.int_of(p), C.fld("seq", r), tick}
    return put_tender(ex, t_set_bids(t, List.append(&2, Bid, t_bids(t), [b])))

# (price, tick, company) of a is below b's
def bid_lt(+a: Bid, +b: Bid) -> Bool:
  +pa = b_price(a)
  +pb = b_price(b)
  +ta = b_tick(a)
  +tb = b_tick(b)
  Bool.or(I.is_lt(pa, pb), Bool.and(I.is_eq(pa, pb), Bool.or(I.is_lt(ta, tb), Bool.and(I.is_eq(ta, tb), String.is_lt(b_company(a), b_company(b))))))

def auction.go(bs: List<&2, Bid>, +best: Bid) -> Bid:
  match bs:
    case Nil{}:
      best
    case Con{+b, t}:
      auction.go(t, Bool.pick(Bid, bid_lt(b, best), b, best))

# tenders.auction: min(bids, key=(price, tick, company)), the first of equal keys
def auction(bs: List<&2, Bid>) -> Maybe<&2, Bid>:
  match bs:
    case Nil{}:
      None{}
    case Con{b, t}:
      Some{auction.go(t, b)}

def award_result(open: Bool, bs: List<&2, Bid>) -> Maybe<&2, Bid>:
  match open:
    case True{}:
      auction(bs)
    case False{}:
      None{}

def best_company(m: Maybe<&2, Bid>) -> Maybe<&2, String>:
  match m:
    case None{}:
      None{}
    case Some{b}:
      Some{b_company(b)}

def best_price(m: Maybe<&2, Bid>) -> Maybe<&2, I.Int>:
  match m:
    case None{}:
      None{}
    case Some{b}:
      Some{b_price(b)}

def award_contract(+ex: Ex, +t: Tender, +winner: String, +price: I.Int, +tick: I.Int) -> Result<&2, &2, String, Ex>:
  do Result<&2, &2, String, Ex>:
    +src : J.Json = t_src(t)
    +cid : String = t_id(t)
    +terms : J.Json = J.obj([("seller", J.JStr{winner}), ("buyer", J.JStr{"exchange"}), ("cargo", C.fld("cargo", src)), ("industry", C.fld("industry", src)), ("quantity", C.fld("quantity", src)), ("window_start", C.fld("window_start", src)), ("deadline", C.fld("deadline", src)), ("price", C.jint(price)), ("penalty_per_unit", C.fld("penalty_per_unit", src)), ("bond", J.JInt{"0"})])
    +c : C.Contract = C.new_contract(cid, "supply", terms, "exchange", Some{winner}, tick, tick, "exchange", Some{winner}, Some{cid}, [C.event(tick, "award", J.JNull{})])
    +c2 : C.Contract <- C.move(c, R.Signed{}, tick, "sign", J.JNull{})
    +c3 : C.Contract = C.c_set_history(C.c_set_responder(C.c_set_signed(c2, Some{tick}), None{}), [history_entry(tick, terms)])
    +e1 : Ex <- move(ex, "exchange", L.escrow_account(cid), price, "tender_escrow", cid, tick)
    +c4 : C.Contract <- activate_if(I.is_ge(tick, C.terms_int(terms, "window_start")), C.c_set_esc_price(c3, price), tick)
    +t2 : Tender = t_set_price(t_set_winner(t_set_status(t, "awarded"), Some{winner}), Some{price})
    return put_tender(add_contract(e1, c4), t2)

def award_go(m: Maybe<&2, Bid>, +ex: Ex, +t: Tender, +tick: I.Int) -> Result<&2, &2, String, Ex>:
  match m:
    case None{}:
      Done{put_tender(ex, t_set_status(t, "unawarded"))}
    case Some{+b}:
      award_contract(ex, t, b_company(b), b_price(b), tick)

def op_tender_award(+ex: Ex, +r: J.Json, +tick: I.Int) -> Result<&2, &2, String, Ex>:
  do Result<&2, &2, String, Ex>:
    +t : Tender <- get_tender(ex, C.fld("tender_id", r))
    Unit <- C.need(Bool.and(String.eq(t_status(t), "open"), I.is_ge(tick, tender_int(t, "bid_close"))), "bad_state")
    +best : Maybe<&2, Bid> = award_result(I.is_lt(tick, tender_int(t, "deadline")), t_bids(t))
    Unit <- C.need(Bool.and(C.py_eq_ostr(C.fld("winner", r), best_company(best)), C.py_eq_oint(C.fld("price", r), best_price(best))), "record_mismatch")
    award_go(best, ex, t, tick)

# Messages and payments
# =====================

def op_msg(+ex: Ex, +r: J.Json, +tick: I.Int, +co: String) -> Result<&2, &2, String, Ex>:
  do Result<&2, &2, String, Ex>:
    +to : String <- company(ex, C.fld("to", r))
    Unit <- C.need(Bool.not(String.eq(to, co)), "bad_message")
    +text : J.Json = C.fld("text", r)
    Unit <- C.need(Bool.and(C.is_str(text), Bool.and(Bool.not(String.is_empty(C.str_of(text))), C.len_le(C.str_of(text), U32.to_nat(4000)))), "bad_message")
    +m : J.Json = J.obj([("seq", C.fld("seq", r)), ("tick", C.jint(tick)), ("from", J.JStr{co}), ("to", J.JStr{to}), ("text", text)])
    return x_set_messages(ex, List.append(&2, J.Json, x_messages(ex), [m]))

def mine(xs: List<&2, Obl>, +co: String, +to: J.Json) -> List<&2, Obl>:
  match xs:
    case Nil{}:
      Nil{}
    case Con{+o, t}:
      obls_of.if(Bool.and(String.eq(o_payer(o), co), C.py_eq_str(to, o_payee(o))), o, mine(t, co, to))

def open_r2(+o: Obl) -> Bool:
  Bool.and(Bool.not(String.eq(o_status(o), "replaced")), Bool.and(I.is_lt(o_paid(o), o_amount(o)), I.is_zero(o_collected(o))))

def open_r1(+o: Obl, +tick: I.Int) -> Bool:
  Bool.and(String.eq(o_status(o), "open"), I.is_lt(tick, o_settle(o)))

def open_obls(xs: List<&2, Obl>, +r2: Bool, +tick: I.Int) -> List<&2, Obl>:
  match xs:
    case Nil{}:
      Nil{}
    case Con{+o, t}:
      obls_of.if(Bool.pick(Bool, r2, open_r2(o), open_r1(o, tick)), o, open_obls(t, r2, tick))

def pay_open.go(cid: J.Json, +ex: Ex, +co: String, +to: J.Json, +tick: I.Int) -> Result<&2, &2, String, List<&2, Obl>>:
  match cid:
    case J.JNull{}:
      Done{Nil{}}
    case J.JStr{+s}:
      +xs = open_obls(mine(contract_obls(ex, s), co, to), x_rules2(ex), tick)
      do Result<&2, &2, String, List<&2, Obl>>:
        Unit <- C.need(Bool.not(List.is_empty(&2, Obl, xs)), "bad_tag")
        return xs
    case _:
      Fail{"bad_tag"}

def pay_to.go(toex: Bool, +ex: Ex, to: J.Json, cidnull: Bool) -> Result<&2, &2, String, Unit>:
  match toex:
    case False{}:
      do Result<&2, &2, String, Unit>:
        String <- company(ex, to)
        return Unit{}
    case True{}:
      C.need(Bool.not(cidnull), "unknown_company")

def pay_ledger(toex: Bool, +ex: Ex, +co: String, +to: J.Json, +amount: I.Int, +ref: String, +tick: I.Int) -> Result<&2, &2, String, Ex>:
  match toex:
    case True{}:
      do Result<&2, &2, String, Ex>:
        Unit <- C.need(L.can_debit(x_ledger(ex), co, amount), "insufficient_funds")
        transfer(ex, co, "exchange", amount, "pay", ref, tick, False{})
    case False{}:
      moved(L.pay(x_ledger(ex), co, C.str_of(to), amount, tick, ref), ex)

# the share of `left` an open obligation takes: (updated obligation, what is left)
def alloc.part(pos: Bool, +o: Obl, +part: I.Int, +left: I.Int, +tick: I.Int) -> +Sigma<Obl, _ => I.Int>:
  match pos:
    case False{}:
      (o, left)
    case True{}:
      +paid = I.add(o_paid(o), part)
      +o1 = o_set_paid(o, paid)
      +o2 = Bool.pick(Obl, I.is_le(tick, o_due(o)), o_set_paid_by_due(o1, I.add(o_paid_by_due(o1), part)), o1)
      +o3 = Bool.pick(Obl, Bool.and(o_r2(o), I.is_lt(tick, o_settle(o))), o_set_paid_by_settle(o2, I.add(o_paid_by_settle(o2), part)), o2)
      +o4 = Bool.pick(Obl, Bool.and(o_r2(o), Bool.and(is_none_int(o_completed(o3)), I.is_ge(paid, o_amount(o3)))), o_set_completed(o3, Some{tick}), o3)
      +o5 = o_set_payments(o4, List.append(&2, J.Json, o_payments(o4), [J.JArr{[C.jint(tick), C.jint(part)]}]))
      (o5, I.sub(left, part))

def alloc.one(+o: Obl, +left: I.Int, +tick: I.Int, last: Bool) -> +Sigma<Obl, _ => I.Int>:
  +part = Bool.pick(I.Int, last, left, I.min(left, I.sub(o_amount(o), o_paid(o))))
  alloc.part(I.is_pos(part), o, part, left, tick)

# Exchange._op_pay's allocation loop (FIFO; the last one takes the rest). A
# `break` at left == 0 is the same as going on: every later part is then <= 0.
def alloc(xs: List<&2, Obl>, +left: I.Int, +tick: I.Int) -> List<&2, Obl>:
  match xs:
    case Nil{}:
      Nil{}
    case Con{o, +t}:
      +p = alloc.one(o, left, tick, List.is_empty(&2, Obl, t))
      Con{fst(Obl, I.Int, p), alloc(t, snd(Obl, I.Int, p), tick)}

def op_pay(+ex: Ex, +r: J.Json, +tick: I.Int, +co: String) -> Result<&2, &2, String, Ex>:
  do Result<&2, &2, String, Ex>:
    +memo : J.Json = C.fld("memo", r)
    Unit <- C.need(Bool.and(C.is_str(memo), C.len_le(C.str_of(memo), 200n)), "bad_memo")
    +cid : J.Json = C.fld("contract_id", r)
    +to : J.Json = C.fld("to", r)
    +opn : List<&2, Obl> <- pay_open.go(cid, ex, co, to, tick)
    +toex : Bool = C.py_eq_str(to, "exchange")
    Unit <- pay_to.go(toex, ex, to, C.is_null(cid))
    +amt : J.Json = C.fld("amount", r)
    Unit <- C.need(Bool.and(C.is_int(amt), I.is_pos(C.int_of(amt))), "bad_amount")
    +ref : String = Bool.pick(String, C.truthy(cid), C.str_of(cid), C.str_of(memo))
    +e1 : Ex <- pay_ledger(toex, ex, co, to, C.int_of(amt), ref, tick)
    return put_obls(e1, alloc(opn, C.int_of(amt), tick))

# apply
# =====

def op_fields(code: U32) -> List<&2, String>:
  match code:
    case 0:
      ["arm", "companies", "seed", "credit_limit", "tender_config", "start_balances", "rules", "horizon_tick"]
    case 1:
      ["contract_id", "kind", "terms", "to", "expires_tick"]
    case 2:
      ["contract_id", "terms", "expires_tick"]
    case 3:
      ["contract_id"]
    case 4:
      ["contract_id"]
    case 5:
      ["contract_id", "terms"]
    case 6:
      ["contract_id"]
    case 7:
      ["contract_id"]
    case 8:
      ["contract_id", "delivered", "parts", "outcome"]
    case 9:
      ["quarter", "tenders"]
    case 10:
      ["tender_id", "price"]
    case 11:
      ["tender_id", "winner", "price"]
    case 12:
      ["to", "text"]
    case 13:
      ["to", "amount", "memo", "contract_id"]
    case 14:
      ["obligation_id", "collected", "paid", "outcome"]
    case _:
      []

def op_optional(code: U32) -> List<&2, String>:
  match code:
    case 0:
      ["rules", "horizon_tick"]
    case 13:
      ["contract_id"]
    case _:
      []

def op_system(code: U32) -> Bool:
  match code:
    case 0:
      True{}
    case 6:
      True{}
    case 7:
      True{}
    case 8:
      True{}
    case 9:
      True{}
    case 11:
      True{}
    case 14:
      True{}
    case _:
      False{}

def op_names() -> List<&2, String>:
  ["init", "offer", "counter", "accept", "withdraw", "amend", "expire", "activate", "settle", "tender_open", "tender_bid", "tender_award", "msg", "pay", "obligation_settle"]

def op_code.go(xs: List<&2, String>, +s: String, +i: U32) -> U32:
  match xs:
    case Nil{}:
      99
    case Con{h, t}:
      Bool.pick(U32, String.eq(h, s), i, op_code.go(t, s, (i + 1 : U32)))

def op_code(+s: String) -> U32:
  op_code.go(op_names(), s, 0)

def want_covered(ws: List<&2, String>, +ks: List<&2, String>, +opt: List<&2, String>) -> Bool:
  match ws:
    case Nil{}:
      True{}
    case Con{+w, t}:
      Bool.and(Bool.or(C.str_in(w, ks), C.str_in(w, opt)), want_covered(t, ks, opt))

# set(record) == want, or set(record) | OPTIONAL_FIELDS[op] == want
def fields_ok(+code: U32, +ks: List<&2, String>) -> Bool:
  +want = List.append(&2, String, ["op", "seq", "tick", "company"], op_fields(code))
  Bool.and(keys_known(ks, want), want_covered(want, ks, op_optional(code)))

def op_of.if(+code: U32) -> Result<&2, &2, String, U32>:
  do Result<&2, &2, String, U32>:
    Unit <- C.need(U32.is_lt(code, 15), "bad_record")
    return code

# record.get("op") in RECORD_FIELDS; an unhashable op raises TypeError (internal)
def op_of(m: Maybe<&2, J.Json>) -> Result<&2, &2, String, U32>:
  match m:
    case None{}:
      Fail{"bad_record"}
    case Some{j}:
      match j:
        case J.JStr{+s}:
          op_of.if(op_code(s))
        case J.JArr{xs}:
          Fail{"internal"}
        case J.JObj{kvs}:
          Fail{"internal"}
        case _:
          Fail{"bad_record"}

# company: a system record must say null; any other must name a company
def company_of(sys: Bool, +ex: Ex, c: J.Json) -> Result<&2, &2, String, String>:
  match sys:
    case True{}:
      do Result<&2, &2, String, String>:
        Unit <- C.need(C.is_null(c), "bad_record")
        return ""
    case False{}:
      company(ex, c)

def dispatch(code: U32, +ex: Ex, +r: J.Json, +tick: I.Int, +co: String) -> Result<&2, &2, String, Ex>:
  match code:
    case 0:
      op_init(ex, r, tick)
    case 1:
      op_offer(ex, r, tick, co)
    case 2:
      op_counter(ex, r, tick, co)
    case 3:
      op_accept(ex, r, tick, co)
    case 4:
      op_withdraw(ex, r, tick, co)
    case 5:
      op_amend(ex, r, tick, co)
    case 6:
      op_expire(ex, r, tick)
    case 7:
      op_activate(ex, r, tick)
    case 8:
      op_settle(ex, r, tick)
    case 9:
      op_tender_open(ex, r, tick)
    case 10:
      op_tender_bid(ex, r, tick, co)
    case 11:
      op_tender_award(ex, r, tick)
    case 12:
      op_msg(ex, r, tick, co)
    case 13:
      op_pay(ex, r, tick, co)
    case _:
      op_obl_settle(ex, r, tick)

# Exchange.apply on a dict record whose op is known
def apply.rec(+code: U32, +ex: Ex, +r: J.Json) -> Result<&2, &2, String, Ex>:
  do Result<&2, &2, String, Ex>:
    Unit <- C.need(fields_ok(code, J.keys(r)), "bad_record")
    Unit <- C.need(C.py_eq_int(C.fld("seq", r), x_n(ex)), "bad_record")
    +tj : J.Json = C.fld("tick", r)
    Unit <- C.need(Bool.and(C.is_int(tj), I.is_ge(C.int_of(tj), I.zero())), "bad_record")
    +tick : I.Int = C.int_of(tj)
    Unit <- C.need(I.is_ge(tick, x_tick(ex)), "time_reversed")
    Unit <- C.need(bool_eq(U32.is_eq(code, 0), I.is_zero(x_n(ex))), "bad_record")
    +co : String <- company_of(op_system(code), ex, C.fld("company", r))
    +e1 : Ex <- dispatch(code, ex, r, tick, co)
    return x_set_tick(x_set_n(e1, I.add(x_n(ex), I.one())), tick)

def apply.op(res: Result<&2, &2, String, U32>, +ex: Ex, +r: J.Json) -> Result<&2, &2, String, Ex>:
  match res:
    case Fail{e}:
      Fail{e}
    case Done{code}:
      apply.rec(code, ex, r)

# Exchange.apply(record)
def apply(+ex: Ex, r: J.Json) -> Result<&2, &2, String, Ex>:
  match r:
    case J.JObj{+kvs}:
      apply.op(op_of(J.get("op", J.JObj{kvs})), ex, J.JObj{kvs})
    case _:
      Fail{"bad_record"}

# Invariants
# ==========

def before_colon(s: String) -> String:
  match s:
    case SNil{}:
      ""
    case SCon{Chr{+c}, t}:
      Bool.pick(String, U32.is_eq(c, 58), "", SCon{Chr{c}, before_colon(t)})

def after_colon(s: String) -> String:
  match s:
    case SNil{}:
      ""
    case SCon{Chr{+c}, +t}:
      Bool.pick(String, U32.is_eq(c, 58), t, after_colon(t))

def escrow_ok.go(m: Maybe<&2, C.Contract>, +sub: String, +bal: I.Int) -> Bool:
  match m:
    case None{}:
      False{}
    case Some{+c}:
      Bool.and(C.is_live(C.c_status(c)), I.is_eq(bal, Bool.pick(I.Int, String.eq(sub, "bond"), C.c_esc_bond(c), C.c_esc_price(c))))

def escrow_one(esc: Bool, +k: String, +bal: I.Int, +cs: List<&2, C.Contract>) -> Bool:
  match esc:
    case False{}:
      True{}
    case True{}:
      +rest = String.drop(k, 7n)
      escrow_ok.go(find_contract(cs, before_colon(rest)), after_colon(rest), bal)

# every escrow account holds exactly what its live contract says
def escrows_ok(es: List<&2, L.Entry>, +cs: List<&2, C.Contract>) -> Bool:
  match es:
    case Nil{}:
      True{}
    case Con{e, t}:
      match e:
        case L.Entry{+k, v}:
          Bool.and(escrow_one(L.is_escrow(k), k, v, cs), escrows_ok(t, cs))

def history_ok(hs: List<&2, L.Transfer>, +tids: List<&2, String>) -> Bool:
  match hs:
    case Nil{}:
      True{}
    case Con{h, t}:
      match h:
        case L.Transfer{seq, tick, src, dst, amount, +reason, ref}:
          +allowed = C.str_in(reason, ["pay", "start_balance", "tender_escrow", "settle_payment", "settle_refund"])
          +free = C.str_in(reason, ["pay", "start_balance"])
          Bool.and(Bool.and(allowed, Bool.or(free, C.str_in(ref, tids))), history_ok(t, tids))

def obls_ok(xs: List<&2, Obl>) -> Bool:
  match xs:
    case Nil{}:
      True{}
    case Con{+o, t}:
      Bool.and(Bool.and(I.is_eq(o_paid(o), sum_payments(o_payments(o))), I.is_le(o_paid_by_due(o), o_paid(o))), obls_ok(t))

# Exchange.check_invariants() passes
def check_invariants(+ex: Ex) -> Bool:
  +led = x_ledger(ex)
  +inited = Bool.not(I.is_zero(x_n(ex)))
  +conserved = L.check_conservation(led)
  +esc = escrows_ok(L.balances(led), x_contracts(ex))
  +cheap = Bool.or(enforced(ex), history_ok(L.history(led), tender_ids(x_tenders(ex))))
  Bool.and(inited, Bool.and(conserved, Bool.and(esc, Bool.and(cheap, obls_ok(x_obls(ex))))))

# State
# =====

def contracts_json(cs: List<&2, C.Contract>) -> List<&2, +Sigma<String, _ => J.Json>>:
  match cs:
    case Nil{}:
      Nil{}
    case Con{+c, t}:
      Con{(C.c_id(c), C.to_json(c)), contracts_json(t)}

def bids_json(bs: List<&2, Bid>) -> List<&2, J.Json>:
  match bs:
    case Nil{}:
      Nil{}
    case Con{b, t}:
      Con{b_json(b), bids_json(t)}

def tender_json(+t: Tender) -> J.Json:
  J.obj(List.append(&2, +Sigma<String, _ => J.Json>, kvs_of(t_src(t)), [("status", J.JStr{t_status(t)}), ("bids", J.JArr{bids_json(t_bids(t))}), ("winner", C.jostr(t_winner(t))), ("price", C.joint(t_price(t)))]))

def tenders_json(ts: List<&2, Tender>) -> List<&2, +Sigma<String, _ => J.Json>>:
  match ts:
    case Nil{}:
      Nil{}
    case Con{+t, r}:
      Con{(t_id(t), tender_json(t)), tenders_json(r)}

def obls_json(xs: List<&2, Obl>) -> List<&2, +Sigma<String, _ => J.Json>>:
  match xs:
    case Nil{}:
      Nil{}
    case Con{+o, t}:
      Con{(o_id(o), obl_json(o)), obls_json(t)}

def balances_json(es: List<&2, L.Entry>) -> List<&2, +Sigma<String, _ => J.Json>>:
  match es:
    case Nil{}:
      Nil{}
    case Con{e, t}:
      match e:
        case L.Entry{k, v}:
          Con{(k, C.jint(v)), balances_json(t)}

def history_json(hs: List<&2, L.Transfer>) -> List<&2, J.Json>:
  match hs:
    case Nil{}:
      Nil{}
    case Con{h, t}:
      match h:
        case L.Transfer{seq, tick, src, dst, amount, reason, ref}:
          Con{J.obj([("seq", C.jint(seq)), ("tick", C.jint(tick)), ("src", J.JStr{src}), ("dst", J.JStr{dst}), ("amount", C.jint(amount)), ("reason", J.JStr{reason}), ("ref", J.JStr{ref})]), history_json(t)}

# Exchange.state() (key order is free: the output is printed with sorted keys)
def state_json(+ex: Ex) -> J.Json:
  +led = x_ledger(ex)
  J.obj([
    ("tick", C.jint(x_tick(ex))),
    ("arm", J.JStr{x_arm(ex)}),
    ("seed", C.jint(x_seed(ex))),
    ("companies", J.JArr{jstrs(x_companies(ex))}),
    ("n_records", C.jint(x_n(ex))),
    ("next_contract", C.jint(x_next_c(ex))),
    ("next_tender", C.jint(x_next_t(ex))),
    ("contracts", J.obj(contracts_json(x_contracts(ex)))),
    ("tenders", J.obj(tenders_json(x_tenders(ex)))),
    ("messages", J.JArr{x_messages(ex)}),
    ("ledger", J.obj([("balances", J.obj(balances_json(L.balances(led)))), ("history", J.JArr{history_json(L.history(led))})])),
    ("next_obligation", C.jint(x_next_o(ex))),
    ("obligations", J.obj(obls_json(x_obls(ex))))])

# Replay (railhead.exchange.bend.reference_result)
# ================================================

def err_json(code: String, seq: I.Int) -> J.Json:
  J.obj([("error", J.JStr{code}), ("seq", C.jint(seq))])

def replay.res(r: Result<&2, &2, String, Ex>, i: I.Int) -> Result<&2, &2, J.Json, Ex>:
  match r:
    case Fail{e}:
      Fail{err_json(e, i)}
    case Done{ex}:
      Done{ex}

def replay.step(acc: Result<&2, &2, J.Json, Ex>, r: J.Json, i: I.Int) -> Result<&2, &2, J.Json, Ex>:
  match acc:
    case Fail{o}:
      Fail{o}
    case Done{ex}:
      replay.res(apply(ex, r), i)

def replay.go(rs: List<&2, J.Json>, acc: Result<&2, &2, J.Json, Ex>, +i: I.Int) -> Result<&2, &2, J.Json, Ex>:
  match rs:
    case Nil{}:
      acc
    case Con{r, t}:
      replay.go(t, replay.step(acc, r, i), I.add(i, I.one()))

def replay.fin.if(ok: Bool, +ex: Ex, n: I.Int) -> J.Json:
  match ok:
    case True{}:
      J.obj([("ok", state_json(ex))])
    case False{}:
      err_json("invariant", n)

def replay.fin(acc: Result<&2, &2, J.Json, Ex>, n: I.Int) -> J.Json:
  match acc:
    case Fail{o}:
      o
    case Done{+ex}:
      replay.fin.if(check_invariants(ex), ex, n)

# the reference outcome of a record list
def replay(+rs: List<&2, J.Json>) -> J.Json:
  replay.fin(replay.go(rs, Done{empty()}, I.zero()), len_int(rs))
