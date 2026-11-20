# PROOF.bend: proofs of LAWS.bend. Pure: it imports the runtime modules,
# the laws and the two proof libraries (proof_nat.bend: Nat algebra and
# the `solve`/`cancel` sum reflection; proof_int.bend: int.bend's add,
# neg, sub and is_eq against the value vp - vn). No IO, no @unsafe, no
# foreign code, so `bend PROOF.bend --verdict` rechecks everything.

import Base
import ./int.bend as I
import ./ledger.bend as L
import ./rules.bend as R
import ./proof_nat.bend as N
import ./proof_int.bend as P
import ./LAWS.bend as Laws

# Shorthands
# ----------

def va(i: Nat) -> N.E:
  N.At{i}

def pl(a: N.E, b: N.E) -> N.E:
  N.Ad{a, b}

def zz() -> N.E:
  N.Z0{}

# two equations over the same atoms, added side by side
def ae(+env: List<&2, Nat>, +x1: N.E, +y1: N.E, +x2: N.E, +y2: N.E, e1: {N.ev(env, x1) == N.ev(env, y1) : Nat}, e2: {N.ev(env, x2) == N.ev(env, y2) : Nat}) -> {N.ev(env, pl(x1, x2)) == N.ev(env, pl(y1, y2)) : Nat}:
  N.add_eq(N.ev(env, x1), N.ev(env, y1), N.ev(env, x2), N.ev(env, y2), e1, e2)

def fl(+env: List<&2, Nat>, +x: N.E, +y: N.E, e: {N.ev(env, x) == N.ev(env, y) : Nat}) -> {N.ev(env, y) == N.ev(env, x) : Nat}:
  Equal.sym(Nat, N.ev(env, x), N.ev(env, y), e)

# x has the value P - N
def Val(+x: I.Int, p: Nat, n: Nat) -> Type:
  P.EqZ(P.vp(x), P.vn(x), p, n)

def bool_disc(e: {True{} == False{} : Bool}) -> Empty:
  P.bool_disc(e)

# L1: lifecycle
# -------------

def Laws.terminal_no_successor(s, e):
  match s:
    case R.Settled{}:
      {==}
    case R.Expired{}:
      {==}
    case R.Withdrawn{}:
      {==}
    case R.Offered{}:
      P.efq({R.transitions(R.Offered{}) == Nil{} : List<&2, R.Status>}, bool_disc(e))
    case R.Countered{}:
      P.efq({R.transitions(R.Countered{}) == Nil{} : List<&2, R.Status>}, bool_disc(e))
    case R.Signed{}:
      P.efq({R.transitions(R.Signed{}) == Nil{} : List<&2, R.Status>}, bool_disc(e))
    case R.Active{}:
      P.efq({R.transitions(R.Active{}) == Nil{} : List<&2, R.Status>}, bool_disc(e))

def Laws.terminal_no_move(s, t, e):
  match s:
    case R.Settled{}:
      {==}
    case R.Expired{}:
      {==}
    case R.Withdrawn{}:
      {==}
    case R.Offered{}:
      P.efq({False{} == R.can_move(R.Offered{}, t) : Bool}, bool_disc(e))
    case R.Countered{}:
      P.efq({False{} == R.can_move(R.Countered{}, t) : Bool}, bool_disc(e))
    case R.Signed{}:
      P.efq({False{} == R.can_move(R.Signed{}, t) : Bool}, bool_disc(e))
    case R.Active{}:
      P.efq({False{} == R.can_move(R.Active{}, t) : Bool}, bool_disc(e))

def done_status(r: Result<&2, &2, String, R.Status>) -> R.Status:
  match r:
    case Fail{e}:
      R.Offered{}
    case Done{x}:
      x

def IsDoneS(r: Result<&2, &2, String, R.Status>) -> Type:
  match r:
    case Fail{e}:
      Empty
    case Done{x}:
      Unit

def mv.ok(-old: R.Status, -new: R.Status, -s: R.Status, ev: {True{} == R.can_move(old, new) : Bool}, eq: {new == s : R.Status}) -> {True{} == R.can_move(old, s) : Bool}:
  %eq : {True{} == R.can_move(old, _) : Bool}
  ev

def fail_done(-s: R.Status, e: {Fail{"bad_transition"} == Done{s} : Result<&2, &2, String, R.Status>}) -> Empty:
  %Equal.sym(Result<&2, &2, String, R.Status>, Fail{"bad_transition"}, Done{s}, e) : IsDoneS(_)
  Unit{}

