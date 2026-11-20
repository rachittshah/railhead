# main.bend: the waybill-ex CLI (docs/WAYBILL-BEND.md section 9).
#
#   waybill-ex <records.jsonl>
#
# One exchange record per line (Python json.dumps text). Prints exactly one
# canonical line: {"ok":<state>}, {"error":"<code>","seq":<i>} or
# {"error":"invariant","seq":<n>} (E.replay), and exits 0. Usage errors exit 2;
# an unreadable file exits through IO.try; a line that is not JSON exits 3
# (the oracle never writes one).
#
# The only module with IO: everything it calls is pure.

import Base
import ./json.bend as J
import ./exchange.bend as E

def all_parsed(xs: List<&2, Maybe<&2, J.Json>>) -> Maybe<&2, List<&2, J.Json>>:
  match xs:
    case Nil{}:
      Some{Nil{}}
    case Con{m, t}:
      match m:
        case None{}:
          None{}
        case Some{j}:
          Maybe.map(&2, List<&2, J.Json>, List<&2, J.Json>, (r => Con{j, r}), all_parsed(t))

def output(m: Maybe<&2, List<&2, J.Json>>) -> IO(String):
  match m:
    case None{}:
      IO.die(String, 3, "waybill-ex: a line is not JSON")
    case Some{rs}:
      IO.pure(String, J.show(E.replay(rs)))

def run(args: List<String>) -> IO(String):
  match args:
    case Con{prog, Con{p, Nil{}}}:
      do IO<String>:
        t : String <- J.read_file(p)
        output(all_parsed(J.parse_lines(t)))
    case _:
      IO.die(String, 2, "usage: waybill-ex <records.jsonl>")

def main() -> IO(Unit):
  do IO<Unit>:
    args : List<String> <- IO.args()
    line : String <- run(args)
    IO.print(line)
