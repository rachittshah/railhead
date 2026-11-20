# json.bend: JSON parser + canonical printer for the Waybill exchange port.
#
# J.show(J.parse(x)) is byte-identical to Python's
#   json.dumps(json.loads(x), sort_keys=True, separators=(",", ":"))
# (ensure_ascii=True), for any x Python's json.loads accepts, given that float
# tokens in x are already Python repr text (they are kept verbatim).
#
# API (import ./json.bend as J; from outside, types and constructors are
# qualified too: J.Json, J.JObj{kvs}, J.JInt{"1"}):
#   type Json is Data:
#     JNull{} | JBool{b: Bool} | JInt{text: String} | JFloat{text: String}
#     | JStr{s: String} | JArr{xs: List<&2, Json>}
#     | JObj{kvs: List<&2, +Sigma<String, _ => Json>>}   (a Data pair, (k, v))
#   J.parse(s: String) -> Maybe<&2, Json>      None = parse error
#   J.show(j: Json) -> String                 canonical, ASCII only
#   J.get(+key: String, j: Json) -> Maybe<&2, Json>   object lookup (last wins)
#   J.keys(j: Json) -> List<&2, String>       object keys, stored order
#   J.obj(kvs: List<&2, +Sigma<String, _ => Json>>) -> Json
#   J.str_eq(a: String, b: String) -> Bool
#   J.parse_lines(s: String) -> List<&2, Maybe<&2, Json>>
#       one entry per '\n'-terminated line (a final line without '\n' counts
#       if non-empty; a blank line in the middle is None)
#   J.read_jsonl(path: String) -> IO(List<&2, Maybe<&2, Json>>)
#
# Semantics (Python json.loads, C scanner):
# - whitespace: space, \t, \n, \r only; extra tokens NaN, Infinity, -Infinity
#   (JFloat with that text).
# - numbers: -?(0|[1-9][0-9]*)(.[0-9]+)?([eE][+-]?[0-9]+)?; no '.'/'e' -> JInt
#   (token text; "-0" is stored as "0", as int("-0") prints), else JFloat.
# - strings: raw chars < 0x20 rejected; escapes \" \\ \/ \b \f \n \r \t \uXXXX
#   (hex either case); a \u high surrogate immediately followed by a \u low
#   surrogate joins into one code point, otherwise each stays a lone surrogate.
# - objects: kvs in input order of first occurrence, value of the last
#   occurrence (Python dict). J.obj does not dedupe; J.show keeps the last of
#   equal keys.
# - J.show sorts keys by code point (String.order), escapes like
#   json.encoder's ensure_ascii path: \" \\ \n \r \t \b \f, other < 0x20 and
#   >= 0x7f as \u00xx/\uxxxx lowercase, > 0xffff as a surrogate pair.
# - not checked: float text is trusted to be Python repr ("1E5" stays "1E5");
#   ints over 4300 digits parse here but Python's json.loads rejects them.
# - size: tail-recursive over input length and array width; on the JS path an
#   object above ~20k keys overflows the stack (List.sort), native does not.

import Base

type Json is Data:
  JNull{}
  JBool{b: Bool}
  JInt{text: String}
  JFloat{text: String}
  JStr{s: String}
  JArr{xs: List<&2, Json>}
  JObj{kvs: List<&2, +Sigma<String, _ => Json>>}

# Parser
# ======
# A push-down automaton: run() consumes one char per step (structural
# recursion on the input), step() is non-recursive and keeps an explicit
# stack of open containers.

type Frame is Data:
  FArr{xs: List<&2, Json>}
  FObj{kvs: List<&2, +Sigma<String, _ => Json>>, key: String}

type Mode is Data:
  MVal{}
  MArr0{}
  MObj0{}
  MKey{}
  MColon{}
  MAfter{}
  MEnd{v: Json}
  MStr{acc: String, key: Bool}
  MEsc{acc: String, key: Bool}
  MHex{acc: String, key: Bool, n: U32, v: U32}
  MHi{acc: String, key: Bool, hi: U32}
  MHiB{acc: String, key: Bool, hi: U32}
  MHex2{acc: String, key: Bool, hi: U32, n: U32, v: U32}
  MNum{acc: String, st: U32}
  MLit{rest: String, v: Json}
  MErr{}

