# t_rules.bend: test driver for rules.bend. One command per line:
#   due ws deadline | split total d1,d2|- | inst fee ws deadline last|none
#   clip floor last|none t:a,t:a|- | outcome d q | move old new | term s
# tests/unit/test_bend_ledger.py drives it against Python.

import Base
import ./int.bend as I
import ./rules.bend as R

def rd(s: String) -> I.Int:
  Maybe.default(&2, I.Int, I.read(s), I.zero())

def pick(-A: Type, c: Bool, a: A, b: A) -> A:
  match c:
    case True{}:
      a
    case False{}:
      b

def br(c: Bool, t: Unit -> String, e: Unit -> String) -> String:
  match c:
    case True{}:
      t(Unit{})
    case False{}:
      e(Unit{})

def map_rd(xs: List<&2, String>) -> List<&2, I.Int>:
  match xs:
    case Nil{}:
      []
    case h <> t:
      rd(h) <> map_rd(t)

def show_ints(xs: List<&2, I.Int>) -> String:
  match xs:
    case Nil{}:
      ""
    case h <> Nil{}:
      I.show(h)
    case h <> t:
      I.show(h) ++ " " ++ show_ints(t)

def ints(+s: String) -> List<&2, I.Int>:
  map_rd(pick(List<&2, String>, String.eq(s, "-"), [], String.split(s, ',')))

def mlast(+s: String) -> Maybe<&2, I.Int>:
  pick(Maybe<&2, I.Int>, String.eq(s, "none"), None{}, I.read(s))

def due(s: String) -> R.Due:
  +p = String.split(s, ':')
  R.Due{rd(Maybe.default(&2, String, List.get(&2, String, p, 0n), "")), rd(Maybe.default(&2, String, List.get(&2, String, p, 1n), ""))}

def map_due(xs: List<&2, String>) -> List<&2, R.Due>:
  match xs:
    case Nil{}:
      []
    case h <> t:
      due(h) <> map_due(t)

def dues(+s: String) -> List<&2, R.Due>:
  map_due(pick(List<&2, String>, String.eq(s, "-"), [], String.split(s, ',')))


def show_due(d: R.Due) -> String:
  match d:
    case R.Due{t, a}:
      I.show(t) ++ ":" ++ I.show(a)

def show_dues.go(xs: List<&2, R.Due>) -> String:
  match xs:
    case Nil{}:
      ""
    case h <> Nil{}:
      show_due(h)
    case h <> t:
      show_due(h) ++ " " ++ show_dues.go(t)

def show_dues(xs: List<&2, R.Due>) -> String:
  "[" ++ show_dues.go(xs) ++ "]"

def st(+s: String) -> R.Status:
  Maybe.default(&2, R.Status, R.status_read(s), R.Withdrawn{})

def mv(r: Result<&2, &2, String, R.Status>) -> String:
  match r:
    case Fail{e}:
      e
    case Done{s}:
      R.status_name(s)

def b2s(b: Bool) -> String:
  match b:
    case True{}:
      "True"
    case False{}:
      "False"

def arg(+xs: List<&2, String>, n: Nat) -> String:
  Maybe.default(&2, String, List.get(&2, String, xs, n), "")

def cmd(+op: String, +a: List<&2, String>) -> String:
  br(String.eq(op, "due"), u => show_ints(R.due_ticks(rd(arg(a, 0n)), rd(arg(a, 1n)))),
  u => br(String.eq(op, "split"), u => show_dues(R.split(rd(arg(a, 0n)), ints(arg(a, 1n)))),
  u => br(String.eq(op, "inst"), u => show_dues(R.instalments(rd(arg(a, 0n)), rd(arg(a, 1n)), rd(arg(a, 2n)), mlast(arg(a, 3n)))),
  u => br(String.eq(op, "clip"), u => show_dues(R.clip_schedule(dues(arg(a, 2n)), mlast(arg(a, 1n)), rd(arg(a, 0n)))),
  u => br(String.eq(op, "outcome"), u => R.outcome_name(R.outcome_for(rd(arg(a, 0n)), rd(arg(a, 1n)))),
  u => br(String.eq(op, "move"), u => mv(R.move(st(arg(a, 0n)), st(arg(a, 1n)))),
  u => br(String.eq(op, "term"), u => b2s(R.is_terminal(st(arg(a, 0n)))),
  u => "badop")))))))

def line(ws: List<&2, String>) -> String:
  match ws:
    case Nil{}:
      "badline"
    case op <> a:
      cmd(op, a)

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
