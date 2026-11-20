"""Differential-testing bridge between the Python exchange (the reference) and the Bend 2 port.

The Bend binary ``waybill-ex <records.jsonl>`` and :func:`reference` print the same single line for the same
records (docs/WAYBILL-BEND.md, section 9). Both lines are canonical JSON
(``json.dumps(x, sort_keys=True, separators=(",", ":"))``, ``ensure_ascii=True``) and are compared as bytes.

Output contract of :func:`reference` (Bend must match it exactly):

- Every record is first put through the wire form: ``json.loads(json.dumps(record))``. The reference runs on
  what the Bend binary reads, not on the in-memory objects (tuples become lists; two separate surrogate
  code points become one character, which changes ``len(text)`` at the message length limit).
- The records are applied in order to a fresh exchange (``Exchange.__new__`` + ``_reset()``, as
  :meth:`Exchange.replay`). The first record that raises stops the run: an :class:`ExchangeError` prints
  ``{"error":<code>,"seq":<index>}``; any other exception (``KeyError``, ``TypeError`` from an unhashable
  ``op`` or ``kind``, an ``AssertionError`` inside a transfer, ...) prints ``{"error":"internal","seq":<index>}``.
  ``index`` is the 0-based position of the record in the input, whatever its ``seq`` field says.
- After the last record ``check_invariants()`` runs: ``AssertionError`` prints
  ``{"error":"invariant","seq":<number of records>}``. **An empty record list is this case**:
  ``check_invariants`` asserts that the ledger exists (it is made by ``init``), so ``[]`` prints
  ``{"error":"invariant","seq":0}``. Any other exception from ``check_invariants`` or ``state()`` prints
  ``{"error":"internal","seq":<number of records>}`` (none is known to be reachable).
- Integers have no size limit on either side. CPython refuses by default to convert an int of more than 4,300
  digits to text (``sys.set_int_max_str_digits``, a guard against slow parsing, not an exchange rule); every
  conversion here runs with that guard lifted (:func:`unlimited_int_digits`), so a computed amount of
  10**4400 is printed, as Bend prints it.
- Otherwise ``{"ok":<Exchange.state()>}``. NaN and infinities are printed as Python does (``NaN``,
  ``Infinity``, ``-Infinity``): they can only be echoed from input (``settle.parts``), never computed.

The input file is :func:`normalize` of the records: one ``json.dumps(record)`` per line (default separators,
ASCII, key order preserved), each line ending in a newline.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ExchangeError
from .exchange import Exchange

REPO = Path(__file__).resolve().parents[3]
BINARY_ENV = "RAILHEAD_BEND_EX"
BEND_ENV = "RAILHEAD_BEND"
DEFAULT_BINARY = REPO / "bend" / "build" / "waybill-ex"
MAIN_BEND = REPO / "bend" / "exchange" / "main.bend"
RUN_TIMEOUT_S = 120.0


class BendError(RuntimeError):
    """The Bend binary could not be built, found or run, or printed something that is not one line."""


# ============================================================================================== reference
@contextlib.contextmanager
def unlimited_int_digits() -> Iterator[None]:
    """Lift CPython's int/str digit limit for the block and restore it after (see the module docstring)."""
    old = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(0)
    try:
        yield
    finally:
        sys.set_int_max_str_digits(old)


def canonical(x: Any) -> str:
    """The canonical one-line JSON both sides print."""
    with unlimited_int_digits():
        return json.dumps(x, sort_keys=True, separators=(",", ":"))


def normalize(records: list[Any]) -> str:
    """The Bend input: one ``json.dumps(record)`` per line, trailing newline (empty string for no records)."""
    with unlimited_int_digits():
        return "".join(json.dumps(r) + "\n" for r in records)


def wire(records: list[Any]) -> list[Any]:
    """The records as the Bend binary reads them (each one through ``json.dumps`` and back)."""
    with unlimited_int_digits():
        return [json.loads(json.dumps(r)) for r in records]


def reference_result(records: list[Any]) -> dict[str, Any]:
    """The reference outcome as a dict (``{"ok": state}`` or ``{"error": code, "seq": i}``); see the module
    docstring for the rules."""
    recs = wire(records)
    ex = Exchange.__new__(Exchange)
    ex._reset()
    for i, r in enumerate(recs):
        try:
            ex.apply(r)
        except ExchangeError as e:
            return {"error": e.code, "seq": i}
        except Exception:  # noqa: BLE001 -- malformed input reaching a KeyError/TypeError: part of the contract
            return {"error": "internal", "seq": i}
    n = len(recs)
    try:
        ex.check_invariants()
    except AssertionError:
        return {"error": "invariant", "seq": n}
    except Exception:  # noqa: BLE001
        return {"error": "internal", "seq": n}
    try:
        state = ex.state()
        canonical(state)
    except Exception:  # noqa: BLE001
        return {"error": "internal", "seq": n}
    return {"ok": state}


