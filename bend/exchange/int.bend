# int.bend: arbitrary-precision signed integers with Python `int` semantics.
#
# Representation: two's complement, least significant bit first.
#   End{s}       the bit s repeated forever: End{False{}} is 0, End{True{}} is -1
#   Bit{b, t}    b + 2 * t
# Every operation answers the canonical form (no Bit{b, End{b}}), built by
# `mk`; every operation also reads non-canonical inputs correctly (the
# value of Bit{b, End{b}} is the value of End{b}), and the comparisons are
# decided on the value (is_eq(a, b) is is_zero(sub(a, b))). PROOF.bend
# proves add/sub/neg/is_zero/is_eq against the value (LAWS.bend); mul,
# floordiv, mod, the order tests, read and show are tested against Python
# (tests/unit/test_bend_int.py), not proven.
#
# API (import ./int.bend as I):
#   type Int is Data                 (I.Int)
#   I.zero() I.one() -> Int
#   I.from_u32(U32) -> Int           I.from_nat(Nat) -> Int     I.from_bool(Bool) -> Int (True = 1)
#   I.read(String) -> Maybe<&2, Int> canonical Python int text only:
#                                    -?(0|[1-9][0-9]*), and not "-0"
#   I.show(Int) -> String            Python str(int)
#   I.add I.sub I.mul (Int, Int) -> Int     I.neg(Int) -> Int
#   I.floordiv(a, b) -> Int          Python a // b (floors toward -inf); b == 0 answers 0
#   I.mod(a, b) -> Int               Python a % b (sign of b); b == 0 answers a
#   I.cmp(a, b) -> Cmp
#   I.is_eq I.is_ne I.is_lt I.is_le I.is_gt I.is_ge (Int, Int) -> Bool
#   I.is_zero I.is_neg I.is_pos (Int) -> Bool
#   I.min I.max (Int, Int) -> Int     I.abs(Int) -> Int
#   I.to_nat(Int) -> Nat             negative -> 0n (unary: only for small counts)
#   I.sum(List<&2, Int>) -> Int

import Base

type Int is Data:
  End{s: Bool}
  Bit{b: Bool, t: Int}

def zero() -> Int:
  End{False{}}

def one() -> Int:
  Bit{True{}, End{False{}}}

def from_bool(b: Bool) -> Int:
  match b:
    case False{}:
      End{False{}}
    case True{}:
      Bit{True{}, End{False{}}}

# Construction
# ------------

# b + 2 * End{s}, canonical
def mk.end(b: Bool, s: Bool) -> Int:
  match b s:
    case False{} False{}:
      End{False{}}
    case True{} True{}:
      End{True{}}
    case False{} True{}:
      Bit{False{}, End{True{}}}
    case True{} False{}:
      Bit{True{}, End{False{}}}

# b + 2 * t, canonical when t is
def mk(b: Bool, t: Int) -> Int:
  match t:
    case End{s}:
      mk.end(b, s)
    case Bit{x, r}:
      Bit{b, Bit{x, r}}

# the canonical form of a
def norm(a: Int) -> Int:
  match a:
    case End{s}:
      End{s}
    case Bit{x, r}:
      mk(x, norm(r))

# Addition
# --------

def sum3(+a: Bool, +b: Bool, +c: Bool) -> Bool:
  Bool.xor(a, Bool.xor(b, c))

def maj(+a: Bool, +b: Bool, +c: Bool) -> Bool:
  Bool.or(Bool.and(a, b), Bool.and(c, Bool.or(a, b)))

# End{s} + End{t} + c
def adc.ee(s: Bool, t: Bool, c: Bool) -> Int:
  match s t:
    case False{} False{}:
      mk(c, End{False{}})
    case True{} True{}:
      mk(c, End{True{}})
    case False{} True{}:
      End{Bool.not(c)}
    case True{} False{}:
      End{Bool.not(c)}

# End{s} + b + c
def adc.e(+s: Bool, b: Int, +c: Bool) -> Int:
  match b:
    case End{t}:
      adc.ee(s, t, c)
    case Bit{+y, r}:
      mk(sum3(s, y, c), adc.e(s, r, maj(s, y, c)))

# a + End{s} + c
def adc.be(a: Int, +s: Bool, +c: Bool) -> Int:
  match a:
    case End{t}:
      adc.ee(t, s, c)
    case Bit{+x, r}:
      mk(sum3(x, s, c), adc.be(r, s, maj(x, s, c)))

# a + b + c
def adc(a: Int, b: Int, +c: Bool) -> Int:
  match a b:
    case End{s} _:
      adc.e(s, b, c)
    case Bit{+x, r} End{s}:
      adc.be(Bit{x, r}, s, c)
    case Bit{+x, r} Bit{+y, q}:
      mk(sum3(x, y, c), adc(r, q, maj(x, y, c)))

def add(a: Int, b: Int) -> Int:
  adc(a, b, False{})

# -1 - a
def not(a: Int) -> Int:
  match a:
    case End{s}:
      End{Bool.not(s)}
    case Bit{x, r}:
      Bit{Bool.not(x), not(r)}