def mv(+old: R.Status, +new: R.Status, +s: R.Status, b: Bool, ev: {b == R.can_move(old, new) : Bool}, e: {R.move.if(new, b) == Done{s} : Result<&2, &2, String, R.Status>})
  -> {True{} == R.can_move(old, s) : Bool} & {s == new : R.Status}:
  match b:
    case True{}:
      +eq = Equal.cong(Result<&2, &2, String, R.Status>, R.Status, r => done_status(r), Done{new}, Done{s}, e)
      (mv.ok(old, new, s, ev, eq), Equal.sym(R.Status, new, s, eq))
    case False{}:
      P.efq({True{} == R.can_move(old, s) : Bool} & {s == new : R.Status}, fail_done(s, e))

def Laws.move_allowed(old, new, s, e):
  mv(old, new, s, R.can_move(old, new), {==}, e)

# L2: outcomes
# ------------

def Laws.outcome_kept(d, q, e):
  %e : {R.outcome_for.go(_, I.is_zero(d)) == R.Kept{} : R.Outcome}
  {==}

def Laws.outcome_broken(d, q, e1, e2):
  %e1 : {R.outcome_for.go(_, I.is_zero(d)) == R.Broken{} : R.Outcome}
  %e2 : {R.outcome_for.go(True{}, _) == R.Broken{} : R.Outcome}
  {==}

def Laws.outcome_partial(d, q, e1, e2):
  %e1 : {R.outcome_for.go(_, I.is_zero(d)) == R.Partial{} : R.Outcome}
  %e2 : {R.outcome_for.go(True{}, _) == R.Partial{} : R.Outcome}
  {==}

# Integer algebra
# ---------------

# a + b has the value Pa + Pb - (Na + Nb) when a and b have Pa - Na and Pb - Nb
def add_val(+a: I.Int, +b: I.Int, +pa: Nat, +na: Nat, +pb: Nat, +nb: Nat, ha: Val(a, pa, na), hb: Val(b, pb, nb)) -> Val(I.add(a, b), Nat.add(pa, pb), Nat.add(na, nb)):
  +s = I.add(a, b)
  +env = {[P.vp(s), P.vn(s), P.vp(a), P.vn(a), P.vp(b), P.vn(b), pa, na, pb, nb] : List<&2, Nat>}
  +al = pl(va(0n), pl(va(3n), va(5n)))
  +ar = pl(pl(pl(va(2n), va(4n)), zz()), va(1n))
  +hal = pl(va(2n), va(7n))
  +har = pl(va(6n), va(3n))
  +hbl = pl(va(4n), va(9n))
  +hbr = pl(va(8n), va(5n))
  e = ae(env, pl(al, hal), pl(ar, har), hbl, hbr, ae(env, al, ar, hal, har, P.add_ok(a, b), ha), hb)
  N.cancel(env, pl(va(0n), pl(va(7n), va(9n))), pl(pl(va(6n), va(8n)), va(1n)), pl(va(3n), pl(va(5n), pl(va(2n), va(4n)))), pl(pl(al, hal), hbl), pl(pl(ar, har), hbr), e, {==}, {==})

def Laws.is_eq_complete(a, b, h):
  P.is_eq_ok(a, b, h)

def Laws.is_eq_sound(a, b, e):
  +s = I.sub(a, b)
  +env = {[P.vp(s), P.vn(s), P.vp(a), P.vn(a), P.vp(b), P.vn(b)] : List<&2, Nat>}
  +sl = pl(va(0n), pl(va(3n), va(4n)))
  +sr = pl(pl(va(2n), va(5n)), va(1n))
  +zl = pl(va(0n), zz())
  +zr = pl(zz(), va(1n))
  e2 = ae(env, sr, sl, zl, zr, fl(env, sl, sr, P.sub_ok(a, b)), P.iz_val(s, e))
  N.cancel(env, pl(va(2n), va(5n)), pl(va(4n), va(3n)), pl(va(1n), va(0n)), pl(sr, zl), pl(sl, zr), e2, {==}, {==})

def Laws.add_value(a, b):
  add_val(a, b, P.vp(a), P.vn(a), P.vp(b), P.vn(b), {==}, {==})

def Laws.sub_value(a, b):
  P.sub_ok(a, b)

def Laws.neg_value(a):
  P.neg_ok(a)

def Laws.eq_refl(a):
  P.is_eq_ok(a, a, {==})

