# proof_nat.bend: Nat algebra for the proofs (pure; no runtime code uses
# it). Base's Nat is unary, which is fine here: these terms are erased.
#
# The workhorse is `solve`, a reflective decision procedure for sums:
# an expression E over atoms (any Nat terms listed in an environment)
# built from +, double, 0 and 1+ is flattened to a sorted bag of atoms
# and a constant; two expressions with the same normal form are equal
# for every value of the atoms. A goal {X == Y : Nat} whose sides are
# sums is then `solve(env, eX, eY, {==})`. `cancel` removes a common
# summand from both sides of a hypothesis.

import Base

# Basic lemmas
# ------------

def add_zero(a: Nat) -> {Nat.add(a, 0n) == a : Nat}:
  match a:
    case 0n:
      {==}
    case 1n+p:
      %add_zero(p) : {1n+Nat.add(p, 0n) == 1n+_ : Nat}
      {==}

def add_succ(a: Nat, -b: Nat) -> {Nat.add(a, 1n+b) == 1n+Nat.add(a, b) : Nat}:
  match a:
    case 0n:
      {==}
    case 1n+p:
      %add_succ(p, b) : {1n+Nat.add(p, 1n+b) == 1n+_ : Nat}
      {==}

def sy(-a: Nat, -b: Nat, e: {a == b : Nat}) -> {b == a : Nat}:
  Equal.sym(Nat, a, b, e)

def add_comm(a: Nat, +b: Nat) -> {Nat.add(a, b) == Nat.add(b, a) : Nat}:
  match a:
    case 0n:
      sy(Nat.add(b, 0n), b, add_zero(b))
    case 1n+p:
      %sy(Nat.add(b, 1n+p), 1n+Nat.add(b, p), add_succ(b, p)) : {1n+Nat.add(p, b) == _ : Nat}
      %add_comm(p, b) : {1n+Nat.add(p, b) == 1n+_ : Nat}
      {==}

def add_assoc(a: Nat, -b: Nat, -c: Nat) -> {Nat.add(Nat.add(a, b), c) == Nat.add(a, Nat.add(b, c)) : Nat}:
  match a:
    case 0n:
      {==}
    case 1n+p:
      %add_assoc(p, b, c) : {1n+Nat.add(Nat.add(p, b), c) == 1n+_ : Nat}
      {==}

def double_add(a: Nat) -> {Nat.double(a) == Nat.add(a, a) : Nat}:
  match a:
    case 0n:
      {==}
    case 1n++p:
      %sy(Nat.add(p, 1n+p), 1n+Nat.add(p, p), add_succ(p, p)) : {2n+Nat.double(p) == 1n+_ : Nat}
      %double_add(p) : {2n+Nat.double(p) == 2n+_ : Nat}
      {==}

def pred(n: Nat) -> Nat:
  match n:
    case 0n:
      0n
    case 1n+p:
      p

def succ_inj(-a: Nat, -b: Nat, e: {1n+a == 1n+b : Nat}) -> {a == b : Nat}:
  Equal.cong(Nat, Nat, n => pred(n), 1n+a, 1n+b, e)

def cancel_l(c: Nat, -a: Nat, -b: Nat, e: {Nat.add(c, a) == Nat.add(c, b) : Nat}) -> {a == b : Nat}:
  match c:
    case 0n:
      e
    case 1n+p:
      cancel_l(p, a, b, succ_inj(Nat.add(p, a), Nat.add(p, b), e))

def add_eq(-a: Nat, -b: Nat, -c: Nat, -d: Nat, e1: {a == b : Nat}, e2: {c == d : Nat}) -> {Nat.add(a, c) == Nat.add(b, d) : Nat}:
  %e1 : {Nat.add(a, c) == Nat.add(_, d) : Nat}
  %e2 : {Nat.add(a, c) == Nat.add(a, _) : Nat}
  {==}

def double_eq(-a: Nat, -b: Nat, e: {a == b : Nat}) -> {Nat.double(a) == Nat.double(b) : Nat}:
  Equal.cong(Nat, Nat, n => Nat.double(n), a, b, e)

# Reflection
# ----------

type E is Data:
  At{i: Nat}
  Ad{a: E, b: E}
  Db{a: E}
  Z0{}
  S1{a: E}

