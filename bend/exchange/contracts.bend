# contracts.bend: Python value semantics on J.Json, the contract terms
# validator and the Contract record of src/railhead/exchange/contracts.py.
#
# API (import ./contracts.bend as C):
#   Results (one error channel: the Python ExchangeError code, or "internal")
#     C.need(ok, code) -> Result<&2, &2, String, Unit>      Fail{code} unless ok
#   Python values (a JSON value as Python's json.loads would hold it)
#     C.is_int(j)  isinstance(j, int) and not bool (ledger.is_int)
#     C.is_str(j) C.is_null(j) C.is_list(j) C.is_dict(j) -> Bool
#     C.int_of(j) -> I.Int   the value of a JInt (0 otherwise; callers check is_int)
#     C.str_of(j) -> String  the value of a JStr ("" otherwise)
#     C.fld(+k, j) -> J.Json j[k], null when absent (exchange.apply's OPTIONAL_FIELDS filling)
#     C.jint(I.Int) C.jstr(String) C.jbool(Bool) C.jostr(Maybe<String>) C.joint(Maybe<I.Int>)
#     C.truthy(j) -> Bool    Python bool(j)
#     C.py_eq_int(j, n) -> Bool   Python j == n for an int n (True == 1, 1.0 == 1, -0.0 == 0;
#                                 a float equals n only if its double value is exactly n)
#     C.py_eq_str(j, s) C.py_eq_ostr(j, Maybe<String>) C.py_eq_oint(j, Maybe<I.Int>) -> Bool
#     C.same_party(a, b) -> Bool  Python == on two str-or-None values
#     C.len_le(s, Nat) -> Bool    len(s) <= n (code points, as Python str)
#     C.jset(j, k, v) -> J.Json   j[k] = v on an object (replaces in place or appends)
#   Terms (contracts.py SCHEMAS / schema_errors / validate_terms / PARTY_FIELDS)
#     C.party_a(kind) C.party_b(kind) -> String   ("seller", "buyer") | ("grantor", "grantee")
#     C.validate_terms(kind, terms, open_party) -> Result<.., J.Json>
#         kind is "supply" or "licence" (offer checks unknown_kind / kind_not_enabled first);
#         "bad_terms" for every schema or cross-field error (one code: their order inside
#         validate_terms does not matter); answers the normalised terms (bond defaults to 0)
#     C.terms_int(terms, k) -> I.Int              an int field of validated terms
#   Contract (contracts.Contract; status from rules.bend)
#     type Contract, getters C.c_<field>, setters C.c_set_<field> (for the mutable fields)
#     C.new_contract(id, kind, terms, offerer, to, tick, expires, proposer, responder, tender,
#                    events) -> Contract          status offered, the dataclass defaults
#     C.move(c, new, tick, event, company: J.Json) -> Result<.., Contract>   Contract.move
#     C.can_respond(c, company) -> Bool           Contract.can_respond
#     C.is_open(R.Status) C.is_live(R.Status) -> Bool   OPEN_STATUSES / LIVE_STATUSES
#     C.event(tick, name, company) -> J.Json      {"tick", "event", "company"}
#     C.to_json(c) -> J.Json                      Contract.to_dict()
#
# Pure: no IO, no @unsafe; every recursion is structural (lists, strings, Nat fuel).

import Base
import ./int.bend as I
import ./json.bend as J
import ./rules.bend as R

# Results
# =======

def need(ok: Bool, code: String) -> Result<&2, &2, String, Unit>:
  match ok:
    case True{}:
      Done{Unit{}}
    case False{}:
      Fail{code}

# Python values
# =============

def is_int(j: J.Json) -> Bool:
  match j:
    case J.JInt{t}:
      True{}
    case _:
      False{}

def is_str(j: J.Json) -> Bool:
  match j:
    case J.JStr{s}:
      True{}
    case _:
      False{}

def is_null(j: J.Json) -> Bool:
  match j:
    case J.JNull{}:
      True{}
    case _:
      False{}

def is_list(j: J.Json) -> Bool:
  match j:
    case J.JArr{xs}:
      True{}
    case _:
      False{}

def is_dict(j: J.Json) -> Bool:
  match j:
    case J.JObj{kvs}:
      True{}
    case _:
      False{}

