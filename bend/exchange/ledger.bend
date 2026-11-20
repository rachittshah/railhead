# ledger.bend: the off-world ledger of src/railhead/exchange/ledger.py, in
# integer pounds (int.bend). Every operation is one balanced transfer, so
# the sum of all balances stays 0: PROOF.bend proves it (law
# transfer_conserves in LAWS.bend), so the per-transfer `assert total == 0`
# of Python is not repeated at runtime.
#
# API (import ./ledger.bend as L):
#   type Entry is Data:    Entry{k: String, v: I.Int}            (one balance)
#   type Transfer is Data: Transfer{seq: I.Int, tick: I.Int, src: String, dst: String,
#                                   amount: I.Int, reason: String, ref: String}
#   type Ledger is Data:   Ledger{companies: List<&2, String>, credit: Maybe<&2, I.Int>,
#                                 bal: List<&2, Entry>, history: List<&2, Transfer>,
#                                 count: I.Int}
#   type Moved is Data:    Moved{ledger: Ledger, t: Transfer}      (a successful transfer)
#     bal is in Python's dict insertion order (companies, "exchange", then escrow accounts as
#     they open; a closed escrow account is removed); history is oldest first; count is its
#     length (the next seq).
#   L.EXCHANGE() L.ESCROW_PREFIX() -> String
#   L.escrow_account(cid) L.bond_account(cid) -> String     "escrow:<cid>", "escrow:<cid>:bond"
#   L.check_company_id(String) -> Bool        non-empty, no ':', not "exchange"
#   L.new(companies, credit: Maybe<&2, I.Int>) -> Result<&2, &2, String, Ledger>
#       errors, in Python's order: "bad_amount" (credit < 0), "bad_company" (bad id, then
#       duplicate). The caller checks Python's types first: a credit_limit that is not an int
#       (or is a bool) is "bad_amount"; a company id that is not a str is "bad_company".
#   L.balance(Ledger, account) -> I.Int       0 when absent
#   L.balances(Ledger) -> List<&2, Entry>   sorted by key (code points, as sorted())
#   L.can_debit(Ledger, account, amount) -> Bool
#   L.transfer(Ledger, src, dst, amount, reason, ref, tick: I.Int, force: Bool)
#       -> Result<&2, &2, String, Moved>
#       checks in Python's order: amount <= 0 "bad_amount"; src then dst not a known account
#       "unknown_account"; src == dst "self_transfer"; escrow src below amount
#       "insufficient_funds"; not force and not can_debit "insufficient_funds".
#       The caller checks Python's types first: an amount that is not an int (or is a bool) is
#       "bad_amount" (before any account check); an account that is not a str is
#       "unknown_account".
#   L.pay(Ledger, src, dst, amount, tick, ref) -> same as transfer, reason "pay"
#       src then dst not a company: "unknown_account"; then transfer's checks.
#   L.check_conservation(Ledger) -> Bool      sum is 0 and the history replays to the balances
#   L.total(Ledger) -> I.Int                  sum of the balances
#   L.after(Result<&2, &2, String, Moved>, Ledger) -> Ledger   the new ledger, or the old on Fail
#   L.companies(Ledger) L.bal(Ledger) L.history(Ledger)        field accessors

import Base
import ./int.bend as I

type Entry is Data:
  Entry{k: String, v: I.Int}

type Transfer is Data:
  Transfer{seq: I.Int, tick: I.Int, src: String, dst: String, amount: I.Int, reason: String, ref: String}

type Ledger is Data:
  Ledger{companies: List<&2, String>, credit: Maybe<&2, I.Int>, bal: List<&2, Entry>, history: List<&2, Transfer>, count: I.Int}

type Moved is Data:
  Moved{ledger: Ledger, t: Transfer}

def EXCHANGE() -> String:
  "exchange"

def ESCROW_PREFIX() -> String:
  "escrow:"

def escrow_account(cid: String) -> String:
  "escrow:" ++ cid

def bond_account(cid: String) -> String:
  "escrow:" ++ cid ++ ":bond"