def nth(env: List<&2, Nat>, i: Nat) -> Nat:
  match env:
    case Nil{}:
      0n
    case h <> t:
      match i:
        case 0n:
          h
        case 1n+p:
          nth(t, p)

def ev(+env: List<&2, Nat>, e: E) -> Nat:
  match e:
    case At{+i}:
      nth(env, i)
    case Ad{+a, +b}:
      Nat.add(ev(env, a), ev(env, b))
    case Db{+a}:
      Nat.double(ev(env, a))
    case Z0{}:
      0n
    case S1{+a}:
      1n+ev(env, a)

type NF is Data:
  NF{bag: List<&2, Nat>, k: Nat}

def nf_add(x: NF, y: NF) -> NF:
  match x y:
    case NF{b1, k1} NF{b2, k2}:
      NF{List.append(&2, Nat, b1, b2), Nat.add(k1, k2)}

def nf_succ(x: NF) -> NF:
  match x:
    case NF{+b, +k}:
      NF{b, 1n+k}

def flat(e: E) -> NF:
  match e:
    case At{+i}:
      NF{[i], 0n}
    case Ad{+a, +b}:
      nf_add(flat(a), flat(b))
    case Db{+a}:
      +f = flat(a)
      nf_add(f, f)
    case Z0{}:
      NF{[], 0n}
    case S1{+a}:
      nf_succ(flat(a))

def ins.if(i: Nat, h: Nat, t: List<&2, Nat>, rest: List<&2, Nat>, le: Bool) -> List<&2, Nat>:
  match le:
    case True{}:
      i <> h <> t
    case False{}:
      h <> rest

def ins(+i: Nat, xs: List<&2, Nat>) -> List<&2, Nat>:
  match xs:
    case Nil{}:
      [i]
    case +h <> +t:
      ins.if(i, h, t, ins(i, t), Nat.is_le(i, h))

def sort(xs: List<&2, Nat>) -> List<&2, Nat>:
  match xs:
    case Nil{}:
      Nil{}
    case h <> t:
      ins(h, sort(t))

def nf_sort(x: NF) -> NF:
  match x:
    case NF{+b, +k}:
      NF{sort(b), k}

def norm(e: E) -> NF:
  nf_sort(flat(e))

def evb(+env: List<&2, Nat>, xs: List<&2, Nat>) -> Nat:
  match xs:
    case Nil{}:
      0n
    case h <> t:
      Nat.add(nth(env, h), evb(env, t))

def evn(+env: List<&2, Nat>, x: NF) -> Nat:
  match x:
    case NF{+b, +k}:
      Nat.add(k, evb(env, b))

# soundness
# ---------

def evb_app(+env: List<&2, Nat>, xs: List<&2, Nat>, -ys: List<&2, Nat>) -> {evb(env, List.append(&2, Nat, xs, ys)) == Nat.add(evb(env, xs), evb(env, ys)) : Nat}:
  match xs:
    case Nil{}:
      {==}
    case +h <> +t:
      %sy(evb(env, List.append(&2, Nat, t, ys)), Nat.add(evb(env, t), evb(env, ys)), evb_app(env, t, ys)) : {Nat.add(nth(env, h), _) == Nat.add(Nat.add(nth(env, h), evb(env, t)), evb(env, ys)) : Nat}
      Equal.sym(Nat, Nat.add(Nat.add(nth(env, h), evb(env, t)), evb(env, ys)), Nat.add(nth(env, h), Nat.add(evb(env, t), evb(env, ys))), add_assoc(nth(env, h), evb(env, t), evb(env, ys)))

# x + (y + z) == y + (x + z)
def add_swap(+x: Nat, +y: Nat, +z: Nat) -> {Nat.add(x, Nat.add(y, z)) == Nat.add(y, Nat.add(x, z)) : Nat}:
  %add_assoc(x, y, z) : {_ == Nat.add(y, Nat.add(x, z)) : Nat}
  %add_assoc(y, x, z) : {Nat.add(Nat.add(x, y), z) == _ : Nat}
  %add_comm(x, y) : {Nat.add(Nat.add(x, y), z) == Nat.add(_, z) : Nat}
  {==}