def int_of(j: J.Json) -> I.Int:
  match j:
    case J.JInt{t}:
      Maybe.default(&2, I.Int, I.read(t), I.zero())
    case _:
      I.zero()

def str_of(j: J.Json) -> String:
  match j:
    case J.JStr{s}:
      s
    case _:
      ""

def fld.or(m: Maybe<&2, J.Json>) -> J.Json:
  match m:
    case None{}:
      J.JNull{}
    case Some{v}:
      v

def fld(+k: String, j: J.Json) -> J.Json:
  fld.or(J.get(k, j))

def jint(n: I.Int) -> J.Json:
  J.JInt{I.show(n)}

def jstr(s: String) -> J.Json:
  J.JStr{s}

def jbool(b: Bool) -> J.Json:
  J.JBool{b}

def jostr(m: Maybe<&2, String>) -> J.Json:
  match m:
    case None{}:
      J.JNull{}
    case Some{s}:
      J.JStr{s}

def joint(m: Maybe<&2, I.Int>) -> J.Json:
  match m:
    case None{}:
      J.JNull{}
    case Some{n}:
      J.JInt{I.show(n)}

def jset.tail(hit: Bool, k: String, v: J.Json) -> List<&2, +Sigma<String, _ => J.Json>>:
  match hit:
    case True{}:
      Nil{}
    case False{}:
      [(k, v)]

def jset.put(same: Bool, k2: String, v2: J.Json, rest: List<&2, +Sigma<String, _ => J.Json>>, v: J.Json) -> List<&2, +Sigma<String, _ => J.Json>>:
  match same:
    case True{}:
      Con{(k2, v), rest}
    case False{}:
      Con{(k2, v2), rest}

# j[k] = v: replaces the value of key k in place, or appends (k, v)
def jset.go(kvs: List<&2, +Sigma<String, _ => J.Json>>, +k: String, +v: J.Json, hit: Bool) -> List<&2, +Sigma<String, _ => J.Json>>:
  match kvs:
    case Nil{}:
      jset.tail(hit, k, v)
    case Con{(+k2, v2), t}:
      jset.put(String.eq(k2, k), k2, v2, jset.go(t, k, v, Bool.or(hit, String.eq(k2, k))), v)

def jset(j: J.Json, +k: String, +v: J.Json) -> J.Json:
  match j:
    case J.JObj{kvs}:
      J.JObj{jset.go(kvs, k, v, False{})}
    case _:
      j

def jhas(+k: String, j: J.Json) -> Bool:
  Maybe.is_some(&2, J.Json, J.get(k, j))

def len_le(s: String, n: Nat) -> Bool:
  match s n:
    case SNil{} _:
      True{}
    case SCon{h, t} 0n:
      False{}
    case SCon{h, t} 1n+p:
      len_le(t, p)

def bl.go(n: Nat, +a: I.Int, acc: I.Int, z: Bool) -> I.Int:
  match n z:
    case 0n _:
      acc
    case 1n+p True{}:
      acc
    case 1n+p False{}:
      +h = I.floordiv(a, I.from_u32(2))
      bl.go(p, h, I.add(acc, I.one()), I.is_zero(h))

# the bit length of a >= 0 (up to 4000 bits; float values stay below 1100)
def bitlen(+a: I.Int) -> I.Int:
  bl.go(U32.to_nat(4000), a, I.zero(), I.is_zero(a))

def pow.go(n: Nat, +b: I.Int) -> I.Int:
  match n:
    case 0n:
      I.one()
    case 1n+p:
      I.mul(b, pow.go(p, b))

# b ** n for 0 <= n <= 1100 (callers bound n)
def pow(+b: I.Int, n: I.Int) -> I.Int:
  pow.go(I.to_nat(n), b)

def r53.round(+q: I.Int, +r: I.Int, +half: I.Int, +p: I.Int) -> I.Int:
  +odd = Bool.not(I.is_zero(I.mod(q, I.from_u32(2))))
  +up = Bool.or(I.is_gt(r, half), Bool.and(I.is_eq(r, half), odd))
  I.mul(I.add(q, I.from_bool(up)), p)

def r53.go(+a: I.Int, +s: I.Int, big: Bool) -> I.Int:
  match big:
    case False{}:
      a
    case True{}:
      +p = pow(I.from_u32(2), s)
      +q = I.floordiv(a, p)
      r53.round(q, I.sub(a, I.mul(q, p)), I.floordiv(p, I.from_u32(2)), p)