type PS is Data:
  PS{mode: Mode, stack: List<&2, Frame>}

def p.err() -> PS:
  PS{MErr{}, Nil{}}

def p.is_ws(c: U32) -> Bool:
  match c:
    case 32:
      True{}
    case 10:
      True{}
    case 13:
      True{}
    case 9:
      True{}
    case _:
      False{}

# Object close: dict semantics. kvs arrives latest-first; folding it while
# prepending yields input order, and an earlier duplicate moves the later
# value to its own (earlier) position.

def p.take.fin(same: Bool, k: String, v: Json,
  r: Maybe<&2, Json> & List<&2, +Sigma<String, _ => Json>>) ->
  Maybe<&2, Json> & List<&2, +Sigma<String, _ => Json>>:
  match same:
    case True{}:
      (m, xs) = r
      (Some{v}, xs)
    case False{}:
      (m, xs) = r
      (m, Con{(k, v), xs})

def p.take(+key: String, xs: List<&2, +Sigma<String, _ => Json>>) ->
  Maybe<&2, Json> & List<&2, +Sigma<String, _ => Json>>:
  match xs:
    case Nil{}:
      (None{}, Nil{})
    case Con{(+k, v), t}:
      p.take.fin(String.eq(k, key), k, v, p.take(key, t))

def p.dedupe.put(k: String, v: Json,
  r: Maybe<&2, Json> & List<&2, +Sigma<String, _ => Json>>) ->
  List<&2, +Sigma<String, _ => Json>>:
  (m, xs) = r
  match m:
    case None{}:
      Con{(k, v), xs}
    case Some{later}:
      Con{(k, later), xs}

def p.dedupe(rev: List<&2, +Sigma<String, _ => Json>>,
  acc: List<&2, +Sigma<String, _ => Json>>) ->
  List<&2, +Sigma<String, _ => Json>>:
  match rev:
    case Nil{}:
      acc
    case Con{(+k, v), t}:
      p.dedupe(t, p.dedupe.put(k, v, p.take(k, acc)))

# Fast path: sort a copy of the keys; only an object with a duplicate key
# pays for the quadratic dict-semantics pass above.
def p.keys(xs: List<&2, +Sigma<String, _ => Json>>, acc: List<&2, String>) ->
  List<&2, String>:
  match xs:
    case Nil{}:
      acc
    case Con{(k, v), t}:
      p.keys(t, Con{k, acc})

def p.has_dup(xs: List<&2, String>, found: Bool) -> Bool:
  match xs:
    case Nil{}:
      found
    case Con{a, Nil{}}:
      found
    case Con{a, Con{+b, t}}:
      p.has_dup(Con{b, t}, Bool.or(found, String.eq(a, b)))

def p.obj.if(dup: Bool, +rev: List<&2, +Sigma<String, _ => Json>>) ->
  List<&2, +Sigma<String, _ => Json>>:
  match dup:
    case False{}:
      List.reverse(&2, +Sigma<String, _ => Json>, rev)
    case True{}:
      p.dedupe(rev, Nil{})

def p.obj(+rev: List<&2, +Sigma<String, _ => Json>>) ->
  List<&2, +Sigma<String, _ => Json>>:
  p.obj.if(p.has_dup(List.sort(~String, ~String.is_le, p.keys(rev, Nil{})), False{}), rev)

# A completed value goes into the innermost open container.
def p.push(v: Json, st: List<&2, Frame>) -> PS:
  match st:
    case Nil{}:
      PS{MEnd{v}, Nil{}}
    case Con{FArr{xs}, r}:
      PS{MAfter{}, Con{FArr{Con{v, xs}}, r}}
    case Con{FObj{kvs, k}, r}:
      PS{MAfter{}, Con{FObj{Con{(k, v), kvs}, SNil{}}, r}}

def p.close_arr(st: List<&2, Frame>) -> PS:
  match st:
    case Con{FArr{xs}, r}:
      p.push(JArr{List.reverse(&2, Json, xs)}, r)
    case _:
      p.err()

def p.close_obj(st: List<&2, Frame>) -> PS:
  match st:
    case Con{FObj{kvs, k}, r}:
      p.push(JObj{p.obj(kvs)}, r)
    case _:
      p.err()

