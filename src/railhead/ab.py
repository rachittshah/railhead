"""A/B experiments: the same models on the same tasks under two (or more) versions of the environment.

    railhead ab plan     <plan.json|plan.toml> [--out DIR]   # check the plan; print cells, order and commands
    railhead ab run      <plan> [--out DIR] [--retry-failed]  # launch, supervise, resume (never reruns a finished cell)
    railhead ab status   <plan> [--out DIR] [--json]
    railhead ab collect  <plan> [--out DIR]                   # each arm's verifier scores each arm's games
    railhead ab report   <plan> [--out DIR] [--boot N] [--seed S]   # paired differences -> report.md + report.json
    railhead ab behaviour <player_dir> --task <task_dir> [--otel DIR] [--json]

An ARM is a Railhead checkout: its ``.venv/bin/railhead`` launches the players, so the ``rh`` a player sees and
the verifier that scores the game are that checkout's code. A CELL is (arm, task, model, replicate) and lives in
``<out>/<arm>/<task>/<vendor>-<label>/r<k>/``; the player itself is in ``<cell>/<label>/`` (arena layout).
The output directory defaults to ``<plan dir>/out``. Nothing is ever deleted. See docs/AB.md.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import shutil
import socket
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path

from railhead.log import event, get_logger, now_iso, setup

log = get_logger("ab")
VENDORS = ("claude", "codex", "opencode", "script")
HARNESS_CLI = {"claude": "claude", "codex": "codex", "opencode": "opencode"}
OTEL_PORT = 4318  # the control arm's supervisor exports to this port and takes no --otel flag
FINISHED, FAILED, RUNNING, PENDING = "finished", "failed_infra", "running", "pending"
BASE_METRICS = ("net_worth", "score_k50", "score_k1000", "net_worth_horizon")
SMALL_N = 6  # below 6 non-zero pairs a two-sided sign test cannot reach p < 0.05 (2 / 2**5 = 0.0625)


# ------------------------------------------------------------------------------------------------------ plan

@dataclass
class Arm:
    name: str
    checkout: Path
    env: dict = field(default_factory=dict)
    tasks_dir: Path | None = None
    task_map: dict = field(default_factory=dict)  # canonical task id -> this arm's task id (variant tasks)
    supervise_args: list = field(default_factory=list)  # e.g. ["--poll", "2"] (only arms whose arena_run has it)

    @property
    def railhead(self) -> Path:
        return self.checkout / ".venv" / "bin" / "railhead"

    @property
    def python(self) -> Path:
        return self.checkout / ".venv" / "bin" / "python"

    @property
    def tasks(self) -> Path:
        return self.tasks_dir or self.checkout / "tasks"

    def task_id(self, task: str) -> str:
        return self.task_map.get(task, task)


@dataclass
class Plan:
    path: Path
    raw: dict
    name: str
    hypothesis: str
    primary_metric: str
    arms: list[Arm]
    models: list[str]
    tasks: list[str]
    replicates: int
    parallel: int
    billing: str
    seed: int | None
    max_resumes: int | None
    otel_dir: Path | None
    secondary: list[str] = field(default_factory=list)

    @property
    def hash(self) -> str:
        return hashlib.sha256(json.dumps(self.raw, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _label(model_spec: str) -> str:
    return model_spec.split(":", 1)[1].split("/")[-1]


def _slug(model_spec: str) -> str:
    vendor = model_spec.split(":", 1)[0]
    return re.sub(r"[^A-Za-z0-9._-]+", "_", f"{vendor}-{_label(model_spec)}")


def load_plan(path: Path) -> Plan:
    """Read a JSON or TOML plan and check it (raises ValueError with every problem found)."""
    path = Path(path).resolve()
    text = path.read_bytes()
    if path.suffix == ".toml":
        import tomllib

        raw = tomllib.loads(text.decode())
    else:
        raw = json.loads(text)
    base = path.parent
    errs: list[str] = []
    arms = []
    for a in raw.get("arms") or []:
        co = (base / str(a.get("checkout", ""))).resolve() if a.get("checkout") else None
        if not a.get("name") or not re.fullmatch(r"[A-Za-z0-9._-]+", str(a["name"])):
            errs.append(f"arm name {a.get('name')!r}: use letters, digits, '.', '_' or '-'")
        if co is None:
            errs.append(f"arm {a.get('name')!r}: no checkout")
            continue
        td = (base / a["tasks_dir"]).resolve() if a.get("tasks_dir") else None
        arms.append(Arm(str(a.get("name")), co, {str(k): str(v) for k, v in (a.get("env") or {}).items()}, td,
                        dict(a.get("task_map") or {}), [str(x) for x in a.get("supervise_args") or []]))
    if len(arms) < 2:
        errs.append("a plan needs at least two arms (the first one is the baseline)")
    if len({a.name for a in arms}) != len(arms):
        errs.append("arm names must be unique")
    models = []
    for m in raw.get("models") or []:
        vendor, _, model = str(m).partition(":")
        if vendor not in VENDORS or not model:
            errs.append(f"model {m!r}: write vendor:model with vendor in {VENDORS}")
            continue
        if vendor == "script":
            model = str((base / model).resolve())
        models.append(f"{vendor}:{model}")
    if not models:
        errs.append("no models")
    if len({_slug(m) for m in models}) != len(models):
        errs.append("two models map to the same directory name (vendor + last path part)")
    tasks = [str(t) for t in raw.get("tasks") or []]
    if not tasks:
        errs.append("no tasks")
    for a in arms:
        if not a.railhead.exists():
            errs.append(f"arm {a.name}: {a.railhead} does not exist")
        for t in tasks:
            if not (a.tasks / a.task_id(t) / "task.json").exists():
                errs.append(f"arm {a.name}: task {a.task_id(t)} not found in {a.tasks}")
    from railhead.behaviour import SCALAR_DEFINITIONS

    primary = str(raw.get("primary_metric") or "")
    if primary not in (*BASE_METRICS, *SCALAR_DEFINITIONS):
        errs.append(f"primary_metric {primary!r} is not a known metric (see `railhead ab plan --metrics`)")
    secondary = [str(x) for x in raw.get("secondary_metrics") or []]
    for s in secondary:
        if s not in (*BASE_METRICS, *SCALAR_DEFINITIONS):
            errs.append(f"secondary metric {s!r} is not a known metric")
    if not str(raw.get("hypothesis") or "").strip():
        errs.append("write the hypothesis before the run (pre-registration)")
    reps, par = int(raw.get("replicates", 1)), int(raw.get("parallel", 1))
    if reps < 1 or par < 1:
        errs.append("replicates and parallel must be >= 1")
    if not str(raw.get("billing") or "").strip():
        errs.append("set billing (for example \"subscription\" or \"api\")")
    if errs:
        raise ValueError("plan " + str(path) + ":\n  - " + "\n  - ".join(errs))
    otel = raw.get("otel_dir")
    return Plan(path, raw, str(raw.get("name") or path.parent.name), str(raw["hypothesis"]).strip(), primary, arms,
                models, tasks, reps, par, str(raw["billing"]), raw.get("seed"), raw.get("max_resumes"),
                (base / otel).resolve() if otel else None, [s for s in secondary if s != primary])


@dataclass(frozen=True)
class Cell:
    arm: str
    task: str
    model: str
    rep: int

    @property
    def id(self) -> str:
        return f"{self.arm}/{self.task}/{_slug(self.model)}/r{self.rep}"

    @property
    def vendor(self) -> str:
        return self.model.split(":", 1)[0]

    @property
    def label(self) -> str:
        return _label(self.model)

    @property
    def key(self) -> tuple[str, str]:
        """Cells with the same key never run at the same time: OTLP records carry only player and task."""
        return (self.task, self.label)

    def dir(self, out: Path) -> Path:
        return Path(out) / self.id

    def player_dir(self, out: Path) -> Path:
        return self.dir(out) / self.label


def cells(plan: Plan) -> list[Cell]:
    return [Cell(a.name, t, m, k) for a in plan.arms for t in plan.tasks for m in plan.models
            for k in range(1, plan.replicates + 1)]


def run_order(all_cells: list[Cell], seed: int) -> list[Cell]:
    """A seeded random order of the cells, so that arm and time of day are not confounded."""
    order = sorted(all_cells, key=lambda c: c.id)
    random.Random(seed).shuffle(order)
    return order


def default_out(plan: Plan) -> Path:
    return plan.path.parent / "out"


# ------------------------------------------------------------------------------------------- files and events

def _write_json(path: Path, obj) -> None:
    """Atomic write (temporary file + rename): a reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}.{threading.get_ident()}")
    tmp.write_text(json.dumps(obj, indent=1, default=str) + "\n")
    os.replace(tmp, path)