# a >= 0 rounded to the nearest double (53 significant bits, ties to even)
def r53(+a: I.Int) -> I.Int:
  +s = I.sub(bitlen(a), I.from_u32(53))
  r53.go(a, s, I.is_pos(s))

# Floats (repr text) as exact values
# ----------------------------------
# A float token is Python repr text: -?digits(.digits)?(e[+-]digits)?, or NaN /
# Infinity / -Infinity. float_int answers its exact value when that is an
# integer (a repr with a non-zero fractional part is never an integral double),
# and r53 rounds an integer to the nearest double (53 significant bits, ties to
# even), so `float == int` is r53(value) == n, as Python compares them exactly.

type FP is Data:
  FP{neg: Bool, mant: I.Int, frac: I.Int, exp: I.Int, eneg: Bool, ph: U32, ok: Bool}

def fp.minus.go(e: Bool, neg: Bool, m: I.Int, f: I.Int, x: I.Int, en: Bool, ph: U32, ok: Bool) -> FP:
  match e:
    case True{}:
      FP{neg, m, f, x, True{}, ph, ok}
    case False{}:
      FP{True{}, m, f, x, en, ph, ok}

def fp.minus(st: FP) -> FP:
  match st:
    case FP{neg, m, f, x, en, +ph, ok}:
      fp.minus.go(U32.is_eq(ph, 2), neg, m, f, x, en, ph, ok)

def fp.phase(st: FP, p: U32) -> FP:
  match st:
    case FP{neg, m, f, x, en, ph, ok}:
      FP{neg, m, f, x, en, p, ok}

def fp.bad(st: FP) -> FP:
  match st:
    case FP{neg, m, f, x, en, ph, ok}:
      FP{neg, m, f, x, en, ph, False{}}

def fp.ten(+a: I.Int, d: I.Int) -> I.Int:
  I.add(I.mul(a, I.from_u32(10)), d)

def fp.dig.go(+ph: U32, +d: I.Int, neg: Bool, m: I.Int, f: I.Int, x: I.Int, en: Bool, ok: Bool) -> FP:
  match ph:
    case 0:
      FP{neg, fp.ten(m, d), f, x, en, 0, ok}
    case 1:
      FP{neg, fp.ten(m, d), I.add(f, I.one()), x, en, 1, ok}
    case _:
      FP{neg, m, f, fp.ten(x, d), en, ph, ok}

def fp.dig(isd: Bool, st: FP, c: U32) -> FP:
  match isd:
    case False{}:
      fp.bad(st)
    case True{}:
      match st:
        case FP{neg, m, f, x, en, ph, ok}:
          fp.dig.go(ph, I.from_u32((c - 48 : U32)), neg, m, f, x, en, ok)

def fp.ch(+c: U32, st: FP) -> FP:
  match c:
    case 45:
      fp.minus(st)
    case 43:
      st
    case 46:
      fp.phase(st, 1)
    case 101:
      fp.phase(st, 2)
    case 69:
      fp.phase(st, 2)
    case _:
      fp.dig((c >= 48 : U32) && (c <= 57 : U32), st, c)

def fp.go(s: String, st: FP) -> FP:
  match s:
    case SNil{}:
      st
    case SCon{Chr{c}, t}:
      fp.go(t, fp.ch(c, st))

def sign(neg: Bool, a: I.Int) -> I.Int:
  match neg:
    case True{}:
      I.neg(a)
    case False{}:
      a

def fp.frac(neg: Bool, +m: I.Int, +p: I.Int, exact: Bool) -> Maybe<&2, I.Int>:
  match exact:
    case True{}:
      Some{sign(neg, I.floordiv(m, p))}
    case False{}:
      None{}

def fp.val.go(neg: Bool, +m: I.Int, +e: I.Int, pos: Bool) -> Maybe<&2, I.Int>:
  match pos:
    case True{}:
      Some{sign(neg, I.mul(m, pow(I.from_u32(10), e)))}
    case False{}:
      +p = pow(I.from_u32(10), I.neg(e))
      fp.frac(neg, m, p, I.is_zero(I.mod(m, p)))

