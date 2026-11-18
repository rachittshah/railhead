"""Waybill delivery extraction (needs the engine): the cargo each company delivers to each cross-region need.

The H3 cargo measure and the H4 market-share measure of ``docs/WAYBILL-PREREG.md`` need deliveries per
(company, cargo, destination industry), which the merged log does not hold. This pass replays a verified
run with :func:`railhead.multi.replay.replay_multi`, called with the arguments of
:func:`railhead.multi.verify.verify_multi`, and installs an engine cargo monitor on every (company,
cargo, industry) of the task's cross-region needs at the start tick (``on_start``). It reads the
monitors after each step (the ``observe`` hook), at the end of the log, at the horizon (after the
verifier's forced market stops) and after the run-off.

Cargo monitors are not in the checksum, but they are not invisible to the market: the GameScript gives a
day's delivery on one key to the first monitor of that key in its list, and numbers the monitors by
position. :class:`MonitorMux` therefore sits between the market's :class:`MonitorBook` and the engine.
The book gets the monitor ids and totals it would get in a plain replay (the integration tests compare the
whole market state). Every number that ``result.json`` also has is compared with it, as the cinema does.

The output (``deliveries.json``) carries the log's sha256 and the check result; ``railhead
waybill-analyze`` uses it only when both match. The H3 and H4 aggregations here are pure functions.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

DELIVERIES_VERSION = 1
TICKS_PER_DAY = 74
EXCLUSIVE_SHARE = 0.8  # prereg H4: a pair supplies "more than 80% of the delivered quantity"
WINDOWS = ("horizon", "end")  # end = after the run-off

Key = tuple[int, int, int]  # (company, cargo, industry), engine ids


class DeliveryMismatch(RuntimeError):
    """The extraction replay does not give the verified result: its deliveries may not be used."""


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _key(r: dict) -> Key | None:
    if r.get("kind") != "industry_delivery":
        return None
    return int(r["company"]), int(r["cargo"]), int(r["target"])


class MonitorMux:
    """One shared set of engine monitors for the market book and the extraction.

    Installed on a :class:`SharedWorld` (its ``monitor_add`` and ``monitor_rows``) before the book's first
    monitor. The extraction's monitors are added first, so they take the engine ids ``base .. base+m-1``
    (``base``: monitors already in the start save). A book monitor added later keeps its engine call (the
    engine still decides whether the industry is valid), but the book sees the id it would have in a plain
    replay (engine id - m). When the extraction monitors the same key, the book's engine monitor reads 0
    (the earlier monitor takes the delivery), so the book is given the extraction monitor's total minus its
    total when the book's monitor was added: what the book's monitor would have counted alone."""

    def __init__(self, sw, keys: list[Key]) -> None:
        self._add, self._rows = sw.monitor_add, sw.monitor_rows
        self.base = len(self._rows())
        self.mine: dict[Key, int] = {}
        for k in keys:
            eid = self._add(*k)
            if eid is not None:
                self.mine[k] = int(eid)
        self.m = len(self.mine)
        self.book: dict[int, tuple[Key | None, int]] = {}  # book id -> (shared key or None, baseline)
        self.shared: list[dict] = []  # book monitors on an extraction key (for the record and the tests)
        self.start = self.totals()
        sw.monitor_add, sw.monitor_rows = self.book_add, self.book_rows

    # ------------------------------------------------------------------ the book's view
    def book_add(self, company: int, cargo: int, industry: int) -> int | None:
        eid = self._add(company, cargo, industry)
        if eid is None:
            return None
        vid = int(eid) - self.m
        k = (int(company), int(cargo), int(industry))
        if k in self.mine:
            base = self._own_total(k)
            self.book[vid] = (k, base)
            self.shared.append({"key": list(k), "book_id": vid, "baseline": base})
        else:
            self.book[vid] = (None, 0)
        return vid

    def book_rows(self) -> list[dict]:
        rows = self._rows()
        by_id = {int(r["id"]): r for r in rows}
        out = [r for r in rows if int(r["id"]) < self.base]
        for vid, (k, baseline) in sorted(self.book.items()):
            r = dict(by_id[vid + self.m], id=vid)
            if k is not None:
                r["total"] = int(by_id[self.mine[k]]["total"]) - baseline
            out.append(r)
        return out

    def _own_total(self, k: Key) -> int:
        eid = self.mine[k]
        return next(int(r["total"]) for r in self._rows() if int(r["id"]) == eid)

    # ------------------------------------------------------------------ the extraction's view
    def totals(self) -> dict[Key, int]:
        """Cumulative deliveries of each extraction key: the sum over every engine monitor of the key (a
        monitor from the start save that comes first would take the delivery), from the engine rows."""
        out = {k: 0 for k in self.mine}
        for r in self._rows():
            k = _key(r)
            if k in out:
                out[k] += int(r["total"])
        return out

    def since_start(self) -> dict[Key, int]:
        now = self.totals()
        return {k: now[k] - self.start[k] for k in self.mine}


class Recorder:
    """``on_start`` installs the mux on the needs of the start snapshot; ``observe`` (after each step)
    records the game-wide total of the extraction keys when it changes."""

    def __init__(self, regions, max_distance: int, extra_keys: list[Key] | None = None,
                 company_ids: list[int] | None = None) -> None:
        self.regions, self.max_distance = regions, max_distance
        self.extra_keys = [tuple(int(x) for x in k) for k in extra_keys or []]
        self.company_ids = company_ids
        self.mux: MonitorMux | None = None
        self.needs: list[tuple[int, int]] = []
        self.series: list[list[int]] = []  # [tick, total delivered to the keys so far]

    def on_start(self, sw, market) -> None:
        from railhead.multi.market import cross_region_need_list, industry_snapshot

        snap = industry_snapshot(sw.industries(), self.regions, sw.ticks)
        self.needs = cross_region_need_list(snap, self.max_distance)
        ids = sorted(self.company_ids if self.company_ids is not None else sw.ids)
        keys = sorted({(c, cargo, ind) for c in ids for ind, cargo in self.needs} | set(self.extra_keys))
        self.mux = MonitorMux(sw, keys)
        self.series = [[sw.ticks, 0]]

    def __call__(self, sw, market, why: str) -> None:
        self.take(sw.ticks)

    def take(self, tick: int) -> dict[Key, int]:
        assert self.mux is not None
        got = self.mux.since_start()
        total = sum(got.values())
        if total != self.series[-1][1]:
            self.series.append([tick, total])
        return got


# ------------------------------------------------------------------------------------- the replay
def extract(start_save: Path, log_path: Path, workdir: Path, *, horizon_days: int | None, runoff_days: int,
            regions, market_cfg, require_seal: bool = True, max_distance: int | None = None,
            extra_keys: list[Key] | None = None, plain_check: bool = False) -> dict:
    """Replay ``log_path`` with the extraction installed, run on to the horizon (the market settling at its
    forced stops, as :func:`verify_multi` does) and through the run-off. Returns the deliveries per key at
    the log end, the horizon and the end, plus the replay's own numbers for :func:`compare`."""
    from railhead.multi.gen import REACH_TILES
    from railhead.multi.replay import horizon_tick, replay_multi
    from railhead.replay import default_whitelist

    rec = Recorder(regions, REACH_TILES if max_distance is None else max_distance, extra_keys)
    res = replay_multi(Path(start_save), Path(log_path), Path(workdir), allowed=default_whitelist() or set(),
                       keep_open=True, horizon_days=horizon_days, regions=regions, market_cfg=market_cfg,
                       require_seal=require_seal, observe=rec, on_start=rec.on_start)
    sw, mk = res.world, res.market
    assert sw is not None and rec.mux is not None
    try:
        if res.divergences:
            raise DeliveryMismatch(f"the replay diverges from the log: {res.divergences[:3]}")
        at = {"log_end": rec.take(sw.ticks)}
        end = horizon_tick(res.start_ticks, horizon_days)
        while end is not None and sw.ticks < end:  # railhead.multi.verify: the market settles to the horizon
            stop = mk.next_forced(sw.ticks) if mk is not None else None
            sw.advance(min(end, stop) - sw.ticks if stop is not None else end - sw.ticks)
            if mk is not None:
                if mk.book.active:
                    mk.book.sample(sw.ticks, sw.monitor_rows())
                mk.tick_records(sw.ticks, sw.industries)
                mk.book.ensure(mk.ex, sw.ticks, sw.monitor_add)
            rec.take(sw.ticks)
        at["horizon"] = rec.take(sw.ticks)
        horizon_ticks = sw.ticks
        at_horizon = sw.net_worth()
        ledger = {int(c["id"]): mk.balance(int(c["id"])) for c in res.companies} if mk is not None else {}
        market_state = mk.state() if mk is not None else None
        if runoff_days > 0:
            sw.advance(runoff_days * TICKS_PER_DAY)
        at["end"] = rec.take(sw.ticks)
        final = sw.net_worth()
        end_ticks = sw.ticks
    finally:
        sw.close()
    return {
        "needs": [list(n) for n in rec.needs], "keys": [list(k) for k in sorted(rec.mux.mine)],
        "monitors": {"base": rec.mux.base, "extraction": rec.mux.m, "shared_with_book": rec.mux.shared},
        "deliveries": {w: _rows(v) for w, v in at.items()}, "series": rec.series,
        "ticks": {"start": res.start_ticks, "log_end": res.end_ticks, "horizon": horizon_ticks, "end": end_ticks},
        "replay": {"verify": {"acts": res.acts, "failed_acts": res.failed_acts, "steps": res.steps,
                              "ticks_log_end": res.end_ticks, "end_ticks": end_ticks},
                   "companies": [{"id": int(c["id"]), "net_worth_horizon": at_horizon[int(c["id"])],
                                  "net_worth": final[int(c["id"])], "ledger_balance": ledger.get(int(c["id"]), 0),
                                  "score": final[int(c["id"])] + ledger.get(int(c["id"]), 0)}
                                 for c in res.companies]},
        "market": market_state, "checkpoints": res.checkpoints, "final_checksum": res.final_checksum,
    }


