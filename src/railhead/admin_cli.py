"""``railhead`` — operator commands (not for the playing model).

    railhead up [task_id]        start/attach the daemon for a task (idempotent; used by Harbor)
    railhead down                stop the daemon and its engines
    railhead ping                daemon health
    railhead play <log.jsonl>    re-issue a recorded action log through the daemon (oracle solve.sh)
    railhead export <out> [ids]  write Harbor task dirs (run inside the image) [--image REF] [--version V]
    railhead gen <family> <split> <from> <to> [--out tasks] [--jobs N] [--years N --map 256|512|1024]   generate certified tasks (years/map: arena only)
    railhead arena-run <task> <dir> claude:<model> codex:<model> ...   launch an arena (headless, traced)
    railhead arena <dir> [--json]  scoreboard of several players on one task (growth curves)
    railhead view --attach <state_dir>  spectate a live daemon world
    railhead view <task_id> [--days N] [--speed PCT]   watch a task live in the GUI (macOS)
    railhead export-manifest <out>  add this CPU architecture's sealed manifest to exported tasks
    railhead hub build <tasks> <harbor> <out> [--credit] | railhead hub push <out> [--repo R]
    railhead hub add-games <out> <arena_dir> <task_dir> [--rename a=b,...]   add an arena's verified games
    railhead otel serve|summary [--port 4318] [--dir traces]   zero-dependency OTLP/HTTP trace sink
    railhead trace bundle <arena_dir> [--otel traces] [--out DIR]   per-player e2e trace bundles (scrubbed, local)
    railhead figures docs/data/<arena>.json ... [--out docs/img]   result charts (SVG, light and dark)
    railhead econ <arena_dir> --task tasks/<id> [--out FILE]   per-player economics from the action logs
    railhead render <state_dir> <out.png> [--title T]   network map of a live world (engine minimap + routes)
    railhead harden <tasks_dir>   apply leak fixes to an existing catalog (ids, scenario, targets)
    railhead recheck <tasks_dir> [ids] [--shard i/n] [--jobs N]   re-verify oracle logs against certificates
"""

from __future__ import annotations

import json
import sys

from pathlib import Path

from railhead.actionlog import read_log
from railhead.client import Client, DaemonError
from railhead.daemon import ensure_up, shutdown


def parse_shard(text: str) -> tuple[int, int]:
    """'i/n' -> (i, n) with 0 <= i < n."""
    try:
        i, n = (int(x) for x in text.split("/"))
    except ValueError:
        raise ValueError(f"shard must be i/n, got {text!r}") from None
    if n < 1 or not 0 <= i < n:
        raise ValueError(f"shard must be i/n with 0 <= i < n, got {text!r}")
    return i, n


def shard_dirs(dirs: list[Path], i: int, n: int) -> list[Path]:
    """Every n-th task, starting at i: the shards partition the sorted catalog exactly once."""
    return dirs[i::n]


def _recheck_one(tdir_s: str) -> dict:
    import tempfile

    from railhead.verify import verify

    tdir = Path(tdir_s)
    cert = json.loads((tdir / "certificates.json").read_text())
    out = verify(tdir, tdir / "start.sav", tdir / "oracle.jsonl", Path(tempfile.mkdtemp(prefix="rh-recheck-")))
    same = out["valid"] == 1 and abs(out["reward"] - cert["oracle_reward"]) < 1e-9
    return {"task": tdir.name, "match": same, "reward": out["reward"], "cert": cert["oracle_reward"],
            "delivered": out.get("delivered"), "valid": out["valid"]}


def recheck(tasks_dir: Path, ids: list[str] | None = None, shard: tuple[int, int] = (0, 1), jobs: int = 1) -> int:
    """Re-score every task's oracle log and compare with the stored certificate (determinism check)."""
    from concurrent.futures import ProcessPoolExecutor

    dirs = sorted(p for p in tasks_dir.iterdir() if (p / "certificates.json").exists())
    dirs = [d for d in shard_dirs(dirs, *shard) if not ids or d.name in ids]
    bad = 0
    with ProcessPoolExecutor(max_workers=max(1, jobs)) as ex:
        for row in ex.map(_recheck_one, [str(d) for d in dirs]):
            bad += 0 if row["match"] else 1
            print(json.dumps(row), flush=True)
    print(json.dumps({"checked": len(dirs), "mismatches": bad, "shard": f"{shard[0]}/{shard[1]}"}))
    return 1 if bad else 0


