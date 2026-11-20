"""bend/exchange/int.bend against Python ints: every op of the I API (+ - * // % neg, comparisons,
read/show) over seeded random values up to 2**200 (negatives and zero included), through the native
build and the JS path. Skips when `bend` is not installed."""

import os
import random
import re
import subprocess
from pathlib import Path

import pytest

from railhead.exchange.bend import find_bend

ROOT = Path(__file__).resolve().parents[2]
DRIVER = ROOT / "bend" / "exchange" / "t_int.bend"


def _bend():
    found = find_bend()  # the same lookup as the bridge: $RAILHEAD_BEND, PATH, ~/.bend/bin/bend
    return str(found) if found is not None else None


BEND = _bend()
pytestmark = pytest.mark.skipif(BEND is None, reason="bend (Bend 2) not installed")
ENV = {**os.environ, "BEND_NO_TELEMETRY": "1"}


@pytest.fixture(scope="module")
def native(tmp_path_factory):
    out = tmp_path_factory.mktemp("bendint") / "t_int"
    subprocess.run([BEND, str(DRIVER), "-o", str(out)], check=True, env=ENV, capture_output=True,
                   timeout=600)
    return [str(out)]


def run_lines(cmd, lines, tmp_path):
    path = tmp_path / "ops.txt"
    path.write_text("".join(line + "\n" for line in lines))
    res = subprocess.run([*cmd, str(path)], env=ENV, capture_output=True, timeout=600, check=False)
    assert res.returncode == 0, res.stderr.decode(errors="replace")
    out = res.stdout.decode("ascii").split("\n")
    assert out[-1] == ""
    return out[:-1]


def rand_int(rng):
    kind = rng.random()
    if kind < 0.1:
        v = rng.choice([0, 1, 2, 3, 7, 10, 255, 256, 2**32 - 1, 2**32, 2**64])
    elif kind < 0.4:
        v = rng.randrange(0, 1000)
    else:
        v = rng.getrandbits(rng.randrange(1, 201))
    return -v if rng.random() < 0.5 else v


def b(x):
    return "True" if x else "False"


OPS2 = {
    "add": lambda a, c: str(a + c), "sub": lambda a, c: str(a - c), "mul": lambda a, c: str(a * c),
    "floordiv": lambda a, c: str(a // c), "mod": lambda a, c: str(a % c),
    "cmp": lambda a, c: str((a > c) - (a < c)),
    "eq": lambda a, c: b(a == c), "ne": lambda a, c: b(a != c), "lt": lambda a, c: b(a < c),
    "le": lambda a, c: b(a <= c), "gt": lambda a, c: b(a > c), "ge": lambda a, c: b(a >= c),
    "min": lambda a, c: str(min(a, c)), "max": lambda a, c: str(max(a, c)),
}
OPS1 = {
    "neg": lambda a: str(-a), "show": lambda a: str(a), "zero": lambda a: b(a == 0),
    "isneg": lambda a: b(a < 0), "ispos": lambda a: b(a > 0),
}
CANONICAL = re.compile(r"-?(0|[1-9][0-9]*)")


def py_read(text):
    if CANONICAL.fullmatch(text) and text != "-0":
        return str(int(text))
    return "None"


def cases(seed, n):
    rng = random.Random(seed)
    ops, want = [], []
    for _ in range(n):
        a, c = rand_int(rng), rand_int(rng)
        name = rng.choice(list(OPS2) + list(OPS1))
        if name in OPS2:
            if name in ("floordiv", "mod") and c == 0:
                c = rng.choice([1, -1, 3, -7, 2**70])
            ops.append(f"{name} {a} {c}")
            want.append(OPS2[name](a, c))
        else:
            ops.append(f"{name} {a}")
            want.append(OPS1[name](a))
    # every sign combination of floor division, exact and inexact
    for a in (7, -7, 6, -6, 0, 2**100 + 1, -(2**100 + 1)):
        for c in (2, -2, 3, -3, 1, -1, 2**50, -(2**50)):
            for name in ("floordiv", "mod"):
                ops.append(f"{name} {a} {c}")
                want.append(OPS2[name](a, c))
    return ops, want


READ_TEXTS = ["0", "-0", "00", "01", "-01", "", "-", "+1", "1a", "a", "12 ", "-5", "10",
              "123456789012345678901234567890123456789012345678901234567890",
              "-99999999999999999999999999999999999999999999", "1.0", "1e5", "--1", "9" * 70]


def read_cases(seed, n):
    rng = random.Random(seed)
    texts = list(READ_TEXTS)
    for _ in range(n):
        texts.append(str(rand_int(rng)))
    ops = [f"read {t}" for t in texts if " " not in t and t != ""]
    want = [py_read(op[5:]) for op in ops]
    return ops, want


def test_int_ops_native(native, tmp_path):
    ops, want = cases(20261120, 4000)
    got = run_lines(native, ops, tmp_path)
    bad = [(o, w, g) for o, w, g in zip(ops, want, got) if w != g]
    assert len(got) == len(want) and not bad, bad[:10]


def test_int_read_show_native(native, tmp_path):
    ops, want = read_cases(7, 1500)
    got = run_lines(native, ops, tmp_path)
    bad = [(o, w, g) for o, w, g in zip(ops, want, got) if w != g]
    assert len(got) == len(want) and not bad, bad[:10]


def test_int_small_conversions_native(native, tmp_path):
    ops = [f"u32 {v}" for v in (0, 1, 2, 4294967295, 65536, 12345)]
    want = [str(v) for v in (0, 1, 2, 4294967295, 65536, 12345)]
    ops += [f"nat {v}" for v in (0, 1, 5, 300, -4)]
    want += ["0", "1", "5", "300", "0"]
    ops += [f"fromnat {v}" for v in (0, 1, 77)]
    want += ["0", "1", "77"]
    got = run_lines(native, ops, tmp_path)
    assert got == want


def test_int_js_agrees(native, tmp_path):
    ops, want = cases(99, 300)
    rops, rwant = read_cases(11, 100)
    got = run_lines([BEND, str(DRIVER)], ops + rops, tmp_path)
    assert got == want + rwant


def test_laws_verdict():
    """bend/exchange/PROOF.bend proves every law of LAWS.bend; --verdict rechecks with the kernel."""
    res = subprocess.run([BEND, str(ROOT / "bend" / "exchange" / "PROOF.bend"), "--verdict"], env=ENV,
                         capture_output=True, timeout=600, check=False)
    out = (res.stdout + res.stderr).decode(errors="replace").strip().splitlines()
    assert out and out[-1] == "ALL PROOFS CHECK", out[-20:]
