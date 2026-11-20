# proof_int.bend: int.bend's addition, negation and subtraction are
# correct, stated against the value of an Int as a pair of Nats
# (vp(a) - vn(a)); proofs are pure and erased.
#
#   vp(End{s}) = 0          vn(End{s}) = s
#   vp(Bit{b, t}) = b + 2 vp(t)   vn(Bit{b, t}) = 2 vn(t)
#
# EqZ(p1, n1, p2, n2) says p1 - n1 == p2 - n2, as p1 + n2 == p2 + n1.
# The value is total (every Int, canonical or not, has one), so these
# facts hold for every input.

import Base
import ./proof_nat.bend as N
import ./int.bend as I

# Values
# ------

def b2n(b: Bool) -> Nat:
  match b:
    case False{}:
      0n
    case True{}:
      1n

def vp(a: I.Int) -> Nat:
  match a:
    case I.End{s}:
      0n
    case I.Bit{x, r}:
      Nat.add(b2n(x), Nat.double(vp(r)))

def vn(a: I.Int) -> Nat:
  match a:
    case I.End{s}:
      b2n(s)
    case I.Bit{x, r}:
      Nat.double(vn(r))

def EqZ(p1: Nat, n1: Nat, p2: Nat, n2: Nat) -> Type:
  {Nat.add(p1, n2) == Nat.add(p2, n1) : Nat}

# a == b as values
def Same(+a: I.Int, +b: I.Int) -> Type:
  EqZ(vp(a), vn(a), vp(b), vn(b))

# expression shorthands for N.solve / N.cancel
def va(i: Nat) -> N.E:
  N.At{i}

def pl(a: N.E, b: N.E) -> N.E:
  N.Ad{a, b}

def db(a: N.E) -> N.E:
  N.Db{a}

def zz() -> N.E:
  N.Z0{}

def one() -> N.E:
  N.S1{N.Z0{}}

# Refutations
# -----------

def efq(-A: Type, e: Empty) -> A:
  match e:

def BT(b: Bool) -> Type:
  match b:
    case True{}:
      Unit
    case False{}:
      Empty

def bool_disc(e: {True{} == False{} : Bool}) -> Empty:
  %e : BT(_)
  Unit{}

def NZ(n: Nat) -> Type:
  match n:
    case 0n:
      Empty
    case 1n+p:
      Unit

def nat_disc(-x: Nat, e: {1n+x == 0n : Nat}) -> Empty:
  %e : NZ(_)
  Unit{}

def double_inj(m: Nat, n: Nat, e: {Nat.double(m) == Nat.double(n) : Nat}) -> {m == n : Nat}:
  match m n:
    case 0n 0n:
      {==}
    case 0n 1n+q:
      efq({0n == 1n+q : Nat}, nat_disc(1n+Nat.double(q), Equal.sym(Nat, 0n, 2n+Nat.double(q), e)))
    case 1n+p 0n:
      efq({1n+p == 0n : Nat}, nat_disc(1n+Nat.double(p), e))
    case 1n+p 1n+q:
      +h = double_inj(p, q, N.succ_inj(Nat.double(p), Nat.double(q), N.succ_inj(1n+Nat.double(p), 1n+Nat.double(q), e)))
      Equal.cong(Nat, Nat, k => 1n+k, p, q, h)

def odd_even(m: Nat, n: Nat, e: {1n+Nat.double(m) == Nat.double(n) : Nat}) -> Empty:
  match m n:
    case _ 0n:
      nat_disc(Nat.double(m), e)
    case 0n 1n+q:
      nat_disc(Nat.double(q), Equal.sym(Nat, 0n, 1n+Nat.double(q), N.succ_inj(0n, 1n+Nat.double(q), e)))
    case 1n+p 1n+q:
      odd_even(p, q, N.succ_inj(1n+Nat.double(p), Nat.double(q), N.succ_inj(2n+Nat.double(p), 1n+Nat.double(q), e)))

# Bit facts
# ---------

def full_add(x: Bool, y: Bool, c: Bool) -> {Nat.add(b2n(x), Nat.add(b2n(y), b2n(c))) == Nat.add(b2n(I.sum3(x, y, c)), Nat.double(b2n(I.maj(x, y, c)))) : Nat}:
  match x y c:
    case False{} False{} False{}:
      {==}
    case False{} False{} True{}:
      {==}
    case False{} True{} False{}:
      {==}
    case False{} True{} True{}:
      {==}
    case True{} False{} False{}:
      {==}
    case True{} False{} True{}:
      {==}
    case True{} True{} False{}:
      {==}
    case True{} True{} True{}:
      {==}

