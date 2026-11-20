"""bend/exchange/json.bend against Python's json: J.show(J.parse(line)) must be byte-identical to
json.dumps(json.loads(line), sort_keys=True, separators=(",", ":")). Seeded random documents, run
through the native build and the JS path. Skips when `bend` is not installed."""

import json
import os
import random
import subprocess
from pathlib import Path

import pytest

from railhead.exchange.bend import find_bend

ROOT = Path(__file__).resolve().parents[2]
DRIVER = ROOT / "bend" / "exchange" / "t_json.bend"


def _bend():
    found = find_bend()  # the same lookup as the bridge: $RAILHEAD_BEND, PATH, ~/.bend/bin/bend
    return str(found) if found is not None else None


BEND = _bend()
pytestmark = pytest.mark.skipif(BEND is None, reason="bend (Bend 2) not installed")
ENV = {**os.environ, "BEND_NO_TELEMETRY": "1"}


@pytest.fixture(scope="module")
def native(tmp_path_factory):
    out = tmp_path_factory.mktemp("bendjson") / "t_json"
    subprocess.run([BEND, str(DRIVER), "-o", str(out)], check=True, env=ENV, capture_output=True,
                   timeout=600)
    return [str(out)]


@pytest.fixture(scope="module", params=["native", "js"])
def runner(request, native):
    return native if request.param == "native" else [BEND, str(DRIVER)]


def run_lines(cmd, lines, tmp_path):
    """Write lines (str) as UTF-8 JSONL, return the driver's output lines."""
    path = tmp_path / "in.jsonl"
    path.write_bytes("".join(line + "\n" for line in lines).encode("utf-8"))
    res = subprocess.run([*cmd, str(path)], env=ENV, capture_output=True, timeout=600, check=False)
    assert res.returncode == 0, res.stderr.decode(errors="replace")
    out = res.stdout.decode("ascii").split("\n")  # never splitlines(): U+2028 etc. are data
    assert out[-1] == ""
    return out[:-1]


def canon(line):
    return json.dumps(json.loads(line), sort_keys=True, separators=(",", ":"))


# Random documents. Objects are pair lists (duplicate keys allowed), serialised by hand so the
# input exercises whitespace, key order and duplicates; scalars go through json.dumps.

SPECIAL_FLOATS = [0.0, -0.0, 1e16, 1e+22, 5e-324, 1.7976931348623157e308, 2.2250738585072014e-308, 0.1,
                  1e-7, 123456789012345680.0, float("nan"), float("inf"), float("-inf"), 1.5, -2.5e-10]


def rand_char(rng, ascii_safe_only):
    k = rng.random()
    if k < 0.45:
        return chr(rng.randint(0x20, 0x7E))
    if k < 0.55:
        return rng.choice('"\\/\b\f\n\r\t')
    if k < 0.62:
        return chr(rng.choice([*range(0x20), 0x7F]))
    if k < 0.80:
        return chr(rng.randint(0x80, 0xFFFF) if ascii_safe_only else rng.choice(
            [rng.randint(0x80, 0xD7FF), rng.randint(0xE000, 0xFFFF)]))
    if k < 0.92:
        return chr(rng.randint(0x10000, 0x10FFFF))
    if ascii_safe_only:  # lone or paired surrogates, only representable as \u escapes
        hi, lo = chr(rng.randint(0xD800, 0xDBFF)), chr(rng.randint(0xDC00, 0xDFFF))
        return rng.choice([hi, lo, hi + lo, lo + hi, hi + hi + lo])
    return chr(rng.choice([0x2028, 0x2029, 0x85, 0xFEFF, 0xFFFF]))


def rand_str(rng, ascii_safe_only, max_len=12):
    return "".join(rand_char(rng, ascii_safe_only) for _ in range(rng.randint(0, max_len)))


def rand_scalar(rng):
    k = rng.random()
    if k < 0.1:
        return rng.choice([None, True, False])
    if k < 0.35:
        return rng.randint(-1000, 1000)
    if k < 0.45:
        digits = rng.randint(15, 400)
        return rng.choice([1, -1]) * rng.randint(10 ** (digits - 1), 10 ** digits)
    if k < 0.55:
        return rng.choice(SPECIAL_FLOATS)
    if k < 0.75:
        return rng.choice([rng.uniform(-1e6, 1e6), rng.random() * 10 ** rng.randint(-330, 308),
                           -rng.random() * 10 ** rng.randint(-20, 30)])
    return "STR"


def rand_doc(rng, ascii_safe_only, depth=0):
    k = rng.random()
    if depth >= 5 or k < 0.4:
        val = rand_scalar(rng)
        return ("scalar", rand_str(rng, ascii_safe_only) if val == "STR" else val)
    if k < 0.7:
        return ("arr", [rand_doc(rng, ascii_safe_only, depth + 1) for _ in range(rng.randint(0, 5))])
    pairs = []
    for _ in range(rng.randint(0, 6)):
        dup = pairs and rng.random() < 0.2
        key = rng.choice(pairs)[0] if dup else rand_str(rng, ascii_safe_only, 6)
        pairs.append((key, rand_doc(rng, ascii_safe_only, depth + 1)))
    return ("obj", pairs)


def ws(rng):
    return rng.choice(["", "", "", " ", "  ", "\t", "\r", " \t\r "])


