"""Ceiling anchors: run community script AIs (NoAI, from BaNaNaS) on a task's start savegame.

    railhead anchor fetch <Name|content-id> ... [--cache DIR]
    railhead anchor <task_dir> --ai <Name> [--ai <Name> ...] [--seeds N] [--out file.json]
                    [--cache DIR] [--chunk-days D] [--ai-settings k=v,...] [--work DIR] [--max-wall S]

``fetch`` downloads AIs and their libraries through the engine's own content client into
``<cache>/content`` (default ``.cache/anchors``, git-ignored). Nothing is shipped with Railhead.

``anchor`` loads ``start.sav`` (the same world the models get), starts the AI as a new company with
``start_ai``, and runs the mandate (``horizon_days``) and then the run-off (``runoff_days``) at full speed.
It samples the AI company every ``chunk_days`` and writes rows in the arena export shape with
``status: "anchor"``. Seed 0 keeps the savegame's world RNG (the same dynamics as the model games);
seed k>0 runs ``reseed`` on the world RNG right after the AI company exists. The AI's own script RNG
is stored in the savegame and is not changed by ``reseed``.

Fairness facts the rows carry: the opening state of both companies (``parity``), the net worth at the end
of the mandate and after the run-off (the AI keeps playing in the run-off: generous to the AI), and the
engine settings that matter for scripts.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tarfile
import time
from pathlib import Path
from typing import Any, Callable

from railhead import rewards
from railhead.bridge import BridgeError
from railhead.engine import REPO_DIR
from railhead.task import TaskSpec

DEFAULT_CACHE = REPO_DIR / ".cache" / "anchors"
MODES = ("road", "rail", "air", "water")
DEATH = "The script died unexpectedly"
ERROR_RE = re.compile(r"Your script made an error: (.*)")
# Engine settings that decide how much a script can do (all stored in the savegame, never changed here).
SETTINGS = (
    "script.script_max_opcode_till_suspend", "script.script_max_memory_megabytes",
    "ai.ai_in_multiplayer", "ai.ai_disable_veh_train", "ai.ai_disable_veh_roadveh",
    "ai.ai_disable_veh_aircraft", "ai.ai_disable_veh_ship", "difficulty.max_no_competitors",
    "difficulty.max_loan", "difficulty.initial_interest", "difficulty.vehicle_breakdowns",
    "vehicle.max_trains", "vehicle.max_roadveh", "vehicle.max_aircraft", "vehicle.max_ships",
    "economy.inflation", "construction.command_pause_level",
)


# ------------------------------------------------------------------ content service
def parse_content_state(lines: list[str]) -> list[dict]:
    """Rows of the console ``content state`` table: ``id, type, state, name, unique_id, md5``."""
    out = []
    for line in lines:
        parts = line.split(", ")
        if len(parts) < 6 or not parts[0].strip().isdigit():
            continue
        out.append({"id": int(parts[0]), "type": parts[1], "state": parts[2], "name": ", ".join(parts[3:-2]),
                    "unique_id": parts[-2], "md5": parts[-1].strip()})
    return out


def pick_content(rows: list[dict], name: str) -> dict:
    """The AI row for a name (case-insensitive, exact) or a numeric content id."""
    if name.isdigit():
        hit = [r for r in rows if r["id"] == int(name)]
    else:
        hit = [r for r in rows if r["type"] == "AI" and r["name"].lower() == name.lower()]
    uniq = {r["id"]: r for r in hit}
    if not uniq:
        raise ValueError(f"{name!r} is not on the content service as an AI")
    if len(uniq) > 1:
        raise ValueError(f"{name!r} is ambiguous: content ids {sorted(uniq)}; pass the id")
    return next(iter(uniq.values()))


_NAME_RE = re.compile(r"GetName\s*\(\s*\)\s*\{\s*return\s*\"([^\"]+)\"")
_VER_RE = re.compile(r"GetVersion\s*\(\s*\)\s*\{\s*return\s*(\d+)")
_LIC_RE = re.compile(r"^(licen[cs]e|copying|gpl)", re.I)


def tar_provenance(path: Path) -> dict:
    """Name, version and licence of one downloaded package. The tar is read as data only."""
    name = version = licence = None
    library = False
    with tarfile.open(path) as t:
        for m in t.getmembers():
            if not m.isfile():
                continue
            base = m.name.rsplit("/", 1)[-1]
            if base in ("info.nut", "library.nut") and name is None:
                text = t.extractfile(m).read().decode("utf-8", "replace")  # type: ignore[union-attr]
                if (n := _NAME_RE.search(text)) and (v := _VER_RE.search(text)):
                    name, version = n.group(1), int(v.group(1))
                    library = "AILibrary" in text
            elif _LIC_RE.match(base) and licence is None:
                text = t.extractfile(m).read().decode("utf-8", "replace")  # type: ignore[union-attr]
                licence = next((ln.strip() for ln in text.splitlines() if ln.strip()), None)
    return {"file": path.name, "name": name, "version": version, "library": library, "licence": licence}


def _link_content(workdir: Path, cache: Path) -> None:
    """Point an engine instance's auto-download search path at the shared AI cache."""
    workdir.mkdir(parents=True, exist_ok=True)
    (cache / "content" / "ai" / "library").mkdir(parents=True, exist_ok=True)
    link = workdir / "content_download"
    if not link.exists():
        os.symlink((cache / "content").resolve(), link)