def _read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return default


class Events:
    """The experiment's append-only event log (``events.jsonl``)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def emit(self, name: str, cell: Cell | None = None, **fields) -> dict:
        rec = {"ts": round(time.time(), 3), "time": now_iso(), "event": name}
        if cell:
            rec.update(cell=cell.id, arm=cell.arm, task=cell.task, model=cell.model, rep=cell.rep)
        rec.update(fields)
        with self.lock, open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
        event(log, name, **{k: v for k, v in rec.items() if k not in ("ts", "time", "event")})
        return rec

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(x) for x in self.path.read_text().splitlines() if x.strip()]


def sha256_file(path: Path) -> str | None:
    p = Path(path)
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# --------------------------------------------------------------------------------------------- provenance

def _run(cmd: list, env: dict | None = None, timeout: float = 60, cwd: Path | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run([str(c) for c in cmd], check=False, capture_output=True, text=True, env=env,
                           timeout=timeout, cwd=cwd)
        return p.returncode, (p.stdout or "") + (p.stderr if p.returncode else "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, f"{type(e).__name__}: {e}"


def arm_env(arm: Arm, base: dict | None = None) -> dict:
    """The environment of every process of an arm: the operator's, without another checkout's Python or Railhead
    settings (RAILHEAD_OPENTTD and RAILHEAD_LOG_LEVEL stay), plus the arm's tasks and extra variables."""
    keep = ("RAILHEAD_OPENTTD", "RAILHEAD_LOG_LEVEL")
    env = {k: v for k, v in (os.environ if base is None else base).items()
           if k not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV") and (not k.startswith("RAILHEAD_") or k in keep)}
    env["RAILHEAD_TASKS"] = str(arm.tasks)
    env["PATH"] = str(arm.railhead.parent) + ":" + env.get("PATH", "")
    env.update(arm.env)
    return env


def _git(checkout: Path, *args: str, timeout: float = 60) -> tuple[int, str]:
    # --no-optional-locks: `git status` must not refresh the index (and take index.lock) in a checkout that
    # another process uses; every call here only reads
    return _run(["git", "--no-optional-locks", "-C", checkout, *args], timeout=timeout)


def git_state(checkout: Path, save_dir: Path | None = None, name: str = "arm") -> dict:
    """Commit, dirty flag and a hash of the uncommitted work (tracked diff + untracked files). Read-only git.
    With ``save_dir``, the uncommitted work is also kept as ``<name>-<hash12>.patch`` (plus the untracked files and
    their hashes), once per distinct hash: a hash identifies a dirty tree, the patch lets you rebuild it."""
    rc, head = _git(checkout, "rev-parse", "HEAD")
    if rc != 0:
        return {"commit": None, "dirty": None, "diff_sha256": None, "error": head.strip()[:200]}
    _, status = _git(checkout, "status", "--porcelain")
    _, diff = _git(checkout, "diff", "HEAD", "--binary", timeout=120)
    _, untracked = _git(checkout, "ls-files", "--others", "--exclude-standard")
    h = hashlib.sha256(diff.encode())
    files = {f: sha256_file(checkout / f) for f in sorted(x for x in untracked.splitlines() if x.strip())}
    for f, fh in files.items():
        h.update(f"\0{f}\0{fh or ''}".encode())
    out = {"commit": head.strip(), "dirty": bool(status.strip()), "diff_sha256": h.hexdigest(),
           "untracked_files": len(files)}
    if save_dir is not None and out["dirty"]:
        patch = Path(save_dir) / f"{name}-{out['diff_sha256'][:12]}.patch"
        if not patch.exists():  # never overwrite
            patch.parent.mkdir(parents=True, exist_ok=True)
            patch.write_text(diff)
            _write_json(patch.with_suffix(".untracked.json"), {"commit": out["commit"], "files": files})
        out["patch"] = str(patch)
    return out


_PROBE = ("import json, railhead, sys\n"
          "from railhead.engine import find_openttd\n"
          "from railhead import arena_run as a\n"
          "try:\n    eng = str(find_openttd())\nexcept Exception as e:\n    eng = None\n"
          "print(json.dumps({'railhead_file': railhead.__file__, 'python': sys.version.split()[0], 'engine': eng,"
          " 'prompt_sha256': __import__('hashlib').sha256(a.PROMPT.encode()).hexdigest(),"
          " 'resume_prompt_sha256': __import__('hashlib').sha256(getattr(a, 'RESUME_PROMPT', '').encode()).hexdigest()}))\n")


class Provenance:
    """What produced a result. The arm's code (git state, prompt, engine path) is read again for every cell,
    because a checkout can change during a long run; the engine hash is cached by path, mtime and size, and the
    harness CLI version once per invocation. ``save_dir`` keeps a patch of each distinct dirty tree."""

    def __init__(self, save_dir: Path | None = None) -> None:
        self.lock = threading.Lock()
        self.save_dir = save_dir
        self._cli: dict[str, dict] = {}
        self._bin: dict[tuple, str | None] = {}

    def arm(self, arm: Arm) -> dict:
        rc, out = _run([arm.python, "-I", "-c", _PROBE], env=arm_env(arm))
        try:
            probe = json.loads(out.strip().splitlines()[-1]) if rc == 0 else {"error": out[-300:]}
        except (json.JSONDecodeError, IndexError):
            probe = {"error": out[-300:]}
        eng = probe.get("engine")
        with self.lock:  # one writer of the patch files at a time
            git = git_state(arm.checkout, self.save_dir, arm.name)
        return {"checkout": str(arm.checkout), "tasks_dir": str(arm.tasks), "git": git,
                **probe, "engine_sha256": self._hash_bin(eng) if eng else None,
                "env": {k: sha256_text(v)[:12] for k, v in sorted(arm.env.items())},
                "own_code": bool(probe.get("railhead_file")) and
                Path(probe["railhead_file"]).resolve().is_relative_to(arm.checkout.resolve())}

    def _hash_bin(self, path: str) -> str | None:
        p = Path(path)
        try:
            key = (str(p), p.stat().st_mtime, p.stat().st_size)
        except OSError:
            return None
        with self.lock:
            if key not in self._bin:
                self._bin[key] = sha256_file(p)
            return self._bin[key]

    def harness(self, vendor: str) -> dict:
        with self.lock:
            if vendor not in self._cli:
                cli = HARNESS_CLI.get(vendor)
                if not cli:
                    self._cli[vendor] = {"cli": "railhead arena-run --play-script", "version": None}
                elif not shutil.which(cli):
                    self._cli[vendor] = {"cli": cli, "version": None, "error": "not on PATH"}
                else:
                    rc, out = _run([cli, "--version"], timeout=30)
                    self._cli[vendor] = {"cli": cli, "version": out.strip().splitlines()[0] if rc == 0 and out.strip()
                                         else None, **({} if rc == 0 else {"error": out.strip()[:200]})}
            return self._cli[vendor]


def task_files(arm: Arm, task: str) -> dict:
    tdir = arm.tasks / arm.task_id(task)
    spec = _read_json(tdir / "task.json", {}) or {}
    return {"dir": str(tdir), "task_json_sha256": sha256_file(tdir / "task.json"),
            "start_sav_sha256": sha256_file(tdir / "start.sav"), "secret_json_sha256": sha256_file(tdir / "secret.json"),
            "briefing_sha256": sha256_text(spec.get("briefing", "")), "horizon_days": spec.get("horizon_days"),
            "limits": spec.get("limits")}


# ----------------------------------------------------------------------------------------- process runner

class Runner:
    """Every process an experiment starts, behind one small interface (tests replace it with a fake)."""

    def __init__(self, plan: Plan, out: Path) -> None:
        self.plan, self.out = plan, Path(out)
        self.arms = {a.name: a for a in plan.arms}

    def _arena(self, cell: Cell, *mode: str) -> list[str]:
        arm = self.arms[cell.arm]
        return [str(arm.railhead), "arena-run", *mode, arm.task_id(cell.task), str(cell.dir(self.out)), cell.model]

    def launch(self, cell: Cell) -> int:
        arm = self.arms[cell.arm]
        with open(cell.dir(self.out) / "launch.log", "ab") as fh:
            return subprocess.run(self._arena(cell), stdout=fh, stderr=subprocess.STDOUT, env=arm_env(arm),
                                  check=False, stdin=subprocess.DEVNULL).returncode

    def resume(self, cell: Cell) -> int:
        arm = self.arms[cell.arm]
        with open(cell.dir(self.out) / "launch.log", "ab") as fh:
            return subprocess.run(self._arena(cell, "--resume"), stdout=fh, stderr=subprocess.STDOUT,
                                  env=arm_env(arm), check=False, stdin=subprocess.DEVNULL).returncode

    def supervise(self, cell: Cell, on_start=None) -> int:
        arm = self.arms[cell.arm]
        extra = list(arm.supervise_args)
        if self.plan.max_resumes is not None and "--max-resumes" not in extra:
            extra += ["--max-resumes", str(self.plan.max_resumes)]
        cmd = self._arena(cell, "--supervise")
        cmd = cmd[:3] + extra + cmd[3:] if extra else cmd
        with open(cell.dir(self.out) / "supervise.log", "ab") as fh:
            proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, env=arm_env(arm),
                                    stdin=subprocess.DEVNULL)
        if on_start:
            on_start(proc.pid)
        return proc.wait()

    def down(self, cell: Cell) -> int:
        arm = self.arms[cell.arm]
        env = arm_env(arm) | {"RAILHEAD_STATE": str(cell.player_dir(self.out) / "state")}
        return _run([arm.railhead, "down"], env=env, timeout=120)[0]

    def verify(self, cell: Cell) -> int:
        arm = self.arms[cell.arm]
        tdir = arm.tasks / arm.task_id(cell.task)
        pdir = cell.player_dir(self.out)
        (pdir / "verify").mkdir(parents=True, exist_ok=True)
        with open(pdir / "verify" / "run.log", "ab") as fh:
            return subprocess.run([str(arm.python), "-I", "-m", "railhead.verify", str(tdir), str(tdir / "start.sav"),
                                   str(pdir / "state" / "run" / "actions.jsonl"), str(pdir / "verify")],
                                  stdout=fh, stderr=subprocess.STDOUT, env=arm_env(arm), check=False,
                                  stdin=subprocess.DEVNULL).returncode

    def export(self, cell: Cell, dest: Path) -> int:
        arm = self.arms[cell.arm]
        tdir = arm.tasks / arm.task_id(cell.task)
        return _run([arm.railhead, "arena", cell.dir(self.out), "--export", dest, "--task", tdir],
                    env=arm_env(arm), timeout=300)[0]

    @staticmethod
    def alive(pid: int | None, needle: str) -> bool:
        """The process runs and its command line contains ``needle`` (a pid can be reused after a reboot)."""
        if not pid:
            return False
        try:
            os.kill(int(pid), 0)
        except (OSError, ValueError):
            return False
        cmdline = subprocess.run(["ps", "-p", str(pid), "-o", "command="], check=False, capture_output=True,
                                 text=True).stdout
        return needle in cmdline