def is_escrow(a: String) -> Bool:
  String.starts_with(a, "escrow:")

def has_colon(s: String) -> Bool:
  match s:
    case SNil{}:
      False{}
    case SCon{Chr{x}, t}:
      Bool.or(U32.is_eq(x, 58), has_colon(t))

def check_company_id(+name: String) -> Bool:
  Bool.not(Bool.or(String.is_empty(name), Bool.or(has_colon(name), String.eq(name, "exchange"))))

def mem(xs: List<&2, String>, +k: String) -> Bool:
  match xs:
    case Nil{}:
      False{}
    case h <> t:
      Bool.or(String.eq(h, k), mem(t, k))

# Balances: an association list
# -----------------------------

def get.if(v: I.Int, rest: I.Int, hit: Bool) -> I.Int:
  match hit:
    case True{}:
      v
    case False{}:
      rest

# the balance of k: the first entry with key k, else 0
def get(l: List<&2, Entry>, +k: String) -> I.Int:
  match l:
    case Nil{}:
      I.zero()
    case kv <> t:
      Entry{k2, v} = kv
      get.if(v, get(t, k), String.eq(k2, k))

def adjust.if(k2: String, v: I.Int, t: List<&2, Entry>, rest: List<&2, Entry>, d: I.Int, hit: Bool) -> List<&2, Entry>:
  match hit:
    case True{}:
      Entry{k2, I.add(v, d)} <> t
    case False{}:
      Entry{k2, v} <> rest

# Python's `bal[k] = bal.get(k, 0) + d`: the first entry with key k gains d;
# a missing key is appended with 0 + d
def adjust(l: List<&2, Entry>, +k: String, +d: I.Int) -> List<&2, Entry>:
  match l:
    case Nil{}:
      [Entry{k, I.add(I.zero(), d)}]
    case kv <> +t:
      Entry{+k2, +v} = kv
      adjust.if(k2, v, t, adjust(t, k, d), d, String.eq(k2, k))

def remove.if(k2: String, v: I.Int, t: List<&2, Entry>, rest: List<&2, Entry>, hit: Bool) -> List<&2, Entry>:
  match hit:
    case True{}:
      t
    case False{}:
      Entry{k2, v} <> rest

# drops the first entry with key k
def remove(l: List<&2, Entry>, +k: String) -> List<&2, Entry>:
  match l:
    case Nil{}:
      Nil{}
    case kv <> +t:
      Entry{+k2, v} = kv
      remove.if(k2, v, t, remove(t, k), String.eq(k2, k))

def purge.if(l: List<&2, Entry>, k: String, drop: Bool) -> List<&2, Entry>:
  match drop:
    case True{}:
      remove(l, k)
    case False{}:
      l

# a closed escrow account disappears from the balance sheet
def purge(+l: List<&2, Entry>, +k: String) -> List<&2, Entry>:
  purge.if(l, k, Bool.and(is_escrow(k), I.is_zero(get(l, k))))

# the sum of the balances
def total.go(l: List<&2, Entry>) -> I.Int:
  match l:
    case Nil{}:
      I.zero()
    case kv <> t:
      Entry{k, v} = kv
      I.add(v, total.go(t))

# the balances after moving amount from src to dst, in Python's order:
# src is debited (read then written), then dst credited, then each closed
# escrow account is dropped
def commit(l: List<&2, Entry>, +src: String, +dst: String, +amount: I.Int) -> List<&2, Entry>:
  purge(purge(adjust(adjust(l, src, I.neg(amount)), dst, amount), src), dst)

# sorting by key (insertion sort: few accounts)
def ins.if(kv: Entry, h: Entry, t: List<&2, Entry>, rest: List<&2, Entry>, before: Bool) -> List<&2, Entry>:
  match before:
    case True{}:
      kv <> h <> t
    case False{}:
      h <> rest

def key(kv: Entry) -> String:
  Entry{k, v} = kv
  k

