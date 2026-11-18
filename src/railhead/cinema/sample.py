"""Cinema stage 1 (needs the engine): replay a verified Waybill run and sample it on a fine tick grid.

The merged log is replayed from the sealed start with :func:`railhead.multi.replay.replay_multi` itself,
called exactly as :func:`railhead.multi.verify.verify_multi` calls it. Its ``observe`` hook runs each step
in pieces that stop on an absolute grid (``start + k * grid``, the same stops in both arms of one seed)
and reads the paused world at every stop and every step end. After the log, the world runs on to the
horizon (the market settling at its forced stops, as the verifier does) and through the run-off, with the
same grid stops. Reads only: ``SharedWorld.query`` (which keeps the bound company right), the market's
own ledger and exchange state.

Every replayed number that ``result.json`` also has is compared with it: the net worth at the horizon and
after the run-off, the ledger balance and the score of each company, the act, failed act and step counts,
the log-end and end ticks, and the contract totals. A difference raises :class:`CinemaMismatch` (the
sample file is still written, with ``check.ok`` false, for the diagnosis).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from collections import Counter
from pathlib import Path

from railhead.render import MODES
from railhead.timelapse import legs_of, minimap_shot

SAMPLE_VERSION = 1
TICKS_PER_DAY = 74
GRID_DAYS = 2


class CinemaMismatch(RuntimeError):
    """The replay does not give the verified result: nothing may be rendered from it."""


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def letter(slot: int) -> str:
    return chr(ord("A") + int(slot))


def load_run(run_dir: Path, task_dir: Path | None = None) -> dict:
    """``run.json``, ``result.json`` and the task directory of one arena run."""
    run_dir = Path(run_dir)
    run = json.loads((run_dir / "run.json").read_text())
    result = json.loads((run_dir / "result.json").read_text())
    task = Path(task_dir) if task_dir is not None else Path(run["task_dir"])
    if not (task / "start.sav").exists():
        raise FileNotFoundError(f"{task}: no start.sav (pass --task)")
    return {"run": run, "result": result, "task_dir": task, "log": run_dir / "coord" / "run" / "actions.jsonl"}


def companies_meta(task_objective: dict, result: dict) -> list[dict]:
    """[{id, slot, name, letter, vendor, model}] in slot order; ``model`` is what really ran (``model_used``)."""
    by_id = {int(c["id"]): c for c in result.get("companies") or []}
    out = []
    for c in sorted(task_objective["companies"], key=lambda c: int(c["slot"])):
        r = by_id.get(int(c["id"]), {})
        out.append({"id": int(c["id"]), "slot": int(c["slot"]), "name": c["name"], "letter": letter(c["slot"]),
                    "vendor": r.get("vendor"), "model": r.get("model_used") or r.get("model")})
    return out


def exchange_counts(state: dict, companies: list[dict], tick: int) -> dict:
    """Per-sample exchange summary: the arena's contract statistics (the result.json counters) plus what is
    live at ``tick``: licences in force, obligations open, messages and records so far."""
    from railhead.multi.arena import contract_stats

    tot = contract_stats(state, companies)["total"]
    live_lic = sum(1 for c in state["contracts"].values()
                   if c["kind"] == "licence" and c["status"] in ("signed", "active")
                   and c["terms"]["window_start"] <= tick < c["terms"]["deadline"])
    keys = ("offered", "signed", "licences_signed", "amended", "tenders_won", "obligations_created",
            "obligations_kept", "obligations_broken", "obligations_partial", "obligations_open",
            "obligations_collected", "obligations_paid_on_time", "tender_contracts", "tender_kept",
            "tender_broken")
    return {**{k: tot[k] for k in keys}, "live_licences": live_lic, "messages": len(state["messages"]),
            "records": state["n_records"]}


class Sampler:
    """The ``observe`` hook: reads every company's network and money at each stop. A company's vehicle
    orders are read again only when it acted since the last sample or its vehicle set changed."""

    def __init__(self, companies: list[dict], act_ticks: dict[int, list[int]]) -> None:
        self.companies = companies
        self.act_ticks = act_ticks
        self.samples: list[dict] = []
        self.phase = "play"
        self.industries: dict[int, list[int]] = {}
        self._n_tenders = -1
        self._last_t: int | None = None
        self._orders: dict[int, tuple[frozenset, dict]] = {}
        self.queries = 0
        self.seconds = 0.0

    def _acted(self, cid: int, now: int) -> bool:
        if self._last_t is None:
            return True
        return any(self._last_t <= t < now for t in self.act_ticks.get(cid, ()))

    def __call__(self, sw, market, why: str) -> None:
        self.take(sw, market, why)

    def take(self, sw, market, why: str) -> dict:
        t0 = time.monotonic()
        now = sw.ticks
        first = self.companies[0]["id"]
        rows = {int(r["id"]): r for r in sw.query(first, "companies")}
        self.queries += 1
        co = {}
        for c in self.companies:
            cid = c["id"]
            stations = sw.query(cid, "stations")
            vehicles = sw.query(cid, "vehicles")
            self.queries += 2
            vset = frozenset(int(v["id"]) for v in vehicles)
            cached = self._orders.get(cid)
            if cached is None or cached[0] != vset or self._acted(cid, now):
                orders = {v["id"]: (v["vtype"], sw.query(cid, "orders", {"vehicle": v["id"]})) for v in vehicles}
                self.queries += len(vehicles)
                self._orders[cid] = (vset, orders)
            orders = self._orders[cid][1]
            fleet = Counter(v["vtype"] for v in vehicles)
            r = rows.get(cid, {"money": 0, "loan": 0})
            co[str(cid)] = {"cash": int(r["money"]), "loan": int(r["loan"]),
                            "nw": int(r["money"]) - int(r["loan"]),
                            "ledger": market.balance(cid) if market is not None else 0,
                            "st": [[s["id"], s["x"], s["y"]] for s in stations], "rt": legs_of(orders),
                            "veh": {m: fleet.get(m, 0) for m in MODES}}
        s = {"t": now, "date": sw.ymd, "phase": self.phase, "why": why, "co": co}
        if market is not None:
            st = market.ex.state()
            s["ex"] = exchange_counts(st, self.companies, now)
            if len(st["tenders"]) != self._n_tenders:  # a tender names industries: keep their tiles
                for i in sw.industries():
                    self.industries[int(i["id"])] = [int(i["x"]), int(i["y"]), int(i.get("type", 0))]
                self.queries += 1
                self._n_tenders = len(st["tenders"])
        self.samples.append(s)
        self._last_t = now
        self.seconds += time.monotonic() - t0
        return s


def act_ticks_of(records: list[dict]) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    for r in records:
        if r.get("kind") == "act":
            out.setdefault(int(r["company"]), []).append(int(r["ticks"]))
    return out


def compare(result: dict, got: dict) -> list[str]:
    """Differences between ``result.json`` and the sampled replay (empty: they agree)."""
    out = []
    v = result.get("verify") or {}
    for k in ("acts", "failed_acts", "steps", "ticks_log_end", "end_ticks"):
        if k in v and v[k] != got["verify"].get(k):
            out.append(f"verify.{k}: result {v[k]}, replay {got['verify'].get(k)}")
    rows = {int(c["id"]): c for c in got["companies"]}
    for c in result.get("companies") or []:
        mine = rows.get(int(c["id"]))
        if mine is None:
            out.append(f"company {c['id']}: not in the replay")
            continue
        for k in ("net_worth_horizon", "net_worth", "ledger_balance", "score"):
            if k in c and c[k] != mine.get(k):
                out.append(f"company {c['id']}.{k}: result {c[k]}, replay {mine.get(k)}")
    for k, want in (result.get("contracts") or {}).items():
        if isinstance(want, int | float) and got["contracts"].get(k) != want:
            out.append(f"contracts.{k}: result {want}, replay {got['contracts'].get(k)}")
    return out


def probe_base(task_dir: Path, out: Path) -> dict:
    """Map size, start date and the engine minimap of the start state (shared by both arms of one seed),
    cached in ``<out>/base/`` by the hash of start.sav."""
    from railhead.world import World

    base = Path(out) / "base"
    meta = base / "probe.json"
    sav = Path(task_dir) / "start.sav"
    sha = _sha(sav)
    if meta.exists() and (base / "minimap.png").exists():
        doc = json.loads(meta.read_text())
        if doc.get("start_sav") == sha:
            return doc
    base.mkdir(parents=True, exist_ok=True)
    work = base / "work"
    shutil.rmtree(work, ignore_errors=True)
    with World.load(work, sav) as w:
        st = w.state()
        info = w.call("map_info")
        minimap_shot(w, base / "minimap.png", name="railhead_cinema_base")
    shutil.rmtree(work, ignore_errors=True)
    doc = {"start_sav": sha, "start_date": int(st["date"]), "start_ticks": int(st["ticks"]),
           "start_ymd": st["ymd"], "size_x": int(info["size_x"]), "size_y": int(info["size_y"])}
    meta.write_text(json.dumps(doc, indent=1) + "\n")
    return doc


def sample_run(run_dir: Path, out: Path, task_dir: Path | None = None, grid_days: int = GRID_DAYS,
               keep_work: bool = False) -> dict:
    """Replay one run with sampling; write ``<out>/samples/<arm>.json`` and return it. Raises
    :class:`CinemaMismatch` if the replay differs from ``result.json`` in any number they share."""
    from railhead.multi.market import MarketConfig
    from railhead.multi.regions import RegionMap
    from railhead.multi.replay import grid_pieces, horizon_tick, replay_multi
    from railhead.replay import default_whitelist
    from railhead.task import TaskSpec

    t_start = time.monotonic()
    info = load_run(run_dir, task_dir)
    run, result, task = info["run"], info["result"], info["task_dir"]
    out = Path(out)
    base = probe_base(task, out)
    spec = TaskSpec.load(task)
    regions = RegionMap.for_task(task)
    market_cfg = MarketConfig.from_objective(spec.objective)
    arm = result.get("arm") or run.get("arm") or "run"
    comps = companies_meta(spec.objective, result)
    records = [json.loads(line) for line in info["log"].read_text().splitlines() if line.strip()]
    sampler = Sampler(comps, act_ticks_of(records))
    grid = int(grid_days) * TICKS_PER_DAY
    work = out / f"work-{arm}"
    shutil.rmtree(work, ignore_errors=True)
    res = replay_multi(task / "start.sav", info["log"], work, allowed=default_whitelist() or set(), keep_open=True,
                       horizon_days=spec.horizon_days, regions=regions, market_cfg=market_cfg, require_seal=True,
                       observe=sampler, grid_ticks=grid)
    sw, mk = res.world, res.market
    assert sw is not None
    try:
        if res.divergences:
            raise CinemaMismatch(f"{arm}: the replay diverges from the log: {res.divergences[:3]}")
        start = res.start_ticks
        sampler.phase = "idle"
        end = horizon_tick(start, spec.horizon_days)
        # the verifier's run to the horizon (railhead.multi.verify), with grid stops in between
        while end is not None and sw.ticks < end:
            stop = mk.next_forced(sw.ticks) if mk is not None else None
            target = min(end, stop) if stop is not None else end
            *pieces, last = grid_pieces(sw.ticks, target, grid, start)
            for p in pieces:
                sw.advance(p)
                sampler.take(sw, mk, "grid")
            sw.advance(last)
            if mk is not None:
                if mk.book.active:
                    mk.book.sample(sw.ticks, sw.monitor_rows())
                mk.tick_records(sw.ticks, sw.industries)
                mk.book.ensure(mk.ex, sw.ticks, sw.monitor_add)
            sampler.take(sw, mk, "stop")
        at_horizon = sw.net_worth()
        horizon_ticks = sw.ticks
        ledger = {c["id"]: mk.balance(c["id"]) for c in comps} if mk is not None else {}
        ex_state = mk.state()["exchange"] if mk is not None else None
        sampler.phase = "runoff"
        for p in grid_pieces(sw.ticks, sw.ticks + spec.runoff_days * TICKS_PER_DAY, grid, start):
            sw.advance(p)
            sampler.take(sw, mk, "grid")
        final = sw.net_worth()
        end_ticks = sw.ticks
    finally:
        sw.close()
        if not keep_work:
            shutil.rmtree(work, ignore_errors=True)
    from railhead.multi.arena import contract_stats

    got = {
        "verify": {"acts": res.acts, "failed_acts": res.failed_acts, "steps": res.steps,
                   "ticks_log_end": res.end_ticks, "end_ticks": end_ticks},
        "companies": [{"id": c["id"], "net_worth_horizon": at_horizon[c["id"]], "net_worth": final[c["id"]],
                       "ledger_balance": ledger.get(c["id"], 0),
                       "score": final[c["id"]] + ledger.get(c["id"], 0)} for c in comps],
        "contracts": contract_stats(ex_state, comps)["total"] if ex_state else {},
    }
    mismatches = compare(result, got)
    doc = {
        "version": SAMPLE_VERSION, "arm": arm, "task_id": spec.task_id, "track": result.get("track"),
        "log_sha256": _sha(info["log"]), "grid_days": int(grid_days), "base": base,
        "start_ticks": res.start_ticks, "horizon_ticks": horizon_ticks, "log_end_ticks": res.end_ticks,
        "end_ticks": end_ticks, "horizon_days": spec.horizon_days, "runoff_days": spec.runoff_days,
        "companies": comps, "regions": regions.to_json() if regions is not None else None,
        "industries": {str(k): v for k, v in sorted(sampler.industries.items())},
        "exchange": ex_state,
        "check": {"ok": not mismatches, "mismatches": mismatches, "replay": got},
        "cost": {"seconds": round(time.monotonic() - t_start, 1), "query_seconds": round(sampler.seconds, 1),
                 "queries": sampler.queries, "samples": len(sampler.samples)},
        "samples": sampler.samples,
    }
    sdir = out / "samples"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / f"{arm}.json").write_text(json.dumps(doc, separators=(",", ":")) + "\n")
    if mismatches:
        raise CinemaMismatch(f"{arm}: the replay differs from result.json: {mismatches}")
    return doc


def arm_of(run_dir: Path) -> str:
    r = json.loads((Path(run_dir) / "result.json").read_text())
    return r.get("arm") or "run"


def cached(run_dir: Path, out: Path) -> str | None:
    """The arm name if ``<out>/samples/<arm>.json`` is current for this run's log and passed its check."""
    arm = arm_of(run_dir)
    f = Path(out) / "samples" / f"{arm}.json"
    if not f.exists():
        return None
    try:
        doc = json.loads(f.read_text())
    except ValueError:
        return None
    log = Path(run_dir) / "coord" / "run" / "actions.jsonl"
    ok = doc.get("version") == SAMPLE_VERSION and doc.get("log_sha256") == _sha(log) and doc["check"]["ok"]
    return arm if ok else None
