"""Multi-company start states for Waybill (docs/WAYBILL.md, section 3).

One task world gets N companies, each founded by the idle AI ``RailheadIdle`` (as ``World`` founds the
single company), with a neutral name and the same start cash. The world is saved as ``start.sav``
with all of them; the task directory also gets ``task.json`` (family ``waybill``) whose objective
lists the companies as ``[{id, name, slot}]``.

    python -m railhead.multi.gen <seed> <out_dir> [--agents N] [--split train] [--horizon DAYS]
        [--runoff DAYS] [--map-log2 K] [--start-cash MONEY]
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

from railhead.seal import seal_task
from railhead.task import TaskSpec
from railhead.world import AGENT_AI, World

FAMILY = "waybill"


def company_name(slot: int) -> str:
    """A neutral name: it does not tell the agents which model plays which company."""
    return f"Company {chr(ord('A') + slot)}" if slot < 26 else f"Company {slot + 1}"


def found_company(world: World, timeout: float = 30.0) -> int:
    """Start one idle AI and return the id of the company that it founds."""
    existing = {c["id"] for c in world.call("companies")}
    world.admin.rcon(f"start_ai {AGENT_AI}")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        new = [c["id"] for c in world.call("companies") if c["id"] not in existing]
        if new:
            return int(new[0])
        time.sleep(0.02)
    raise TimeoutError("company was not founded (start_ai RailheadIdle)")


def found_companies(world: World, n: int, start_cash: int | None = None) -> list[dict]:
    """Found ``n`` companies in ``world`` and return ``[{id, name, slot}]`` in slot order.

    ``start_cash``: the loan (and so the cash) of every company at the start. A new company starts with
    its cash equal to its loan; the generator sets the loan to ``start_cash``, so that cash equals
    ``start_cash`` and net worth stays 0. It must be a multiple of the loan interval and at most the
    maximum loan."""
    if n < 1:
        raise ValueError("at least one company")
    out = []
    for slot in range(n):
        cid = found_company(world)
        world.call("bind_company", company=cid)
        name = company_name(slot)
        world.call_args("company_rename", {"name": name})
        if start_cash is not None:
            world.call_args("company_loan", {"amount": int(start_cash)})
        out.append({"id": cid, "name": name, "slot": slot})
    world.call("bind_company", company=out[0]["id"])
    return out


def build_start(world: World, dest: Path, n: int, start_cash: int | None = None,
                history_days: int = 31) -> list[dict]:
    """Run ``history_days`` (so production figures are real), found the companies, save ``dest``."""
    if history_days > 0:
        world.step(days=history_days)
    companies = found_companies(world, n, start_cash=start_cash)
    world.save(Path(dest))
    return companies


def write_task(tdir: Path, task_id: str, companies: list[dict], horizon_days: int, runoff_days: int = 0,
               scenario: dict | None = None, split: str = "train", agent_turn_s: float = 600.0) -> TaskSpec:
    spec = TaskSpec(
        task_id=task_id, family=FAMILY, horizon_days=horizon_days, runoff_days=runoff_days,
        limits={"max_forks": 0, "max_fork_days": 0, "agent_turn_s": agent_turn_s},
        objective={"companies": companies}, scenario=scenario or {}, split=split,
        title="Waybill: a shared world",
        briefing=("You run one of several transport companies in one shared world. Each company is played by "
                  "a different agent. Game time passes only when every company waits (`rh wait`). "
                  "Your score is your net worth (cash minus loan) at the end."),
    )
    spec.save(Path(tdir))
    if (Path(tdir) / "start.sav").exists():
        seal_task(Path(tdir))  # the verifier then refuses a changed start.sav (tamper=1)
    return spec


def gen_waybill(seed: int, out: Path, n: int = 3, split: str = "train", horizon_days: int = 730,
                runoff_days: int = 0, map_log2: int | None = None, start_cash: int | None = None) -> TaskSpec:
    from railhead.gen.scenario import sample_scenario, server_config, task_id

    sc = sample_scenario(seed, split, FAMILY)
    if map_log2:
        sc["map_log2"] = map_log2
    tid = task_id(FAMILY, split, seed)
    tdir = Path(out) / tid
    tdir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f"rh-gen-{tid}-"))
    try:
        with World.create(work / "gen", server_config(sc), found_company=False) as w:
            companies = build_start(w, tdir / "start.sav", n, start_cash=start_cash)
        return write_task(tdir, tid, companies, horizon_days, runoff_days, scenario=sc, split=split)
    except Exception:
        shutil.rmtree(tdir, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    opts = {"--agents": "3", "--split": "train", "--horizon": "730", "--runoff": "0", "--map-log2": "",
            "--start-cash": ""}
    pos = []
    it = iter(argv)
    for a in it:
        if a in opts:
            opts[a] = next(it)
        else:
            pos.append(a)
    if len(pos) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    spec = gen_waybill(int(pos[0]), Path(pos[1]), n=int(opts["--agents"]), split=opts["--split"],
                       horizon_days=int(opts["--horizon"]), runoff_days=int(opts["--runoff"]),
                       map_log2=int(opts["--map-log2"]) if opts["--map-log2"] else None,
                       start_cash=int(opts["--start-cash"]) if opts["--start-cash"] else None)
    print(spec.task_id, [c["name"] for c in spec.objective["companies"]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