# ---------------------------------------------------------------------------------------------- cell state

def manifest_path(cell: Cell, out: Path) -> Path:
    return cell.dir(out) / "manifest.json"


def read_manifest(cell: Cell, out: Path) -> dict:
    return _read_json(manifest_path(cell, out), {}) or {}


def cell_state(cell: Cell, out: Path) -> str:
    return read_manifest(cell, out).get("state", PENDING)


def _submitted(pdir: Path) -> bool:
    p = pdir / "state" / "run" / "actions.jsonl"
    return p.exists() and '"m":"submit"' in p.read_text(encoding="utf-8", errors="replace")


def _agent_marker(cell: Cell) -> str:
    return "play-script" if cell.vendor == "script" else cell.vendor


def ended_cleanly(vendor: str, agent_log: Path) -> bool:
    """The agent's last run ended by itself (so a dead agent is the model's decision, not an interruption).
    Claude: the last stream-json event is a non-error ``result``. Script: the runner printed its end marker.
    Codex and opencode: not detectable from the transcript, so False (the harness resumes; see docs/AB.md)."""
    p = Path(agent_log)
    if not p.exists():
        return False
    lines = [x for x in p.read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()]
    if vendor == "script":
        return bool(lines) and lines[-1].startswith("=== script finished")
    if vendor == "claude":
        for x in reversed(lines):
            if x.startswith("{"):
                try:
                    e = json.loads(x)
                except json.JSONDecodeError:
                    return False
                return e.get("type") == "result" and not e.get("is_error")
        return False
    return False