def dump(rng, doc, ensure_ascii):
    kind, val = doc
    if kind == "scalar":
        return json.dumps(val, ensure_ascii=ensure_ascii)
    if kind == "arr":
        return "[" + ws(rng) + ",".join(ws(rng) + dump(rng, d, ensure_ascii) + ws(rng) for d in val) \
            + ws(rng) + "]"
    parts = [ws(rng) + json.dumps(key, ensure_ascii=ensure_ascii) + ws(rng) + ":" + ws(rng)
             + dump(rng, d, ensure_ascii) + ws(rng) for key, d in val]
    return "{" + ws(rng) + ",".join(parts) + ws(rng) + "}"


def gen_lines(seed, n):
    rng = random.Random(seed)
    lines = []
    for i in range(n):
        mode = i % 3
        if mode == 0:  # ASCII input with \u escapes, lone surrogates included
            line = dump(rng, rand_doc(rng, True), True)
        elif mode == 1:  # raw UTF-8 input (no surrogates: they have no UTF-8 encoding)
            line = dump(rng, rand_doc(rng, False), False)
        else:  # exactly what the exchange sees: json.dumps(record)
            line = json.dumps(json.loads(dump(rng, rand_doc(rng, True), True)))
        lines.append(ws(rng) + line + ws(rng))
    return lines


def test_duplicate_key_dict_semantics():
    # Python dict: first position, last value; the canonical form sorts anyway.
    assert canon('{"b":1,"a":2,"b":3}') == '{"a":2,"b":3}'


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_random_documents(runner, tmp_path, seed):
    lines = gen_lines(seed, 1500)
    assert run_lines(runner, lines, tmp_path) == [canon(line) for line in lines]


FIXED = [
    '{"b": 1, "a": [true, false, null, -0, 1e+16, 5e-324, -0.0]}',
    '"\\ud83d\\ude00"', '"\\ud83d"', '"\\ude00\\ud83d"', '"\\ud83d\\ud83d\\ude00"', '"\\ud83dx"',
    '"\\ud83d\\n"', '"\\ud83d\\\\u0041"', '"\\uD83D\\uDE00\\u00E9\\u00e9"', '"\\u0000\\u001f\\u007f\\u0080"',
    '"\\/\\b\\f\\n\\r\\t\\"\\\\"', '"\U0001F600\u00e9\x7f"', "NaN", "-Infinity", "Infinity",
    "[NaN,Infinity,-Infinity]", " 0 ", "-0", "1e+300", "-0.0015", "1.5e-07", "[]", "{}", "[[[[]]]]",
    '{"":{"":[]}}', '{"a":1,"a":{"x":1},"a":2}', '{"\\u00e9":1,"z":2,"\\ud800":3,"\U0001F600":4}',
    "12345678901234567890123456789012345678901234567890", "-98765432109876543210",
]

INVALID = [
    "", " ", "[", "]", "{", "}", "[1,]", "[,1]", "{,}", '{"a"}', '{"a":}', '{"a" 1}', '{"a":1,}',
    "{1:2}", "01", "-01", "1.", ".5", "+1", "1e", "1e+", "-", "--1", "-NaN", "nan", "inf",
    "-infinity", "tru", "truee", "nul", "True", "[1 2]", '"abc', '"\\x"', '"\\u12"', '"\\u12g4"',
    '"\\ud800\\u12g4"', '"\\ud800\\x"', '"a\tb"', '"\x01"', "1 2", "[1]]", "[1]x", '"a"b',
    "\u00a01", "[1,,2]", "{'a':1}", "0x10", "1_000", "Infinityy", "-Infinity1",
]


def test_fixed_cases(runner, tmp_path):
    assert run_lines(runner, FIXED, tmp_path) == [canon(line) for line in FIXED]


def test_invalid_inputs_rejected(runner, tmp_path):
    for line in INVALID:
        with pytest.raises(ValueError):
            json.loads(line)
    # "" is a blank line: the driver reports it as a parse error too.
    assert run_lines(runner, INVALID, tmp_path) == ["PARSE_ERROR"] * len(INVALID)


def run_api(cmd, lines, tmp_path):
    path = tmp_path / "api.jsonl"
    path.write_bytes("".join(line + "\n" for line in lines).encode("utf-8"))
    res = subprocess.run([*cmd, str(path), "api"], env=ENV, capture_output=True, timeout=600, check=False)
    assert res.returncode == 0, res.stderr.decode(errors="replace")
    return res.stdout.decode("ascii").split("\n")[:-1]


def api_expected(line):
    """What the driver's api mode prints: J.keys order, J.get per key, J.obj with a duplicate key."""
    d = json.loads(line)
    keys = list(d)
    d2 = dict(d)
    if keys:
        d2[keys[0]] = None
    return json.dumps([keys, [d[k] for k in keys], d2], sort_keys=True, separators=(",", ":"))


def test_api_keys_get_obj(runner, tmp_path):
    # J.parse (not the lines reader), J.keys (first-occurrence order), J.get (last value), J.obj + J.show
    # with a duplicate key (last wins).
    rng = random.Random(9)
    lines = ['{"b": 1, "a": 2, "b": 3}', "{}", '{"x":1,"x":2,"x":3}']
    while len(lines) < 600:
        doc = rand_doc(rng, len(lines) % 2 == 0)
        if doc[0] == "obj":
            lines.append(dump(rng, doc, len(lines) % 2 == 0))
    assert run_api(runner, lines, tmp_path) == [api_expected(line) for line in lines]