def neg(a: Int) -> Int:
  adc(not(a), End{False{}}, True{})

def sub(a: Int, b: Int) -> Int:
  add(a, neg(b))

# Tests
# -----

def is_neg(a: Int) -> Bool:
  match a:
    case End{s}:
      s
    case Bit{x, r}:
      is_neg(r)

def is_zero(a: Int) -> Bool:
  match a:
    case End{s}:
      Bool.not(s)
    case Bit{False{}, r}:
      is_zero(r)
    case Bit{True{}, r}:
      False{}

def is_pos(+a: Int) -> Bool:
  Bool.not(Bool.or(is_neg(a), is_zero(a)))

def sign.cmp.go(n: Bool, z: Bool) -> Cmp:
  match n z:
    case True{} _:
      LT{}
    case False{} True{}:
      EQ{}
    case False{} False{}:
      GT{}

def sign.cmp(+d: Int) -> Cmp:
  sign.cmp.go(is_neg(d), is_zero(d))

def cmp(a: Int, b: Int) -> Cmp:
  sign.cmp(sub(a, b))

def is_eq(a: Int, b: Int) -> Bool:
  is_zero(sub(a, b))

def is_ne(a: Int, b: Int) -> Bool:
  Bool.not(is_eq(a, b))

def is_lt(a: Int, b: Int) -> Bool:
  is_neg(sub(a, b))

def is_le(a: Int, b: Int) -> Bool:
  Bool.not(is_neg(sub(b, a)))

def is_gt(a: Int, b: Int) -> Bool:
  is_neg(sub(b, a))

def is_ge(a: Int, b: Int) -> Bool:
  Bool.not(is_neg(sub(a, b)))

def pick(c: Bool, a: Int, b: Int) -> Int:
  match c:
    case True{}:
      a
    case False{}:
      b

def min(+a: Int, +b: Int) -> Int:
  pick(is_le(a, b), a, b)

def max(+a: Int, +b: Int) -> Int:
  pick(is_ge(a, b), a, b)

def abs(+a: Int) -> Int:
  pick(is_neg(a), neg(a), a)

# Multiplication and division
# ---------------------------

# a * b, by the bits of a (a = x + 2 * r)
def mul.end(s: Bool, b: Int) -> Int:
  match s:
    case False{}:
      End{False{}}
    case True{}:
      neg(b)

# r2 = 2 * r + x; subtract b when r2 >= b
def mul(a: Int, +b: Int) -> Int:
  match a:
    case End{s}:
      mul.end(s, b)
    case Bit{x, r}:
      add(pick(x, b, zero()), mk(False{}, mul(r, b)))

def divmod.step(q: Int, r2: Int, +b: Int, ge: Bool) -> Int & Int:
  match ge:
    case True{}:
      (mk(True{}, q), sub(r2, b))
    case False{}:
      (mk(False{}, q), r2)

def divmod.next(x: Bool, qr: Int & Int, +b: Int) -> Int & Int:
  (q, r) = qr
  +r2 = mk(x, r)
  divmod.step(q, r2, b, is_ge(r2, b))

# quotient and remainder of a >= 0 by b > 0, long division over the bits
# of a, most significant first (the recursion reaches the top bit first)
def divmod.mag(a: Int, +b: Int) -> Int & Int:
  match a:
    case End{s}:
      (End{False{}}, End{False{}})
    case Bit{x, r}:
      divmod.next(x, divmod.mag(r, b), b)

# floor division fix-up: same signs keep q; else -q when exact, else -q-1
def floordiv.fix(same: Bool, q: Int, exact: Bool) -> Int:
  match same exact:
    case True{} _:
      q
    case False{} True{}:
      neg(q)
    case False{} False{}:
      not(q)

def floordiv.go(+a: Int, +b: Int, qr: Int & Int) -> Int:
  (q, r) = qr
  floordiv.fix(Bool.not(Bool.xor(is_neg(a), is_neg(b))), q, is_zero(r))

def floordiv.if(+a: Int, +b: Int, z: Bool) -> Int:
  match z:
    case True{}:
      End{False{}}
    case False{}:
      floordiv.go(a, b, divmod.mag(abs(a), abs(b)))

# Python a // b (b == 0 answers 0; callers never divide by 0)
def floordiv(+a: Int, +b: Int) -> Int:
  floordiv.if(a, b, is_zero(b))

# Python a % b == a - b * (a // b)
def mod(+a: Int, +b: Int) -> Int:
  sub(a, mul(floordiv(a, b), b))

# Conversions
# -----------

def u32.bit0(x: U32) -> Bool:
  U32.is_eq(U32.and(x, 1), 1)

def from_u32.go(n: Nat, +x: U32) -> Int:
  match n:
    case 0n:
      End{False{}}
    case 1n+p:
      mk(u32.bit0(x), from_u32.go(p, U32.shr(x)))

def from_u32(x: U32) -> Int:
  from_u32.go(32n, x)

def from_nat(n: Nat) -> Int:
  match n:
    case 0n:
      End{False{}}
    case 1n+p:
      add(from_nat(p), one())