def Laws.add_comm(a, b):
  +x = I.add(a, b)
  +y = I.add(b, a)
  +env = {[P.vp(x), P.vn(x), P.vp(y), P.vn(y), P.vp(a), P.vn(a), P.vp(b), P.vn(b)] : List<&2, Nat>}
  +xl = pl(va(0n), pl(va(5n), va(7n)))
  +xr = pl(pl(pl(va(4n), va(6n)), zz()), va(1n))
  +yl = pl(va(2n), pl(va(7n), va(5n)))
  +yr = pl(pl(pl(va(6n), va(4n)), zz()), va(3n))
  e = ae(env, xl, xr, yr, yl, P.add_ok(a, b), fl(env, yl, yr, P.add_ok(b, a)))
  P.is_eq_ok(x, y, N.cancel(env, pl(va(0n), va(3n)), pl(va(2n), va(1n)), pl(va(5n), pl(va(7n), pl(va(4n), va(6n)))), pl(xl, yr), pl(xr, yl), e, {==}, {==}))

def Laws.add_assoc(a, b, c):
  +x1 = I.add(a, b)
  +x = I.add(x1, c)
  +y1 = I.add(b, c)
  +y = I.add(a, y1)
  +env = {[P.vp(x), P.vn(x), P.vp(y), P.vn(y), P.vp(x1), P.vn(x1), P.vp(y1), P.vn(y1), P.vp(a), P.vn(a), P.vp(b), P.vn(b), P.vp(c), P.vn(c)] : List<&2, Nat>}
  +a1l = pl(va(4n), pl(va(9n), va(11n)))
  +a1r = pl(pl(pl(va(8n), va(10n)), zz()), va(5n))
  +a2l = pl(va(0n), pl(va(5n), va(13n)))
  +a2r = pl(pl(pl(va(4n), va(12n)), zz()), va(1n))
  +b1l = pl(va(6n), pl(va(11n), va(13n)))
  +b1r = pl(pl(pl(va(10n), va(12n)), zz()), va(7n))
  +b2l = pl(va(2n), pl(va(9n), va(7n)))
  +b2r = pl(pl(pl(va(8n), va(6n)), zz()), va(3n))
  e1 = ae(env, a1l, a1r, a2l, a2r, P.add_ok(a, b), P.add_ok(x1, c))
  e2 = ae(env, pl(a1l, a2l), pl(a1r, a2r), b1r, b1l, e1, fl(env, b1l, b1r, P.add_ok(b, c)))
  e3 = ae(env, pl(pl(a1l, a2l), b1r), pl(pl(a1r, a2r), b1l), b2r, b2l, e2, fl(env, b2l, b2r, P.add_ok(a, y1)))
  +cc = pl(va(4n), pl(va(9n), pl(va(11n), pl(va(5n), pl(va(13n), pl(va(10n), pl(va(12n), pl(va(7n), pl(va(8n), va(6n))))))))))
  P.is_eq_ok(x, y, N.cancel(env, pl(va(0n), va(3n)), pl(va(2n), va(1n)), cc, pl(pl(pl(a1l, a2l), b1r), b2r), pl(pl(pl(a1r, a2r), b1l), b2l), e3, {==}, {==}))

def Laws.sub_add(a, b):
  +s = I.sub(a, b)
  +x = I.add(s, b)
  +env = {[P.vp(x), P.vn(x), P.vp(s), P.vn(s), P.vp(a), P.vn(a), P.vp(b), P.vn(b)] : List<&2, Nat>}
  +sl = pl(va(2n), pl(va(5n), va(6n)))
  +sr = pl(pl(va(4n), va(7n)), va(3n))
  +xl = pl(va(0n), pl(va(3n), va(7n)))
  +xr = pl(pl(pl(va(2n), va(6n)), zz()), va(1n))
  e = ae(env, sl, sr, xl, xr, P.sub_ok(a, b), P.add_ok(s, b))
  P.is_eq_ok(x, a, N.cancel(env, pl(va(0n), va(5n)), pl(va(4n), va(1n)), pl(va(2n), pl(va(6n), pl(va(3n), va(7n)))), pl(sl, xl), pl(sr, xr), e, {==}, {==}))

# L4: ledger conservation
# -----------------------

def T(l: List<&2, L.Entry>) -> I.Int:
  L.total.go(l)

