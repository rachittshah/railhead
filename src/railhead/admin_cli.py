"""``railhead`` — operator commands (not for the playing model).

    railhead up [task_id]        start/attach the daemon for a task (idempotent; used by Harbor)
    railhead down                stop the daemon and its engines
    railhead ping                daemon health
    railhead play <log.jsonl>    re-issue a recorded action log through the daemon (oracle solve.sh)
    railhead export <out> [ids]  write Harbor task dirs (run inside the image) [--image REF] [--version V]
    railhead gen <family> <split> <from> <to> [--out tasks] [--jobs N]   generate certified tasks
"""

from __future__ import annotations

import json
import sys

from pathlib import Path

from railhead.actionlog import read_log
from railhead.client import Client, DaemonError
from railhead.daemon import ensure_up, shutdown


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