def reference(records: list[Any]) -> str:
    """The canonical line the Bend binary must print for ``records``."""
    return canonical(reference_result(records))


def outcome_label(line: str) -> str:
    """``ok`` or the error code of one output line (for coverage histograms)."""
    x = json.loads(line)
    return "ok" if "ok" in x else str(x.get("error"))


# ============================================================================================== extraction
def _lines(path: Path) -> list[str]:
    """Non-empty lines of a JSONL file. An unterminated last line is a torn write (railhead.daemon
    ``_drop_torn_tail``): it is dropped here, in memory only (the file is never modified)."""
    data = path.read_bytes()
    if data and not data.endswith(b"\n"):
        data = data[:data.rfind(b"\n") + 1]
    return [ln for ln in data.decode("utf-8").split("\n") if ln.strip()]


def log_path(path: Path) -> Path:
    """The merged log of a run dir, or ``path`` itself."""
    if path.is_dir():
        p = path / "coord" / "run" / "actions.jsonl"
        if not p.is_file():
            raise FileNotFoundError(f"{path}: no coord/run/actions.jsonl")
        return p
    return path


def extract_ex_records(path: Path | str) -> list[Any]:
    """The exchange records of a run dir (``<dir>/coord/run/actions.jsonl``), a merged log, or a plain
    records file (one exchange record per line), in log order.

    A file is a merged log when its first line is an object with ``"kind": "header"`` and no ``op`` (a
    mutated exchange record may carry a ``kind``); then the ``x`` of every ``"kind": "ex"`` line is
    returned. Otherwise every line is one record (any JSON value)."""
    lines = _lines(log_path(Path(path)))
    if not lines:
        return []
    values = []
    for n, ln in enumerate(lines, 1):
        try:
            values.append(json.loads(ln))
        except json.JSONDecodeError as e:
            raise ValueError(f"{path}:{n}: not JSON ({e})") from None
    first = values[0]
    if isinstance(first, dict) and first.get("kind") == "header" and "op" not in first:
        return [v["x"] for v in values if isinstance(v, dict) and v.get("kind") == "ex"]
    return values


# ============================================================================================== binary
def find_binary() -> Path | None:
    """``$RAILHEAD_BEND_EX`` if set, else ``bend/build/waybill-ex`` if it exists, else None."""
    env = os.environ.get(BINARY_ENV)
    if env:
        return Path(env)
    return DEFAULT_BINARY if DEFAULT_BINARY.is_file() else None


def find_bend() -> Path | None:
    """The ``bend`` compiler: ``$RAILHEAD_BEND``, else on PATH, else ``~/.bend/bin/bend``."""
    env = os.environ.get(BEND_ENV)
    if env:
        return Path(env)
    on_path = shutil.which("bend")
    if on_path:
        return Path(on_path)
    home = Path.home() / ".bend" / "bin" / "bend"
    return home if home.is_file() else None


def build(out: Path | None = None) -> Path:
    """Compile ``bend/exchange/main.bend`` to ``out`` (default ``bend/build/waybill-ex``)."""
    bend = find_bend()
    if bend is None:
        raise BendError(f"bend not found (set {BEND_ENV}, put bend on PATH, or install to ~/.bend/bin)")
    out = Path(out) if out is not None else DEFAULT_BINARY
    out.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "BEND_NO_TELEMETRY": "1"}
    proc = subprocess.run([str(bend), str(MAIN_BEND), "-o", str(out)], cwd=REPO, env=env, capture_output=True,
                          text=True, check=False)
    if proc.returncode != 0 or not out.is_file():
        raise BendError(f"bend build failed ({proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}")
    return out