def fp.val(neg: Bool, +m: I.Int, +e: I.Int, sane: Bool) -> Maybe<&2, I.Int>:
  match sane:
    case True{}:
      fp.val.go(neg, m, e, I.is_ge(e, I.zero()))
    case False{}:
      None{}

def fp.fin(st: FP) -> Maybe<&2, I.Int>:
  match st:
    case FP{neg, +m, f, x, en, ph, ok}:
      +e = I.sub(sign(en, x), f)
      +lim = I.from_u32(1000)
      fp.val(neg, m, e, Bool.and(ok, Bool.and(I.is_le(e, lim), I.is_ge(e, I.neg(lim)))))

# the exact value of float text t when it is a finite integer, else None
def float_int(t: String) -> Maybe<&2, I.Int>:
  fp.fin(fp.go(t, FP{False{}, I.zero(), I.zero(), I.zero(), False{}, 0, True{}}))

def float_eq_int.go(m: Maybe<&2, I.Int>, +n: I.Int) -> Bool:
  match m:
    case None{}:
      False{}
    case Some{+v}:
      I.is_eq(sign(I.is_neg(v), r53(I.abs(v))), n)

# Python float(t) == n
def float_eq_int(t: String, +n: I.Int) -> Bool:
  float_eq_int.go(float_int(t), n)

def read_eq(m: Maybe<&2, I.Int>, +n: I.Int) -> Bool:
  match m:
    case None{}:
      False{}
    case Some{v}:
      I.is_eq(v, n)

# Python j == n, n an int
def py_eq_int(j: J.Json, +n: I.Int) -> Bool:
  match j:
    case J.JInt{t}:
      read_eq(I.read(t), n)
    case J.JBool{b}:
      I.is_eq(I.from_bool(b), n)
    case J.JFloat{t}:
      float_eq_int(t, n)
    case _:
      False{}

# Python j == s, s a str
def py_eq_str(j: J.Json, s: String) -> Bool:
  match j:
    case J.JStr{t}:
      String.eq(t, s)
    case _:
      False{}

# Python j == m, m a str or None
def py_eq_ostr(j: J.Json, m: Maybe<&2, String>) -> Bool:
  match m:
    case None{}:
      is_null(j)
    case Some{s}:
      py_eq_str(j, s)

# Python j == m, m an int or None
def py_eq_oint(j: J.Json, m: Maybe<&2, I.Int>) -> Bool:
  match m:
    case None{}:
      is_null(j)
    case Some{n}:
      py_eq_int(j, n)

# Python a == b for two values that are each a str or None
def same_party(a: J.Json, b: J.Json) -> Bool:
  match a:
    case J.JNull{}:
      is_null(b)
    case J.JStr{s}:
      py_eq_str(b, s)
    case _:
      False{}

def float_truthy(m: Maybe<&2, I.Int>) -> Bool:
  match m:
    case None{}:
      True{}
    case Some{v}:
      Bool.not(I.is_zero(v))

def kvs_empty(kvs: List<&2, +Sigma<String, _ => J.Json>>) -> Bool:
  match kvs:
    case Nil{}:
      True{}
    case Con{h, t}:
      False{}

# Python bool(j)
def truthy(j: J.Json) -> Bool:
  match j:
    case J.JNull{}:
      False{}
    case J.JBool{b}:
      b
    case J.JInt{t}:
      Bool.not(String.eq(t, "0"))
    case J.JFloat{t}:
      float_truthy(float_int(t))
    case J.JStr{s}:
      Bool.not(String.is_empty(s))
    case J.JArr{xs}:
      Bool.not(List.is_empty(&2, J.Json, xs))
    case J.JObj{kvs}:
      Bool.not(kvs_empty(kvs))

def str_in(+s: String, xs: List<&2, String>) -> Bool:
  match xs:
    case Nil{}:
      False{}
    case Con{h, t}:
      Bool.or(String.eq(h, s), str_in(s, t))

# Terms
# =====

def is_supply(+kind: String) -> Bool:
  String.eq(kind, "supply")

def is_licence(+kind: String) -> Bool:
  String.eq(kind, "licence")

def party_a(kind: String) -> String:
  Bool.pick(String, is_supply(kind), "seller", "grantor")

def party_b(kind: String) -> String:
  Bool.pick(String, is_supply(kind), "buyer", "grantee")