def not_bit(x: Bool) -> {Nat.add(b2n(Bool.not(x)), b2n(x)) == 1n : Nat}:
  match x:
    case False{}:
      {==}
    case True{}:
      {==}

# End{s} has the value of Bit{s, End{s}}
def end_bit(s: Bool) -> EqZ(0n, b2n(s), Nat.add(b2n(s), Nat.double(0n)), Nat.double(b2n(s))):
  match s:
    case False{}:
      {==}
    case True{}:
      {==}

def mk_ok(b: Bool, t: I.Int) -> EqZ(vp(I.mk(b, t)), vn(I.mk(b, t)), Nat.add(b2n(b), Nat.double(vp(t))), Nat.double(vn(t))):
  match b t:
    case False{} I.End{False{}}:
      {==}
    case False{} I.End{True{}}:
      {==}
    case True{} I.End{False{}}:
      {==}
    case True{} I.End{True{}}:
      {==}
    case False{} I.Bit{x, r}:
      {==}
    case True{} I.Bit{x, r}:
      {==}

# Addition
# --------

def ADC(+a: I.Int, +b: I.Int, c: Bool, +r: I.Int) -> Type:
  EqZ(vp(r), vn(r), Nat.add(Nat.add(vp(a), vp(b)), b2n(c)), Nat.add(vn(a), vn(b)))

# one ripple-carry step, over the values: the result mk(S, R') of operands
# A = x + 2a and B = y + 2b with carry c, when R' = a + b + K
def step(+vR: Nat, +nR: Nat, +vR2: Nat, +nR2: Nat, +pA: Nat, +nA: Nat, +pB: Nat, +nB: Nat,
  +pa: Nat, +na: Nat, +pb: Nat, +nb: Nat, +x: Nat, +y: Nat, +c: Nat, +S: Nat, +K: Nat,
  m: EqZ(vR, nR, Nat.add(S, Nat.double(vR2)), Nat.double(nR2)),
  ih: EqZ(vR2, nR2, Nat.add(Nat.add(pa, pb), K), Nat.add(na, nb)),
  f: {Nat.add(x, Nat.add(y, c)) == Nat.add(S, Nat.double(K)) : Nat},
  ha: EqZ(pA, nA, Nat.add(x, Nat.double(pa)), Nat.double(na)),
  hb: EqZ(pB, nB, Nat.add(y, Nat.double(pb)), Nat.double(nb)))
  -> EqZ(vR, nR, Nat.add(Nat.add(pA, pB), c), Nat.add(nA, nB)):
  +env = {[vR, nR, vR2, nR2, pA, nA, pB, nB, pa, na, pb, nb, x, y, c, S, K] : List<&2, Nat>}
  +ml = pl(va(0n), db(va(3n)))
  +mr = pl(pl(va(15n), db(va(2n))), va(1n))
  +il = pl(va(2n), pl(va(9n), va(11n)))
  +ir = pl(pl(pl(va(8n), va(10n)), va(16n)), va(3n))
  +fl = pl(va(12n), pl(va(13n), va(14n)))
  +fr = pl(va(15n), db(va(16n)))
  +al = pl(va(4n), db(va(9n)))
  +ar = pl(pl(va(12n), db(va(8n))), va(5n))
  +bl = pl(va(6n), db(va(11n)))
  +br = pl(pl(va(13n), db(va(10n))), va(7n))
  +gl = pl(va(0n), pl(va(5n), va(7n)))
  +gr = pl(pl(pl(va(4n), va(6n)), va(14n)), va(1n))
  +cc = pl(db(va(3n)), pl(db(va(2n)), pl(db(va(9n)), pl(db(va(11n)), pl(va(15n), pl(db(va(16n)), pl(va(12n), pl(db(va(8n)), pl(va(13n), db(va(10n)))))))))))
  +e1 = N.add_eq(Nat.add(vR, Nat.double(nR2)), Nat.add(Nat.add(S, Nat.double(vR2)), nR), Nat.double(Nat.add(vR2, Nat.add(na, nb))), Nat.double(Nat.add(Nat.add(Nat.add(pa, pb), K), nR2)), m, N.double_eq(Nat.add(vR2, Nat.add(na, nb)), Nat.add(Nat.add(Nat.add(pa, pb), K), nR2), ih))
  +e2 = N.add_eq(N.ev(env, pl(ml, db(il))), N.ev(env, pl(mr, db(ir))), Nat.add(S, Nat.double(K)), Nat.add(x, Nat.add(y, c)), e1, N.sy(Nat.add(x, Nat.add(y, c)), Nat.add(S, Nat.double(K)), f))
  +e3 = N.add_eq(N.ev(env, pl(pl(ml, db(il)), fr)), N.ev(env, pl(pl(mr, db(ir)), fl)), Nat.add(Nat.add(x, Nat.double(pa)), nA), Nat.add(pA, Nat.double(na)), e2, N.sy(Nat.add(pA, Nat.double(na)), Nat.add(Nat.add(x, Nat.double(pa)), nA), ha))
  +e4 = N.add_eq(N.ev(env, pl(pl(pl(ml, db(il)), fr), ar)), N.ev(env, pl(pl(pl(mr, db(ir)), fl), al)), Nat.add(Nat.add(y, Nat.double(pb)), nB), Nat.add(pB, Nat.double(nb)), e3, N.sy(Nat.add(pB, Nat.double(nb)), Nat.add(Nat.add(y, Nat.double(pb)), nB), hb))
  N.cancel(env, gl, gr, cc, pl(pl(pl(pl(ml, db(il)), fr), ar), br), pl(pl(pl(pl(mr, db(ir)), fl), al), bl), e4, {==}, {==})