def ins(+kv: Entry, l: List<&2, Entry>) -> List<&2, Entry>:
  match l:
    case Nil{}:
      [kv]
    case +h <> +t:
      ins.if(kv, h, t, ins(kv, t), String.is_le(key(kv), key(h)))

def sort(l: List<&2, Entry>) -> List<&2, Entry>:
  match l:
    case Nil{}:
      Nil{}
    case h <> t:
      ins(h, sort(t))

# Ledger
# ------

def companies(l: Ledger) -> List<&2, String>:
  match l:
    case Ledger{cs, cr, b, h, n}:
      cs

def bal(l: Ledger) -> List<&2, Entry>:
  match l:
    case Ledger{cs, cr, b, h, n}:
      b

def history(l: Ledger) -> List<&2, Transfer>:
  match l:
    case Ledger{cs, cr, b, h, n}:
      h

def total(l: Ledger) -> I.Int:
  total.go(bal(l))

def balance(l: Ledger, account: String) -> I.Int:
  get(bal(l), account)

def balances(l: Ledger) -> List<&2, Entry>:
  sort(bal(l))

def known(+cs: List<&2, String>, +a: String) -> Bool:
  Bool.or(mem(cs, a), Bool.or(String.eq(a, "exchange"), is_escrow(a)))

def can_debit.co(cr: Maybe<&2, I.Int>, b: I.Int, +amount: I.Int) -> Bool:
  match cr:
    case None{}:
      True{}
    case Some{c}:
      I.is_ge(I.sub(b, amount), I.neg(c))

def can_debit.sel(cr: Maybe<&2, I.Int>, +b: I.Int, +amount: I.Int, ex: Bool, esc: Bool) -> Bool:
  match ex esc:
    case True{} _:
      True{}
    case False{} True{}:
      I.is_ge(b, amount)
    case False{} False{}:
      can_debit.co(cr, b, amount)

def can_debit.go(cr: Maybe<&2, I.Int>, b: I.Int, +account: String, +amount: I.Int) -> Bool:
  can_debit.sel(cr, b, amount, String.eq(account, "exchange"), is_escrow(account))

# True when a voluntary (non-forced) debit of amount from account is allowed
def can_debit(l: Ledger, +account: String, +amount: I.Int) -> Bool:
  match l:
    case Ledger{cs, cr, b, h, n}:
      can_debit.go(cr, get(b, account), account, amount)

def first(bad: Bool, code: String, rest: Maybe<&2, String>) -> Maybe<&2, String>:
  match bad:
    case True{}:
      Some{code}
    case False{}:
      rest

# the first failing check of Python's transfer, in its order; None passes
def check(+cs: List<&2, String>, +cr: Maybe<&2, I.Int>, +b: List<&2, Entry>, +src: String, +dst: String, +amount: I.Int, force: Bool) -> Maybe<&2, String>:
  first(Bool.not(I.is_pos(amount)), "bad_amount",
  first(Bool.not(known(cs, src)), "unknown_account",
  first(Bool.not(known(cs, dst)), "unknown_account",
  first(String.eq(src, dst), "self_transfer",
  first(Bool.and(is_escrow(src), I.is_lt(get(b, src), amount)), "insufficient_funds",
  first(Bool.and(Bool.not(force), Bool.not(can_debit.go(cr, get(b, src), src, amount))), "insufficient_funds",
  None{}))))))

def transfer.go(err: Maybe<&2, String>, cs: List<&2, String>, cr: Maybe<&2, I.Int>, b: List<&2, Entry>, h: List<&2, Transfer>, +n: I.Int, +src: String, +dst: String, +amount: I.Int, reason: String, ref: String, tick: I.Int) -> Result<&2, &2, String, Moved>:
  match err:
    case Some{e}:
      Fail{e}
    case None{}:
      +t = {Transfer{n, tick, src, dst, amount, reason, ref} : Transfer}
      Done{Moved{Ledger{cs, cr, commit(b, src, dst, amount), List.append(&2, Transfer, h, [t]), I.add(n, I.one())}, t}}

