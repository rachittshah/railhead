"""``railhead`` — operator commands (not for the playing model).

    railhead up [task_id]        start/attach the daemon for a task (idempotent; used by Harbor)
    railhead down                stop the daemon and its engines
    railhead ping                daemon health
    railhead play <log.jsonl>    re-issue a recorded action log through the daemon (oracle solve.sh)
    railhead export <out> [ids]  write Harbor task dirs (run inside the image) [--image REF] [--version V]
    railhead gen <family> <split> <from> <to> [--out tasks] [--jobs N]   generate certified tasks
    railhead export-manifest <out>  add this CPU architecture's sealed manifest to exported tasks
    railhead hub build <tasks> <harbor> <out> [--credit] | railhead hub push <out> [--repo R]
    railhead recheck <tasks_dir> [ids]   re-verify oracle logs against certificates (cross-platform check)
"""

from __future__ import annotations

import json
import sys

from pathlib import Path

from railhead.actionlog import read_log
from railhead.client import Client, DaemonError
from railhead.daemon import ensure_up, shutdown


def recheck(tasks_dir: Path, ids: list[str] | None = None) -> int:
    """Re-score every task's oracle log and compare with the stored certificate (determinism check)."""
    import tempfile

    from railhead.verify import verify

    bad = 0
    dirs = sorted(p for p in tasks_dir.iterdir() if (p / "certificates.json").exists())
    for tdir in dirs:
        if ids and tdir.name not in ids:
            continue
        cert = json.loads((tdir / "certificates.json").read_text())
        out = verify(tdir, tdir / "start.sav", tdir / "oracle.jsonl", Path(tempfile.mkdtemp(prefix="rh-recheck-")))
        same = out["valid"] == 1 and abs(out["reward"] - cert["oracle_reward"]) < 1e-9
        bad += 0 if same else 1
        print(json.dumps({"task": tdir.name, "match": same, "reward": out["reward"], "cert": cert["oracle_reward"],
                          "delivered": out.get("delivered"), "valid": out["valid"]}), flush=True)
    print(json.dumps({"checked": len(dirs), "mismatches": bad}))
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
        if cmd == "export-manifest":
            from railhead.harbor_export import add_arch_manifests

            print(json.dumps({"manifests": add_arch_manifests(Path(argv[1]))}))
            return 0
        if cmd == "hub":
            from railhead import hub

            if argv[1] == "build":
                counts = hub.build(Path(argv[2]), Path(argv[3]), Path(argv[4]), credit="--credit" in argv)
                print(json.dumps(counts))
            elif argv[1] == "push":
                repo = argv[argv.index("--repo") + 1] if "--repo" in argv else "rachittshah/railhead"
                print(hub.push(Path(argv[2]), repo=repo))
            return 0
        if cmd == "recheck":
            return recheck(Path(argv[1]), argv[2:] or None)
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