def p.comma(st: List<&2, Frame>) -> PS:
  match st:
    case Con{FArr{xs}, r}:
      PS{MVal{}, Con{FArr{xs}, r}}
    case Con{FObj{kvs, k}, r}:
      PS{MKey{}, Con{FObj{kvs, k}, r}}
    case _:
      p.err()

# After a value: whitespace, ',' or a closer.
def p.after(c: U32, st: List<&2, Frame>) -> PS:
  match c:
    case 32:
      PS{MAfter{}, st}
    case 10:
      PS{MAfter{}, st}
    case 13:
      PS{MAfter{}, st}
    case 9:
      PS{MAfter{}, st}
    case 44:
      p.comma(st)
    case 93:
      p.close_arr(st)
    case 125:
      p.close_obj(st)
    case _:
      p.err()

def p.end.if(v: Json, ws: Bool) -> PS:
  match ws:
    case True{}:
      PS{MEnd{v}, Nil{}}
    case False{}:
      p.err()

# A char right after a number token: the state the number left behind.
def p.post(c: U32, ps: PS) -> PS:
  match ps:
    case PS{MAfter{}, st}:
      p.after(c, st)
    case PS{MEnd{v}, Nil{}}:
      p.end.if(v, p.is_ws(c))
    case _:
      p.err()

def p.digit.if(c: U32, st: List<&2, Frame>, d: Bool) -> PS:
  match d:
    case True{}:
      PS{MNum{SCon{Chr{c}, SNil{}}, 2}, st}
    case False{}:
      p.err()

# The first char of a value (whitespace handled by the caller's mode `w`).
def p.value(+c: U32, w: Mode, st: List<&2, Frame>) -> PS:
  match c:
    case 32:
      PS{w, st}
    case 10:
      PS{w, st}
    case 13:
      PS{w, st}
    case 9:
      PS{w, st}
    case 34:
      PS{MStr{SNil{}, False{}}, st}
    case 123:
      PS{MObj0{}, Con{FObj{Nil{}, SNil{}}, st}}
    case 91:
      PS{MArr0{}, Con{FArr{Nil{}}, st}}
    case 116:
      PS{MLit{"rue", JBool{True{}}}, st}
    case 102:
      PS{MLit{"alse", JBool{False{}}}, st}
    case 110:
      PS{MLit{"ull", JNull{}}, st}
    case 78:
      PS{MLit{"aN", JFloat{"NaN"}}, st}
    case 73:
      PS{MLit{"nfinity", JFloat{"Infinity"}}, st}
    case 45:
      PS{MNum{"-", 0}, st}
    case 48:
      PS{MNum{"0", 1}, st}
    case _:
      p.digit.if(c, st, (c >= 49 : U32) && (c <= 57 : U32))

# Numbers: st 0 '-', 1 '0', 2 int digits, 3 '.', 4 frac, 5 'e', 6 exp sign,
# 7 exp digits. Terminal: 1 2 4 7. next(): 0..7 a state, 8 token ends,
# 9 error, 10 "-I" (-Infinity).
def p.ncls.d(d: Bool) -> U32:
  match d:
    case True{}:
      1
    case False{}:
      6

def p.ncls(+c: U32) -> U32:
  match c:
    case 48:
      0
    case 46:
      2
    case 101:
      3
    case 69:
      3
    case 43:
      4
    case 45:
      4
    case 73:
      5
    case _:
      p.ncls.d((c >= 49 : U32) && (c <= 57 : U32))

def p.nnext(st: U32, k: U32) -> U32:
  match st k:
    case 0 0:
      1
    case 0 1:
      2
    case 0 5:
      10
    case 0 _:
      9
    case 1 2:
      3
    case 1 3:
      5
    case 1 _:
      8
    case 2 0:
      2
    case 2 1:
      2
    case 2 2:
      3
    case 2 3:
      5
    case 2 _:
      8
    case 3 0:
      4
    case 3 1:
      4
    case 3 _:
      9
    case 4 0:
      4
    case 4 1:
      4
    case 4 3:
      5
    case 4 _:
      8
    case 5 4:
      6
    case 5 0:
      7
    case 5 1:
      7
    case 5 _:
      9
    case 6 0:
      7
    case 6 1:
      7
    case 6 _:
      9
    case 7 0:
      7
    case 7 1:
      7
    case 7 _:
      8
    case _ _:
      9