# (a + b) + (c + d) == (a + c) + (b + d)
def add_inner(+a: Nat, +b: Nat, +c: Nat, +d: Nat) -> {Nat.add(Nat.add(a, b), Nat.add(c, d)) == Nat.add(Nat.add(a, c), Nat.add(b, d)) : Nat}:
  %sy(Nat.add(Nat.add(a, b), Nat.add(c, d)), Nat.add(a, Nat.add(b, Nat.add(c, d))), add_assoc(a, b, Nat.add(c, d))) : {_ == Nat.add(Nat.add(a, c), Nat.add(b, d)) : Nat}
  %sy(Nat.add(Nat.add(a, c), Nat.add(b, d)), Nat.add(a, Nat.add(c, Nat.add(b, d))), add_assoc(a, c, Nat.add(b, d))) : {Nat.add(a, Nat.add(b, Nat.add(c, d))) == _ : Nat}
  %add_swap(b, c, d) : {Nat.add(a, Nat.add(b, Nat.add(c, d))) == Nat.add(a, _) : Nat}
  {==}

def evn_add(+env: List<&2, Nat>, x: NF, y: NF) -> {evn(env, nf_add(x, y)) == Nat.add(evn(env, x), evn(env, y)) : Nat}:
  match x y:
    case NF{+b1, +k1} NF{+b2, +k2}:
      %sy(evb(env, List.append(&2, Nat, b1, b2)), Nat.add(evb(env, b1), evb(env, b2)), evb_app(env, b1, b2)) : {Nat.add(Nat.add(k1, k2), _) == Nat.add(Nat.add(k1, evb(env, b1)), Nat.add(k2, evb(env, b2))) : Nat}
      add_inner(k1, k2, evb(env, b1), evb(env, b2))

def flat_ok.succ(+env: List<&2, Nat>, x: NF, -v: Nat, e: {v == evn(env, x) : Nat}) -> {1n+v == evn(env, nf_succ(x)) : Nat}:
  match x:
    case NF{+b, +k}:
      %e : {1n+v == 1n+_ : Nat}
      {==}

def flat_ok(+env: List<&2, Nat>, e: E) -> {ev(env, e) == evn(env, flat(e)) : Nat}:
  match e:
    case At{+i}:
      Equal.sym(Nat, Nat.add(nth(env, i), 0n), nth(env, i), add_zero(nth(env, i)))
    case Ad{+a, +b}:
      %sy(evn(env, nf_add(flat(a), flat(b))), Nat.add(evn(env, flat(a)), evn(env, flat(b))), evn_add(env, flat(a), flat(b))) : {Nat.add(ev(env, a), ev(env, b)) == _ : Nat}
      add_eq(ev(env, a), evn(env, flat(a)), ev(env, b), evn(env, flat(b)), flat_ok(env, a), flat_ok(env, b))
    case Db{+a}:
      %sy(evn(env, nf_add(flat(a), flat(a))), Nat.add(evn(env, flat(a)), evn(env, flat(a))), evn_add(env, flat(a), flat(a))) : {Nat.double(ev(env, a)) == _ : Nat}
      %sy(Nat.double(ev(env, a)), Nat.add(ev(env, a), ev(env, a)), double_add(ev(env, a))) : {_ == Nat.add(evn(env, flat(a)), evn(env, flat(a))) : Nat}
      add_eq(ev(env, a), evn(env, flat(a)), ev(env, a), evn(env, flat(a)), flat_ok(env, a), flat_ok(env, a))
    case Z0{}:
      {==}
    case S1{+a}:
      flat_ok.succ(env, flat(a), ev(env, a), flat_ok(env, a))

def ins_ok.fin(+env: List<&2, Nat>, +i: Nat, +h: Nat, +t: List<&2, Nat>, +rest: List<&2, Nat>, rec: {evb(env, rest) == Nat.add(nth(env, i), evb(env, t)) : Nat}, le: Bool)
  -> {evb(env, ins.if(i, h, t, rest, le)) == Nat.add(nth(env, i), Nat.add(nth(env, h), evb(env, t))) : Nat}:
  match le:
    case True{}:
      {==}
    case False{}:
      %sy(evb(env, rest), Nat.add(nth(env, i), evb(env, t)), rec) : {Nat.add(nth(env, h), _) == Nat.add(nth(env, i), Nat.add(nth(env, h), evb(env, t))) : Nat}
      add_swap(nth(env, h), nth(env, i), evb(env, t))

