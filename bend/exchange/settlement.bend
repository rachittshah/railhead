# settlement.bend: the supply settlement arithmetic of
# src/railhead/exchange/settlement.py (compute_supply_settlement, outcome_for).
#
# API (import ./settlement.bend as S):
#   type Settle is Data: Settle{delivered, quantity, outcome: String, seller_payment,
#                               buyer_refund, penalty, from_bond, from_balance, bond_returned}
#     getters S.s_<field>
#   S.compute(terms: J.Json, delivered: J.Json) -> Result<&2, &2, String, Settle>
#       compute_supply_settlement(terms, delivered): "bad_delivered" unless delivered is a
#       non-negative int (bool excluded); with Q, P, K, B = quantity, price,
#       penalty_per_unit, bond (default 0) and counted = min(D, Q):
#       seller_payment = P * counted // Q, buyer_refund = P - seller_payment,
#       penalty = K * (Q - counted), from_bond = min(B, penalty),
#       from_balance = penalty - from_bond, bond_returned = B - from_bond,
#       outcome = R.outcome_for(D, Q)
#       No sign is assumed: tender terms are not schema-checked, so K may be negative.
#   S.fields(Settle) -> the result dict's (key, value) pairs, in Python's key names
#       (penalty_from_bond, penalty_from_balance, ...)
#
# Pure: no IO, no @unsafe.

import Base
import ./int.bend as I
import ./json.bend as J
import ./rules.bend as R
import ./contracts.bend as C

type Settle is Data:
  Settle{delivered: I.Int, quantity: I.Int, outcome: String, seller_payment: I.Int, buyer_refund: I.Int, penalty: I.Int, from_bond: I.Int, from_balance: I.Int, bond_returned: I.Int}

def s_delivered(x: Settle) -> I.Int:
  match x:
    case Settle{delivered, quantity, outcome, seller_payment, buyer_refund, penalty, from_bond, from_balance, bond_returned}:
      delivered

def s_quantity(x: Settle) -> I.Int:
  match x:
    case Settle{delivered, quantity, outcome, seller_payment, buyer_refund, penalty, from_bond, from_balance, bond_returned}:
      quantity

def s_outcome(x: Settle) -> String:
  match x:
    case Settle{delivered, quantity, outcome, seller_payment, buyer_refund, penalty, from_bond, from_balance, bond_returned}:
      outcome

def s_seller_payment(x: Settle) -> I.Int:
  match x:
    case Settle{delivered, quantity, outcome, seller_payment, buyer_refund, penalty, from_bond, from_balance, bond_returned}:
      seller_payment

def s_buyer_refund(x: Settle) -> I.Int:
  match x:
    case Settle{delivered, quantity, outcome, seller_payment, buyer_refund, penalty, from_bond, from_balance, bond_returned}:
      buyer_refund

def s_penalty(x: Settle) -> I.Int:
  match x:
    case Settle{delivered, quantity, outcome, seller_payment, buyer_refund, penalty, from_bond, from_balance, bond_returned}:
      penalty

def s_from_bond(x: Settle) -> I.Int:
  match x:
    case Settle{delivered, quantity, outcome, seller_payment, buyer_refund, penalty, from_bond, from_balance, bond_returned}:
      from_bond

def s_from_balance(x: Settle) -> I.Int:
  match x:
    case Settle{delivered, quantity, outcome, seller_payment, buyer_refund, penalty, from_bond, from_balance, bond_returned}:
      from_balance

def s_bond_returned(x: Settle) -> I.Int:
  match x:
    case Settle{delivered, quantity, outcome, seller_payment, buyer_refund, penalty, from_bond, from_balance, bond_returned}:
      bond_returned

def compute.go(+d: I.Int, +q: I.Int, +price: I.Int, +ppu: I.Int, +bond: I.Int) -> Settle:
  +counted = I.min(d, q)
  +sp = I.floordiv(I.mul(price, counted), q)
  +pen = I.mul(ppu, I.sub(q, counted))
  +fb = I.min(bond, pen)
  Settle{d, q, R.outcome_name(R.outcome_for(d, q)), sp, I.sub(price, sp), pen, fb, I.sub(pen, fb), I.sub(bond, fb)}

# compute_supply_settlement(terms, delivered)
def compute(+terms: J.Json, +delivered: J.Json) -> Result<&2, &2, String, Settle>:
  do Result<&2, &2, String, Settle>:
    Unit <- C.need(Bool.and(C.is_int(delivered), I.is_ge(C.int_of(delivered), I.zero())), "bad_delivered")
    return compute.go(C.int_of(delivered), C.terms_int(terms, "quantity"), C.terms_int(terms, "price"), C.terms_int(terms, "penalty_per_unit"), C.terms_int(terms, "bond"))

# the dict compute_supply_settlement returns
def fields(+s: Settle) -> List<&2, +Sigma<String, _ => J.Json>>:
  [("delivered", C.jint(s_delivered(s))),
   ("quantity", C.jint(s_quantity(s))),
   ("outcome", J.JStr{s_outcome(s)}),
   ("seller_payment", C.jint(s_seller_payment(s))),
   ("buyer_refund", C.jint(s_buyer_refund(s))),
   ("penalty", C.jint(s_penalty(s))),
   ("penalty_from_bond", C.jint(s_from_bond(s))),
   ("penalty_from_balance", C.jint(s_from_balance(s))),
   ("bond_returned", C.jint(s_bond_returned(s)))]
