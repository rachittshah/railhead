# rules.bend: the contract lifecycle (contracts.py TRANSITIONS), supply
# outcomes (settlement.py outcome_for) and the licence fee schedule
# (exchange.py instalments, clip_schedule, due_ticks, split).
#
# API (import ./rules.bend as R):
#   type Status is Data: Offered{} Countered{} Signed{} Active{} Settled{} Expired{} Withdrawn{}
#   R.status_name(Status) -> String           "offered", ...
#   R.status_read(String) -> Maybe<&2, Status>
#   R.transitions(Status) -> List<&2, Status> Python's TRANSITIONS[s] (in a fixed order)
#   R.can_move(old, new) -> Bool              new in TRANSITIONS[old]
#   R.move(old, new) -> Result<&2, &2, String, Status>   "bad_transition" when not allowed
#   R.is_terminal(Status) -> Bool             settled, expired, withdrawn
#   type Outcome is Data: Kept{} Partial{} Broken{}
#   R.outcome_name(Outcome) -> String         "kept" | "partial" | "broken"
#   R.outcome_for(delivered: I.Int, quantity: I.Int) -> Outcome
#   type Due is Data: Due{tick: I.Int, amount: I.Int}        one instalment
#   R.INSTALMENT_TICKS() -> I.Int             90 * 74 = 6660
#   R.due_ticks(window_start, deadline) -> List<&2, I.Int>
#       range(window_start + 6660, deadline, 6660) + [deadline]
#   R.split(total, dues: List<&2, I.Int>) -> List<&2, Due>
#       [] when total <= 0 or no dues; equal parts total // n, the remainder on the last; zero
#       parts dropped. (Python drops parts `a <= 0`; with total > 0 no part is negative, so
#       dropping the zero ones is the same list; tests/unit/test_bend_rules.py checks it.)
#   R.clip_schedule(schedule: List<&2, Due>, last_due: Maybe<&2, I.Int>, floor: I.Int) -> List<&2, Due>
#   R.instalments(fee, window_start, deadline, last_due: Maybe<&2, I.Int>) -> List<&2, Due>

import Base
import ./int.bend as I

# Lifecycle
# ---------

type Status is Data:
  Offered{}
  Countered{}
  Signed{}
  Active{}
  Settled{}
  Expired{}
  Withdrawn{}

def status_name(s: Status) -> String:
  match s:
    case Offered{}:
      "offered"
    case Countered{}:
      "countered"
    case Signed{}:
      "signed"
    case Active{}:
      "active"
    case Settled{}:
      "settled"
    case Expired{}:
      "expired"
    case Withdrawn{}:
      "withdrawn"

def status_read.if(h: Status, rest: Maybe<&2, Status>, hit: Bool) -> Maybe<&2, Status>:
  match hit:
    case True{}:
      Some{h}
    case False{}:
      rest

def status_read.go(+s: String, xs: List<&2, Status>) -> Maybe<&2, Status>:
  match xs:
    case Nil{}:
      None{}
    case +h <> t:
      status_read.if(h, status_read.go(s, t), String.eq(status_name(h), s))

def status_read(+s: String) -> Maybe<&2, Status>:
  status_read.go(s, [Offered{}, Countered{}, Signed{}, Active{}, Settled{}, Expired{}, Withdrawn{}])

def transitions(s: Status) -> List<&2, Status>:
  match s:
    case Offered{}:
      [Countered{}, Signed{}, Expired{}, Withdrawn{}]
    case Countered{}:
      [Countered{}, Signed{}, Expired{}, Withdrawn{}]
    case Signed{}:
      [Active{}, Settled{}]
    case Active{}:
      [Settled{}]
    case Settled{}:
      []
    case Expired{}:
      []
    case Withdrawn{}:
      []

def status_eq(a: Status, b: Status) -> Bool:
  String.eq(status_name(a), status_name(b))

def elem(xs: List<&2, Status>, +s: Status) -> Bool:
  match xs:
    case Nil{}:
      False{}
    case h <> t:
      Bool.or(status_eq(h, s), elem(t, s))

def can_move(old: Status, new: Status) -> Bool:
  elem(transitions(old), new)

def move.if(new: Status, ok: Bool) -> Result<&2, &2, String, Status>:
  match ok:
    case True{}:
      Done{new}
    case False{}:
      Fail{"bad_transition"}

def move(old: Status, +new: Status) -> Result<&2, &2, String, Status>:
  move.if(new, can_move(old, new))

def is_terminal(s: Status) -> Bool:
  match s:
    case Settled{}:
      True{}
    case Expired{}:
      True{}
    case Withdrawn{}:
      True{}
    case Offered{}:
      False{}
    case Countered{}:
      False{}
    case Signed{}:
      False{}
    case Active{}:
      False{}

# Outcomes
# --------

type Outcome is Data:
  Kept{}
  Partial{}
  Broken{}

def outcome_name(o: Outcome) -> String:
  match o:
    case Kept{}:
      "kept"
    case Partial{}:
      "partial"
    case Broken{}:
      "broken"

def outcome_for.go(lt: Bool, zero: Bool) -> Outcome:
  match lt zero:
    case False{} _:
      Kept{}
    case True{} True{}:
      Broken{}
    case True{} False{}:
      Partial{}