def int_ge(j: J.Json, +lo: I.Int) -> Bool:
  match j:
    case J.JInt{t}:
      Bool.and(True{}, I.is_ge(Maybe.default(&2, I.Int, I.read(t), I.zero()), lo))
    case _:
      False{}

def party_ok(j: J.Json) -> Bool:
  match j:
    case J.JNull{}:
      True{}
    case J.JStr{s}:
      Bool.not(String.is_empty(s))
    case _:
      False{}

def all_int0(xs: List<&2, J.Json>) -> Bool:
  match xs:
    case Nil{}:
      True{}
    case Con{h, t}:
      Bool.and(int_ge(h, I.zero()), all_int0(t))

def rect_ok(j: J.Json) -> Bool:
  match j:
    case J.JArr{+xs}:
      Bool.and(Nat.is_eq(List.length(&2, J.Json, xs), 4n), all_int0(xs))
    case _:
      False{}

def supply_props() -> List<&2, String>:
  ["seller", "buyer", "cargo", "industry", "quantity", "window_start", "deadline", "price", "penalty_per_unit", "bond"]

def supply_required() -> List<&2, String>:
  ["seller", "buyer", "cargo", "industry", "quantity", "window_start", "deadline", "price", "penalty_per_unit"]

def licence_props() -> List<&2, String>:
  ["grantor", "grantee", "rect", "window_start", "deadline", "fee"]

def props(kind: String) -> List<&2, String>:
  Bool.pick(List<&2, String>, is_supply(kind), supply_props(), licence_props())

def required(kind: String) -> List<&2, String>:
  Bool.pick(List<&2, String>, is_supply(kind), supply_required(), licence_props())

def prop_ok.num.go(rect: Bool, +k: String, v: J.Json) -> Bool:
  match rect:
    case True{}:
      rect_ok(v)
    case False{}:
      int_ge(v, I.from_bool(String.eq(k, "quantity")))

def prop_ok.num(+k: String, v: J.Json) -> Bool:
  prop_ok.num.go(String.eq(k, "rect"), k, v)

def prop_ok.party(p: Bool, +k: String, v: J.Json) -> Bool:
  match p:
    case True{}:
      party_ok(v)
    case False{}:
      prop_ok.num(k, v)

# one property of a supply/licence terms dict against its schema
def prop_ok(+k: String, v: J.Json) -> Bool:
  prop_ok.party(str_in(k, ["seller", "buyer", "grantor", "grantee"]), k, v)

def all_present(ks: List<&2, String>, +terms: J.Json) -> Bool:
  match ks:
    case Nil{}:
      True{}
    case Con{k, t}:
      Bool.and(jhas(k, terms), all_present(t, terms))

def all_known(kvs: List<&2, +Sigma<String, _ => J.Json>>, +ps: List<&2, String>) -> Bool:
  match kvs:
    case Nil{}:
      True{}
    case Con{(+k, v), t}:
      Bool.and(Bool.and(str_in(k, ps), prop_ok(k, v)), all_known(t, ps))

# schema_errors(terms, SCHEMAS[kind]) == []
def schema_ok(+kind: String, terms: J.Json) -> Bool:
  match terms:
    case J.JObj{+kvs}:
      Bool.and(all_present(required(kind), J.JObj{kvs}), all_known(kvs, props(kind)))
    case _:
      False{}

def null_count(a: J.Json, b: J.Json) -> U32:
  (Bool.to_u32(is_null(a)) + Bool.to_u32(is_null(b)) : U32)

def terms_int(+terms: J.Json, +k: String) -> I.Int:
  int_of(fld(k, terms))

def setdefault_bond.go(has: Bool, t: J.Json) -> J.Json:
  match has:
    case True{}:
      t
    case False{}:
      jset(t, "bond", J.JInt{"0"})

def setdefault_bond(supply: Bool, +t: J.Json) -> J.Json:
  match supply:
    case False{}:
      t
    case True{}:
      setdefault_bond.go(jhas("bond", t), t)

def rect_order_ok.go(xs: List<&2, J.Json>) -> Bool:
  match xs:
    case Con{x0, Con{y0, Con{x1, Con{y1, Nil{}}}}}:
      Bool.and(I.is_ge(int_of(x1), int_of(x0)), I.is_ge(int_of(y1), int_of(y0)))
    case _:
      False{}