def _rows(d: dict[Key, int]) -> list[list[int]]:
    """{key: amount} -> [[company, cargo, industry, amount]] in key order (JSON has no tuple keys)."""
    return [[*k, int(v)] for k, v in sorted(d.items())]


def keyed(rows: list[list[int]]) -> dict[Key, int]:
    return {(int(c), int(g), int(i)): int(a) for c, g, i, a in rows}


def compare(result: dict, scores: dict, got: dict) -> list[str]:
    """Differences between the run's verified files and the extraction replay (empty: they agree): the
    cinema's checks against ``result.json`` and the market state of ``verify/scores.json``."""
    from railhead.cinema.sample import compare as cinema_compare
    from railhead.multi.arena import contract_stats

    comps = [{"id": c["id"]} for c in got["replay"]["companies"]]
    ex = (got.get("market") or {}).get("exchange")
    rep = dict(got["replay"], contracts=contract_stats(ex, comps)["total"] if ex else {})
    out = cinema_compare(result, rep)
    want = scores.get("market")
    if want is not None and json.loads(json.dumps(got.get("market"))) != want:  # as written (str keys)
        out.append("market state: differs from verify/scores.json")
    return out


def extract_run(run_dir: Path, task_dir: Path | None = None, out: Path | None = None,
                keep_work: bool = False) -> dict:
    """Extract one arena run; write ``deliveries.json`` (default: in the run directory) and return it.
    Raises :class:`DeliveryMismatch` (after writing the file with ``check.ok`` false) on a difference."""
    from railhead.cinema.sample import load_run
    from railhead.multi.market import MarketConfig
    from railhead.multi.regions import RegionMap
    from railhead.task import TaskSpec

    t0 = time.monotonic()
    info = load_run(Path(run_dir), task_dir)
    task, result = info["task_dir"], info["result"]
    scores = json.loads((Path(run_dir) / "verify" / "scores.json").read_text()) \
        if (Path(run_dir) / "verify" / "scores.json").exists() else {}
    spec = TaskSpec.load(task)
    work = Path(tempfile.mkdtemp(prefix="rh-wb-deliveries-"))
    try:
        got = extract(task / "start.sav", info["log"], work / "replay", horizon_days=spec.horizon_days,
                      runoff_days=spec.runoff_days, regions=RegionMap.for_task(task),
                      market_cfg=MarketConfig.from_objective(spec.objective), require_seal=True)
    finally:
        if not keep_work:
            shutil.rmtree(work, ignore_errors=True)
    mismatches = compare(result, scores, got)
    doc = {"version": DELIVERIES_VERSION, "task_id": spec.task_id, "arm": result.get("arm"),
           "log_sha256": _sha(info["log"]), "check": {"ok": not mismatches, "mismatches": mismatches},
           **{k: got[k] for k in ("needs", "keys", "monitors", "ticks", "deliveries", "series")},
           "seconds": round(time.monotonic() - t0, 1)}
    path = Path(out) if out is not None else Path(run_dir) / "deliveries.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1) + "\n")
    if mismatches:
        raise DeliveryMismatch(f"{Path(run_dir).name}: the replay differs from the verified run: {mismatches}")
    return doc