def fetch(names: list[str], cache: Path = DEFAULT_CACHE, wait: float = 120.0) -> list[dict]:
    """Download AIs (and the libraries they need) with the engine's content client; write a manifest."""
    from railhead.server import OpenTTDServer, ServerConfig

    cache = Path(cache)
    work = cache / "dl"
    _link_content(work, cache)
    server = OpenTTDServer(work, ServerConfig(game_script=None, map_x_log2=6, map_y_log2=6)).start()
    try:
        admin = server.admin()
        admin.rcon("content update ai")
        admin.rcon("content update ai_library")
        deadline = time.monotonic() + wait
        rows: list[dict] = []
        # The list streams in, and a late duplicate packet for an id resets its selection: wait until the
        # listing stops growing, then (re)select until the selection holds.
        while time.monotonic() < deadline:
            prev = len(rows)
            rows = parse_content_state(admin.rcon("content state"))
            if rows and len(rows) == prev and all(_found(rows, n) for n in names):
                break
            time.sleep(3.0)
        picks = [pick_content(rows, n) for n in names]
        selected: list[dict] = []
        for _ in range(5):
            for p in picks:
                if p["state"] != "Installed":
                    admin.rcon(f"content select {p['id']}")
            time.sleep(3.0)  # dependency info arrives asynchronously
            selected = parse_content_state(admin.rcon("content select"))
            got = {r["id"] for r in selected}
            if all(p["id"] in got for p in picks if p["state"] != "Installed"):
                break
        if selected:
            admin.rcon("content download")
        want = {r["id"] for r in selected}
        while want and time.monotonic() < deadline + wait:
            state = {r["id"]: r for r in parse_content_state(admin.rcon("content state"))}
            if all(state.get(i, {}).get("state") == "Installed" for i in want):
                break
            time.sleep(1.0)
        listing = parse_content_state(admin.rcon("content state"))
        admin.close()
    finally:
        server.stop()
    return write_manifest(cache, listing)


def _found(rows: list[dict], name: str) -> bool:
    try:
        pick_content(rows, name)
        return True
    except ValueError as e:
        return "ambiguous" in str(e)


def write_manifest(cache: Path, listing: list[dict]) -> list[dict]:
    """Provenance of every package in the cache, joined to the content-service rows by unique id.

    A downloaded file is named ``<unique id, byte-reversed hex>-<name>-<version>.tar``. Libraries can have
    several versions under one unique id, so ``content_ids`` lists every service row for that id."""
    by_uid: dict[str, dict[int, dict]] = {}
    for r in listing:
        by_uid.setdefault(r["unique_id"].upper(), {})[r["id"]] = r
    out = []
    for tar in sorted((cache / "content" / "ai").rglob("*.tar")):
        prov = tar_provenance(tar)
        head = tar.stem.split("-")[0]
        uid = "".join(reversed([head[i:i + 2] for i in range(0, len(head), 2)])).upper()
        svc = list(by_uid.get(uid, {}).values())
        prov["script_name"] = prov["name"]  # GetName() in info.nut: the name start_ai needs
        if svc:
            prov["name"] = svc[0]["name"]  # the content-service name (can differ, e.g. "Trans AI" vs "Trans")
        prov["package_version"] = tar.stem.split("-")[-1]
        prov.update(unique_id=uid, content_ids=sorted(r["id"] for r in svc),
                    content_id=svc[0]["id"] if len(svc) == 1 else None,
                    md5=svc[0]["md5"] if len(svc) == 1 else None, content_type=svc[0]["type"] if svc else None,
                    source="OpenTTD content service (BaNaNaS), engine console 'content download'")
        out.append(prov)
    (cache / "manifest.json").write_text(json.dumps(out, indent=1))
    return out