def run_bend(records: list[Any], binary: Path | str, timeout: float = RUN_TIMEOUT_S) -> str:
    """Run the binary on :func:`normalize` (records) and return its single output line (without newline).
    Raises :class:`BendError` on a non-zero exit, a timeout, or output that is not exactly one line."""
    with tempfile.TemporaryDirectory(prefix="rh-bend-") as tmp:
        inp = Path(tmp) / "records.jsonl"
        inp.write_bytes(normalize(records).encode("ascii"))
        try:
            proc = subprocess.run([str(binary), str(inp)], capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise BendError(f"{binary}: timed out after {timeout}s") from None
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise BendError(f"{binary}: exit {proc.returncode}: {err[:500]}")
    out = proc.stdout
    if not out.endswith(b"\n") or out.count(b"\n") != 1:
        raise BendError(f"{binary}: expected exactly one line, got {out[:200]!r}")
    return out[:-1].decode("utf-8", "replace")


@dataclass(frozen=True)
class Result:
    """One differential comparison."""
    n_records: int
    expected: str          # the Python reference line
    actual: str            # the Bend line
    diff: str | None       # None when the lines are byte-equal, else where they first differ

    @property
    def match(self) -> bool:
        return self.diff is None


def _path_str(path: list[str | int]) -> str:
    return "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in path)


def _first_diff(a: Any, b: Any, path: list[str | int]) -> str | None:
    if type(a) is not type(b):
        return f"{_path_str(path)}: {canonical(a)[:80]} != {canonical(b)[:80]}"
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                side = "expected" if k in a else "actual"
                return f"{_path_str([*path, k])}: only in {side}"
            d = _first_diff(a[k], b[k], [*path, k])
            if d:
                return d
        return None
    if isinstance(a, list):
        for i, (x, y) in enumerate(zip(a, b)):
            d = _first_diff(x, y, [*path, i])
            if d:
                return d
        if len(a) != len(b):
            return f"{_path_str(path)}: length {len(a)} != {len(b)}"
        return None
    if canonical(a) != canonical(b):
        return f"{_path_str(path)}: {canonical(a)[:80]} != {canonical(b)[:80]}"
    return None


def describe_diff(expected: str, actual: str) -> str | None:
    """None if the lines are byte-equal, else the JSON path of the first differing value (or the first
    differing byte offset when the values are equal but the bytes are not, or a line is not JSON)."""
    if expected == actual:
        return None
    try:
        with unlimited_int_digits():
            d = _first_diff(json.loads(expected), json.loads(actual), [])
    except json.JSONDecodeError:
        d = None
    if d:
        return d
    i = next((k for k, (x, y) in enumerate(zip(expected, actual)) if x != y), min(len(expected), len(actual)))
    return f"byte {i}: expected {expected[max(0, i - 20):i + 40]!r}, got {actual[max(0, i - 20):i + 40]!r}"


def compare(records: list[Any], binary: Path | str, timeout: float = RUN_TIMEOUT_S) -> Result:
    """Run both sides on ``records`` and compare the lines byte for byte."""
    expected = reference(records)
    actual = run_bend(records, binary, timeout=timeout)
    return Result(len(records), expected, actual, describe_diff(expected, actual))


# ============================================================================================== CLI
USAGE = """usage:
  railhead waybill-bend verify PATH... [--binary B]
      PATH: a run dir, a merged log, or a records .jsonl. One line per path: `match <n records>` or
      `DIFF <first difference>`; exit 1 on any DIFF or error.
  railhead waybill-bend corpus --out DIR [--kind sessions|mutations|edge|captured|all] [--seed N] [--count K]
      [--captured-dir D]   write case-XXXXX.jsonl (input) + case-XXXXX.expected (reference line)"""


def _opt(argv: list[str], name: str, default: str | None = None) -> tuple[str | None, list[str]]:
    if name not in argv:
        return default, argv
    k = argv.index(name)
    if k + 1 >= len(argv):
        raise ValueError(f"{name} needs a value")
    return argv[k + 1], argv[:k] + argv[k + 2:]


def _verify(argv: list[str]) -> int:
    b, paths = _opt(argv, "--binary")
    if not paths:
        print(USAGE, file=sys.stderr)
        return 2
    binary = Path(b) if b else find_binary()
    if binary is None:
        binary = build()
    bad = 0
    for p in paths:
        try:
            res = compare(extract_ex_records(p), binary)
        except (OSError, ValueError, BendError) as e:
            print(f"{p}: ERROR {e}")
            bad += 1
            continue
        if res.match:
            print(f"{p}: match {res.n_records}")
        else:
            print(f"{p}: DIFF {res.diff}")
            bad += 1
    return 1 if bad else 0


def _corpus(argv: list[str]) -> int:
    from . import corpus

    out, argv = _opt(argv, "--out")
    kind, argv = _opt(argv, "--kind", "all")
    seed, argv = _opt(argv, "--seed", "0")
    count, argv = _opt(argv, "--count", "100")
    cap, argv = _opt(argv, "--captured-dir")
    if out is None or argv or kind not in (*corpus.KINDS, "all"):
        print(USAGE, file=sys.stderr)
        return 2
    assert seed is not None and count is not None
    report = corpus.write_corpus(Path(out), kind, int(seed), int(count),
                                 captured_dir=Path(cap) if cap else None)
    print(json.dumps(report, sort_keys=True))
    return 0


def main(argv: list[str]) -> int:
    """``railhead waybill-bend verify|corpus ...``."""
    cmd, rest = (argv[0], argv[1:]) if argv else ("help", [])
    try:
        if cmd == "verify":
            return _verify(rest)
        if cmd == "corpus":
            return _corpus(rest)
    except (BendError, OSError, ValueError) as e:
        print(f"railhead waybill-bend {cmd}: {e}", file=sys.stderr)
        return 1
    print(USAGE)
    return 0 if cmd in ("help", "-h", "--help") else 2