# ---------------------------------------------------------------------------------- the measures
def cargo_total(deliveries: dict[Key, int], needs: list[tuple[int, int]] | None = None) -> int:
    """H3 cargo: the cargo delivered to the industries of cross-region needs (all companies). ``needs``
    restricts to those (industry, cargo) pairs (default: every key)."""
    want = None if needs is None else {(int(i), int(c)) for i, c in needs}
    return sum(a for (_co, cargo, ind), a in deliveries.items() if want is None or (ind, cargo) in want)


def licence_pairs(exchange_state: dict) -> set[frozenset[int]]:
    """The licence partner pairs of one game: {grantor, grantee} of every licence that was signed (engine
    company ids)."""
    out = set()
    for c in (exchange_state or {}).get("contracts", {}).values():
        if c.get("kind") == "licence" and c.get("signed_tick") is not None:
            a, b = int(c["terms"]["grantor"]), int(c["terms"]["grantee"])
            if a != b:
                out.add(frozenset((a, b)))
    return out


def market_shares(deliveries: dict[Key, int], pairs: set[frozenset[int]],
                  threshold: float = EXCLUSIVE_SHARE) -> dict:
    """H4: for each cargo market (cargo, destination industry) with deliveries, the largest share that one
    licence partner pair supplies (the two companies' deliveries together); a market is exclusive when that
    share is more than ``threshold``. Markets with no delivery are not counted. Returns the per-market rows
    and the game's share of exclusive markets (None when no market has a delivery)."""
    by_market: dict[tuple[int, int], dict[int, int]] = defaultdict(dict)
    for (co, cargo, ind), a in deliveries.items():
        if a > 0:
            by_market[(cargo, ind)][co] = by_market[(cargo, ind)].get(co, 0) + a
    rows = []
    for (cargo, ind), per in sorted(by_market.items()):
        total = sum(per.values())
        best, best_pair = 0, None
        for p in sorted(pairs, key=sorted):
            q = sum(per.get(c, 0) for c in p)
            if q > best:
                best, best_pair = q, sorted(p)
        share = best / total
        rows.append({"cargo": cargo, "industry": ind, "delivered": total, "suppliers": len(per),
                     "by_company": {str(c): a for c, a in sorted(per.items())}, "pair": best_pair,
                     "pair_share": share, "exclusive": share > threshold})
    n = len(rows)
    excl = sum(r["exclusive"] for r in rows)
    return {"markets": n, "exclusive": excl, "multi_supplier": sum(r["suppliers"] >= 2 for r in rows),
            "exclusive_multi_supplier": sum(r["exclusive"] and r["suppliers"] >= 2 for r in rows),
            "share_exclusive": (excl / n) if n else None, "threshold": threshold, "rows": rows}