def p.int_text(acc: String) -> String:
  match acc:
    case SCon{Chr{48}, SCon{Chr{45}, SNil{}}}:
      "0"
    case _:
      String.reverse(acc)

def p.numval(acc: String, st: U32) -> Json:
  match st:
    case 1:
      JInt{p.int_text(acc)}
    case 2:
      JInt{p.int_text(acc)}
    case _:
      JFloat{String.reverse(acc)}

def p.is_term(st: U32) -> Bool:
  match st:
    case 1:
      True{}
    case 2:
      True{}
    case 4:
      True{}
    case 7:
      True{}
    case _:
      False{}

def p.num.go(+c: U32, acc: String, st: U32, nx: U32, s: List<&2, Frame>) ->
  PS:
  match nx:
    case 8:
      p.post(c, p.push(p.numval(acc, st), s))
    case 9:
      p.err()
    case 10:
      PS{MLit{"nfinity", JFloat{"-Infinity"}}, s}
    case _:
      PS{MNum{SCon{Chr{c}, acc}, nx}, s}

def p.num(+c: U32, acc: String, +st: U32, s: List<&2, Frame>) -> PS:
  p.num.go(c, acc, st, p.nnext(st, p.ncls(c)), s)

# Literals: the chars still expected, then the value.
def p.lit.ok(r: String, v: Json, s: List<&2, Frame>) -> PS:
  match r:
    case SNil{}:
      p.push(v, s)
    case SCon{h, t}:
      PS{MLit{SCon{h, t}, v}, s}

def p.lit.if(r: String, v: Json, s: List<&2, Frame>, same: Bool) -> PS:
  match same:
    case True{}:
      p.lit.ok(r, v, s)
    case False{}:
      p.err()

def p.lit(c: U32, rest: String, v: Json, s: List<&2, Frame>) -> PS:
  match rest:
    case SNil{}:
      p.err()
    case SCon{Chr{x}, r}:
      p.lit.if(r, v, s, U32.is_eq(c, x))

# Strings.
def p.key_end(str: String, s: List<&2, Frame>) -> PS:
  match s:
    case Con{FObj{kvs, k}, r}:
      PS{MColon{}, Con{FObj{kvs, str}, r}}
    case _:
      p.err()

def p.str_end(str: String, key: Bool, s: List<&2, Frame>) -> PS:
  match key:
    case False{}:
      p.push(JStr{str}, s)
    case True{}:
      p.key_end(str, s)

def p.str.raw(c: U32, acc: String, key: Bool, s: List<&2, Frame>,
  ctl: Bool) -> PS:
  match ctl:
    case True{}:
      p.err()
    case False{}:
      PS{MStr{SCon{Chr{c}, acc}, key}, s}

def p.str(+c: U32, acc: String, key: Bool, s: List<&2, Frame>) -> PS:
  match c:
    case 34:
      p.str_end(String.reverse(acc), key, s)
    case 92:
      PS{MEsc{acc, key}, s}
    case _:
      p.str.raw(c, acc, key, s, (c < 32 : U32))

def p.chr(c: U32, acc: String, key: Bool, s: List<&2, Frame>) -> PS:
  PS{MStr{SCon{Chr{c}, acc}, key}, s}

def p.esc(c: U32, acc: String, key: Bool, s: List<&2, Frame>) -> PS:
  match c:
    case 34:
      p.chr(34, acc, key, s)
    case 92:
      p.chr(92, acc, key, s)
    case 47:
      p.chr(47, acc, key, s)
    case 98:
      p.chr(8, acc, key, s)
    case 102:
      p.chr(12, acc, key, s)
    case 110:
      p.chr(10, acc, key, s)
    case 114:
      p.chr(13, acc, key, s)
    case 116:
      p.chr(9, acc, key, s)
    case 117:
      PS{MHex{acc, key, 4, 0}, s}
    case _:
      p.err()