def adj_if(+k2: String, +v: I.Int, +t: List<&2, L.Entry>, +rest: List<&2, L.Entry>, +d: I.Int, hit: Bool, ih: Val(T(rest), Nat.add(P.vp(T(t)), P.vp(d)), Nat.add(P.vn(T(t)), P.vn(d))))
  -> Val(T(L.adjust.if(k2, v, t, rest, d, hit)), Nat.add(P.vp(I.add(v, T(t))), P.vp(d)), Nat.add(P.vn(I.add(v, T(t))), P.vn(d))):
  match hit:
    case True{}:
      +tt = T(t)
      +sv = I.add(v, tt)
      h3 = add_val(v, tt, P.vp(v), P.vn(v), P.vp(tt), P.vn(tt), {==}, {==})
      +l3 = pl(va(2n), pl(va(5n), va(9n)))
      +r3 = pl(pl(va(4n), va(8n)), va(3n))
      +gl = pl(va(0n), pl(va(3n), va(7n)))
      +gr = pl(pl(va(2n), va(6n)), va(1n))
      +cc = pl(va(5n), pl(va(9n), pl(va(4n), va(8n))))
      +r = I.add(I.add(v, d), tt)
      +env = {[P.vp(r), P.vn(r), P.vp(sv), P.vn(sv), P.vp(v), P.vn(v), P.vp(d), P.vn(d), P.vp(tt), P.vn(tt)] : List<&2, Nat>}
      h1 = add_val(v, d, P.vp(v), P.vn(v), P.vp(d), P.vn(d), {==}, {==})
      h2 = add_val(I.add(v, d), tt, Nat.add(P.vp(v), P.vp(d)), Nat.add(P.vn(v), P.vn(d)), P.vp(tt), P.vn(tt), h1, {==})
      +l2 = pl(va(0n), pl(pl(va(5n), va(7n)), va(9n)))
      +r2 = pl(pl(pl(va(4n), va(6n)), va(8n)), va(1n))
      e = ae(env, l2, r2, r3, l3, h2, fl(env, l3, r3, h3))
      N.cancel(env, gl, gr, cc, pl(l2, r3), pl(r2, l3), e, {==}, {==})
    case False{}:
      +tt = T(t)
      +sv = I.add(v, tt)
      h3 = add_val(v, tt, P.vp(v), P.vn(v), P.vp(tt), P.vn(tt), {==}, {==})
      +l3 = pl(va(2n), pl(va(5n), va(9n)))
      +r3 = pl(pl(va(4n), va(8n)), va(3n))
      +gl = pl(va(0n), pl(va(3n), va(7n)))
      +gr = pl(pl(va(2n), va(6n)), va(1n))
      +cc = pl(va(5n), pl(va(9n), pl(va(4n), va(8n))))
      +r = I.add(v, T(rest))
      +env = {[P.vp(r), P.vn(r), P.vp(sv), P.vn(sv), P.vp(v), P.vn(v), P.vp(d), P.vn(d), P.vp(tt), P.vn(tt)] : List<&2, Nat>}
      h2 = add_val(v, T(rest), P.vp(v), P.vn(v), Nat.add(P.vp(tt), P.vp(d)), Nat.add(P.vn(tt), P.vn(d)), {==}, ih)
      +l2 = pl(va(0n), pl(va(5n), pl(va(9n), va(7n))))
      +r2 = pl(pl(va(4n), pl(va(8n), va(6n))), va(1n))
      e = ae(env, l2, r2, r3, l3, h2, fl(env, l3, r3, h3))
      N.cancel(env, gl, gr, cc, pl(l2, r3), pl(r2, l3), e, {==}, {==})

# adjust(l, k, d) adds d to the sum
def adj_ok(l: List<&2, L.Entry>, +k: String, +d: I.Int) -> Val(T(L.adjust(l, k, d)), Nat.add(P.vp(T(l)), P.vp(d)), Nat.add(P.vn(T(l)), P.vn(d))):
  match l:
    case Nil{}:
      +w = I.add(I.zero(), d)
      +x = I.add(w, I.zero())
      h1 = add_val(I.zero(), d, 0n, 0n, P.vp(d), P.vn(d), {==}, {==})
      h2 = add_val(w, I.zero(), P.vp(d), P.vn(d), 0n, 0n, h1, {==})
      z = N.solve({[P.vp(d), P.vn(d)] : List<&2, Nat>}, pl(pl(va(0n), zz()), va(1n)), pl(va(0n), pl(va(1n), zz())), {==})
      P.eqz_trans(P.vp(x), P.vn(x), Nat.add(P.vp(d), 0n), Nat.add(P.vn(d), 0n), P.vp(d), P.vn(d), h2, z)
    case L.Entry{+k2, +v} <> +t:
      adj_if(k2, v, t, L.adjust(t, k, d), d, String.eq(k2, k), adj_ok(t, k, d))

