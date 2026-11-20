# t_ledger.bend: test driver for ledger.bend. Reads a script (first
# argument), one command per line, words split on ' ' ("-" is the empty
# string):
#   new <credit|none> <c1,c2,...|->       -> ok | <error>
#   transfer src dst amount reason ref tick force(0|1) -> ok <seq> | <error>
#   pay src dst amount tick ref           -> ok <seq> | <error>
#   balance acct | candebit acct amount | cons | total
#   state                                  -> sorted balances, then the history
# tests/unit/test_bend_ledger.py drives it against Python's Ledger.

import Base
import ./int.bend as I
import ./ledger.bend as L

def b2s(b: Bool) -> String:
  match b:
    case True{}:
      "True"
    case False{}:
      "False"

def rd(s: String) -> I.Int:
  Maybe.default(&2, I.Int, I.read(s), I.zero())

def pick(-A: Type, c: Bool, a: A, b: A) -> A:
  match c:
    case True{}:
      a
    case False{}:
      b

def word(+s: String) -> String:
  pick(String, String.eq(s, "-"), "", s)

def br(c: Bool, t: Unit -> String & Maybe<&2, L.Ledger>, e: Unit -> String & Maybe<&2, L.Ledger>) -> String & Maybe<&2, L.Ledger>:
  match c:
    case True{}:
      t(Unit{})
    case False{}:
      e(Unit{})

def show_bal(es: List<&2, L.Entry>) -> String:
  match es:
    case Nil{}:
      ""
    case e <> t:
      match e:
        case L.Entry{k, v}:
          " " ++ k ++ "=" ++ I.show(v) ++ show_bal(t)

def show_hist(ts: List<&2, L.Transfer>) -> String:
  match ts:
    case Nil{}:
      ""
    case x <> t:
      match x:
        case L.Transfer{seq, tick, src, dst, amount, reason, ref}:
          " (" ++ I.show(seq) ++ "," ++ I.show(tick) ++ "," ++ src ++ "," ++ dst ++ "," ++ I.show(amount) ++ "," ++ reason ++ "," ++ ref ++ ")" ++ show_hist(t)

def moved(r: Result<&2, &2, String, L.Moved>, old: L.Ledger) -> String & Maybe<&2, L.Ledger>:
  match r:
    case Fail{e}:
      (e, Some{old})
    case Done{m}:
      match m:
        case L.Moved{l, t}:
          match t:
            case L.Transfer{seq, tick, src, dst, amount, reason, ref}:
              ("ok " ++ I.show(seq), Some{l})

def made(r: Result<&2, &2, String, L.Ledger>) -> String & Maybe<&2, L.Ledger>:
  match r:
    case Fail{e}:
      (e, None{})
    case Done{l}:
      ("ok", Some{l})

def credit(+s: String) -> Maybe<&2, I.Int>:
  pick(Maybe<&2, I.Int>, String.eq(s, "none"), None{}, I.read(s))

def cos(+s: String) -> List<&2, String>:
  pick(List<&2, String>, String.eq(s, "-"), [], String.split(s, ','))

def force(s: String) -> Bool:
  String.eq(s, "1")

def arg(+xs: List<&2, String>, n: Nat) -> String:
  Maybe.default(&2, String, List.get(&2, String, xs, n), "")

def onl(+op: String, +a: List<&2, String>, l: L.Ledger) -> String & Maybe<&2, L.Ledger>:
  +l2 = l
  br(String.eq(op, "transfer"), u => moved(L.transfer(l2, word(arg(a, 0n)), word(arg(a, 1n)), rd(arg(a, 2n)), word(arg(a, 3n)), word(arg(a, 4n)), rd(arg(a, 5n)), force(arg(a, 6n))), l2),
  u => br(String.eq(op, "pay"), u => moved(L.pay(l2, word(arg(a, 0n)), word(arg(a, 1n)), rd(arg(a, 2n)), rd(arg(a, 3n)), word(arg(a, 4n))), l2),
  u => br(String.eq(op, "balance"), u => (I.show(L.balance(l2, word(arg(a, 0n)))), Some{l2}),
  u => br(String.eq(op, "candebit"), u => (b2s(L.can_debit(l2, word(arg(a, 0n)), rd(arg(a, 1n)))), Some{l2}),
  u => br(String.eq(op, "cons"), u => (b2s(L.check_conservation(l2)), Some{l2}),
  u => br(String.eq(op, "total"), u => (I.show(L.total(l2)), Some{l2}),
  u => br(String.eq(op, "state"), u => ("bal" ++ show_bal(L.balances(l2)) ++ " | hist" ++ show_hist(L.history(l2)), Some{l2}),
  u => ("badline", Some{l2}))))))))

def onst(+op: String, +a: List<&2, String>, st: Maybe<&2, L.Ledger>) -> String & Maybe<&2, L.Ledger>:
  match st:
    case None{}:
      ("noledger", None{})
    case Some{l}:
      onl(op, a, l)

def cmd2(+op: String, +a: List<&2, String>, st: Maybe<&2, L.Ledger>) -> String & Maybe<&2, L.Ledger>:
  br(String.eq(op, "new"), u => made(L.new(cos(arg(a, 1n)), credit(arg(a, 0n)))), u => onst(op, a, st))

def cmd(ws: List<&2, String>, st: Maybe<&2, L.Ledger>) -> String & Maybe<&2, L.Ledger>:
  match ws:
    case Nil{}:
      ("badline", st)
    case op <> a:
      cmd2(op, a, st)

def nonempty(s: String) -> Bool:
  Bool.not(String.is_empty(s))

def out(r: String & Maybe<&2, L.Ledger>, t: List<&2, String>, acc: List<&2, String>) -> List<&2, String> & (List<&2, String> & Maybe<&2, L.Ledger>):
  (o, st) = r
  ((o ++ "\n") <> acc, (t, st))

@unsafe def run(ls: List<&2, String>, st: Maybe<&2, L.Ledger>, acc: List<&2, String>) -> String:
  match ls:
    case Nil{}:
      String.concat(List.reverse(&2, String, acc))
    case h <> t:
      run.next(out(cmd(String.split(h, ' '), st), t, acc))

@unsafe def run.next(r: List<&2, String> & (List<&2, String> & Maybe<&2, L.Ledger>)) -> String:
  (acc, rest) = r
  (t, st) = rest
  run(t, st, acc)

law go:
  for m: File & Result<&1, &1, (U32 & String), String>
  IO(Unit)

def go(m):
  (f, r) = m
  do IO<Unit>:
    s : String <- IO.pass(String, r)
    u : Unit <- IO.write(run(List.filter(~String, ~nonempty, String.lines(s)), None{}, []))
    File.close(f)

def main() -> IO(Unit):
  do IO<Unit>:
    args : List<String> <- IO.args()
    path : String = Maybe.default(&1, String, List.get(&1, String, List.drop(&1, String, args, 1n), 0n), "")
    f : File <- IO.try(File, File.open(path, "r"))
    m : File & Result<&1, &1, (U32 & String), String> <- File.read(f, 268435456)
    go(m)