# Hex digit value, 16 if not a hex digit.
def p.hex.big(+c: U32, up: Bool) -> U32:
  match up:
    case True{}:
      (c - 55 : U32)
    case False{}:
      16

def p.hex.af(+c: U32, lo: Bool) -> U32:
  match lo:
    case True{}:
      (c - 87 : U32)
    case False{}:
      p.hex.big(c, (c >= 65 : U32) && (c <= 70 : U32))

def p.hex.if(+c: U32, d: Bool) -> U32:
  match d:
    case True{}:
      (c - 48 : U32)
    case False{}:
      p.hex.af(c, (c >= 97 : U32) && (c <= 102 : U32))

def p.hex(+c: U32) -> U32:
  p.hex.if(c, (c >= 48 : U32) && (c <= 57 : U32))

def p.is_hi(+v: U32) -> Bool:
  (v >= 55296 : U32) && (v <= 56319 : U32)

def p.is_lo(+v: U32) -> Bool:
  (v >= 56320 : U32) && (v <= 57343 : U32)

# A finished \u escape: a high surrogate waits for a possible low one.
def p.uend.if(v: U32, acc: String, key: Bool, s: List<&2, Frame>, hi: Bool) ->
  PS:
  match hi:
    case True{}:
      PS{MHi{acc, key, v}, s}
    case False{}:
      PS{MStr{SCon{Chr{v}, acc}, key}, s}

def p.uend(+v: U32, acc: String, key: Bool, s: List<&2, Frame>) -> PS:
  p.uend.if(v, acc, key, s, p.is_hi(v))

def p.hex1.fin(+v: U32, n: U32, acc: String, key: Bool, s: List<&2, Frame>) ->
  PS:
  match n:
    case 0:
      p.uend(v, acc, key, s)
    case _:
      PS{MHex{acc, key, n, v}, s}

def p.hex1(d: U32, acc: String, key: Bool, n: U32, v: U32,
  s: List<&2, Frame>) -> PS:
  match d:
    case 16:
      p.err()
    case _:
      p.hex1.fin((v * 16 + d : U32), (n - 1 : U32), acc, key, s)

def p.pair.if(hi: U32, +v: U32, acc: String, key: Bool, s: List<&2, Frame>,
  lo: Bool) -> PS:
  match lo:
    case True{}:
      PS{MStr{SCon{Chr{((hi - 55296) * 1024 + (v - 56320) + 65536 : U32)},
        acc}, key}, s}
    case False{}:
      p.uend(v, SCon{Chr{hi}, acc}, key, s)

def p.hex2.fin(hi: U32, +v: U32, n: U32, acc: String, key: Bool,
  s: List<&2, Frame>) -> PS:
  match n:
    case 0:
      p.pair.if(hi, v, acc, key, s, p.is_lo(v))
    case _:
      PS{MHex2{acc, key, hi, n, v}, s}

def p.hex2(d: U32, acc: String, key: Bool, hi: U32, n: U32, v: U32,
  s: List<&2, Frame>) -> PS:
  match d:
    case 16:
      p.err()
    case _:
      p.hex2.fin(hi, (v * 16 + d : U32), (n - 1 : U32), acc, key, s)

def p.hi(+c: U32, acc: String, key: Bool, hi: U32, s: List<&2, Frame>) -> PS:
  match c:
    case 92:
      PS{MHiB{acc, key, hi}, s}
    case _:
      p.str(c, SCon{Chr{hi}, acc}, key, s)

def p.hib(+c: U32, acc: String, key: Bool, hi: U32, s: List<&2, Frame>) ->
  PS:
  match c:
    case 117:
      PS{MHex2{acc, key, hi, 4, 0}, s}
    case _:
      p.esc(c, SCon{Chr{hi}, acc}, key, s)

def p.arr0(+c: U32, s: List<&2, Frame>) -> PS:
  match c:
    case 93:
      p.close_arr(s)
    case _:
      p.value(c, MArr0{}, s)

def p.obj0(c: U32, s: List<&2, Frame>) -> PS:
  match c:
    case 34:
      PS{MStr{SNil{}, True{}}, s}
    case 125:
      p.close_obj(s)
    case 32:
      PS{MObj0{}, s}
    case 10:
      PS{MObj0{}, s}
    case 13:
      PS{MObj0{}, s}
    case 9:
      PS{MObj0{}, s}
    case _:
      p.err()