def rect_order_ok(rect: J.Json) -> Bool:
  match rect:
    case J.JArr{xs}:
      rect_order_ok.go(xs)
    case _:
      False{}

def licence_rect_ok(licence: Bool, +t: J.Json) -> Bool:
  match licence:
    case True{}:
      rect_order_ok(fld("rect", t))
    case False{}:
      True{}

# contracts.validate_terms(kind, terms, open_party=open) for kind in ENABLED_KINDS
def validate_terms(+kind: String, +terms: J.Json, open: Bool) -> Result<&2, &2, String, J.Json>:
  do Result<&2, &2, String, J.Json>:
    Unit <- need(schema_ok(kind, terms), "bad_terms")
    +a : J.Json = fld(party_a(kind), terms)
    +b : J.Json = fld(party_b(kind), terms)
    +nulls : U32 = null_count(a, b)
    Unit <- need(Bool.or(U32.is_eq(nulls, 0), Bool.and(open, U32.is_eq(nulls, 1))), "bad_terms")
    Unit <- need(Bool.not(Bool.and(Bool.not(is_null(a)), same_party(a, b))), "bad_terms")
    +t : J.Json = setdefault_bond(is_supply(kind), terms)
    Unit <- need(I.is_gt(terms_int(t, "deadline"), terms_int(t, "window_start")), "bad_terms")
    Unit <- need(licence_rect_ok(is_licence(kind), t), "bad_terms")
    return t

# Contract
# ========

# The dataclass fields of contracts.Contract, in to_dict() order (pending is the
# {by, tick, terms} dict; history and events are the JSON lists Python keeps).
type Contract is Data:
  Contract{id: String, kind: String, terms: J.Json, offerer: String, to: Maybe<&2, String>, created: I.Int, expires: I.Int, proposer: String, responder: Maybe<&2, String>, status: R.Status, tender: Maybe<&2, String>, signed: Maybe<&2, I.Int>, history: List<&2, J.Json>, pending: Maybe<&2, J.Json>, esc_price: I.Int, esc_bond: I.Int, outcome: Maybe<&2, String>, settlement: Maybe<&2, J.Json>, events: List<&2, J.Json>}

def c_id(x: Contract) -> String:
  match x:
    case Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, signed, history, pending, esc_price, esc_bond, outcome, settlement, events}:
      id

def c_kind(x: Contract) -> String:
  match x:
    case Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, signed, history, pending, esc_price, esc_bond, outcome, settlement, events}:
      kind

def c_terms(x: Contract) -> J.Json:
  match x:
    case Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, signed, history, pending, esc_price, esc_bond, outcome, settlement, events}:
      terms

def c_offerer(x: Contract) -> String:
  match x:
    case Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, signed, history, pending, esc_price, esc_bond, outcome, settlement, events}:
      offerer

def c_to(x: Contract) -> Maybe<&2, String>:
  match x:
    case Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, signed, history, pending, esc_price, esc_bond, outcome, settlement, events}:
      to

def c_created(x: Contract) -> I.Int:
  match x:
    case Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, signed, history, pending, esc_price, esc_bond, outcome, settlement, events}:
      created

def c_expires(x: Contract) -> I.Int:
  match x:
    case Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, signed, history, pending, esc_price, esc_bond, outcome, settlement, events}:
      expires

def c_proposer(x: Contract) -> String:
  match x:
    case Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, signed, history, pending, esc_price, esc_bond, outcome, settlement, events}:
      proposer

def c_responder(x: Contract) -> Maybe<&2, String>:
  match x:
    case Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, signed, history, pending, esc_price, esc_bond, outcome, settlement, events}:
      responder

def c_status(x: Contract) -> R.Status:
  match x:
    case Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, signed, history, pending, esc_price, esc_bond, outcome, settlement, events}:
      status

def c_tender(x: Contract) -> Maybe<&2, String>:
  match x:
    case Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, signed, history, pending, esc_price, esc_bond, outcome, settlement, events}:
      tender

def c_signed(x: Contract) -> Maybe<&2, I.Int>:
  match x:
    case Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, signed, history, pending, esc_price, esc_bond, outcome, settlement, events}:
      signed

def c_history(x: Contract) -> List<&2, J.Json>:
  match x:
    case Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, signed, history, pending, esc_price, esc_bond, outcome, settlement, events}:
      history