def _supervise_result(path: Path, label: str) -> str | None:
    """The final status ("done" | "failed") that ``arena-run --supervise`` printed for this player, if any."""
    if not path.exists():
        return None
    for line in reversed(path.read_text(encoding="utf-8", errors="replace").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if set(d) == {label} and d[label] in ("done", "failed"):
                return d[label]
    return None


class Experiment:
    def __init__(self, plan: Plan, out: Path | None = None, runner: Runner | None = None,
                 prov: Provenance | None = None, poll: float = 5.0) -> None:
        self.plan = plan
        self.out = Path(out or default_out(plan)).resolve()
        self.out.mkdir(parents=True, exist_ok=True)
        setup(self.out / "harness.jsonl")
        self.events = Events(self.out / "events.jsonl")
        self.runner = runner or Runner(plan, self.out)
        self.prov = prov or Provenance(self.out / "arm-code")
        self.poll = poll
        self.mlock = threading.Lock()
        self.sink: dict = {}

    # ---- manifests
    def save(self, cell: Cell, **updates) -> dict:
        with self.mlock:
            m = read_manifest(cell, self.out)
            m.update(updates)
            _write_json(manifest_path(cell, self.out), m)
            return m

    def _static(self, cell: Cell) -> dict:
        arm = next(a for a in self.plan.arms if a.name == cell.arm)
        return {"cell_id": cell.id, "plan_hash": self.plan.hash, "plan_file": str(self.plan.path),
                "experiment": self.plan.name, "arm": cell.arm, "task": cell.task, "arm_task_id": arm.task_id(cell.task),
                "model": cell.model, "vendor": cell.vendor, "label": cell.label, "replicate": cell.rep,
                "billing": self.plan.billing, "harness": self.prov.harness(cell.vendor), "arm_info": self.prov.arm(arm),
                "task_files": task_files(arm, cell.task), "otel_sink": self.sink.get("info"),
                "player_dir": str(cell.player_dir(self.out))}

    # ---- run
    def run_info(self) -> dict:
        path = self.out / "run.json"
        info = _read_json(path, {}) or {}
        if info and info.get("plan_hash") != self.plan.hash:
            raise SystemExit(f"the plan changed since the first run of {self.out} (hash {info['plan_hash'][:12]} -> "
                             f"{self.plan.hash[:12]}). A pre-registered plan must not change: use a new --out.")
        if not info:
            seed = self.plan.seed if self.plan.seed is not None else random.SystemRandom().randrange(2**31)
            info = {"plan_hash": self.plan.hash, "plan_file": str(self.plan.path), "seed": seed,
                    "first_run_at": now_iso(), "hypothesis": self.plan.hypothesis,
                    "primary_metric": self.plan.primary_metric,
                    "order": [c.id for c in run_order(cells(self.plan), seed)]}
            snap = self.out / f"plan.{self.plan.hash[:12]}.json"
            if not snap.exists():
                _write_json(snap, self.plan.raw)
            _write_json(path, info)
        return info

    def check_arms(self) -> None:
        infos = {a.name: self.prov.arm(a) for a in self.plan.arms}
        bad = [f"{n}: {i.get('railhead_file') or i.get('error')}" for n, i in infos.items() if not i.get("own_code")]
        if bad:
            raise SystemExit("an arm does not run its own code (its .venv imports railhead from elsewhere): "
                             + "; ".join(bad))

    def run(self, retry_failed: bool = False) -> dict[str, str]:
        info = self.run_info()
        self.check_arms()
        by_id = {c.id: c for c in cells(self.plan)}
        order = [by_id[i] for i in info["order"]]
        todo = []
        for c in order:
            st = cell_state(c, self.out)
            if st == FINISHED or (st == FAILED and not retry_failed):
                if st == FAILED:
                    self.events.emit("skipped", c, reason="failed_infra (use --retry-failed)")
                continue
            todo.append(c)
        todo.sort(key=lambda c: cell_state(c, self.out) != RUNNING)  # interrupted cells first, order kept
        self.events.emit("run_started", plan_hash=self.plan.hash, seed=info["seed"], todo=len(todo),
                         parallel=self.plan.parallel, pid=os.getpid())
        self.start_sink()
        results: dict[str, str] = {}
        try:
            with ThreadPoolExecutor(max_workers=self.plan.parallel) as ex:
                active: dict = {}
                while todo or active:
                    keys = {c.key for c in active.values()}
                    for c in list(todo):
                        if len(active) >= self.plan.parallel:
                            break
                        if c.key in keys:
                            continue
                        todo.remove(c)
                        keys.add(c.key)
                        active[ex.submit(self.run_cell, c, retry_failed)] = c
                    done, _ = wait(list(active), return_when=FIRST_COMPLETED)
                    for f in done:
                        c = active.pop(f)
                        try:
                            results[c.id] = f.result()
                        except Exception as e:  # noqa: BLE001 - one broken cell must not stop the others
                            results[c.id] = FAILED
                            self.save(c, state=FAILED, exit_state="harness_error", error=f"{type(e).__name__}: {e}",
                                      ended_at=now_iso())
                            self.events.emit("failed", c, reason=f"harness error: {type(e).__name__}: {e}")
                            if not self.runner.alive(_read_int(c.player_dir(self.out) / "agent.pid"),
                                                     _agent_marker(c)):
                                self.runner.down(c)  # no player left: stop its daemon (a live one is resumable)
        finally:
            self.stop_sink()
            self.events.emit("run_ended", results=results)
        return results

    def run_cell(self, cell: Cell, retry_failed: bool = False) -> str:
        cdir, pdir = cell.dir(self.out), cell.player_dir(self.out)
        cdir.mkdir(parents=True, exist_ok=True)
        m = read_manifest(cell, self.out)
        state = m.get("state", PENDING)
        now = time.time()
        windows = m.get("windows") or []
        if state == FAILED:
            self.events.emit("retry", cell, previous=m.get("exit_state"))
        static = self._static(cell)
        if not (pdir / "agent.pid").exists():
            m = self.save(cell, **static, state=RUNNING, started_at=m.get("started_at") or now_iso(now),
                          resume_count=m.get("resume_count", 0), windows=windows + [[now, None]])
            self.events.emit("started", cell, dir=str(cdir))
            rc = self.runner.launch(cell)
            if rc != 0 or not (pdir / "agent.pid").exists():
                return self._fail(cell, f"launch exited {rc}")
        else:
            m = self.save(cell, **static, state=RUNNING, windows=windows + [[now, None]])
            sup = m.get("supervisor_pid")
            if self.runner.alive(sup, "--supervise"):
                self.events.emit("attached", cell, supervisor_pid=sup)
                while self.runner.alive(sup, "--supervise"):
                    time.sleep(self.poll)
                return self._finish(cell, None)
            agent_pid = _read_int(pdir / "agent.pid")
            if self.runner.alive(agent_pid, _agent_marker(cell)):
                self.events.emit("attached", cell, agent_pid=agent_pid)
            elif _submitted(pdir) or ended_cleanly(cell.vendor, pdir / "agent.log"):
                self.events.emit("attached", cell, note="agent already ended")
            else:
                n = int(m.get("resume_count", 0)) + 1
                self.save(cell, resume_count=n)
                self.events.emit("resumed", cell, resume_count=n, reason="interrupted (agent not running)")
                rc = self.runner.resume(cell)
                if rc != 0:
                    return self._fail(cell, f"resume exited {rc}")
        rc = self.runner.supervise(cell, on_start=lambda pid: self.save(cell, supervisor_pid=pid))
        return self._finish(cell, rc)

    def _close_window(self, cell: Cell) -> list:
        w = read_manifest(cell, self.out).get("windows") or []
        if w and w[-1][1] is None:
            w[-1][1] = time.time()
        return w

    def _finish(self, cell: Cell, rc: int | None) -> str:
        status = _supervise_result(cell.dir(self.out) / "supervise.log", cell.label)
        self.runner.down(cell)
        resumes = _count_supervisor_resumes(cell.dir(self.out) / "supervise.log", cell.label)
        if status == "done":
            self.save(cell, state=FINISHED, exit_state="submitted" if _submitted(cell.player_dir(self.out)) else
                      "agent_ended", ended_at=now_iso(), windows=self._close_window(cell), supervisor_rc=rc,
                      provider_resumes=resumes)
            self.events.emit("finished", cell, submitted=_submitted(cell.player_dir(self.out)),
                             provider_resumes=resumes)
            return FINISHED
        why = "provider errors exceeded max resumes" if status == "failed" else f"supervisor exited {rc} without a status"
        return self._fail(cell, why, provider_resumes=resumes, supervisor_rc=rc)

    def _fail(self, cell: Cell, why: str, **extra) -> str:
        self.runner.down(cell)
        self.save(cell, state=FAILED, exit_state=why, ended_at=now_iso(), windows=self._close_window(cell), **extra)
        self.events.emit("failed", cell, reason=why)
        return FAILED

    # ---- OTLP sink
    def start_sink(self) -> None:
        d = self.out / "otel"
        rec = _read_json(d / "sink.json", {}) or {}
        if _port_open(OTEL_PORT):
            if rec.get("pid") and self.runner.alive(rec["pid"], "otel serve") and rec.get("dir") == str(d):
                self.sink = {"info": {"started_by": "ab (earlier run)", "dir": str(d), "pid": rec["pid"],
                                      "port": OTEL_PORT}, "pid": rec["pid"]}
            else:
                self.sink = {"info": {"started_by": "external", "port": OTEL_PORT,
                                      "dir": str(self.plan.otel_dir) if self.plan.otel_dir else None,
                                      "note": "port in use by a sink this experiment did not start"}}
            self.events.emit("sink", **self.sink["info"])
            return
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "serve.log", "ab") as fh:
            proc = subprocess.Popen([sys.executable, "-m", "railhead.admin_cli", "otel", "serve", "--port",
                                     str(OTEL_PORT), "--dir", str(d)], stdout=fh, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
        for _ in range(100):
            if _port_open(OTEL_PORT) or proc.poll() is not None:
                break
            time.sleep(0.1)
        _write_json(d / "sink.json", {"pid": proc.pid, "dir": str(d), "port": OTEL_PORT, "started_at": now_iso()})
        self.sink = {"info": {"started_by": "ab", "dir": str(d), "pid": proc.pid, "port": OTEL_PORT}, "proc": proc,
                     "pid": proc.pid}
        self.events.emit("sink_started", **self.sink["info"])

    def stop_sink(self) -> None:
        """Stop the sink only if this experiment started it."""
        pid = self.sink.get("pid")
        if not pid or self.sink["info"].get("started_by") == "external":
            return
        proc = self.sink.get("proc")
        try:
            if proc is not None:
                proc.terminate()
                proc.wait(timeout=10)
            elif self.runner.alive(pid, "otel serve"):
                os.kill(pid, 15)
        except (OSError, subprocess.TimeoutExpired) as e:
            self.events.emit("sink_stop_failed", pid=pid, error=str(e))
            return
        self.events.emit("sink_stopped", pid=pid)

    # ---- status
    def status(self) -> list[dict]:
        rows = []
        for c in cells(self.plan):
            m = read_manifest(c, self.out)
            pdir = c.player_dir(self.out)
            v = _read_json(pdir / "verify" / "reward.json")
            rows.append({"cell": c.id, "state": m.get("state", PENDING), "exit": m.get("exit_state"),
                         "resumes": m.get("resume_count", 0), "provider_resumes": m.get("provider_resumes"),
                         "started": m.get("started_at"), "ended": m.get("ended_at"), "submitted": _submitted(pdir),
                         "verified": None if v is None else ("valid" if v.get("valid") == 1 and "net_worth" in v
                                                              else "invalid"),
                         "net_worth": (v or {}).get("net_worth")})
        return rows

    # ---- collect
    def otel_dir(self) -> Path | None:
        if self.plan.otel_dir:
            return self.plan.otel_dir
        d = self.out / "otel"
        return d if (d / "logs.jsonl").exists() else None

    def collect(self) -> dict:
        """Verify every finished game with its arm's verifier, then write per-arm exports (arena row shape)."""
        from railhead import behaviour

        done = [c for c in cells(self.plan) if cell_state(c, self.out) == FINISHED]

        def verify_one(c: Cell) -> None:
            verdict = c.player_dir(self.out) / "verify" / "reward.json"
            if verdict.exists():
                old = _read_json(verdict, {}) or {}
                if not old.get("error"):
                    return
                # the verifier crashed (an infrastructure failure, not a verdict on the game): keep it, verify again
                verdict.rename(verdict.with_name(f"reward.error-{int(time.time())}.json"))
                self.events.emit("reverify", c, reason="previous verdict has error=1")
            t0 = time.time()
            rc = self.runner.verify(c)
            v = _read_json(c.player_dir(self.out) / "verify" / "reward.json", {}) or {}
            self.save(c, verified_at=now_iso(), verify_rc=rc)
            self.events.emit("verified", c, rc=rc, seconds=round(time.time() - t0, 1),
                             **{k: v.get(k) for k in ("valid", "net_worth", "reward", "tamper", "rejected", "error")})

        with ThreadPoolExecutor(max_workers=self.plan.parallel) as ex:
            list(ex.map(verify_one, done))
        arms = {a.name: a for a in self.plan.arms}
        exports: dict[str, dict] = {}
        for c in done:
            arm = arms[c.arm]
            m = read_manifest(c, self.out)
            dest = c.dir(self.out) / "export.json"
            tdir = arm.tasks / arm.task_id(c.task)
            rc = self.runner.export(c, dest)
            doc = _read_json(dest) if rc == 0 else None
            export_by = "arm"
            if not doc:  # the arm's `arena --export` needs a scored verdict; a rejected game still gets a row
                from railhead.stats import collect_map

                doc = collect_map(c.dir(self.out), tdir)
                export_by = "harness (arm export failed)"
                self.events.emit("export_fallback", c, rc=rc)
            ws = m.get("windows") or []
            span = [[ws[0][0], ws[-1][1]]] if ws and ws[-1][1] else None  # first start to last end of the cell
            b = behaviour.analyse(c.player_dir(self.out), tdir, self.otel_dir(), arm.task_id(c.task), windows=span,
                                  player=c.label)
            _write_json(c.dir(self.out) / "behaviour.json", b)
            extra = {"arm": c.arm, "model": c.model, "vendor": c.vendor, "replicate": c.rep, "cell": c.id,
                     "billing": self.plan.billing, "plan_hash": self.plan.hash, "end": b["end"], "export_by": export_by,
                     "days_played": b["days_played"], "submitted": b["submitted"],
                     "manifest_sha256": sha256_file(manifest_path(c, self.out)), "behaviour": behaviour.scalars(b),
                     "first_vehicle_mode": b["first_vehicle_mode"],
                     "first_profitable_route_mode": b["first_profitable_route_mode"],
                     "rail_status": b["rail"]["status"], "water_status": b["water"]["status"]}
            ex_doc = exports.setdefault(c.arm, {"arm": c.arm, "checkout": str(arm.checkout), "plan_hash": self.plan.hash,
                                                "billing": self.plan.billing, "maps": {}})
            mdoc = ex_doc["maps"].setdefault(c.task, {"task": c.task, "arm_task_id": arm.task_id(c.task),
                                                      "results": [], "curves": {}, "fleet_mix": {}})
            key = c.label if self.plan.replicates == 1 else f"{c.label}/r{c.rep}"
            for r in doc.get("results", []):
                if r.get("status") == "certificate":
                    if not any(x.get("player") == r["player"] for x in mdoc["results"]):
                        mdoc["results"].append(r)
                    continue
                mdoc["results"].append({**r, **extra})
            for part in ("curves", "fleet_mix"):  # one player per cell: re-key it by label (and replicate)
                vals = list((doc.get(part) or {}).values())
                if vals:
                    mdoc[part][key] = vals[0]
        for name, ex_doc in exports.items():
            ex_doc["maps"] = [ex_doc["maps"][t] for t in self.plan.tasks if t in ex_doc["maps"]]
            _write_json(self.out / "exports" / f"{name}.json", ex_doc)
        self.events.emit("collected", cells=len(done), arms=sorted(exports))
        return {"verified_cells": len(done), "exports": {n: str(self.out / "exports" / f"{n}.json") for n in exports}}


def _read_int(path: Path) -> int | None:
    try:
        return int(Path(path).read_text().strip())
    except (OSError, ValueError):
        return None


def _count_supervisor_resumes(path: Path, label: str) -> int:
    n = 0
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("{") and f'"player": "{label}"' in line and '"resume"' in line:
                n += 1
    return n


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


# ------------------------------------------------------------------------------------------------- report

def _metric(row: dict, name: str):
    if name in row and name not in ("behaviour",):
        return row.get(name)
    return (row.get("behaviour") or {}).get(name)


def _included(row: dict) -> str | None:
    if row.get("status") != "verified":
        return f"status={row.get('status')}"
    if row.get("valid", 1) != 1:
        return "valid=0"
    return None


def cell_table(exports: dict[str, dict], metrics: list[str]) -> tuple[dict, list[dict]]:
    """arm -> (task, label) -> metric -> mean over replicates (verified, valid games only); and exclusions."""
    table: dict[str, dict] = {}
    excluded: list[dict] = []
    for arm, doc in exports.items():
        acc: dict[tuple, list[dict]] = {}
        for mdoc in doc.get("maps", []):
            for r in mdoc.get("results", []):
                if r.get("status") == "certificate":
                    continue
                why = _included(r)
                if why:
                    excluded.append({"arm": arm, "task": mdoc["task"], "player": r.get("player"),
                                     "replicate": r.get("replicate"), "reason": why})
                    continue
                acc.setdefault((mdoc["task"], r["player"]), []).append(r)
        for key, rows in acc.items():
            cell = {"games": len(rows)}
            for mname in metrics:
                vals = [float(v) for v in (_metric(r, mname) for r in rows) if v is not None]
                cell[mname] = statistics.fmean(vals) if vals else None
            table.setdefault(arm, {})[key] = cell
    return table, excluded


def compare(a_cells: dict, b_cells: dict, metric: str, boot: int, rng) -> dict:
    """b - a on the (task, model) units both arms have. Pooled: one difference per task (the mean over its
    models), bootstrap over tasks, sign test over tasks. Also per model."""
    from railhead.stats import bootstrap, sign_test

    units = sorted(k for k in a_cells if k in b_cells and a_cells[k].get(metric) is not None
                   and b_cells[k].get(metric) is not None)

    def summarise(keys_by_task: dict[str, list[tuple]]) -> dict:
        if not keys_by_task:
            return {"n": 0, "n_units": 0, "mean_a": None, "mean_b": None, "diff": None, "lo": None, "hi": None,
                    "tied": None, "sign_p": None, "n_nonzero": 0, "wins_b": 0, "wins_a": 0, "tasks": []}
        diffs, avals, bvals = [], [], []
        for t, keys in sorted(keys_by_task.items()):
            diffs.append(statistics.fmean(b_cells[k][metric] - a_cells[k][metric] for k in keys))
            avals += [a_cells[k][metric] for k in keys]
            bvals += [b_cells[k][metric] for k in keys]
        lo, hi = bootstrap(diffs, boot, rng)
        p, nz = sign_test(diffs)
        return {"n": len(diffs), "n_units": len(avals), "mean_a": statistics.fmean(avals),
                "mean_b": statistics.fmean(bvals), "diff": statistics.fmean(diffs), "lo": lo, "hi": hi,
                "tied": bool(lo <= 0 <= hi), "sign_p": p, "n_nonzero": nz, "wins_b": sum(d > 0 for d in diffs),
                "wins_a": sum(d < 0 for d in diffs), "tasks": sorted(keys_by_task), "task_diffs": diffs}

    pooled: dict[str, list] = {}
    per_model: dict[str, dict[str, list]] = {}
    for k in units:
        pooled.setdefault(k[0], []).append(k)
        per_model.setdefault(k[1], {}).setdefault(k[0], []).append(k)
    return {"metric": metric, "pooled": summarise(pooled),
            "per_model": {m: summarise(v) for m, v in sorted(per_model.items())}}


def small_n_note(n: int) -> str:
    if n == 0:
        return "No paired task: nothing can be compared."
    min_p = min(1.0, 2 / 2 ** n)
    distinct = __import__("math").comb(2 * n - 1, n)
    if n < SMALL_N:
        return (f"n = {n} task(s) is too small to conclude anything: the smallest possible two-sided sign-test "
                f"p is {min_p:.3g} (p < 0.05 needs at least {SMALL_N} tasks with a non-zero difference), and the "
                f"bootstrap over {n} task(s) has only {distinct} distinct resamples. Read the numbers as a "
                f"description of these games, not as evidence of an effect.")
    return f"n = {n} tasks: the smallest possible two-sided sign-test p is {min_p:.3g}."


def build_report(plan: Plan, out: Path, boot: int = 10_000, seed: int = 0) -> dict:
    import numpy as np

    from railhead.behaviour import SCALAR_DEFINITIONS

    exports = {a.name: _read_json(Path(out) / "exports" / f"{a.name}.json") for a in plan.arms}
    missing = [n for n, d in exports.items() if d is None]
    exports = {n: d for n, d in exports.items() if d}
    first = [plan.primary_metric, *plan.secondary]
    metrics = first + [m for m in (*BASE_METRICS, *SCALAR_DEFINITIONS) if m not in first]
    table, excluded = cell_table(exports, metrics)
    categorical = categorical_counts(exports)
    info = _read_json(Path(out) / "run.json", {}) or {}
    base = plan.arms[0].name
    rng = np.random.default_rng(seed)
    comparisons = []
    for arm in plan.arms[1:]:
        if base not in table or arm.name not in table:
            continue
        comparisons.append({"a": base, "b": arm.name,
                            "metrics": [compare(table[base], table[arm.name], m, boot, rng) for m in metrics]})
    # provenance consistency: task files and arm code per arm, from the manifests
    task_hashes: dict[str, dict[str, set]] = {}
    arm_code: dict[str, dict] = {}
    harness: dict[str, set] = {}
    for c in cells(plan):
        m = read_manifest(c, out)
        if not m:
            continue
        tf = m.get("task_files") or {}
        task_hashes.setdefault(c.task, {}).setdefault(c.arm, set()).add(
            (tf.get("task_json_sha256"), tf.get("start_sav_sha256")))
        ai = m.get("arm_info") or {}
        code = {"commit": (ai.get("git") or {}).get("commit"), "dirty": (ai.get("git") or {}).get("dirty"),
                "diff_sha256": (ai.get("git") or {}).get("diff_sha256"), "engine_sha256": ai.get("engine_sha256"),
                "prompt_sha256": ai.get("prompt_sha256")}
        versions = arm_code.setdefault(c.arm, [])
        if code not in versions:
            versions.append(code)
        h = m.get("harness") or {}
        harness.setdefault(c.vendor, set()).add(h.get("version"))
    task_warnings = []
    for t, by_arm in sorted(task_hashes.items()):
        sets = {a: s for a, s in by_arm.items()}
        if len({frozenset(s) for s in sets.values()}) > 1 or any(len(s) > 1 for s in sets.values()):
            task_warnings.append(f"task {t}: task.json/start.sav differ between arms or between cells "
                                 f"({', '.join(sorted(sets))}); the maps may not be the same")
    for arm, versions in arm_code.items():
        if len(versions) > 1:
            task_warnings.append(f"arm {arm}: its code changed during the run ({len(versions)} different commit/diff/"
                                 "engine/prompt states in its manifests); see <out>/arm-code/ for the patches")
    flat = [json.dumps(v, sort_keys=True) for vs in arm_code.values() for v in vs]
    same_code = len(set(flat)) == 1 and len(arm_code) > 1
    return {"experiment": plan.name, "plan_file": str(plan.path), "plan_hash": plan.hash,
            "hypothesis": plan.hypothesis, "primary_metric": plan.primary_metric,
            "notes": plan.raw.get("notes"), "billing": plan.billing,
            "preregistration": {"first_run_at": info.get("first_run_at"), "plan_hash_at_first_run": info.get("plan_hash"),
                                "unchanged": info.get("plan_hash") == plan.hash if info else None},
            "seed": info.get("seed"), "boot": boot, "boot_seed": seed, "baseline_arm": base,
            "arms": [a.name for a in plan.arms], "missing_exports": missing, "arm_code": arm_code,
            "arms_run_identical_code": same_code,
            "harness_versions": {k: sorted(str(x) for x in v) for k, v in harness.items()},
            "task_warnings": task_warnings, "metric_definitions": {m: SCALAR_DEFINITIONS.get(m, {
                "net_worth": "verified net worth (cash - loan) after the run-off",
                "net_worth_horizon": "verified net worth at the end of the mandate",
                "score_k50": "arena score: tycoon_log(net worth, w_idle, w_base, k=50), the task reward",
                "score_k1000": "tycoon_log with k=1000 (separates strong games)"}.get(m)) for m in metrics},
            "secondary_metrics": plan.secondary, "categorical": categorical,
            "cells": {arm: {f"{t}|{p}": v for (t, p), v in rows.items()} for arm, rows in table.items()},
            "comparisons": comparisons, "excluded": excluded}


CATEGORICAL = ("first_vehicle_mode", "first_profitable_route_mode", "rail_status", "water_status", "end")


def categorical_counts(exports: dict[str, dict]) -> dict[str, dict[str, dict[str, int]]]:
    """metric -> arm -> value -> number of games (every finished game, verified or not)."""
    out: dict[str, dict[str, dict[str, int]]] = {k: {} for k in CATEGORICAL}
    for arm, doc in exports.items():
        for mdoc in doc.get("maps", []):
            for r in mdoc.get("results", []):
                if r.get("status") == "certificate":
                    continue
                for k in CATEGORICAL:
                    v = str(r.get(k))
                    out[k].setdefault(arm, {})[v] = out[k].setdefault(arm, {}).get(v, 0) + 1
    return out


def _fmt(x, metric: str = "") -> str:
    if x is None:
        return "-"
    if metric.startswith(("net_worth", "vehicle_spend_total", "spend_total", "max_loan", "final_loan")):
        return f"{x / 1e6:,.3f}M"
    if isinstance(x, float) and abs(x) < 10 and x != int(x):
        return f"{x:.3f}"
    return f"{x:,.1f}" if isinstance(x, float) and x != int(x) else f"{int(x):,}"


def render_markdown(rep: dict) -> str:
    L = [f"# A/B report: {rep['experiment']}", "",
         "## Pre-registration (written before the run)", "",
         f"- Hypothesis: {rep['hypothesis']}",
         f"- Primary metric: `{rep['primary_metric']}` ({rep['metric_definitions'].get(rep['primary_metric'])})",
         "- Secondary metrics: " + (", ".join(f"`{m}`" for m in rep.get("secondary_metrics") or []) or "none")]
    pr = rep["preregistration"]
    if pr["unchanged"] is None:
        L.append("- The experiment has no run record (run.json): nothing ran yet.")
    else:
        L.append(f"- Plan hash `{rep['plan_hash'][:12]}`; first run {pr['first_run_at']}; plan "
                 + ("unchanged since then." if pr["unchanged"] else "CHANGED after the first run: not pre-registered."))
    if rep.get("notes"):
        L.append(f"- Notes: {rep['notes']}")
    L += ["", (f"Arms: {', '.join(rep['arms'])} (baseline `{rep['baseline_arm']}`; differences are arm - baseline). "
               f"Billing: {rep['billing']}. Run order seed: {rep['seed']}. Bootstrap: B={rep['boot']}, "
               f"seed {rep['boot_seed']}, resampling tasks.")]
    if rep["arms_run_identical_code"]:
        L.append("All arms ran identical code, engine and prompt (an A/A check): every difference must be zero.")
    for w in rep["task_warnings"]:
        L.append(f"WARNING: {w}")
    if rep["missing_exports"]:
        L.append(f"WARNING: no export for arm(s) {', '.join(rep['missing_exports'])}: run `railhead ab collect` first.")
    for cmp_ in rep["comparisons"]:
        prim = cmp_["metrics"][0]["pooled"]
        L += ["", f"## {cmp_['b']} - {cmp_['a']}", "", small_n_note(prim["n"]), ""]
        game = [m for m in cmp_["metrics"] if m["pooled"]["n"] and not m["metric"].startswith("model_")]
        if game and all(m["pooled"]["diff"] == 0 and m["pooled"]["lo"] == 0 == m["pooled"]["hi"] for m in game):
            L += [("The arms are TIED on every game metric: every paired difference is exactly zero "
                   "(model_* metrics, such as wall-clock time and tokens, vary between identical runs)."), ""]
        L += ["| metric | n tasks | mean " + cmp_["a"] + " | mean " + cmp_["b"] + " | diff | 95% CI | sign p | "
              "tasks b>a / b<a | tied |", "|---|---|---|---|---|---|---|---|---|"]
        for i, m in enumerate(cmp_["metrics"]):
            p = m["pooled"]
            if not p["n"]:
                continue
            name = f"**{m['metric']}** (primary)" if i == 0 else m["metric"]
            sp = "-" if p["sign_p"] is None else f"{p['sign_p']:.3f}"
            L.append(f"| {name} | {p['n']} | {_fmt(p['mean_a'], m['metric'])} | {_fmt(p['mean_b'], m['metric'])} | "
                     f"{_fmt(p['diff'], m['metric'])} | [{_fmt(p['lo'], m['metric'])}, {_fmt(p['hi'], m['metric'])}] | "
                     f"{sp} | {p['wins_b']} / {p['wins_a']} | {'yes' if p['tied'] else 'no'} |")
        prim_m = cmp_["metrics"][0]
        if prim_m["per_model"]:
            L += ["", f"Primary metric per model (`{prim_m['metric']}`):", "",
                  "| model | n tasks | mean a | mean b | diff | 95% CI | tied |", "|---|---|---|---|---|---|---|"]
            for model, p in prim_m["per_model"].items():
                L.append(f"| {model} | {p['n']} | {_fmt(p['mean_a'])} | {_fmt(p['mean_b'])} | {_fmt(p['diff'])} | "
                         f"[{_fmt(p['lo'])}, {_fmt(p['hi'])}] | {'yes' if p['tied'] else 'no'} |")
    if any(rep["categorical"].values()):
        L += ["", "## Categorical behaviour (number of finished games)", "", "| metric | arm | values |", "|---|---|---|"]
        for k, by_arm in rep["categorical"].items():
            for arm, counts in sorted(by_arm.items()):
                L.append(f"| {k} | {arm} | " + ", ".join(f"{v}: {n}" for v, n in sorted(counts.items())) + " |")
    L += ["", "## Cells (mean over replicates; verified and valid games only)", ""]
    for arm, rows in rep["cells"].items():
        for key, v in sorted(rows.items()):
            L.append(f"- {arm} {key}: games {v['games']}, {rep['primary_metric']} "
                     f"{_fmt(v.get(rep['primary_metric']), rep['primary_metric'])}, net worth "
                     f"{_fmt(v.get('net_worth'), 'net_worth')}, score {_fmt(v.get('score_k50'))}")
    if rep["excluded"]:
        L += ["", "Excluded games: " + "; ".join(f"{x['arm']} {x['task']} {x['player']} r{x['replicate']}: "
                                                   f"{x['reason']}" for x in rep["excluded"])]
    L += ["", "## Provenance", ""]
    for arm, versions in rep["arm_code"].items():
        for c in versions:
            L.append(f"- {arm}: commit {str(c['commit'])[:10]}, dirty {c['dirty']}, diff {str(c['diff_sha256'])[:12]}, "
                     f"engine {str(c['engine_sha256'])[:12]}, prompt {str(c['prompt_sha256'])[:12]}")
    L.append(f"- harness versions: {rep['harness_versions']}")
    L += ["", "## Metric definitions", ""] + [f"- `{k}`: {v}" for k, v in rep["metric_definitions"].items()]
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------------------------------- CLI

def _opt(argv: list[str], flag: str, default=None):
    return argv[argv.index(flag) + 1] if flag in argv and argv.index(flag) + 1 < len(argv) else default


USAGE = __doc__


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0 if argv else 2
    sub, rest = argv[0], argv[1:]
    if sub == "behaviour":
        from railhead.behaviour import main as behaviour_main

        return behaviour_main(rest)
    if sub == "plan" and "--metrics" in rest:
        from railhead.behaviour import SCALAR_DEFINITIONS

        print("\n".join([*BASE_METRICS, *SCALAR_DEFINITIONS]))
        return 0
    if not rest or rest[0].startswith("--"):
        print(USAGE, file=sys.stderr)
        return 2
    try:
        plan = load_plan(Path(rest[0]))
    except (ValueError, OSError) as e:
        print(f"railhead ab: {e}", file=sys.stderr)
        return 2
    out = Path(_opt(rest, "--out")) if "--out" in rest else default_out(plan)
    if sub == "plan":
        seed = plan.seed if plan.seed is not None else 0
        order = run_order(cells(plan), seed)
        runner, prov = Runner(plan, out), Provenance()
        arms = {}
        for a in plan.arms:  # pre-flight: each arm runs its own code; commit, engine and prompt it will record
            i = prov.arm(a)
            arms[a.name] = {"checkout": str(a.checkout), "own_code": i.get("own_code"),
                            "commit": (i.get("git") or {}).get("commit"), "dirty": (i.get("git") or {}).get("dirty"),
                            "engine": i.get("engine"), "prompt_sha256": i.get("prompt_sha256"),
                            "task_sha256": {t: task_files(a, t)["start_sav_sha256"] for t in plan.tasks}}
        print(json.dumps({"plan_hash": plan.hash, "experiment": plan.name, "hypothesis": plan.hypothesis,
                          "primary_metric": plan.primary_metric, "arms": arms,
                          "harness": {v: prov.harness(v) for v in sorted({m.split(':')[0] for m in plan.models})},
                          "otlp_port_in_use": _port_open(OTEL_PORT),  # True: set otel_dir, or the data goes elsewhere
                          "cells": len(order), "parallel": plan.parallel, "out": str(out),
                          "order_seed": plan.seed if plan.seed is not None else "chosen at the first run",
                          "order": [c.id for c in order],
                          "first_commands": [runner._arena(c) for c in order[:2]]}, indent=1))
        return 0 if all(x["own_code"] for x in arms.values()) else 1
    if sub in ("status", "report", "collect") and not (out / "run.json").exists():
        print(f"railhead ab {sub}: {out} has no run yet (run `railhead ab run` first)", file=sys.stderr)
        return 1
    exp = Experiment(plan, out)
    if sub == "run":
        res = exp.run(retry_failed="--retry-failed" in rest)
        print(json.dumps(res, indent=1))
        return 0 if all(v == FINISHED for v in res.values()) else 1
    if sub == "status":
        rows = exp.status()
        if "--json" in rest:
            print(json.dumps(rows, indent=1))
        else:
            for r in rows:
                print(f"{r['cell']:<60} {r['state']:<13} resumes {r['resumes']}  submitted {r['submitted']!s:<5} "
                      f"verified {r['verified'] or '-':<8} {_fmt(r['net_worth'], 'net_worth')}")
            counts: dict[str, int] = {}
            for r in rows:
                counts[r["state"]] = counts.get(r["state"], 0) + 1
            print(json.dumps(counts))
        return 0
    if sub == "collect":
        print(json.dumps(exp.collect(), indent=1))
        return 0
    if sub == "report":
        rep = build_report(plan, exp.out, boot=int(_opt(rest, "--boot", 10_000)), seed=int(_opt(rest, "--seed", 0)))
        md = render_markdown(rep)
        _write_json(exp.out / "report.json", rep)
        (exp.out / "report.md").write_text(md)
        print(md)
        return 0
    print(USAGE, file=sys.stderr)
    return 2