def to_nat.bit(x: Bool) -> Nat:
  match x:
    case False{}:
      0n
    case True{}:
      1n

def to_nat.go(a: Int) -> Nat:
  match a:
    case End{s}:
      0n
    case Bit{x, r}:
      Nat.add(to_nat.bit(x), Nat.double(to_nat.go(r)))

def to_nat.if(a: Int, n: Bool) -> Nat:
  match n:
    case True{}:
      0n
    case False{}:
      to_nat.go(a)

def to_nat(+a: Int) -> Nat:
  to_nat.if(a, is_neg(a))

def sum(xs: List<&2, Int>) -> Int:
  match xs:
    case Nil{}:
      End{False{}}
    case h <> t:
      add(h, sum(t))

# Text
# ----

# decimal digits, least significant first: 2 * ds + c
def dec.dbl.fix(d: U32, c: Bool, rest: List<&2, U32>) -> List<&2, U32>:
  match c:
    case True{}:
      U32.sub(d, 10) <> rest
    case False{}:
      d <> rest

def dec.dbl.end(c: Bool) -> List<&2, U32>:
  match c:
    case True{}:
      [1]
    case False{}:
      []

def dec.dbl(ds: List<&2, U32>, c: Bool) -> List<&2, U32>:
  match ds:
    case Nil{}:
      dec.dbl.end(c)
    case h <> t:
      +d = U32.add(U32.mul(h, 2), Bool.to_u32(c))
      +ge = U32.is_ge(d, 10)
      dec.dbl.fix(d, ge, dec.dbl(t, ge))

# the decimal digits of a >= 0, least significant first ([] for 0)
def dec(a: Int) -> List<&2, U32>:
  match a:
    case End{s}:
      []
    case Bit{x, r}:
      dec.dbl(dec(r), x)

def digits.str(ds: List<&2, U32>, acc: String) -> String:
  match ds:
    case Nil{}:
      acc
    case h <> t:
      digits.str(t, SCon{Chr{U32.add(h, 48)}, acc})

def show.digits(ds: List<&2, U32>) -> String:
  match ds:
    case Nil{}:
      "0"
    case h <> t:
      digits.str(h <> t, SNil{})

def show.mag(a: Int) -> String:
  show.digits(dec(a))

def show.if(+a: Int, n: Bool) -> String:
  match n:
    case True{}:
      SCon{'-', show.mag(neg(a))}
    case False{}:
      show.mag(a)

def show(+a: Int) -> String:
  show.if(a, is_neg(a))

# 10 * a + d
def mul10.add(+a: Int, d: U32) -> Int:
  add(add(mk(False{}, mk(False{}, mk(False{}, a))), mk(False{}, a)), from_u32(d))

def is_digit(+x: U32) -> Bool:
  Bool.and(U32.is_ge(x, 48), U32.is_le(x, 57))

def read.gate(ok: Bool, m: Maybe<&2, Int>) -> Maybe<&2, Int>:
  match ok:
    case True{}:
      m
    case False{}:
      None{}

def read.digits(s: String, +acc: Int) -> Maybe<&2, Int>:
  match s:
    case SNil{}:
      Some{acc}
    case SCon{Chr{+x}, t}:
      read.gate(is_digit(x), read.digits(t, mul10.add(acc, U32.sub(x, 48))))

# a non-negative canonical decimal: "0", or [1-9][0-9]*
def read.nat.zero(t: String) -> Maybe<&2, Int>:
  match t:
    case SNil{}:
      Some{End{False{}}}
    case SCon{h, r}:
      None{}

def read.nat.lead(t: String, +x: U32, ok: Bool) -> Maybe<&2, Int>:
  match ok:
    case True{}:
      read.digits(t, from_u32(U32.sub(x, 48)))
    case False{}:
      None{}

def read.nat.first(t: String, +x: U32, zero: Bool) -> Maybe<&2, Int>:
  match zero:
    case True{}:
      read.nat.zero(t)
    case False{}:
      read.nat.lead(t, x, is_digit(x))

def read.nat(s: String) -> Maybe<&2, Int>:
  match s:
    case SNil{}:
      None{}
    case SCon{Chr{+x}, t}:
      read.nat.first(t, x, U32.is_eq(x, 48))

def read.neg.nz(+v: Int, z: Bool) -> Maybe<&2, Int>:
  match z:
    case True{}:
      None{}
    case False{}:
      Some{neg(v)}

def read.neg.of(m: Maybe<&2, Int>) -> Maybe<&2, Int>:
  match m:
    case None{}:
      None{}
    case Some{+v}:
      read.neg.nz(v, is_zero(v))

def read.sign(t: String, +x: U32, minus: Bool) -> Maybe<&2, Int>:
  match minus:
    case True{}:
      read.neg.of(read.nat(t))
    case False{}:
      read.nat(SCon{Chr{x}, t})

def read(s: String) -> Maybe<&2, Int>:
  match s:
    case SNil{}:
      None{}
    case SCon{Chr{+x}, t}:
      read.sign(t, x, U32.is_eq(x, 45))