def p.key(c: U32, s: List<&2, Frame>) -> PS:
  match c:
    case 34:
      PS{MStr{SNil{}, True{}}, s}
    case 32:
      PS{MKey{}, s}
    case 10:
      PS{MKey{}, s}
    case 13:
      PS{MKey{}, s}
    case 9:
      PS{MKey{}, s}
    case _:
      p.err()

def p.colon(c: U32, s: List<&2, Frame>) -> PS:
  match c:
    case 58:
      PS{MVal{}, s}
    case 32:
      PS{MColon{}, s}
    case 10:
      PS{MColon{}, s}
    case 13:
      PS{MColon{}, s}
    case 9:
      PS{MColon{}, s}
    case _:
      p.err()

def p.step(+c: U32, ps: PS) -> PS:
  match ps:
    case PS{mode, s}:
      match mode:
        case MVal{}:
          p.value(c, MVal{}, s)
        case MArr0{}:
          p.arr0(c, s)
        case MObj0{}:
          p.obj0(c, s)
        case MKey{}:
          p.key(c, s)
        case MColon{}:
          p.colon(c, s)
        case MAfter{}:
          p.after(c, s)
        case MEnd{v}:
          p.post(c, PS{MEnd{v}, s})
        case MStr{acc, key}:
          p.str(c, acc, key, s)
        case MEsc{acc, key}:
          p.esc(c, acc, key, s)
        case MHex{acc, key, n, v}:
          p.hex1(p.hex(c), acc, key, n, v, s)
        case MHi{acc, key, hi}:
          p.hi(c, acc, key, hi, s)
        case MHiB{acc, key, hi}:
          p.hib(c, acc, key, hi, s)
        case MHex2{acc, key, hi, n, v}:
          p.hex2(p.hex(c), acc, key, hi, n, v, s)
        case MNum{acc, st}:
          p.num(c, acc, st, s)
        case MLit{rest, v}:
          p.lit(c, rest, v, s)
        case MErr{}:
          p.err()

def p.run(s: String, ps: PS) -> PS:
  match s:
    case SNil{}:
      ps
    case SCon{Chr{c}, t}:
      p.run(t, p.step(c, ps))

def p.finish.num(acc: String, st: U32, ok: Bool) -> Maybe<&2, Json>:
  match ok:
    case True{}:
      Some{p.numval(acc, st)}
    case False{}:
      None{}

def p.finish(ps: PS) -> Maybe<&2, Json>:
  match ps:
    case PS{MEnd{v}, Nil{}}:
      Some{v}
    case PS{MNum{acc, +st}, Nil{}}:
      p.finish.num(acc, st, p.is_term(st))
    case _:
      None{}

def p.init() -> PS:
  PS{MVal{}, Nil{}}

def parse(s: String) -> Maybe<&2, Json>:
  p.finish(p.run(s, p.init()))

# JSON lines: one record per '\n'; built reversed, then flipped.
def p.lines.end(ps: PS, empty: Bool, out: List<&2, Maybe<&2, Json>>) ->
  List<&2, Maybe<&2, Json>>:
  match empty:
    case True{}:
      out
    case False{}:
      Con{p.finish(ps), out}

def p.lines(s: String, ps: PS, empty: Bool,
  out: List<&2, Maybe<&2, Json>>) -> List<&2, Maybe<&2, Json>>:
  match s:
    case SNil{}:
      p.lines.end(ps, empty, out)
    case SCon{Chr{c}, t}:
      match c:
        case 10:
          p.lines(t, p.init(), True{}, Con{p.finish(ps), out})
        case k:
          p.lines(t, p.step(k, ps), False{}, out)

def parse_lines(s: String) -> List<&2, Maybe<&2, Json>>:
  List.reverse(&2, Maybe<&2, Json>, p.lines(s, p.init(), True{}, Nil{}))

# Printer
# =======
# canon() sorts every object's keys (stable merge sort, then keeps the last
# of equal keys); emit() writes into a reversed accumulator.

def s.le(a: +Sigma<String, _ => Json>, b: +Sigma<String, _ => Json>) -> Bool:
  (ka, va) = a
  (kb, vb) = b
  String.is_le(ka, kb)