# Python's Ledger.transfer
def transfer(l: Ledger, +src: String, +dst: String, +amount: I.Int, reason: String, ref: String, tick: I.Int, force: Bool) -> Result<&2, &2, String, Moved>:
  match l:
    case Ledger{+cs, +cr, +b, h, n}:
      transfer.go(check(cs, cr, b, src, dst, amount, force), cs, cr, b, h, n, src, dst, amount, reason, ref, tick)

# the ledger after a transfer: the new one when it succeeded, else the old one
def after(r: Result<&2, &2, String, Moved>, l: Ledger) -> Ledger:
  match r:
    case Fail{e}:
      l
    case Done{Moved{l2, t}}:
      l2

def pay.go(ok: Bool, l: Ledger, +src: String, +dst: String, +amount: I.Int, tick: I.Int, ref: String) -> Result<&2, &2, String, Moved>:
  match ok:
    case False{}:
      Fail{"unknown_account"}
    case True{}:
      transfer(l, src, dst, amount, "pay", ref, tick, False{})

# Python's Ledger.pay: a voluntary company-to-company transfer
def pay(+l: Ledger, +src: String, +dst: String, +amount: I.Int, tick: I.Int, ref: String) -> Result<&2, &2, String, Moved>:
  pay.go(Bool.and(mem(companies(l), src), mem(companies(l), dst)), l, src, dst, amount, tick, ref)

# Construction
# ------------

def has_dup(xs: List<&2, String>) -> Bool:
  match xs:
    case Nil{}:
      False{}
    case +h <> +t:
      Bool.or(mem(t, h), has_dup(t))

def all_ids(xs: List<&2, String>) -> Bool:
  match xs:
    case Nil{}:
      True{}
    case h <> t:
      Bool.and(check_company_id(h), all_ids(t))

def credit_bad(cr: Maybe<&2, I.Int>) -> Bool:
  match cr:
    case None{}:
      False{}
    case Some{c}:
      I.is_neg(c)

def zeros(cs: List<&2, String>) -> List<&2, Entry>:
  match cs:
    case Nil{}:
      [Entry{"exchange", I.zero()}]
    case c <> t:
      Entry{c, I.zero()} <> zeros(t)

def new.go(err: Maybe<&2, String>, +cs: List<&2, String>, cr: Maybe<&2, I.Int>) -> Result<&2, &2, String, Ledger>:
  match err:
    case Some{e}:
      Fail{e}
    case None{}:
      Done{Ledger{cs, cr, zeros(cs), [], I.zero()}}

# Python's Ledger(companies, credit_limit)
def new(+cs: List<&2, String>, +cr: Maybe<&2, I.Int>) -> Result<&2, &2, String, Ledger>:
  new.go(first(credit_bad(cr), "bad_amount", first(Bool.not(all_ids(cs)), "bad_company", first(has_dup(cs), "bad_company", None{}))), cs, cr)

# Audit
# -----

def replay(h: List<&2, Transfer>, acc: List<&2, Entry>) -> List<&2, Entry>:
  match h:
    case Nil{}:
      acc
    case t <> rest:
      match t:
        case Transfer{seq, tick, +src, +dst, +amount, reason, ref}:
          replay(rest, adjust(adjust(acc, src, I.neg(amount)), dst, amount))

def agree(ks: List<&2, Entry>, +r: List<&2, Entry>, +b: List<&2, Entry>) -> Bool:
  match ks:
    case Nil{}:
      True{}
    case kv <> t:
      Entry{+k, v} = kv
      Bool.and(I.is_eq(get(r, k), get(b, k)), agree(t, r, b))

# Python's check_conservation, as a verdict: the sum is 0 and the history
# replays to the same balance for every account
def check_conservation(l: Ledger) -> Bool:
  match l:
    case Ledger{cs, cr, +b, h, n}:
      +r = replay(h, [])
      Bool.and(I.is_zero(total.go(b)), Bool.and(agree(r, r, b), agree(b, r, b)))