def Rem(+rm: List<&2, L.Entry>, +g: I.Int, +l: List<&2, L.Entry>) -> Type:
  P.EqZ(Nat.add(P.vp(T(rm)), P.vp(g)), Nat.add(P.vn(T(rm)), P.vn(g)), P.vp(T(l)), P.vn(T(l)))

def rem_if(+k2: String, +v: I.Int, +t: List<&2, L.Entry>, +rest: List<&2, L.Entry>, +g: I.Int, hit: Bool, ih: Rem(rest, g, t))
  -> Rem(L.remove.if(k2, v, t, rest, hit), L.get.if(v, g, hit), L.Entry{k2, v} <> t):
  match hit:
    case True{}:
      +tt = T(t)
      +sv = I.add(v, tt)
      h3 = add_val(v, tt, P.vp(v), P.vn(v), P.vp(tt), P.vn(tt), {==}, {==})
      +env = {[P.vp(sv), P.vn(sv), P.vp(v), P.vn(v), P.vp(tt), P.vn(tt)] : List<&2, Nat>}
      +l3 = pl(va(0n), pl(va(3n), va(5n)))
      +r3 = pl(pl(va(2n), va(4n)), va(1n))
      N.extend(env, pl(pl(va(4n), va(2n)), va(1n)), pl(va(0n), pl(va(5n), va(3n))), zz(), r3, l3, fl(env, l3, r3, h3), {==}, {==})
    case False{}:
      +tt = T(t)
      +sv = I.add(v, tt)
      h3 = add_val(v, tt, P.vp(v), P.vn(v), P.vp(tt), P.vn(tt), {==}, {==})
      +tr = T(rest)
      +u = I.add(v, tr)
      +env = {[P.vp(sv), P.vn(sv), P.vp(u), P.vn(u), P.vp(v), P.vn(v), P.vp(tt), P.vn(tt), P.vp(tr), P.vn(tr), P.vp(g), P.vn(g)] : List<&2, Nat>}
      h4 = add_val(v, tr, P.vp(v), P.vn(v), P.vp(tr), P.vn(tr), {==}, {==})
      +l4 = pl(va(2n), pl(va(5n), va(9n)))
      +r4 = pl(pl(va(4n), va(8n)), va(3n))
      +l3 = pl(va(0n), pl(va(5n), va(7n)))
      +r3 = pl(pl(va(4n), va(6n)), va(1n))
      +il = pl(pl(va(8n), va(10n)), va(7n))
      +ir = pl(va(6n), pl(va(9n), va(11n)))
      e = ae(env, pl(l4, r3), pl(r4, l3), il, ir, ae(env, l4, r4, r3, l3, h4, fl(env, l3, r3, h3)), ih)
      +cc = pl(va(5n), pl(va(9n), pl(va(4n), pl(va(6n), pl(va(8n), va(7n))))))
      N.cancel(env, pl(pl(va(2n), va(10n)), va(1n)), pl(va(0n), pl(va(3n), va(11n))), cc, pl(pl(l4, r3), il), pl(pl(r4, l3), ir), e, {==}, {==})

# removing k takes its balance out of the sum
def rem_ok(l: List<&2, L.Entry>, +k: String) -> Rem(L.remove(l, k), L.get(l, k), l):
  match l:
    case Nil{}:
      {==}
    case L.Entry{+k2, +v} <> +t:
      rem_if(k2, v, t, L.remove(t, k), L.get(t, k), String.eq(k2, k), rem_ok(t, k))

def and_r(a: Bool, -b: Bool, e: {True{} == Bool.and(a, b) : Bool}) -> {True{} == b : Bool}:
  match a:
    case True{}:
      e
    case False{}:
      P.efq({True{} == b : Bool}, bool_disc(e))