def ai_info(cache: Path, ai: str) -> dict:
    try:
        rows = json.loads((Path(cache) / "manifest.json").read_text())
    except (OSError, ValueError):
        return {}
    hit = [r for r in rows if not r.get("library")
           and ai.lower() in (str(r.get("name")).lower(), str(r.get("script_name")).lower())]
    return max(hit, key=lambda r: r.get("version") or 0) if hit else {}


# ------------------------------------------------------------------ observation
def _bind(world, cid: int) -> bool:
    try:
        world.call("bind_company", company=cid)
        return True
    except BridgeError:
        return False


def opening(world, cid: int) -> dict:
    _bind(world, cid)
    c = world.call("company")
    return {"date": world.state()["ymd"], "money": int(c["money"]), "loan": int(c["loan"]),
            "max_loan": int(c["max_loan"]), "value": int(c.get("value") or 0)}


def parity(model: dict, ai: dict) -> dict:
    """Opening state of the model's company against the AI's company (same savegame, same instant)."""
    diff = {k: ai[k] - model[k] for k in ("money", "loan", "max_loan", "value") if ai[k] != model[k]}
    same = not diff and ai["date"] == model["date"]
    return {"same": same, "model": model, "ai": ai, "diff": diff,
            "net_worth": {"model": model["money"] - model["loan"], "ai": ai["money"] - ai["loan"]}}


def sample(world, ai_id: int, model_id: int, start_date: int) -> dict | None:
    """One point of the AI company; None when the company no longer exists (bankrupt or removed)."""
    rows = {c["id"]: c for c in world.call("companies")}
    if ai_id not in rows or not _bind(world, ai_id):
        return None
    c = world.call("company")
    veh = world.call("vehicles")
    fleet = {m: 0 for m in MODES}
    for v in veh:
        if v.get("vtype") in fleet:
            fleet[v["vtype"]] += 1
    st = world.state()
    m = rows.get(model_id)
    return {"date": st["ymd"], "day": int(st["date"]) - start_date, "money": int(c["money"]),
            "loan": int(c["loan"]), "net_worth": int(c["money"]) - int(c["loan"]),
            "value": int(c.get("value") or 0), "vehicles": len(veh), "fleet": fleet,
            "stations": len(world.call("stations")),
            "model_net_worth": int(m["money"]) - int(m["loan"]) if m else None}


def script_deaths(log: str) -> int:
    return log.count(DEATH)


def last_script_error(log: str) -> str | None:
    hits = ERROR_RE.findall(log)
    return hits[-1].strip() if hits else None


def engine_settings(world) -> dict:
    out: dict[str, str | None] = {}
    for name in SETTINGS:
        lines = world.admin.rcon(f"setting {name}")
        m = re.search(r"is '([^']*)'", " ".join(lines))
        out[name] = m.group(1) if m else None
    return out


def reseed_value(seed: int) -> int:
    return (0x5EED0000 + 7919 * seed) & 0xFFFFFFFF


# ------------------------------------------------------------------ play
def play(world, spec: TaskSpec, ai: str, *, seed: int = 0, ai_settings: str = "", chunk_days: int = 91,
         log_text: Callable[[], str] = lambda: "", start_timeout: float = 30.0, max_wall: float | None = None,
         step_timeout: float = 7200.0) -> dict[str, Any]:
    """Start ``ai`` as a company in a loaded task world, run mandate + run-off, return the trace."""
    t0 = time.monotonic()
    st = world.state()
    start_date, model_id = int(st["date"]), int(st["company"])
    res: dict[str, Any] = {"ai": ai, "seed": seed, "reseed": reseed_value(seed) if seed else None,
                           "ai_settings": ai_settings, "status": "ok", "model_company": model_id,
                           "ai_company": None, "mandate": None, "final": None, "samples": [], "crash": None,
                           "gone_date": None, "parity": None, "start": st["ymd"]}
    model_open = opening(world, model_id)
    existing = {c["id"] for c in world.call("companies")}
    deaths0 = script_deaths(log_text())
    out = world.admin.rcon(f'start_ai "{ai}" {ai_settings}'.strip())
    res["rcon"] = out
    new: list[int] = []
    deadline = time.monotonic() + start_timeout
    while not new and time.monotonic() < deadline:
        new = [c["id"] for c in world.call("companies") if c["id"] not in existing]
        if not new:
            time.sleep(0.02)
    if not new:
        res["status"] = "failed_to_start"
        res["wall_seconds"] = round(time.monotonic() - t0, 1)
        return res
    ai_id = new[0]
    res["ai_company"] = ai_id
    if seed:
        r = world.admin.rcon(f"reseed {reseed_value(seed)}")
        if not r or not r[0].startswith("Reseeded"):
            raise RuntimeError(f"engine lacks the Railhead reseed patch: {r!r}")
    res["parity"] = parity(model_open, opening(world, ai_id))
    first = sample(world, ai_id, model_id, start_date)
    res["samples"].append(first)
    mandate_end = start_date + spec.horizon_days
    final_day = mandate_end + spec.runoff_days
    today = start_date
    for target, key in ((mandate_end, "mandate"), (final_day, "final")):
        while today < target:
            world.step(days=min(chunk_days, target - today), timeout=step_timeout)
            today = int(world.state()["date"])
            s = sample(world, ai_id, model_id, start_date)
            if s is None:
                res["status"] = "bankrupt"
                res["gone_date"] = world.state()["ymd"]
                res["wall_seconds"] = round(time.monotonic() - t0, 1)
                return res
            res["samples"].append(s)
            log = log_text()
            if res["crash"] is None and script_deaths(log) > deaths0:
                res["crash"] = {"date": s["date"], "error": last_script_error(log)}
                res["status"] = "crashed"
            if max_wall is not None and time.monotonic() - t0 > max_wall:
                res["status"] = "timeout"
                res["wall_seconds"] = round(time.monotonic() - t0, 1)
                return res
        res[key] = res["samples"][-1]
    res["wall_seconds"] = round(time.monotonic() - t0, 1)
    return res