def measures(doc: dict, exchange_state: dict) -> dict:
    """The H3 cargo and H4 market-share numbers of one run, for each window, from its ``deliveries.json``
    and its exchange state."""
    pairs = licence_pairs(exchange_state)
    out = {"licence_pairs": sorted(sorted(p) for p in pairs)}
    for w in WINDOWS:
        d = keyed(doc["deliveries"][w])
        out[w] = {"cargo": cargo_total(d, doc["needs"]), "market_share": market_shares(d, pairs)}
    return out


def usable(run_dir: Path) -> tuple[dict | None, str | None]:
    """``deliveries.json`` of a run if it is current (same log sha256) and passed its check, else a reason."""
    p = Path(run_dir) / "deliveries.json"
    if not p.exists():
        return None, "no deliveries.json (run railhead waybill-deliveries)"
    doc = json.loads(p.read_text())
    log = Path(run_dir) / "coord" / "run" / "actions.jsonl"
    if not log.exists() or doc.get("log_sha256") != _sha(log):
        return None, "deliveries.json is for another log"
    if not (doc.get("check") or {}).get("ok"):
        return None, "deliveries.json failed its check against the verified run"
    return doc, None


def main(argv: list[str] | None = None) -> int:
    """railhead waybill-deliveries <run_dir>... [--task DIR]: write <run_dir>/deliveries.json for each run."""
    argv = list(sys.argv[1:] if argv is None else argv)
    task = Path(argv[argv.index("--task") + 1]) if "--task" in argv else None
    runs = [a for j, a in enumerate(argv) if not a.startswith("--") and (j == 0 or argv[j - 1] != "--task")]
    if not runs:
        print(main.__doc__, file=sys.stderr)
        return 2
    rc = 0
    for r in runs:
        try:
            doc = extract_run(Path(r), task)
        except DeliveryMismatch as e:
            print(f"waybill-deliveries: {e}", file=sys.stderr)
            rc = 1
            continue
        d = doc["deliveries"]
        print(json.dumps({"run": Path(r).name, "arm": doc["arm"], "needs": len(doc["needs"]),
                          "monitors": doc["monitors"]["extraction"],
                          "shared_with_book": len(doc["monitors"]["shared_with_book"]),
                          **{f"cargo_{w}": sum(x[3] for x in d[w]) for w in ("log_end", *WINDOWS)},
                          "seconds": doc["seconds"]}), flush=True)
    return rc