def c_pending(x: Contract) -> Maybe<&2, J.Json>:
  match x:
    case Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, signed, history, pending, esc_price, esc_bond, outcome, settlement, events}:
      pending

def c_esc_price(x: Contract) -> I.Int:
  match x:
    case Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, signed, history, pending, esc_price, esc_bond, outcome, settlement, events}:
      esc_price

def c_esc_bond(x: Contract) -> I.Int:
  match x:
    case Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, signed, history, pending, esc_price, esc_bond, outcome, settlement, events}:
      esc_bond

def c_outcome(x: Contract) -> Maybe<&2, String>:
  match x:
    case Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, signed, history, pending, esc_price, esc_bond, outcome, settlement, events}:
      outcome

def c_settlement(x: Contract) -> Maybe<&2, J.Json>:
  match x:
    case Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, signed, history, pending, esc_price, esc_bond, outcome, settlement, events}:
      settlement

def c_events(x: Contract) -> List<&2, J.Json>:
  match x:
    case Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, signed, history, pending, esc_price, esc_bond, outcome, settlement, events}:
      events

def c_set_terms(x: Contract, v: J.Json) -> Contract:
  match x:
    case Contract{id, kind, _, offerer, to, created, expires, proposer, responder, status, tender, signed, history, pending, esc_price, esc_bond, outcome, settlement, events}:
      Contract{id, kind, v, offerer, to, created, expires, proposer, responder, status, tender, signed, history, pending, esc_price, esc_bond, outcome, settlement, events}

def c_set_expires(x: Contract, v: I.Int) -> Contract:
  match x:
    case Contract{id, kind, terms, offerer, to, created, _, proposer, responder, status, tender, signed, history, pending, esc_price, esc_bond, outcome, settlement, events}:
      Contract{id, kind, terms, offerer, to, created, v, proposer, responder, status, tender, signed, history, pending, esc_price, esc_bond, outcome, settlement, events}

def c_set_proposer(x: Contract, v: String) -> Contract:
  match x:
    case Contract{id, kind, terms, offerer, to, created, expires, _, responder, status, tender, signed, history, pending, esc_price, esc_bond, outcome, settlement, events}:
      Contract{id, kind, terms, offerer, to, created, expires, v, responder, status, tender, signed, history, pending, esc_price, esc_bond, outcome, settlement, events}

def c_set_responder(x: Contract, v: Maybe<&2, String>) -> Contract:
  match x:
    case Contract{id, kind, terms, offerer, to, created, expires, proposer, _, status, tender, signed, history, pending, esc_price, esc_bond, outcome, settlement, events}:
      Contract{id, kind, terms, offerer, to, created, expires, proposer, v, status, tender, signed, history, pending, esc_price, esc_bond, outcome, settlement, events}

def c_set_status(x: Contract, v: R.Status) -> Contract:
  match x:
    case Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, _, tender, signed, history, pending, esc_price, esc_bond, outcome, settlement, events}:
      Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, v, tender, signed, history, pending, esc_price, esc_bond, outcome, settlement, events}

def c_set_signed(x: Contract, v: Maybe<&2, I.Int>) -> Contract:
  match x:
    case Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, _, history, pending, esc_price, esc_bond, outcome, settlement, events}:
      Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, v, history, pending, esc_price, esc_bond, outcome, settlement, events}

def c_set_history(x: Contract, v: List<&2, J.Json>) -> Contract:
  match x:
    case Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, signed, _, pending, esc_price, esc_bond, outcome, settlement, events}:
      Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, signed, v, pending, esc_price, esc_bond, outcome, settlement, events}

def c_set_pending(x: Contract, v: Maybe<&2, J.Json>) -> Contract:
  match x:
    case Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, signed, history, _, esc_price, esc_bond, outcome, settlement, events}:
      Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, signed, history, v, esc_price, esc_bond, outcome, settlement, events}

def c_set_esc_price(x: Contract, v: I.Int) -> Contract:
  match x:
    case Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, signed, history, pending, _, esc_bond, outcome, settlement, events}:
      Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, signed, history, pending, v, esc_bond, outcome, settlement, events}

def c_set_esc_bond(x: Contract, v: I.Int) -> Contract:
  match x:
    case Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, signed, history, pending, esc_price, _, outcome, settlement, events}:
      Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, signed, history, pending, esc_price, v, outcome, settlement, events}