def purge_if(+l: List<&2, L.Entry>, +k: String, drop: Bool, ev: {drop == Bool.and(L.is_escrow(k), I.is_zero(L.get(l, k))) : Bool})
  -> Val(T(L.purge.if(l, k, drop)), P.vp(T(l)), P.vn(T(l))):
  match drop:
    case False{}:
      {==}
    case True{}:
      +g = L.get(l, k)
      +rm = L.remove(l, k)
      z = P.iz_val(g, and_r(L.is_escrow(k), I.is_zero(g), ev))
      +env = {[P.vp(T(rm)), P.vn(T(rm)), P.vp(T(l)), P.vn(T(l)), P.vp(g), P.vn(g)] : List<&2, Nat>}
      +rl = pl(pl(va(0n), va(4n)), va(3n))
      +rr = pl(va(2n), pl(va(1n), va(5n)))
      +zl = pl(va(4n), zz())
      +zr = pl(zz(), va(5n))
      e = ae(env, rl, rr, zr, zl, rem_ok(l, k), fl(env, zl, zr, z))
      N.cancel(env, pl(va(0n), va(3n)), pl(va(2n), va(1n)), pl(va(4n), va(5n)), pl(rl, zr), pl(rr, zl), e, {==}, {==})

# dropping a closed escrow account keeps the sum
def purge_ok(+l: List<&2, L.Entry>, +k: String) -> Val(T(L.purge(l, k)), P.vp(T(l)), P.vn(T(l))):
  purge_if(l, k, Bool.and(L.is_escrow(k), I.is_zero(L.get(l, k))), {==})

# a committed transfer keeps the sum
def commit_ok(+l: List<&2, L.Entry>, +src: String, +dst: String, +amt: I.Int) -> Val(T(L.commit(l, src, dst, amt)), P.vp(T(l)), P.vn(T(l))):
  +n = I.neg(amt)
  +l1 = L.adjust(l, src, n)
  +l2 = L.adjust(l1, dst, amt)
  +l3 = L.purge(l2, src)
  +l4 = L.purge(l3, dst)
  t42 = P.eqz_trans(P.vp(T(l4)), P.vn(T(l4)), P.vp(T(l3)), P.vn(T(l3)), P.vp(T(l2)), P.vn(T(l2)), purge_ok(l3, dst), purge_ok(l2, src))
  +env = {[P.vp(T(l2)), P.vn(T(l2)), P.vp(T(l1)), P.vn(T(l1)), P.vp(T(l)), P.vn(T(l)), P.vp(n), P.vn(n), P.vp(amt), P.vn(amt)] : List<&2, Nat>}
  +h2l = pl(va(0n), pl(va(3n), va(9n)))
  +h2r = pl(pl(va(2n), va(8n)), va(1n))
  +h1l = pl(va(2n), pl(va(5n), va(7n)))
  +h1r = pl(pl(va(4n), va(6n)), va(3n))
  +nl = pl(va(6n), va(8n))
  +nr = pl(va(9n), va(7n))
  e = ae(env, pl(h2l, h1l), pl(h2r, h1r), nl, nr, ae(env, h2l, h2r, h1l, h1r, adj_ok(l1, dst, amt), adj_ok(l, src, n)), P.neg_ok(amt))
  +cc = pl(va(3n), pl(va(9n), pl(va(2n), pl(va(7n), pl(va(6n), va(8n))))))
  v2 = N.cancel(env, pl(va(0n), va(5n)), pl(va(4n), va(1n)), cc, pl(pl(h2l, h1l), nl), pl(pl(h2r, h1r), nr), e, {==}, {==})
  P.eqz_trans(P.vp(T(l4)), P.vn(T(l4)), P.vp(T(l2)), P.vn(T(l2)), P.vp(T(l)), P.vn(T(l)), t42, v2)

def go_val(m: Maybe<&2, String>, +cs: List<&2, String>, +cr: Maybe<&2, I.Int>, +b: List<&2, L.Entry>, +h: List<&2, L.Transfer>, +n: I.Int, +src: String, +dst: String, +amount: I.Int, reason: String, ref: String, tick: I.Int)
  -> P.Same(L.total(L.after(L.transfer.go(m, cs, cr, b, h, n, src, dst, amount, reason, ref, tick), L.Ledger{cs, cr, b, h, n})), L.total(L.Ledger{cs, cr, b, h, n})):
  match m:
    case Some{e}:
      {==}
    case None{}:
      commit_ok(b, src, dst, amount)

# the sum after a transfer has the value of the sum before
def conserve(l: L.Ledger, +src: String, +dst: String, +amount: I.Int, reason: String, ref: String, tick: I.Int, force: Bool)
  -> P.Same(L.total(L.after(L.transfer(l, src, dst, amount, reason, ref, tick, force), l)), L.total(l)):
  match l:
    case L.Ledger{+cs, +cr, +b, +h, +n}:
      go_val(L.check(cs, cr, b, src, dst, amount, force), cs, cr, b, h, n, src, dst, amount, reason, ref, tick)