def run_anchor(task_dir: Path, ai: str, seed: int, workdir: Path, cache: Path = DEFAULT_CACHE,
               horizon_days: int | None = None, runoff_days: int | None = None, **kw: Any) -> dict:
    """Boot an engine on the task's start savegame with the AI cache linked in, play, stop the engine."""
    from railhead.world import World

    task_dir, workdir = Path(task_dir), Path(workdir)
    spec = TaskSpec.load(task_dir)
    if horizon_days is not None:
        spec.horizon_days = horizon_days
    if runoff_days is not None:
        spec.runoff_days = runoff_days
    if workdir.exists():
        shutil.rmtree(workdir)
    _link_content(workdir, Path(cache))
    world = World.load(workdir, task_dir / "start.sav", speed=0)
    try:
        log = world.server.log_path
        res = play(world, spec, ai, seed=seed, log_text=lambda: log.read_text(errors="replace"), **kw)
        res["settings"] = engine_settings(world)
    finally:
        world.close()
    res["task"] = spec.task_id
    return res


# ------------------------------------------------------------------ rows
def make_row(spec: TaskSpec, res: dict, info: dict) -> dict:
    """One arena-shaped result row (``arena.py`` fields and rounding) plus anchor provenance."""
    obj = spec.full_objective
    w_idle, w_base = float(obj.get("w_idle", 0)), float(obj.get("w_base", 1))
    fin, man = res.get("final"), res.get("mandate")
    last = (res.get("samples") or [{}])[-1] or {}
    nw = fin["net_worth"] if fin else None
    # Name rows by the content-service name, so "--ai Trans" (its script name) and "--ai 'Trans AI'" match.
    row: dict[str, Any] = {"player": f"anchor:{info.get('name') or res['ai']}", "status": "anchor", "net_worth": nw,
                           "net_worth_horizon": man["net_worth"] if man else None}
    if nw is not None:
        row["gain_x_baseline"] = round((nw - w_idle) / (w_base - w_idle), 1)
        row["score_k50"] = round(rewards.tycoon_log(nw, w_idle, w_base, 50), 3)
        row["score_k1000"] = round(rewards.tycoon_log(nw, w_idle, w_base, 1000), 3)
    else:
        row["gain_x_baseline"] = row["score_k50"] = row["score_k1000"] = None
    src = fin or last
    row.update(vehicles=src.get("vehicles"), stations=src.get("stations"),
               reached=res.get("gone_date") or src.get("date"),
               outcome=res["status"], seed=res["seed"], reseed=res.get("reseed"),
               fleet=src.get("fleet"), fleet_horizon=(man or {}).get("fleet"),
               value_horizon=(man or {}).get("value"), value=src.get("value"),
               runoff_generous_to_ai=True, crash=res.get("crash"), wall_seconds=res.get("wall_seconds"),
               ai=res["ai"], ai_version=info.get("version"), ai_package=info.get("file"),
               content_id=info.get("content_id"),
               md5=info.get("md5"), licence=info.get("licence"), ai_settings=res.get("ai_settings") or "",
               parity=res.get("parity"), settings=res.get("settings"))
    return row