def ins_ok(+env: List<&2, Nat>, +i: Nat, +xs: List<&2, Nat>) -> {evb(env, ins(i, xs)) == Nat.add(nth(env, i), evb(env, xs)) : Nat}:
  match xs:
    case Nil{}:
      {==}
    case +h <> +t:
      ins_ok.fin(env, i, h, t, ins(i, t), ins_ok(env, i, t), Nat.is_le(i, h))

def sort_ok(+env: List<&2, Nat>, xs: List<&2, Nat>) -> {evb(env, sort(xs)) == evb(env, xs) : Nat}:
  match xs:
    case Nil{}:
      {==}
    case +h <> +t:
      %sy(evb(env, ins(h, sort(t))), Nat.add(nth(env, h), evb(env, sort(t))), ins_ok(env, h, sort(t))) : {_ == Nat.add(nth(env, h), evb(env, t)) : Nat}
      %sort_ok(env, t) : {Nat.add(nth(env, h), evb(env, sort(t))) == Nat.add(nth(env, h), _) : Nat}
      {==}

def nf_sort_ok(+env: List<&2, Nat>, x: NF) -> {evn(env, nf_sort(x)) == evn(env, x) : Nat}:
  match x:
    case NF{+b, +k}:
      %sort_ok(env, b) : {Nat.add(k, evb(env, sort(b))) == Nat.add(k, _) : Nat}
      {==}

def norm_ok(+env: List<&2, Nat>, +e: E) -> {ev(env, e) == evn(env, norm(e)) : Nat}:
  %sy(evn(env, nf_sort(flat(e))), evn(env, flat(e)), nf_sort_ok(env, flat(e))) : {ev(env, e) == _ : Nat}
  flat_ok(env, e)

# ev(env, a) == ev(env, b) whenever a and b have the same normal form
def solve(+env: List<&2, Nat>, +a: E, +b: E, h: {norm(a) == norm(b) : NF}) -> {ev(env, a) == ev(env, b) : Nat}:
  %sy(ev(env, a), evn(env, norm(a)), norm_ok(env, a)) : {_ == ev(env, b) : Nat}
  %Equal.sym(NF, norm(a), norm(b), h) : {evn(env, _) == ev(env, b) : Nat}
  Equal.sym(Nat, ev(env, b), evn(env, norm(b)), norm_ok(env, b))

# from x == y, where x and y are a + c and b + c up to normal form: a == b
def cancel(+env: List<&2, Nat>, +a: E, +b: E, +c: E, +x: E, +y: E, e: {ev(env, x) == ev(env, y) : Nat}, hx: {norm(x) == norm(Ad{c, a}) : NF}, hy: {norm(y) == norm(Ad{c, b}) : NF}) -> {ev(env, a) == ev(env, b) : Nat}:
  +l = Equal.trans(Nat, ev(env, Ad{c, a}), ev(env, x), ev(env, y), Equal.sym(Nat, ev(env, x), ev(env, Ad{c, a}), solve(env, x, Ad{c, a}, hx)), e)
  +r = Equal.trans(Nat, ev(env, Ad{c, a}), ev(env, y), ev(env, Ad{c, b}), l, solve(env, y, Ad{c, b}, hy))
  cancel_l(ev(env, c), ev(env, a), ev(env, b), r)

# from x == y, where a and b are x + c and y + c up to normal form: a == b
def extend(+env: List<&2, Nat>, +a: E, +b: E, +c: E, +x: E, +y: E, e: {ev(env, x) == ev(env, y) : Nat}, ha: {norm(a) == norm(Ad{x, c}) : NF}, hb: {norm(b) == norm(Ad{y, c}) : NF}) -> {ev(env, a) == ev(env, b) : Nat}:
  +m = add_eq(ev(env, x), ev(env, y), ev(env, c), ev(env, c), e, {==})
  +l = Equal.trans(Nat, ev(env, a), ev(env, Ad{x, c}), ev(env, Ad{y, c}), solve(env, a, Ad{x, c}, ha), m)
  Equal.trans(Nat, ev(env, a), ev(env, Ad{y, c}), ev(env, b), l, Equal.sym(Nat, ev(env, b), ev(env, Ad{y, c}), solve(env, b, Ad{y, c}, hb)))