def s.dedup.put(same: Bool, k: String, v: Json,
  acc: List<&2, +Sigma<String, _ => Json>>) ->
  List<&2, +Sigma<String, _ => Json>>:
  match same:
    case True{}:
      acc
    case False{}:
      Con{(k, v), acc}

# Sorted input; keeps the last of each run of equal keys. acc is reversed.
def s.dedup(xs: List<&2, +Sigma<String, _ => Json>>,
  acc: List<&2, +Sigma<String, _ => Json>>) ->
  List<&2, +Sigma<String, _ => Json>>:
  match xs:
    case Nil{}:
      List.reverse(&2, +Sigma<String, _ => Json>, acc)
    case Con{(ka, va), Nil{}}:
      List.reverse(&2, +Sigma<String, _ => Json>, Con{(ka, va), acc})
    case Con{(+ka, va), Con{(+kb, vb), t}}:
      s.dedup(Con{(kb, vb), t}, s.dedup.put(String.eq(ka, kb), ka, va, acc))

# Tail-recursive over container width: children are canonicalised into a
# reversed accumulator, and an object is sorted once, at its end.
def s.canon(j: Json, ax: List<&2, Json>,
  ak: List<&2, +Sigma<String, _ => Json>>) -> Json:
  match j:
    case JArr{Nil{}}:
      JArr{List.reverse(&2, Json, ax)}
    case JArr{Con{h, t}}:
      s.canon(JArr{t}, Con{s.canon(h, Nil{}, Nil{}), ax}, Nil{})
    case JObj{Nil{}}:
      JObj{s.dedup(List.sort(~+Sigma<String, _ => Json>, ~s.le,
        List.reverse(&2, +Sigma<String, _ => Json>, ak)), Nil{})}
    case JObj{Con{(k, v), t}}:
      s.canon(JObj{t}, Nil{}, Con{(k, s.canon(v, Nil{}, Nil{})), ak})
    case x:
      x

def e.hd.if(d: U32, small: Bool) -> Char:
  match small:
    case True{}:
      Chr{(d + 48 : U32)}
    case False{}:
      Chr{(d + 87 : U32)}

def e.hd(+d: U32) -> Char:
  e.hd.if(d, (d < 10 : U32))

def e.u4(+v: U32, acc: String) -> String:
  SCon{e.hd((v % 16 : U32)), SCon{e.hd((v / 16 % 16 : U32)),
    SCon{e.hd((v / 256 % 16 : U32)), SCon{e.hd((v / 4096 % 16 : U32)),
    SCon{Chr{117}, SCon{Chr{92}, acc}}}}}}

def e.wide(+c: U32, acc: String, big: Bool) -> String:
  match big:
    case True{}:
      e.u4(((c - 65536) % 1024 + 56320 : U32),
        e.u4(((c - 65536) / 1024 + 55296 : U32), acc))
    case False{}:
      e.u4(c, acc)

def e.plain(+c: U32, acc: String, ok: Bool) -> String:
  match ok:
    case True{}:
      SCon{Chr{c}, acc}
    case False{}:
      e.wide(c, acc, (c >= 65536 : U32))

def e.two(k: U32, acc: String) -> String:
  SCon{Chr{k}, SCon{Chr{92}, acc}}

def e.char(+c: U32, acc: String) -> String:
  match c:
    case 34:
      e.two(34, acc)
    case 92:
      e.two(92, acc)
    case 10:
      e.two(110, acc)
    case 13:
      e.two(114, acc)
    case 9:
      e.two(116, acc)
    case 8:
      e.two(98, acc)
    case 12:
      e.two(102, acc)
    case _:
      e.plain(c, acc, (c >= 32 : U32) && (c <= 126 : U32))

def e.chars(s: String, acc: String) -> String:
  match s:
    case SNil{}:
      acc
    case SCon{Chr{c}, t}:
      e.chars(t, e.char(c, acc))

def e.str(s: String, acc: String) -> String:
  SCon{Chr{34}, e.chars(s, SCon{Chr{34}, acc})}

def e.open(mode: Bool, c: U32) -> Char:
  match mode:
    case True{}:
      Chr{c}
    case False{}:
      Chr{44}

