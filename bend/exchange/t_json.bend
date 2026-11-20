# Test driver for json.bend.
#   t_json <file.jsonl>      J.show of each line (J.parse_lines), or PARSE_ERROR
#   t_json <file.jsonl> api  per line, via J.parse: J.show of
#                            [J.keys(o), [J.get(k, o) for k in keys], J.obj(kvs + [(first key, null)])]
import Base
import ./json.bend as J

def out(xs: List<&2, Maybe<&2, J.Json>>, acc: List<&2, String>) ->
  List<&2, String>:
  match xs:
    case Nil{}:
      List.reverse(&2, String, acc)
    case Con{None{}, t}:
      out(t, Con{"PARSE_ERROR", acc})
    case Con{Some{j}, t}:
      out(t, Con{J.show(j), acc})

def lines.end(cur: String, acc: List<&2, String>) -> List<&2, String>:
  match cur:
    case SNil{}:
      acc
    case SCon{h, t}:
      Con{String.reverse(SCon{h, t}), acc}

# '\n'-separated lines, tail-recursive; drops the empty segment after a final '\n'.
def lines(s: String, cur: String, acc: List<&2, String>) -> List<&2, String> :
  match s:
    case SNil{}:
      List.reverse(&2, String, lines.end(cur, acc))
    case SCon{Chr{c}, t}:
      match c:
        case 10:
          lines(t, SNil{}, Con{String.reverse(cur), acc})
        case k:
          lines(t, SCon{Chr{k}, cur}, acc)

def orelse(m: Maybe<&2, J.Json>) -> J.Json:
  match m:
    case None{}:
      J.JStr{"MISSING"}
    case Some{v}:
      v

def gets(ks: List<&2, String>, +j: J.Json) -> List<&2, J.Json>:
  match ks:
    case Nil{}:
      Nil{}
    case Con{k, t}:
      Con{orelse(J.get(k, j)), gets(t, j)}

def strs(ks: List<&2, String>) -> List<&2, J.Json>:
  match ks:
    case Nil{}:
      Nil{}
    case Con{k, t}:
      Con{J.JStr{k}, strs(t)}

def kvs(j: J.Json) -> List<&2, +Sigma<String, _ => J.Json>>:
  match j:
    case J.JObj{xs}:
      xs
    case _:
      Nil{}

def dup_first(ks: List<&2, String>, +j: J.Json) -> J.Json:
  match ks:
    case Nil{}:
      J.obj(kvs(j))
    case Con{k, t}:
      J.obj(List.append(&2, +Sigma<String, _ => J.Json>, kvs(j), [(k, J.JNull{})]))

def api1(m: Maybe<&2, J.Json>) -> String:
  match m:
    case None{}:
      "PARSE_ERROR"
    case Some{+j}:
      +ks = J.keys(j)
      J.show(J.JArr{[J.JArr{strs(ks)}, J.JArr{gets(ks, j)}, dup_first(ks, j)]})

def api(ls: List<&2, String>) -> List<&2, String>:
  match ls:
    case Nil{}:
      Nil{}
    case Con{l, t}:
      Con{api1(J.parse(l)), api(t)}

def run(args: List<String>) -> IO(String):
  match args:
    case Con{prog, Con{p, Nil{}}}:
      do IO<String>:
        xs : List<&2, Maybe<&2, J.Json>> <- J.read_jsonl(p)
        return String.join(out(xs, Nil{}), "\n")
    case Con{prog, Con{p, Con{mode, rest}}}:
      do IO<String>:
        t : String <- J.read_file(p)
        return String.join(api(lines(t, SNil{}, Nil{})), "\n")
    case _:
      IO.die(String, 2, "usage: t_json <file.jsonl> [api]")

def main() -> IO(Unit):
  do IO<Unit>:
    args : List<String> <- IO.args()
    text : String <- run(args)
    IO.print(text)