def adc_ee(s: Bool, t: Bool, c: Bool) -> ADC(I.End{s}, I.End{t}, c, I.adc.ee(s, t, c)):
  match s t c:
    case False{} False{} False{}:
      {==}
    case False{} False{} True{}:
      {==}
    case False{} True{} False{}:
      {==}
    case False{} True{} True{}:
      {==}
    case True{} False{} False{}:
      {==}
    case True{} False{} True{}:
      {==}
    case True{} True{} False{}:
      {==}
    case True{} True{} True{}:
      {==}

def adc_e(+s: Bool, b: I.Int, +c: Bool) -> ADC(I.End{s}, b, c, I.adc.e(s, b, c)):
  match b:
    case I.End{t}:
      adc_ee(s, t, c)
    case I.Bit{+y, +r}:
      +k = I.maj(s, y, c)
      +sm = I.sum3(s, y, c)
      +r2 = I.adc.e(s, r, k)
      step(vp(I.mk(sm, r2)), vn(I.mk(sm, r2)), vp(r2), vn(r2), 0n, b2n(s), vp(I.Bit{y, r}), vn(I.Bit{y, r}), 0n, b2n(s), vp(r), vn(r), b2n(s), b2n(y), b2n(c), b2n(sm), b2n(k),
        mk_ok(sm, r2), adc_e(s, r, k), full_add(s, y, c), end_bit(s), {==})

def adc_be(a: I.Int, +s: Bool, +c: Bool) -> ADC(a, I.End{s}, c, I.adc.be(a, s, c)):
  match a:
    case I.End{t}:
      adc_ee(t, s, c)
    case I.Bit{+x, +r}:
      +k = I.maj(x, s, c)
      +sm = I.sum3(x, s, c)
      +r2 = I.adc.be(r, s, k)
      step(vp(I.mk(sm, r2)), vn(I.mk(sm, r2)), vp(r2), vn(r2), vp(I.Bit{x, r}), vn(I.Bit{x, r}), 0n, b2n(s), vp(r), vn(r), 0n, b2n(s), b2n(x), b2n(s), b2n(c), b2n(sm), b2n(k),
        mk_ok(sm, r2), adc_be(r, s, k), full_add(x, s, c), {==}, end_bit(s))

# adc(a, b, c) is a + b + c
def adc_ok(a: I.Int, b: I.Int, +c: Bool) -> ADC(a, b, c, I.adc(a, b, c)):
  match a b:
    case I.End{s} _:
      adc_e(s, b, c)
    case I.Bit{+x, +r} I.End{s}:
      adc_be(I.Bit{x, r}, s, c)
    case I.Bit{+x, +r} I.Bit{+y, +q}:
      +k = I.maj(x, y, c)
      +sm = I.sum3(x, y, c)
      +r2 = I.adc(r, q, k)
      step(vp(I.mk(sm, r2)), vn(I.mk(sm, r2)), vp(r2), vn(r2), vp(I.Bit{x, r}), vn(I.Bit{x, r}), vp(I.Bit{y, q}), vn(I.Bit{y, q}), vp(r), vn(r), vp(q), vn(q), b2n(x), b2n(y), b2n(c), b2n(sm), b2n(k),
        mk_ok(sm, r2), adc_ok(r, q, k), full_add(x, y, c), {==}, {==})

