"""bend/exchange/rules.bend against Python: lifecycle transitions (contracts.TRANSITIONS), outcome_for,
and the licence schedule helpers (due_ticks, split, clip_schedule, instalments) over seeded random
inputs, through the native build and the JS path. Skips when `bend` is not installed."""

import os
import random
import subprocess
from pathlib import Path

import pytest

from railhead.exchange.bend import find_bend
from railhead.exchange.contracts import TRANSITIONS
from railhead.exchange.exchange import INSTALMENT_TICKS, clip_schedule, due_ticks, instalments, split
from railhead.exchange.settlement import outcome_for

ROOT = Path(__file__).resolve().parents[2]
DRIVER = ROOT / "bend" / "exchange" / "t_rules.bend"


def _bend():
    found = find_bend()  # the same lookup as the bridge: $RAILHEAD_BEND, PATH, ~/.bend/bin/bend
    return str(found) if found is not None else None


BEND = _bend()
pytestmark = pytest.mark.skipif(BEND is None, reason="bend (Bend 2) not installed")
ENV = {**os.environ, "BEND_NO_TELEMETRY": "1"}


@pytest.fixture(scope="module")
def native(tmp_path_factory):
    out = tmp_path_factory.mktemp("bendrules") / "t_rules"
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


def dues_s(s):
    return "[" + " ".join(f"{d}:{a}" for d, a in s) + "]"


def lst(xs):
    return ",".join(str(x) for x in xs) or "-"


def tick(rng):
    return rng.choice([0, 1, 6659, 6660, 6661, 13320]) if rng.random() < 0.2 else rng.randrange(0, 80000)


def cases(seed, n):
    rng = random.Random(seed)
    ops, want = [], []
    for old in TRANSITIONS:
        for new in TRANSITIONS:
            ops.append(f"move {old} {new}")
            want.append(new if new in TRANSITIONS[old] else "bad_transition")
        ops.append(f"term {old}")
        want.append(str(not TRANSITIONS[old]))
    for _ in range(n):
        r = rng.random()
        if r < 0.15:
            ws, dl = tick(rng), tick(rng)
            ops.append(f"due {ws} {dl}")
            want.append(" ".join(str(x) for x in due_ticks(ws, dl)))
        elif r < 0.4:
            total = rng.choice([0, -3, 1, 2, 3, 7, rng.randrange(0, 10**6), rng.getrandbits(150)])
            dues = sorted(rng.sample(range(100000), rng.randrange(0, 7)))
            ops.append(f"split {total} {lst(dues)}")
            want.append(dues_s(split(total, dues)))
        elif r < 0.7:
            fee = rng.choice([0, 1, 3, 999, rng.randrange(0, 10**7), rng.getrandbits(100)])
            ws = tick(rng)
            dl = ws + rng.choice([0, 1, INSTALMENT_TICKS, rng.randrange(0, 60000)])
            last = rng.choice([None, None, ws, dl, rng.randrange(0, 90000)])
            ops.append(f"inst {fee} {ws} {dl} {'none' if last is None else last}")
            want.append(dues_s(instalments(fee, ws, dl, last)))
        elif r < 0.85:
            sched = [(rng.randrange(0, 50000), rng.choice([0, rng.randrange(0, 1000)]))
                     for _ in range(rng.randrange(0, 5))]
            sched.sort()
            last = rng.choice([None, rng.randrange(0, 60000)])
            floor = rng.randrange(0, 60000)
            ops.append(f"clip {floor} {'none' if last is None else last} "
                       f"{','.join(f'{d}:{a}' for d, a in sched) or '-'}")
            want.append(dues_s(clip_schedule(sched, last, floor)))
        else:
            q = rng.randrange(1, 50)
            d = rng.choice([0, q, q - 1, q + 3, rng.randrange(0, 60)])
            ops.append(f"outcome {d} {q}")
            want.append(outcome_for(d, q))
    return ops, want


def test_rules_native(native, tmp_path):
    ops, want = cases(5, 3000)
    got = run_lines(native, ops, tmp_path)
    bad = [(o, w, g) for o, w, g in zip(ops, want, got) if w != g]
    assert len(got) == len(want) and not bad, bad[:10]


def test_rules_js(native, tmp_path):
    ops, want = cases(6, 200)
    assert run_lines([BEND, str(DRIVER)], ops, tmp_path) == want
