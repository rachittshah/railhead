"""bend/exchange/ledger.bend against src/railhead/exchange/ledger.py: seeded random scripts of
constructor calls, transfers (valid and invalid, forced or not, escrow and bond accounts, huge
amounts) and pays; error codes, balances, history and conservation must agree after every step.
Native build and the JS path. Skips when `bend` is not installed."""

import os
import random
import subprocess
from pathlib import Path

import pytest

from railhead.exchange.bend import find_bend
from railhead.exchange.errors import ExchangeError
from railhead.exchange.ledger import Ledger

ROOT = Path(__file__).resolve().parents[2]
DRIVER = ROOT / "bend" / "exchange" / "t_ledger.bend"


def _bend():
    found = find_bend()  # the same lookup as the bridge: $RAILHEAD_BEND, PATH, ~/.bend/bin/bend
    return str(found) if found is not None else None


BEND = _bend()
pytestmark = pytest.mark.skipif(BEND is None, reason="bend (Bend 2) not installed")
ENV = {**os.environ, "BEND_NO_TELEMETRY": "1"}


@pytest.fixture(scope="module")
def native(tmp_path_factory):
    out = tmp_path_factory.mktemp("bendledger") / "t_ledger"
    subprocess.run([BEND, str(DRIVER), "-o", str(out)], check=True, env=ENV, capture_output=True,
                   timeout=600)
    return [str(out)]


def run_lines(cmd, lines, tmp_path):
    path = tmp_path / "script.txt"
    path.write_text("".join(line + "\n" for line in lines))
    res = subprocess.run([*cmd, str(path)], env=ENV, capture_output=True, timeout=600, check=False)
    assert res.returncode == 0, res.stderr.decode(errors="replace")
    out = res.stdout.decode("ascii").split("\n")
    assert out[-1] == ""
    return out[:-1]


def w(s):
    return "-" if s == "" else s


def amount(rng):
    r = rng.random()
    if r < 0.08:
        return rng.choice([0, -1, -(2**90)])
    if r < 0.2:
        return rng.getrandbits(rng.randrange(1, 200)) + 1
    return rng.randrange(1, 400)


class Model:
    """Runs the same script on Python's Ledger and records the expected driver output."""

    def __init__(self):
        self.ledger = None

    def new(self, companies, credit):
        try:
            self.ledger = Ledger(companies, credit)
            return "ok"
        except ExchangeError as e:
            self.ledger = None
            return e.code

    def transfer(self, src, dst, amt, reason, ref, tick, force):
        if self.ledger is None:
            return "noledger"
        try:
            t = self.ledger.transfer(src, dst, amt, reason=reason, ref=ref, tick=tick, force=force)
            return f"ok {t.seq}"
        except ExchangeError as e:
            return e.code

    def pay(self, src, dst, amt, tick, ref):
        if self.ledger is None:
            return "noledger"
        try:
            return f"ok {self.ledger.pay(src, dst, amt, tick=tick, ref=ref).seq}"
        except ExchangeError as e:
            return e.code

    def state(self):
        if self.ledger is None:
            return "noledger"
        bal = "".join(f" {k}={v}" for k, v in self.ledger.balances().items())
        hist = "".join(f" ({t.seq},{t.tick},{t.src},{t.dst},{t.amount},{t.reason},{t.ref})"
                       for t in self.ledger.history)
        return f"bal{bal} | hist{hist}"


def script(seed, steps):
    rng = random.Random(seed)
    m = Model()
    lines, want = [], []
    n = rng.randrange(1, 5)
    companies = [f"c{i}" for i in range(n)]
    if rng.random() < 0.15:  # a bad constructor first
        bad = rng.choice([companies + [""], companies + ["x:y"], companies + ["exchange"],
                          companies + [companies[0]]])
        cr = rng.choice([None, 10, -1])
        lines.append(f"new {'none' if cr is None else cr} {','.join(bad)}")
        want.append(m.new(bad, cr))
    credit = rng.choice([None, 0, 100, 10**6, -5]) if rng.random() < 0.9 else None
    lines.append(f"new {'none' if credit is None else credit} {','.join(companies) or '-'}")
    want.append(m.new(companies, credit))
    if m.ledger is None:
        lines.append(f"new none {','.join(companies)}")
        want.append(m.new(companies, None))
    contracts = [f"k{i}" for i in range(3)]
    accounts = companies + ["exchange"] + [f"escrow:{k}" for k in contracts] + \
        [f"escrow:{k}:bond" for k in contracts] + ["zz", "escrow"]
    for _ in range(steps):
        r = rng.random()
        if r < 0.7:
            src, dst = rng.choice(accounts), rng.choice(accounts)
            if rng.random() < 0.3 and src != "exchange":  # fund escrow from a company
                src, dst = rng.choice(companies + ["exchange"]), rng.choice(accounts[n + 1:n + 7])
            amt = amount(rng)
            reason, ref = rng.choice(["pay", "sign", "settle", "penalty"]), rng.choice(["", "k0", "o3"])
            tick, force = rng.randrange(0, 10**6), rng.random() < 0.3
            lines.append(f"transfer {src} {dst} {amt} {reason} {w(ref)} {tick} {int(force)}")
            want.append(m.transfer(src, dst, amt, reason, ref, tick, force))
        elif r < 0.85:
            src, dst = rng.choice(accounts), rng.choice(accounts)
            amt, tick, ref = amount(rng), rng.randrange(0, 1000), rng.choice(["", "memo"])
            lines.append(f"pay {src} {dst} {amt} {tick} {w(ref)}")
            want.append(m.pay(src, dst, amt, tick, ref))
        elif r < 0.9:
            a = rng.choice(accounts)
            lines.append(f"balance {a}")
            want.append(str(m.ledger.balance(a)))
        elif r < 0.95:
            a, amt = rng.choice(accounts), amount(rng)
            lines.append(f"candebit {a} {amt}")
            want.append(str(m.ledger.can_debit(a, amt)))
        else:
            lines.append("state")
            want.append(m.state())
    lines += ["state", "cons", "total"]
    want += [m.state(), "True", str(sum(m.ledger.balances().values()))]
    return lines, want


def check(cmd, seeds, steps, tmp_path):
    lines, want = [], []
    for seed in seeds:
        sl, sw = script(seed, steps)
        lines += sl
        want += sw
    got = run_lines(cmd, lines, tmp_path)
    bad = [(i, lines[i], want[i], got[i]) for i in range(min(len(got), len(want))) if got[i] != want[i]]
    assert len(got) == len(want) and not bad, bad[:5]
    return len(lines)


def test_ledger_scripts_native(native, tmp_path):
    assert check(native, range(300), 40, tmp_path) > 10000


def test_ledger_scripts_js(native, tmp_path):
    check([BEND, str(DRIVER)], range(1000, 1015), 40, tmp_path)