# not(a) is -1 - a
def not_ok(a: I.Int) -> EqZ(vp(I.not(a)), vn(I.not(a)), vn(a), 1n+vp(a)):
  match a:
    case I.End{False{}}:
      {==}
    case I.End{True{}}:
      {==}
    case I.Bit{+x, +r}:
      +env = {[vp(I.not(r)), vn(I.not(r)), vp(r), vn(r), b2n(Bool.not(x)), b2n(x)] : List<&2, Nat>}
      +il = pl(va(0n), N.S1{va(2n)})
      +ir = pl(va(3n), va(1n))
      +e = N.add_eq(Nat.double(N.ev(env, il)), Nat.double(N.ev(env, ir)), Nat.add(b2n(Bool.not(x)), b2n(x)), 1n, N.double_eq(N.ev(env, il), N.ev(env, ir), not_ok(r)), not_bit(x))
      N.cancel(env, pl(pl(va(4n), db(va(0n))), N.S1{pl(va(5n), db(va(2n)))}), pl(db(va(3n)), db(va(1n))), one(), pl(db(il), pl(va(4n), va(5n))), pl(db(ir), one()), e, {==}, {==})

# neg(a) is -a
def neg_ok(+a: I.Int) -> EqZ(vp(I.neg(a)), vn(I.neg(a)), vn(a), vp(a)):
  +n = I.neg(a)
  +na = I.not(a)
  +env = {[vp(n), vn(n), vp(na), vn(na), vp(a), vn(a)] : List<&2, Nat>}
  +al = pl(va(0n), pl(va(3n), zz()))
  +ar = pl(pl(pl(va(2n), zz()), one()), va(1n))
  +tl = pl(va(2n), N.S1{va(4n)})
  +tr = pl(va(5n), va(3n))
  +e = N.add_eq(N.ev(env, al), N.ev(env, ar), N.ev(env, tl), N.ev(env, tr), adc_ok(na, I.End{False{}}, True{}), not_ok(a))
  N.cancel(env, pl(va(0n), va(4n)), pl(va(5n), va(1n)), pl(va(3n), N.S1{va(2n)}), pl(al, tl), pl(ar, tr), e, {==}, {==})

# sub(a, b) is a - b
def sub_ok(+a: I.Int, +b: I.Int) -> EqZ(vp(I.sub(a, b)), vn(I.sub(a, b)), Nat.add(vp(a), vn(b)), Nat.add(vn(a), vp(b))):
  +n = I.neg(b)
  +s = I.sub(a, b)
  +env = {[vp(s), vn(s), vp(a), vn(a), vp(b), vn(b), vp(n), vn(n)] : List<&2, Nat>}
  +al = pl(va(0n), pl(va(3n), va(7n)))
  +ar = pl(pl(pl(va(2n), va(6n)), zz()), va(1n))
  +nl = pl(va(6n), va(4n))
  +nr = pl(va(5n), va(7n))
  +e = N.add_eq(N.ev(env, al), N.ev(env, ar), N.ev(env, nl), N.ev(env, nr), adc_ok(a, n, False{}), neg_ok(b))
  N.cancel(env, pl(va(0n), pl(va(3n), va(4n))), pl(pl(va(2n), va(5n)), va(1n)), pl(va(7n), va(6n)), pl(al, nl), pl(ar, nr), e, {==}, {==})

# add(a, b) is a + b
def add_ok(+a: I.Int, +b: I.Int) -> EqZ(vp(I.add(a, b)), vn(I.add(a, b)), Nat.add(Nat.add(vp(a), vp(b)), 0n), Nat.add(vn(a), vn(b))):
  adc_ok(a, b, False{})

# Zero and equality
# -----------------