def Laws.transfer_conserves(l, src, dst, amount, reason, ref, tick, force):
  P.is_eq_ok(L.total(L.after(L.transfer(l, src, dst, amount, reason, ref, tick, force), l)), L.total(l), conserve(l, src, dst, amount, reason, ref, tick, force))

def zeros_ok(cs: List<&2, String>) -> Val(T(L.zeros(cs)), 0n, 0n):
  match cs:
    case Nil{}:
      add_val(I.zero(), I.zero(), 0n, 0n, 0n, 0n, {==}, {==})
    case c <> +t:
      add_val(I.zero(), T(L.zeros(t)), 0n, 0n, 0n, 0n, {==}, zeros_ok(t))

def done_ledger(r: Result<&2, &2, String, L.Ledger>) -> L.Ledger:
  match r:
    case Fail{e}:
      L.Ledger{[], None{}, [], [], I.zero()}
    case Done{x}:
      x

def IsDoneL(r: Result<&2, &2, String, L.Ledger>) -> Type:
  match r:
    case Fail{e}:
      Empty
    case Done{x}:
      Unit

def fail_done_l(-x: String, -l: L.Ledger, e: {Fail{x} == Done{l} : Result<&2, &2, String, L.Ledger>}) -> Empty:
  %Equal.sym(Result<&2, &2, String, L.Ledger>, Fail{x}, Done{l}, e) : IsDoneL(_)
  Unit{}

def new_zero.done(+x: L.Ledger, +l: L.Ledger, eq: {x == l : L.Ledger}, z: {True{} == I.is_zero(L.total(x)) : Bool}) -> {True{} == I.is_zero(L.total(l)) : Bool}:
  %eq : {True{} == I.is_zero(L.total(_)) : Bool}
  z

def new_zero(m: Maybe<&2, String>, +cs: List<&2, String>, +cr: Maybe<&2, I.Int>, +l: L.Ledger, e: {L.new.go(m, cs, cr) == Done{l} : Result<&2, &2, String, L.Ledger>})
  -> {True{} == I.is_zero(L.total(l)) : Bool}:
  match m:
    case Some{x}:
      P.efq({True{} == I.is_zero(L.total(l)) : Bool}, fail_done_l(x, l, e))
    case None{}:
      +x = {L.Ledger{cs, cr, L.zeros(cs), [], I.zero()} : L.Ledger}
      eq = Equal.cong(Result<&2, &2, String, L.Ledger>, L.Ledger, r => done_ledger(r), Done{x}, Done{l}, e)
      new_zero.done(x, l, eq, P.val_iz(T(L.zeros(cs)), zeros_ok(cs)))

def Laws.new_total_zero(cs, cr, l, e):
  new_zero(L.first(L.credit_bad(cr), "bad_amount", L.first(Bool.not(L.all_ids(cs)), "bad_company", L.first(L.has_dup(cs), "bad_company", None{}))), cs, cr, l, e)

# a transfer keeps a zero sum zero
def keep_zero(+l: L.Ledger, +src: String, +dst: String, +amount: I.Int, +reason: String, +ref: String, +tick: I.Int, +force: Bool, z: {True{} == I.is_zero(L.total(l)) : Bool})
  -> {True{} == I.is_zero(L.total(L.after(L.transfer(l, src, dst, amount, reason, ref, tick, force), l))) : Bool}:
  +l2 = L.after(L.transfer(l, src, dst, amount, reason, ref, tick, force), l)
  P.val_iz(L.total(l2), P.eqz_trans(P.vp(L.total(l2)), P.vn(L.total(l2)), P.vp(L.total(l)), P.vn(L.total(l)), 0n, 0n, conserve(l, src, dst, amount, reason, ref, tick, force), P.iz_val(L.total(l), z)))

def keep_all(rs: List<&2, Laws.Req>, +l: L.Ledger, z: {True{} == I.is_zero(L.total(l)) : Bool}) -> {True{} == I.is_zero(L.total(Laws.apply_all(rs, l))) : Bool}:
  match rs:
    case Nil{}:
      z
    case Laws.Req{+src, +dst, +amount, +reason, +ref, +tick, +force} <> t:
      keep_all(t, L.after(L.transfer(l, src, dst, amount, reason, ref, tick, force), l), keep_zero(l, src, dst, amount, reason, ref, tick, force, z))