# kept when delivered >= quantity; else broken when delivered == 0; else partial
def outcome_for(+delivered: I.Int, quantity: I.Int) -> Outcome:
  outcome_for.go(I.is_lt(delivered, quantity), I.is_zero(delivered))

# Licence schedule
# ----------------

type Due is Data:
  Due{tick: I.Int, amount: I.Int}

def INSTALMENT_TICKS() -> I.Int:
  I.from_u32(6660)

def due_ticks.go(n: Nat, +t: I.Int, deadline: I.Int) -> List<&2, I.Int>:
  match n:
    case 0n:
      [deadline]
    case 1n+p:
      t <> due_ticks.go(p, I.add(t, INSTALMENT_TICKS()), deadline)

# range(window_start + INST, deadline, INST) + [deadline]: the range has
# ceil((deadline - start) / INST) elements when start < deadline
def due_ticks(window_start: I.Int, +deadline: I.Int) -> List<&2, I.Int>:
  +start = I.add(window_start, INSTALMENT_TICKS())
  +n = I.floordiv(I.add(I.sub(deadline, start), I.sub(INSTALMENT_TICKS(), I.one())), INSTALMENT_TICKS())
  due_ticks.go(I.to_nat(n), start, deadline)

def len(xs: List<&2, I.Int>) -> I.Int:
  match xs:
    case Nil{}:
      I.zero()
    case h <> t:
      I.add(I.one(), len(t))

def split.keep(z: Bool, d: Due, rest: List<&2, Due>) -> List<&2, Due>:
  match z:
    case True{}:
      rest
    case False{}:
      d <> rest

def split.cons(+a: I.Int, d: I.Int, rest: List<&2, Due>) -> List<&2, Due>:
  split.keep(I.is_zero(a), Due{d, a}, rest)

# the parts of the dues h <> t: each one `each`, the last the remainder rem
def split.go(t: List<&2, I.Int>, h: I.Int, +each: I.Int, +rem: I.Int) -> List<&2, Due>:
  match t:
    case Nil{}:
      split.cons(rem, h, [])
    case h2 <> t2:
      split.cons(each, h, split.go(t2, h2, each, I.sub(rem, each)))

def split.dues(dues: List<&2, I.Int>, +total: I.Int) -> List<&2, Due>:
  match dues:
    case Nil{}:
      []
    case +h <> +t:
      split.go(t, h, I.floordiv(total, len(h <> t)), total)

def split.if(pos: Bool, dues: List<&2, I.Int>, total: I.Int) -> List<&2, Due>:
  match pos:
    case True{}:
      split.dues(dues, total)
    case False{}:
      []

# total over dues in equal integer parts, the remainder on the last; no zero parts
def split(+total: I.Int, dues: List<&2, I.Int>) -> List<&2, Due>:
  split.if(I.is_pos(total), dues, total)

def due_tick(d: Due) -> I.Int:
  match d:
    case Due{t, a}:
      t

def due_amount(d: Due) -> I.Int:
  match d:
    case Due{t, a}:
      a

def kept.if(d: Due, rest: List<&2, Due>, ok: Bool) -> List<&2, Due>:
  match ok:
    case True{}:
      d <> rest
    case False{}:
      rest

# the instalments due at or before last
def kept(s: List<&2, Due>, +last: I.Int) -> List<&2, Due>:
  match s:
    case Nil{}:
      []
    case +d <> t:
      kept.if(d, kept(t, last), I.is_le(due_tick(d), last))

# the total of the instalments due after last
def late(s: List<&2, Due>, +last: I.Int) -> I.Int:
  match s:
    case Nil{}:
      I.zero()
    case +d <> t:
      I.add(I.pick(I.is_gt(due_tick(d), last), due_amount(d), I.zero()), late(t, last))

# adds x to the amount of the last instalment
def bump_last(s: List<&2, Due>, +x: I.Int) -> List<&2, Due>:
  match s:
    case Nil{}:
      []
    case Due{t, a} <> Nil{}:
      [Due{t, I.add(a, x)}]
    case d <> t:
      d <> bump_last(t, x)

def clip.fold(k: List<&2, Due>, lt: I.Int, z: Bool) -> List<&2, Due>:
  match z:
    case True{}:
      k
    case False{}:
      bump_last(k, lt)

def clip.fin(k: List<&2, Due>, +lt: I.Int, +last: I.Int, floor: I.Int) -> List<&2, Due>:
  match k:
    case Nil{}:
      [Due{I.max(floor, last), lt}]
    case h <> t:
      clip.fold(h <> t, lt, I.is_zero(lt))

def clip.go(s: List<&2, Due>, last: Maybe<&2, I.Int>, floor: I.Int) -> List<&2, Due>:
  match s:
    case Nil{}:
      []
    case +h <> +t:
      match last:
        case None{}:
          h <> t
        case Some{+l}:
          clip.fin(kept(h <> t, l), late(h <> t, l), l, floor)

# the instalments due at or before last_due, with the later amounts folded
# into the last of them; if none is in time, the whole amount at
# max(floor, last_due)
def clip_schedule(s: List<&2, Due>, last_due: Maybe<&2, I.Int>, floor: I.Int) -> List<&2, Due>:
  clip.go(s, last_due, floor)

def instalments(fee: I.Int, +window_start: I.Int, deadline: I.Int, last_due: Maybe<&2, I.Int>) -> List<&2, Due>:
  clip_schedule(split(fee, due_ticks(window_start, deadline)), last_due, window_start)