# is_zero(a) answers True only on the value 0
def iz_val(a: I.Int, h: {True{} == I.is_zero(a) : Bool}) -> EqZ(vp(a), vn(a), 0n, 0n):
  match a:
    case I.End{False{}}:
      {==}
    case I.End{True{}}:
      efq(EqZ(0n, 1n, 0n, 0n), bool_disc(h))
    case I.Bit{False{}, +r}:
      +env = {[vp(r), vn(r)] : List<&2, Nat>}
      +e = N.double_eq(Nat.add(vp(r), 0n), vn(r), iz_val(r, h))
      N.extend(env, pl(db(va(0n)), zz()), db(va(1n)), zz(), db(pl(va(0n), zz())), db(va(1n)), e, {==}, {==})
    case I.Bit{True{}, r}:
      efq(EqZ(vp(I.Bit{True{}, r}), vn(I.Bit{True{}, r}), 0n, 0n), bool_disc(h))

# and on every representation of 0
def val_iz(a: I.Int, h: EqZ(vp(a), vn(a), 0n, 0n)) -> {True{} == I.is_zero(a) : Bool}:
  match a:
    case I.End{False{}}:
      {==}
    case I.End{True{}}:
      efq({True{} == False{} : Bool}, nat_disc(0n, Equal.sym(Nat, 0n, 1n, h)))
    case I.Bit{False{}, +r}:
      +d = Equal.trans(Nat, Nat.double(vp(r)), Nat.add(Nat.double(vp(r)), 0n), Nat.double(vn(r)), N.sy(Nat.add(Nat.double(vp(r)), 0n), Nat.double(vp(r)), N.add_zero(Nat.double(vp(r)))), h)
      val_iz(r, Equal.trans(Nat, Nat.add(vp(r), 0n), vp(r), vn(r), N.add_zero(vp(r)), double_inj(vp(r), vn(r), d)))
    case I.Bit{True{}, +r}:
      +z = N.sy(Nat.add(Nat.double(vp(r)), 0n), Nat.double(vp(r)), N.add_zero(Nat.double(vp(r))))
      +o = Equal.trans(Nat, 1n+Nat.double(vp(r)), 1n+Nat.add(Nat.double(vp(r)), 0n), Nat.double(vn(r)), Equal.cong(Nat, Nat, k => 1n+k, Nat.double(vp(r)), Nat.add(Nat.double(vp(r)), 0n), z), h)
      efq({True{} == False{} : Bool}, odd_even(vp(r), vn(r), o))

# is_eq decides equality of values
def is_eq_ok(+a: I.Int, +b: I.Int, h: Same(a, b)) -> {True{} == I.is_eq(a, b) : Bool}:
  +s = I.sub(a, b)
  +env = {[vp(s), vn(s), vp(a), vn(a), vp(b), vn(b)] : List<&2, Nat>}
  +sl = pl(va(0n), pl(va(3n), va(4n)))
  +sr = pl(pl(va(2n), va(5n)), va(1n))
  +hl = pl(va(2n), va(5n))
  +hr = pl(va(4n), va(3n))
  +e = N.add_eq(N.ev(env, sl), N.ev(env, sr), N.ev(env, hl), N.ev(env, hr), sub_ok(a, b), h)
  val_iz(s, N.cancel(env, pl(va(0n), zz()), pl(zz(), va(1n)), pl(pl(va(3n), va(4n)), pl(va(2n), va(5n))), pl(sl, hl), pl(sr, hr), e, {==}, {==}))

# Same is an equivalence
def same_refl(+a: I.Int) -> Same(a, a):
  {==}

def eqz_trans(+p1: Nat, +n1: Nat, +p2: Nat, +n2: Nat, +p3: Nat, +n3: Nat, h12: EqZ(p1, n1, p2, n2), h23: EqZ(p2, n2, p3, n3)) -> EqZ(p1, n1, p3, n3):
  +env = {[p1, n1, p2, n2, p3, n3] : List<&2, Nat>}
  +e = N.add_eq(Nat.add(p1, n2), Nat.add(p2, n1), Nat.add(p2, n3), Nat.add(p3, n2), h12, h23)
  N.cancel(env, pl(va(0n), va(5n)), pl(va(4n), va(1n)), pl(va(3n), va(2n)), pl(pl(va(0n), va(3n)), pl(va(2n), va(5n))), pl(pl(va(2n), va(1n)), pl(va(4n), va(3n))), e, {==}, {==})

def eqz_sym(-p1: Nat, -n1: Nat, +p2: Nat, +n2: Nat, h: EqZ(p1, n1, p2, n2)) -> EqZ(p2, n2, p1, n1):
  Equal.sym(Nat, Nat.add(p1, n2), Nat.add(p2, n1), h)