def Laws.built_total_zero(cs, cr, l, rs, e):
  keep_all(rs, l, Laws.new_total_zero(cs, cr, l, e))

# L5: the licence fee split
# -------------------------

def PT(ds: List<&2, R.Due>) -> I.Int:
  Laws.parts_total(ds)

def keep_if(+a: I.Int, +d: I.Int, +rest: List<&2, R.Due>, +p: Nat, +n: Nat, ih: Val(PT(rest), p, n), z: Bool, ev: {z == I.is_zero(a) : Bool})
  -> Val(PT(R.split.keep(z, R.Due{d, a}, rest)), Nat.add(P.vp(a), p), Nat.add(P.vn(a), n)):
  match z:
    case False{}:
      add_val(a, PT(rest), P.vp(a), P.vn(a), p, n, {==}, ih)
    case True{}:
      +r = PT(rest)
      +env = {[P.vp(r), P.vn(r), P.vp(a), P.vn(a), p, n] : List<&2, Nat>}
      +il = pl(va(0n), va(5n))
      +ir = pl(va(4n), va(1n))
      +zl = pl(va(2n), zz())
      +zr = va(3n)
      e = ae(env, il, ir, zr, zl, ih, fl(env, zl, zr, P.iz_val(a, ev)))
      N.extend(env, pl(va(0n), pl(va(3n), va(5n))), pl(pl(va(2n), va(4n)), va(1n)), zz(), pl(il, zr), pl(ir, zl), e, {==}, {==})

def cons_val(+a: I.Int, +d: I.Int, +rest: List<&2, R.Due>, +p: Nat, +n: Nat, ih: Val(PT(rest), p, n))
  -> Val(PT(R.split.cons(a, d, rest)), Nat.add(P.vp(a), p), Nat.add(P.vn(a), n)):
  keep_if(a, d, rest, p, n, ih, I.is_zero(a), {==})

# the parts of split.go sum to the running remainder, whatever each is
def go_sum(t: List<&2, I.Int>, +h: I.Int, +each: I.Int, +rem: I.Int) -> Val(PT(R.split.go(t, h, each, rem)), P.vp(rem), P.vn(rem)):
  match t:
    case Nil{}:
      +x = PT(R.split.cons(rem, h, []))
      +z = N.solve({[P.vp(rem), P.vn(rem)] : List<&2, Nat>}, pl(pl(va(0n), zz()), va(1n)), pl(va(0n), pl(va(1n), zz())), {==})
      P.eqz_trans(P.vp(x), P.vn(x), Nat.add(P.vp(rem), 0n), Nat.add(P.vn(rem), 0n), P.vp(rem), P.vn(rem), cons_val(rem, h, [], 0n, 0n, {==}), z)
    case +h2 <> +t2:
      +s = I.sub(rem, each)
      +rest = R.split.go(t2, h2, each, s)
      +x = PT(R.split.cons(each, h, rest))
      cv = cons_val(each, h, rest, P.vp(s), P.vn(s), go_sum(t2, h2, each, s))
      +env = {[P.vp(x), P.vn(x), P.vp(each), P.vn(each), P.vp(s), P.vn(s), P.vp(rem), P.vn(rem)] : List<&2, Nat>}
      +cl = pl(va(0n), pl(va(3n), va(5n)))
      +cr = pl(pl(va(2n), va(4n)), va(1n))
      +sl = pl(va(4n), pl(va(7n), va(2n)))
      +sr = pl(pl(va(6n), va(3n)), va(5n))
      e = ae(env, cl, cr, sl, sr, cv, P.sub_ok(rem, each))
      N.cancel(env, pl(va(0n), va(7n)), pl(va(6n), va(1n)), pl(va(3n), pl(va(5n), pl(va(4n), va(2n)))), pl(cl, sl), pl(cr, sr), e, {==}, {==})

def split_pos(+total: I.Int, +h: I.Int, +t: List<&2, I.Int>) -> {True{} == I.is_eq(PT(R.split.if(True{}, h <> t, total)), total) : Bool}:
  +each = I.floordiv(total, R.len(h <> t))
  P.is_eq_ok(PT(R.split.go(t, h, each, total)), total, go_sum(t, h, each, total))

def Laws.split_sums(total, h, t, e):
  %e : {True{} == I.is_eq(Laws.parts_total(R.split.if(_, h <> t, total)), total) : Bool}
  split_pos(total, h, t)