def play(log_path: str) -> int:
    """Replay a recorded log *through the live daemon* (so it is re-logged exactly as a player would)."""
    c = Client()
    n = 0
    for rec in read_log(Path(log_path)):
        r = rec.raw
        if rec.kind == "act":
            try:
                c.request("act", m=r["m"], a=r.get("a") or {})
            except DaemonError:
                pass  # recorded failures fail again; they are part of the log
        elif rec.kind == "wait":
            a = {"ticks": int(r["end_ticks"]) - int(r["ticks"])}
            if (r.get("a") or {}).get("until"):
                a["until"] = r["a"]["until"]
            c.request("wait", **a)
        elif rec.kind == "meta":
            c.request("meta", m=r["m"], a=r.get("a") or {})
        else:
            continue
        n += 1
    return n


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "help"
    try:
        if cmd == "up":
            pid = ensure_up(argv[1] if len(argv) > 1 else None)
            print(json.dumps({"up": True, "pid": pid}))
            return 0
        if cmd == "down":
            shutdown()
            print(json.dumps({"down": True}))
            return 0
        if cmd == "ping":
            print(json.dumps(Client(timeout=10).request("ping")))
            return 0
        if cmd == "play":
            n = play(argv[1])
            print(json.dumps({"played": n}))
            return 0
        if cmd == "arena-run":
            from railhead.arena_run import main as arena_run_main

            return arena_run_main(argv[1:])
        if cmd == "arena":
            from railhead.arena import main as arena_main

            return arena_main(argv[1:])
        if cmd == "view":
            from railhead.viewer import attach, view

            try:
                if len(argv) > 2 and argv[1] == "--attach":
                    attach(Path(argv[2]))
                    return 0
                days = int(argv[argv.index("--days") + 1]) if "--days" in argv else 365
                speed = int(argv[argv.index("--speed") + 1]) if "--speed" in argv else 300
                log = Path(argv[argv.index("--log") + 1]) if "--log" in argv else None
                view(argv[1], days=days, speed=speed, log=log)
            except RuntimeError as e:  # no LG monitor, or the window did not land on it
                print(f"railhead view: {e}", file=sys.stderr)
                return 1
            return 0
        if cmd == "export-manifest":
            from railhead.harbor_export import add_arch_manifests

            print(json.dumps({"manifests": add_arch_manifests(Path(argv[1]))}))
            return 0
        if cmd == "hub":
            from railhead import hub

            if argv[1] == "build":
                counts = hub.build(Path(argv[2]), Path(argv[3]), Path(argv[4]), credit="--credit" in argv)
                print(json.dumps(counts))
            elif argv[1] == "add-games":
                names = dict(x.split("=", 1) for x in argv[argv.index("--rename") + 1].split(",")) \
                    if "--rename" in argv else None
                print(json.dumps(hub.add_games(Path(argv[2]), Path(argv[3]), Path(argv[4]), rename=names)))
            elif argv[1] == "push":
                repo = argv[argv.index("--repo") + 1] if "--repo" in argv else "rachittshah/railhead"
                print(hub.push(Path(argv[2]), repo=repo))
            return 0
        if cmd == "otel":
            from railhead.otel import main as otel_main

            return otel_main(argv[1:])
        if cmd == "trace":
            from railhead.trace import main as trace_main

            return trace_main(argv[1:])
        if cmd == "econ":
            from railhead.econ import main as econ_main

            return econ_main(argv[1:])
        if cmd == "figures":
            from railhead.figures import main as figures_main

            return figures_main(argv[1:])
        if cmd == "render":
            from railhead.render import main as render_main

            return render_main(argv[1:])
        if cmd == "harden":
            from railhead.gen.build import harden_catalog

            if "--ids-only" not in argv:
                print(json.dumps(harden_catalog(Path(argv[1]))))
            from railhead.gen.build import opaque_catalog

            print(json.dumps(opaque_catalog(Path(argv[1]))))
            return 0
        if cmd == "recheck":
            rest, shard, jobs = argv[2:], (0, 1), 1
            if "--shard" in rest:
                k = rest.index("--shard")
                shard, rest = parse_shard(rest[k + 1]), rest[:k] + rest[k + 2:]
            if "--jobs" in rest:
                k = rest.index("--jobs")
                jobs, rest = int(rest[k + 1]), rest[:k] + rest[k + 2:]
            return recheck(Path(argv[1]), rest or None, shard=shard, jobs=jobs)
        if cmd == "gen":
            from railhead.gen.build import main as gen_main

            return gen_main(argv[1:])
        if cmd == "export":
            from railhead.harbor_export import DEFAULT_IMAGE, export_all

            image, version, rest = DEFAULT_IMAGE, "0.1.0", []
            it = iter(argv[1:])
            for a in it:
                if a == "--image":
                    image = next(it)
                elif a == "--version":
                    version = next(it)
                else:
                    rest.append(a)
            out = export_all(Path(rest[0]), rest[1:] or None, image=image, version=version)
            print(json.dumps({"exported": len(out)}))
            return 0
    except (DaemonError, OSError) as e:
        print(f"railhead {cmd}: {e}", file=sys.stderr)
        return 1
    print(__doc__)
    return 0 if cmd in ("help", "-h", "--help") else 2


if __name__ == "__main__":
    raise SystemExit(main())
