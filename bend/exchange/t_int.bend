# t_int.bend: test driver for int.bend. Reads the ops file named by the
# first argument, one op per line ("add a b", "neg a", "read text", ...),
# and prints one result per line. tests/unit/test_bend_int.py drives it.

import Base
import ./int.bend as I

def b2s(b: Bool) -> String:
  match b:
    case True{}:
      "True"
    case False{}:
      "False"

def c2s(c: Cmp) -> String:
  match c:
    case LT{}:
      "-1"
    case EQ{}:
      "0"
    case GT{}:
      "1"

def rd(s: String) -> I.Int:
  Maybe.default(&2, I.Int, I.read(s), I.zero())

def showm(m: Maybe<&2, I.Int>) -> String:
  match m:
    case None{}:
      "None"
    case Some{v}:
      I.show(v)

def br(c: Bool, t: Unit -> String, e: Unit -> String) -> String:
  match c:
    case True{}:
      t(Unit{})
    case False{}:
      e(Unit{})

def op2(+o: String, +a: I.Int, +b: I.Int) -> String:
  br(String.eq(o, "add"), u => I.show(I.add(a, b)), u => br(String.eq(o, "sub"), u => I.show(I.sub(a, b)), u => br(String.eq(o, "mul"), u => I.show(I.mul(a, b)), u => br(String.eq(o, "floordiv"), u => I.show(I.floordiv(a, b)), u => br(String.eq(o, "mod"), u => I.show(I.mod(a, b)), u => br(String.eq(o, "cmp"), u => c2s(I.cmp(a, b)), u => br(String.eq(o, "eq"), u => b2s(I.is_eq(a, b)), u => br(String.eq(o, "ne"), u => b2s(I.is_ne(a, b)), u => br(String.eq(o, "lt"), u => b2s(I.is_lt(a, b)), u => br(String.eq(o, "le"), u => b2s(I.is_le(a, b)), u => br(String.eq(o, "gt"), u => b2s(I.is_gt(a, b)), u => br(String.eq(o, "ge"), u => b2s(I.is_ge(a, b)), u => br(String.eq(o, "min"), u => I.show(I.min(a, b)), u => br(String.eq(o, "max"), u => I.show(I.max(a, b)), u => "badop"))))))))))))))

def op1(+o: String, +s: String) -> String:
  br(String.eq(o, "neg"), u => I.show(I.neg(rd(s))), u => br(String.eq(o, "read"), u => showm(I.read(s)), u => br(String.eq(o, "zero"), u => b2s(I.is_zero(rd(s))), u => br(String.eq(o, "isneg"), u => b2s(I.is_neg(rd(s))), u => br(String.eq(o, "ispos"), u => b2s(I.is_pos(rd(s))), u => br(String.eq(o, "u32"), u => I.show(I.from_u32(Maybe.default(&2, U32, U32.read(s), 0))), u => br(String.eq(o, "nat"), u => Nat.show(I.to_nat(rd(s))), u => br(String.eq(o, "fromnat"), u => I.show(I.from_nat(I.to_nat(rd(s)))), u => br(String.eq(o, "show"), u => I.show(rd(s)), u => "badop")))))))))

def line(ws: List<&2, String>) -> String:
  match ws:
    case [op, a]:
      op1(op, a)
    case [op, a, b]:
      op2(op, rd(a), rd(b))
    case _:
      "badline"

def run(ls: List<&2, String>, acc: List<&2, String>) -> String:
  match ls:
    case Nil{}:
      String.concat(List.reverse(&2, String, acc))
    case h <> t:
      run(t, (line(String.split(h, ' ')) ++ "\n") <> acc)

def nonempty(s: String) -> Bool:
  Bool.not(String.is_empty(s))

law go:
  for m: File & Result<&1, &1, (U32 & String), String>
  IO(Unit)

def go(m):
  (f, r) = m
  do IO<Unit>:
    s : String <- IO.pass(String, r)
    u : Unit <- IO.write(run(List.filter(~String, ~nonempty, String.lines(s)), []))
    File.close(f)

def main() -> IO(Unit):
  do IO<Unit>:
    args : List<String> <- IO.args()
    path : String = Maybe.default(&1, String, List.get(&1, String, List.drop(&1, String, args, 1n), 0n), "")
    f : File <- IO.try(File, File.open(path, "r"))
    m : File & Result<&1, &1, (U32 & String), String> <- File.read(f, 268435456)
    go(m)