def c_set_outcome(x: Contract, v: Maybe<&2, String>) -> Contract:
  match x:
    case Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, signed, history, pending, esc_price, esc_bond, _, settlement, events}:
      Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, signed, history, pending, esc_price, esc_bond, v, settlement, events}

def c_set_settlement(x: Contract, v: Maybe<&2, J.Json>) -> Contract:
  match x:
    case Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, signed, history, pending, esc_price, esc_bond, outcome, _, events}:
      Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, signed, history, pending, esc_price, esc_bond, outcome, v, events}

def c_set_events(x: Contract, v: List<&2, J.Json>) -> Contract:
  match x:
    case Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, signed, history, pending, esc_price, esc_bond, outcome, settlement, _}:
      Contract{id, kind, terms, offerer, to, created, expires, proposer, responder, status, tender, signed, history, pending, esc_price, esc_bond, outcome, settlement, v}

def is_open(s: R.Status) -> Bool:
  match s:
    case R.Offered{}:
      True{}
    case R.Countered{}:
      True{}
    case _:
      False{}

def is_live(s: R.Status) -> Bool:
  match s:
    case R.Signed{}:
      True{}
    case R.Active{}:
      True{}
    case _:
      False{}

def event(tick: I.Int, name: String, company: J.Json) -> J.Json:
  J.obj([("tick", jint(tick)), ("event", J.JStr{name}), ("company", company)])

def new_contract(id: String, kind: String, terms: J.Json, offerer: String, to: Maybe<&2, String>, +tick: I.Int, expires: I.Int, proposer: String, responder: Maybe<&2, String>, tender: Maybe<&2, String>, events: List<&2, J.Json>) -> Contract:
  Contract{id, kind, terms, offerer, to, tick, expires, proposer, responder, R.Offered{}, tender, None{}, [], None{}, I.zero(), I.zero(), None{}, None{}, events}

def move.fin(r: Result<&2, &2, String, R.Status>, +c: Contract, ev: J.Json) -> Result<&2, &2, String, Contract>:
  match r:
    case Fail{e}:
      Fail{e}
    case Done{s}:
      Done{c_set_events(c_set_status(c, s), List.append(&2, J.Json, c_events(c), [ev]))}

# Contract.move: new must be in TRANSITIONS[status] ("bad_transition"), then the event is logged
def move(+c: Contract, +new: R.Status, tick: I.Int, name: String, company: J.Json) -> Result<&2, &2, String, Contract>:
  move.fin(R.move(c_status(c), new), c, event(tick, name, company))

def ostr_eq(m: Maybe<&2, String>, +s: String) -> Bool:
  match m:
    case None{}:
      False{}
    case Some{x}:
      String.eq(x, s)

def can_respond.go(mine: Bool, r: Maybe<&2, String>, +company: String) -> Bool:
  match mine:
    case True{}:
      False{}
    case False{}:
      match r:
        case None{}:
          True{}
        case Some{x}:
          String.eq(x, company)

# Contract.can_respond
def can_respond(+c: Contract, +company: String) -> Bool:
  can_respond.go(String.eq(company, c_proposer(c)), c_responder(c), company)

# Contract.to_dict()
def to_json(+c: Contract) -> J.Json:
  J.obj([
    ("id", J.JStr{c_id(c)}),
    ("kind", J.JStr{c_kind(c)}),
    ("terms", c_terms(c)),
    ("offerer", J.JStr{c_offerer(c)}),
    ("to", jostr(c_to(c))),
    ("created_tick", jint(c_created(c))),
    ("expires_tick", jint(c_expires(c))),
    ("proposer", J.JStr{c_proposer(c)}),
    ("responder", jostr(c_responder(c))),
    ("status", J.JStr{R.status_name(c_status(c))}),
    ("tender_id", jostr(c_tender(c))),
    ("signed_tick", joint(c_signed(c))),
    ("terms_history", J.JArr{c_history(c)}),
    ("pending_amendment", Maybe.default(&2, J.Json, c_pending(c), J.JNull{})),
    ("escrow_price", jint(c_esc_price(c))),
    ("escrow_bond", jint(c_esc_bond(c))),
    ("outcome", jostr(c_outcome(c))),
    ("settlement", Maybe.default(&2, J.Json, c_settlement(c), J.JNull{})),
    ("events", J.JArr{c_events(c)})])
