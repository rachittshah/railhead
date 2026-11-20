# LAWS.bend: the laws of the Waybill exchange core (Bend port). Each law
# is a claim about the real runtime modules; PROOF.bend proves them, and
# `bend PROOF.bend --verdict` rechecks the proofs with the Lean-verified
# kernel. A parameter is + when a proof spends it twice.
#
# Integers are int.bend's Int; "a equals b" is I.is_eq(a, b), the same
# value test the runtime uses (Python ==). is_eq_sound/is_eq_complete pin
# it to the value of an Int (V.Same), so it holds for every
# representation of a value.

import Base
import ./int.bend as I
import ./ledger.bend as L
import ./rules.bend as R
import ./proof_int.bend as V

# L1: contract lifecycle
# ----------------------

# LAW: a terminal status (settled, expired, withdrawn) has no successor
law terminal_no_successor:
  for s: R.Status
  for e: {True{} == R.is_terminal(s) : Bool}
  {R.transitions(s) == Nil{} : List<&2, R.Status>}

# LAW: no move out of a terminal status is allowed
law terminal_no_move:
  for +s: R.Status
  for  t: R.Status
  for  e: {True{} == R.is_terminal(s) : Bool}
  {False{} == R.can_move(s, t) : Bool}

# LAW: a successful move yields the requested status, and it is one that
# TRANSITIONS allows from the old status
law move_allowed:
  for +old: R.Status
  for +new: R.Status
  for +s: R.Status
  for  e: {R.move(old, new) == Done{s} : Result<&2, &2, String, R.Status>}
  {True{} == R.can_move(old, s) : Bool} & {s == new : R.Status}

# L2: supply outcomes
# -------------------

# LAW: kept iff not delivered < quantity
law outcome_kept:
  for +d: I.Int
  for  q: I.Int
  for  e: {False{} == I.is_lt(d, q) : Bool}
  {R.outcome_for(d, q) == R.Kept{} : R.Outcome}

# LAW: broken iff delivered == 0 and delivered < quantity
law outcome_broken:
  for +d: I.Int
  for  q: I.Int
  for e1: {True{} == I.is_lt(d, q) : Bool}
  for e2: {True{} == I.is_zero(d) : Bool}
  {R.outcome_for(d, q) == R.Broken{} : R.Outcome}

# LAW: partial otherwise (0 < delivered < quantity)
law outcome_partial:
  for +d: I.Int
  for  q: I.Int
  for e1: {True{} == I.is_lt(d, q) : Bool}
  for e2: {False{} == I.is_zero(d) : Bool}
  {R.outcome_for(d, q) == R.Partial{} : R.Outcome}

# Integer algebra (what L4 and L5 rest on)
# ----------------------------------------
# The value of an Int is V.vp(a) - V.vn(a) (two Nats; proof_int.bend):
# vp(End{s}) = 0, vn(End{s}) = s, vp(Bit{b, t}) = b + 2 vp(t),
# vn(Bit{b, t}) = 2 vn(t). V.Same(a, b) says a and b have the same value,
# V.EqZ(p1, n1, p2, n2) says p1 - n1 == p2 - n2.

# LAW: is_eq answers True only on equal values
law is_eq_sound:
  for +a: I.Int
  for +b: I.Int
  for  e: {True{} == I.is_eq(a, b) : Bool}
  V.Same(a, b)

# LAW: and on every pair of equal values
law is_eq_complete:
  for +a: I.Int
  for +b: I.Int
  for  h: V.Same(a, b)
  {True{} == I.is_eq(a, b) : Bool}

# LAW: add, sub and neg compute the sum, difference and negation of values
law add_value:
  for +a: I.Int
  for +b: I.Int
  V.EqZ(V.vp(I.add(a, b)), V.vn(I.add(a, b)), Nat.add(V.vp(a), V.vp(b)), Nat.add(V.vn(a), V.vn(b)))

law sub_value:
  for +a: I.Int
  for +b: I.Int
  V.EqZ(V.vp(I.sub(a, b)), V.vn(I.sub(a, b)), Nat.add(V.vp(a), V.vn(b)), Nat.add(V.vn(a), V.vp(b)))

law neg_value:
  for +a: I.Int
  V.EqZ(V.vp(I.neg(a)), V.vn(I.neg(a)), V.vn(a), V.vp(a))

# LAW: addition commutes
law add_comm:
  for +a: I.Int
  for +b: I.Int
  {True{} == I.is_eq(I.add(a, b), I.add(b, a)) : Bool}

# LAW: addition associates
law add_assoc:
  for +a: I.Int
  for +b: I.Int
  for +c: I.Int
  {True{} == I.is_eq(I.add(I.add(a, b), c), I.add(a, I.add(b, c))) : Bool}

# LAW: subtraction undoes addition
law sub_add:
  for +a: I.Int
  for +b: I.Int
  {True{} == I.is_eq(I.add(I.sub(a, b), b), a) : Bool}

# LAW: a value equals itself, whatever its representation
law eq_refl:
  for +a: I.Int
  {True{} == I.is_eq(a, a) : Bool}

# L4: ledger conservation
# -----------------------

# LAW: a transfer, successful or not, forced or not, between any accounts,
# leaves the sum of all balances unchanged
law transfer_conserves:
  for +l: L.Ledger
  for +src: String
  for +dst: String
  for +amount: I.Int
  for +reason: String
  for +ref: String
  for +tick: I.Int
  for +force: Bool
  {True{} == I.is_eq(L.total(L.after(L.transfer(l, src, dst, amount, reason, ref, tick, force), l)), L.total(l)) : Bool}

# LAW: a new ledger sums to 0
law new_total_zero:
  for +cs: List<&2, String>
  for +cr: Maybe<&2, I.Int>
  for +l: L.Ledger
  for  e: {L.new(cs, cr) == Done{l} : Result<&2, &2, String, L.Ledger>}
  {True{} == I.is_zero(L.total(l)) : Bool}

# a transfer request, and a ledger after a sequence of them (each applied
# when it succeeds, skipped when it fails)
type Req is Data:
  Req{src: String, dst: String, amount: I.Int, reason: String, ref: String, tick: I.Int, force: Bool}

def apply_all(rs: List<&2, Req>, +l: L.Ledger) -> L.Ledger:
  match rs:
    case Nil{}:
      l
    case Req{+src, +dst, +amount, reason, ref, tick, force} <> t:
      apply_all(t, L.after(L.transfer(l, src, dst, amount, reason, ref, tick, force), l))

# LAW: a ledger built only by transfers from a new ledger sums to 0
law built_total_zero:
  for +cs: List<&2, String>
  for +cr: Maybe<&2, I.Int>
  for +l: L.Ledger
  for +rs: List<&2, Req>
  for  e: {L.new(cs, cr) == Done{l} : Result<&2, &2, String, L.Ledger>}
  {True{} == I.is_zero(L.total(apply_all(rs, l))) : Bool}

# L5: licence fee split
# ---------------------

# the sum of the instalment amounts
def parts_total(ds: List<&2, R.Due>) -> I.Int:
  match ds:
    case Nil{}:
      I.zero()
    case R.Due{t, a} <> r:
      I.add(a, parts_total(r))

# LAW: split(total, dues) parts sum to total when total > 0 and dues is
# non-empty (zero parts are dropped; the parts that remain still sum to it)
law split_sums:
  for +total: I.Int
  for +h: I.Int
  for +t: List<&2, I.Int>
  for  e: {True{} == I.is_pos(total) : Bool}
  {True{} == I.is_eq(parts_total(R.split(total, h <> t)), total) : Bool}