def curve(res: dict) -> list[dict]:
    keys = ("date", "net_worth", "money", "loan", "vehicles", "stations", "value")
    return [{k: s[k] for k in keys} for s in res.get("samples") or [] if s]


def _merge_task(entry: dict, rows: list[dict], curves: dict, fleet: dict) -> dict:
    keep = [r for r in entry.get("results", [])
            if not any(r.get("player") == n["player"] and r.get("seed") == n.get("seed") for n in rows)]
    entry["results"] = keep + rows
    entry.setdefault("curves", {}).update(curves)
    entry.setdefault("fleet_mix", {}).update(fleet)
    return entry


def merge_doc(doc: dict, task: str, rows: list[dict], curves: dict, fleet: dict) -> dict:
    """Add rows to an anchors document: one task -> arena shape; several tasks -> {"tasks": [...]}."""
    if not doc:
        return _merge_task({"task": task}, rows, curves, fleet)
    tasks = doc["tasks"] if "tasks" in doc else [doc]
    for entry in tasks:
        if entry["task"] == task:
            _merge_task(entry, rows, curves, fleet)
            break
    else:
        tasks.append(_merge_task({"task": task}, rows, curves, fleet))
    return tasks[0] if len(tasks) == 1 else {"tasks": tasks}


# ------------------------------------------------------------------ cli
def parse_args(argv: list[str]) -> argparse.Namespace:
    if argv and argv[0] == "fetch":
        p = argparse.ArgumentParser(prog="railhead anchor fetch")
        p.add_argument("names", nargs="+")
        p.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
        ns = p.parse_args(argv[1:])
        ns.cmd = "fetch"
        return ns
    p = argparse.ArgumentParser(prog="railhead anchor")
    p.add_argument("task_dir", type=Path)
    p.add_argument("--ai", action="append", required=True)
    p.add_argument("--seeds", type=int, default=1)
    p.add_argument("--first-seed", type=int, default=0)  # seeds are first-seed .. first-seed+seeds-1
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    p.add_argument("--work", type=Path, default=Path("out/anchors"))
    p.add_argument("--chunk-days", type=int, default=91)
    p.add_argument("--ai-settings", default="")
    p.add_argument("--max-wall", type=float, default=None)
    # Pilots and tests only: a shorter run is not the task, so --out refuses it.
    p.add_argument("--horizon-days", type=int, default=None)
    p.add_argument("--runoff-days", type=int, default=None)
    ns = p.parse_args(argv)
    if ns.out and (ns.horizon_days is not None or ns.runoff_days is not None):
        p.error("--out records task results; do not combine it with --horizon-days/--runoff-days")
    ns.cmd = "run"
    return ns


def main(argv: list[str]) -> int:
    a = parse_args(argv)
    if a.cmd == "fetch":
        for row in fetch(a.names, a.cache):
            print(json.dumps(row))
        return 0
    spec = TaskSpec.load(a.task_dir)
    for ai in a.ai:
        for seed in range(a.first_seed, a.first_seed + a.seeds):
            work = a.work / f"{spec.task_id}-{ai.replace(' ', '_')}-s{seed}"
            res = run_anchor(a.task_dir, ai, seed, work, a.cache, chunk_days=a.chunk_days,
                             ai_settings=a.ai_settings, max_wall=a.max_wall, horizon_days=a.horizon_days,
                             runoff_days=a.runoff_days)
            (work.parent / f"{work.name}.json").write_text(json.dumps(res, indent=1))
            row = make_row(spec, res, ai_info(a.cache, ai))
            print(json.dumps({k: row[k] for k in ("player", "seed", "outcome", "net_worth_horizon", "net_worth",
                                                  "vehicles", "fleet", "wall_seconds")}), flush=True)
            if a.out:
                key = f"{row['player']}#{seed}"
                add_to_doc(a.out, spec.task_id, [row], {key: curve(res)}, {key: row["fleet"] or {}})
    return 0


def add_to_doc(path: Path, task: str, rows: list[dict], curves: dict, fleet: dict) -> None:
    """Read-merge-write under an exclusive lock: several anchor processes may share one --out file."""
    import fcntl

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    import hashlib
    import tempfile

    # The lock lives outside the repository so docs/data gets no stray files.
    key = hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:16]
    with open(Path(tempfile.gettempdir()) / f"railhead-anchor-{key}.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        doc = json.loads(path.read_text()) if path.exists() and path.stat().st_size else {}
        doc = merge_doc(doc, task, rows, curves, fleet)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(doc, indent=1))
        tmp.replace(path)