def e.empty(mode: Bool, o: U32, c: U32, acc: String) -> String:
  match mode:
    case True{}:
      SCon{Chr{c}, SCon{Chr{o}, acc}}
    case False{}:
      SCon{Chr{c}, acc}

def e.bool(b: Bool, acc: String) -> String:
  match b:
    case True{}:
      String.reverse.go("true", acc)
    case False{}:
      String.reverse.go("false", acc)

# first: True = the container's opening char is still to be written.
def e.emit(j: Json, first: Bool, acc: String) -> String:
  match j:
    case JNull{}:
      String.reverse.go("null", acc)
    case JBool{b}:
      e.bool(b, acc)
    case JInt{t}:
      String.reverse.go(t, acc)
    case JFloat{t}:
      String.reverse.go(t, acc)
    case JStr{str}:
      e.str(str, acc)
    case JArr{Nil{}}:
      e.empty(first, 91, 93, acc)
    case JArr{Con{h, t}}:
      e.emit(JArr{t}, False{}, e.emit(h, True{}, SCon{e.open(first, 91), acc}))
    case JObj{Nil{}}:
      e.empty(first, 123, 125, acc)
    case JObj{Con{(k, v), t}}:
      e.emit(JObj{t}, False{}, e.emit(v, True{},
        SCon{Chr{58}, e.str(k, SCon{e.open(first, 123), acc})}))

def show(j: Json) -> String:
  String.reverse(e.emit(s.canon(j, Nil{}, Nil{}), True{}, SNil{}))

# Helpers for the exchange port
# =============================

def str_eq(a: String, b: String) -> Bool:
  String.eq(a, b)

def obj(kvs: List<&2, +Sigma<String, _ => Json>>) -> Json:
  JObj{kvs}

def g.pick(same: Bool, v: Json, found: Maybe<&2, Json>) -> Maybe<&2, Json>:
  match same:
    case True{}:
      Some{v}
    case False{}:
      found

def g.find(+key: String, xs: List<&2, +Sigma<String, _ => Json>>,
  found: Maybe<&2, Json>) -> Maybe<&2, Json>:
  match xs:
    case Nil{}:
      found
    case Con{(k, v), t}:
      g.find(key, t, g.pick(String.eq(k, key), v, found))

def get(+key: String, j: Json) -> Maybe<&2, Json>:
  match j:
    case JObj{kvs}:
      g.find(key, kvs, None{})
    case _:
      None{}

def g.keys(xs: List<&2, +Sigma<String, _ => Json>>) -> List<&2, String>:
  match xs:
    case Nil{}:
      Nil{}
    case Con{(k, v), t}:
      Con{k, g.keys(t)}

def keys(j: Json) -> List<&2, String>:
  match j:
    case JObj{kvs}:
      g.keys(kvs)
    case _:
      Nil{}

# File reading
# ============

def rd.size(p: File & Result<&1, &1, U32 & String, U32>) -> IO(File & U32):
  (f, r) = p
  do IO<File & U32>:
    n : U32 <- IO.pass(U32, r)
    return (f, n)

def rd.text(p: File & Result<&1, &1, U32 & String, String>) ->
  IO(File & String):
  (f, r) = p
  do IO<File & String>:
    t : String <- IO.pass(String, r)
    return (f, t)

def rd.close(p: File & String) -> IO(String):
  (f, t) = p
  do IO<String>:
    File.close(f)
    return t

def rd.read(p: File & U32) -> IO(File & String):
  (f, n) = p
  do IO<File & String>:
    q : File & Result<&1, &1, U32 & String, String> <- File.read(f, n)
    rd.text(q)

def read_file(path: String) -> IO(String):
  do IO<String>:
    f : File <- IO.try(File, File.open(path, "r"))
    p : File & Result<&1, &1, U32 & String, U32> <- File.size(f)
    q : File & U32 <- rd.size(p)
    r : File & String <- rd.read(q)
    rd.close(r)

def read_jsonl(path: String) -> IO(List<&2, Maybe<&2, Json>>):
  do IO<List<&2, Maybe<&2, Json>>>:
    t : String <- read_file(path)
    return parse_lines(t)
