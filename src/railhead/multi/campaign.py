"""Waybill campaign: all games of a schedule, several at a time, with each game's Claude seats on one of a
pool of Claude logins (docs/WAYBILL.md, section 14).

    railhead waybill-campaign plan <campaign.json>     the games, the accounts and the projected spend (no change)
    railhead waybill-campaign run <campaign.json>      start, adopt and resume games until all are done or a cap stops it
    railhead waybill-campaign status <campaign.json> [--usage]   the state; --usage also asks each account's usage
    python -m railhead.multi.campaign ...              the same commands

The campaign file (JSON; relative paths are relative to the file)::

    {"schedule": "docs/data/waybill-schedule-short.json",  "tasks_dir": "...",  "runs_dir": "...",
     "concurrency": 3,  "usd_cap": 900,  "reserve_usd": 20,
     "accounts": [{"name": "main", "profile": "default", "usd_cap": 250},
                  {"name": "second", "profile": "~/.claude-second", "usd_cap": 400, "max_games": 1}],
     "runner": ["--all-slots", "--turn-s", "300", "--max-turns", "200", "--grace-s", "180", "--traces", "off",
                "--usd-cap", "60", "--run-s", "9000"],
     "arms": ["enforced", "cheap_talk"],  "games": null,  "horizon_days": null,  "players": {},
     "stagger_s": 30,  "poll_s": 10,  "max_restarts": 3,  "max_replacements": 10,  "game_usd_estimate": 14.4}

``games`` limits the schedule to its first N seeds; ``horizon_days`` makes short smoke tasks (task ids end in
``-h<days>``); ``players`` replaces roster entries (``{"claude:claude-opus-5-5": "stub:/path"}``, for tests).

The state is ``<runs_dir>/campaign/state.json``: per game the account name, the code commit, the spend and
the exclusion reasons. The account of a game is never written into the run's result.json (the runner keeps
the login directory in run.json only); the state file has account names, never e-mail addresses or tokens.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from railhead.multi import arena
from railhead.multi.analysis import exclusion_reasons, load_run
from railhead.multi.gen import ARMS, SeedRejected, TaskPlan

STATE_V = 1
TERMINAL = ("done", "excluded", "replaced", "dropped", "failed")
DEFAULTS = {"concurrency": 1, "reserve_usd": 20.0, "stagger_s": 30.0, "poll_s": 10.0, "max_restarts": 3,
            "max_replacements": 10, "runner": [], "players": {}, "arms": list(ARMS), "games": None,
            "horizon_days": None, "usd_cap": None, "state": None, "game_usd_estimate": 14.4}
SEED_TRIES = 50  # a replacement seed is searched at most this far past the last seed


# ------------------------------------------------------------------------------------------- configuration
@dataclass
class Account:
    name: str
    profile: str | None  # normalised: "default" or a directory
    usd_cap: float
    max_games: int = 1  # games at the same time: one game of 2 Claude seats uses about $18 per hour


def load_config(path: Path) -> dict:
    path = Path(path).resolve()
    cfg = DEFAULTS | json.loads(path.read_text())
    base = path.parent

    def rel(p: str | None) -> Path | None:
        return None if p is None else (base / os.path.expanduser(p)).resolve()

    for k in ("schedule", "tasks_dir", "runs_dir"):
        if not cfg.get(k):
            raise ValueError(f"{path.name}: {k} is missing")
        cfg[k] = rel(cfg[k])
    cfg["state"] = rel(cfg["state"]) or cfg["runs_dir"] / "campaign" / "state.json"
    accts = []
    for a in cfg.get("accounts") or []:
        if not a.get("name") or a.get("usd_cap") is None:
            raise ValueError(f"{path.name}: each account needs a name and a usd_cap")
        accts.append(Account(str(a["name"]), arena.normalise_claude_profile(a.get("profile") or "default"),
                             float(a["usd_cap"]), int(a.get("max_games", 1))))
    if not accts:
        raise ValueError(f"{path.name}: no accounts")
    if len({a.name for a in accts}) != len(accts) or len({a.profile for a in accts}) != len(accts):
        raise ValueError(f"{path.name}: two accounts with the same name or profile")
    cfg["accounts"] = accts
    for a in cfg["arms"]:
        if a not in ARMS:
            raise ValueError(f"unknown arm {a!r}")
    for flag in ("--claude-profile", "--sandbox-deny"):
        if flag in cfg["runner"]:
            raise ValueError(f"runner: {flag} is set by the campaign")
    cfg["schedule_doc"] = json.loads(cfg["schedule"].read_text())
    return cfg


def players(cfg: dict, game: dict) -> list[str]:
    """The player specs of a schedule game in seat order (seat s takes company slot s; the task's rotation
    gives slot s the region type (s + rotation) mod N)."""
    seats = sorted(game["seats"], key=lambda s: int(s["seat"]))
    return [cfg["players"].get(s["player"], s["player"]) for s in seats]


# ------------------------------------------------------------------------------------------- state
def _read(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return default


def _write(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=str) + "\n")
    os.replace(tmp, path)


def new_state(cfg: dict) -> dict:
    doc = cfg["schedule_doc"]
    games = doc["games"][: cfg["games"]] if cfg["games"] else doc["games"]
    return {"v": STATE_V, "track": doc["track"],
            "pairs": [{"seed": int(g["seed"]), "rotation": int(g["rotation"]), "replaces": None, "status": "active"}
                      for g in games],
            "next_seed": max(int(g["seed"]) for g in doc["games"]) + 1, "rejected_seeds": [], "games": {}}


def game_key(cfg: dict, track: str, seed: int, rotation: int, arm: str) -> str:
    return TaskPlan(track, seed, rotation, arm, cfg["horizon_days"]).task_id


def schedule_game(cfg: dict, rotation: int) -> dict:
    """The seat list of a rotation (a replacement seed keeps the rotation of the seed that it replaces)."""
    for g in cfg["schedule_doc"]["games"]:
        if int(g["rotation"]) == rotation:
            return g
    raise ValueError(f"no game of rotation {rotation} in the schedule")


# ------------------------------------------------------------------------------------------- the campaign
class Campaign:
    def __init__(self, cfg: dict, launcher=None, gen_fn=None, alive_fn=None, now=time.time) -> None:
        self.cfg = cfg
        self.state = _read(cfg["state"]) or new_state(cfg)
        self.launcher = launcher or self._popen
        self.gen_fn = gen_fn or self._gen
        self.alive_fn = alive_fn or runner_alive
        self.now = now
        self.procs: dict[str, subprocess.Popen] = {}
        self.meters: dict[str, arena.SpendMeter] = {}
        self.last_start = 0.0

    # ---------------------------------------------------------------- paths
    @property
    def track(self) -> str:
        return self.state["track"]

    def run_dir(self, key: str) -> Path:
        return self.cfg["runs_dir"] / key

    def task_dir(self, key: str) -> Path:
        return self.cfg["tasks_dir"] / key

    def save(self) -> None:
        _write(self.cfg["state"], self.state)

    def account(self, name: str | None) -> Account | None:
        return next((a for a in self.cfg["accounts"] if a.name == name), None)

    def account_of_profile(self, profile: str | None) -> str | None:
        return next((a.name for a in self.cfg["accounts"] if a.profile == profile), None)

    # ---------------------------------------------------------------- games
    def games(self) -> list[tuple[dict, str, str]]:
        """(pair, arm, key) in play order: both arms of a seed, seed after seed."""
        out = []
        for p in self.state["pairs"]:
            if p["seed"] is None:
                continue
            for arm in self.cfg["arms"]:
                out.append((p, arm, game_key(self.cfg, self.track, p["seed"], p["rotation"], arm)))
        return out

    def game(self, key: str) -> dict:
        return self.state["games"].setdefault(key, {"status": "pending"})

    def refresh(self) -> None:
        """Bring every game up to date from its run directory (a lost or old state file is rebuilt here)."""
        for pair, arm, key in self.games():
            g = self.game(key)
            g.update(seed=pair["seed"], rotation=pair["rotation"], arm=arm)
            if g["status"] in TERMINAL and g.get("recorded"):
                continue
            rd = self.run_dir(key)
            if (rd / "result.json").exists():
                self.record(pair, key, g)
            elif g["status"] == "running" and not (rd / "run.json").exists():
                proc = self.procs.get(key)
                alive = proc.poll() is None if proc is not None else self.alive_fn(g.get("pid"), rd)
                if not alive:  # the runner stopped before it wrote run.json: start the game again
                    g["start_failures"] = int(g.get("start_failures") or 0) + 1
                    g["status"] = "pending"
                    if g["start_failures"] > int(self.cfg["max_restarts"]):
                        g.update(status="failed", recorded=True,
                                 exclusion=[f"the runner did not start {g['start_failures']} times"])
            elif (rd / "run.json").exists():
                if g.get("account") is None:  # a run that the state does not know: its profile is in run.json
                    g["account"] = self.account_of_profile((_read(rd / "run.json") or {}).get("claude_profile"))
                proc = self.procs.get(key)
                alive = proc.poll() is None if proc is not None else self.alive_fn(g.get("pid"), rd)
                g["status"] = "running" if alive else "stalled"
        self.apply_pairing()

    def record(self, pair: dict, key: str, g: dict) -> None:
        """A finished game: its spend, the exclusion rules (prereg 7) and the Codex weekly window."""
        rd = self.run_dir(key)
        res = _read(rd / "result.json") or {}
        if g.get("account") is None:
            g["account"] = self.account_of_profile((_read(rd / "run.json") or {}).get("claude_profile"))
        g["usd"] = float((res.get("cost") or {}).get("usd_reported") or 0.0)
        g["ended"] = (res.get("run") or {}).get("ended")
        g["exclusion"] = exclusion_reasons(load_run(rd))
        g["codex_weekly_used_percent"] = codex_weekly_used(rd)
        g["finished"] = g.get("finished") or self.now()
        g["status"] = "excluded" if g["exclusion"] else ("replaced" if pair["status"] == "excluded" else "done")
        g["recorded"] = True
        self.procs.pop(key, None)
        self.meters.pop(key, None)

    def apply_pairing(self) -> None:
        """Prereg 7: when one arm of a seed is excluded, both arms are replaced by the next accepted seed (same
        rotation). The other arm is not started; if it already runs, it ends normally and is kept as
        ``replaced`` (its spend counts)."""
        for pair in list(self.state["pairs"]):
            if pair["seed"] is None or pair["status"] != "active":
                continue
            keys = [game_key(self.cfg, self.track, pair["seed"], pair["rotation"], a) for a in self.cfg["arms"]]
            if not any(self.game(k)["status"] in ("excluded", "failed") for k in keys):
                continue
            pair["status"] = "excluded"
            for k in keys:
                g = self.game(k)
                if g["status"] == "pending":
                    g["status"] = "dropped"
                elif g["status"] == "done":
                    g["status"] = "replaced"
            used = sum(1 for p in self.state["pairs"] if p["replaces"] is not None)
            if used < int(self.cfg["max_replacements"]):
                self.state["pairs"].append({"seed": None, "rotation": pair["rotation"], "replaces": pair["seed"],
                                            "status": "active"})
            else:
                self.state.setdefault("notes", []).append(f"seed {pair['seed']} excluded: no replacements left")

    def allocate_seeds(self) -> None:
        """Give each open replacement the next accepted seed (its tasks are generated here)."""
        for pair in self.state["pairs"]:
            if pair["seed"] is not None:
                continue
            start = int(self.state["next_seed"])
            for seed in range(start, start + SEED_TRIES):
                try:
                    self.ensure_tasks(seed, pair["rotation"])
                except SeedRejected:
                    self.state["rejected_seeds"].append(seed)
                    continue
                pair["seed"], self.state["next_seed"] = seed, seed + 1
                break
            else:
                raise RuntimeError(f"no accepted seed in {start}..{start + SEED_TRIES - 1}")
            self.save()

    def ensure_tasks(self, seed: int, rotation: int) -> None:
        keys = [game_key(self.cfg, self.track, seed, rotation, a) for a in self.cfg["arms"]]
        if not all((self.task_dir(k) / "task.json").exists() for k in keys):
            self.gen_fn(self.track, seed, self.cfg["tasks_dir"], [rotation], tuple(self.cfg["arms"]),
                        self.cfg["horizon_days"])
        for k in keys:  # the task must be the schedule's game: same seed and rotation
            obj = (_read(self.task_dir(k) / "task.json") or {}).get("objective") or {}
            if (obj.get("seed"), obj.get("rotation")) != (seed, rotation):
                raise ValueError(f"{self.task_dir(k)}: seed/rotation {obj.get('seed')}/{obj.get('rotation')}, "
                                 f"the schedule wants {seed}/{rotation}")

    @staticmethod
    def _gen(track, seed, out, rotations, arms, horizon_days) -> None:
        from railhead.multi.gen import gen_track

        gen_track(track, seed, Path(out), rotations=rotations, arms=arms, horizon_days=horizon_days)

    # ---------------------------------------------------------------- money
    def live_usd(self, key: str) -> float:
        """The dollars that a running game's agents reported so far (incremental OTLP reader of the runner)."""
        try:
            if key not in self.meters:
                self.meters[key] = arena.SpendMeter.for_run(self.run_dir(key))
            return self.meters[key].read()
        except (OSError, ValueError, KeyError, TypeError):
            return 0.0

    def ledger(self) -> dict[str, dict]:
        """For each account: spent (finished games), committed (running games: the larger of their spend so
        far and the reserve), running games, and the headroom under its cap."""
        reserve = float(self.cfg["reserve_usd"])
        out = {a.name: {"cap": a.usd_cap, "spent": 0.0, "committed": 0.0, "running": 0, "max_games": a.max_games}
               for a in self.cfg["accounts"]}
        out["(none)"] = {"cap": 0.0, "spent": 0.0, "committed": 0.0, "running": 0, "max_games": 0}
        for key, g in self.state["games"].items():
            row = out.get(g.get("account") or "(none)", out["(none)"])
            if g.get("recorded"):
                row["spent"] += float(g.get("usd") or 0.0)
            elif g["status"] in ("running", "stalled"):
                live = self.live_usd(key)
                g["usd_live"] = round(live, 4)
                row["committed"] += max(live, reserve)
                row["running"] += 1
        for row in out.values():
            row["headroom"] = round(row["cap"] - row["spent"] - row["committed"], 4)
        return out

    def total_committed(self, ledger: dict) -> float:
        return sum(r["spent"] + r["committed"] for r in ledger.values())

    def choose_account(self, ledger: dict, need: float) -> str | None:
        """The account with the most headroom that fits ``need`` and has a free game slot."""
        fits = [(r["headroom"], n) for n, r in ledger.items()
                if n != "(none)" and r["headroom"] >= need and r["running"] < r["max_games"]]
        return max(fits)[1] if fits else None

    # ---------------------------------------------------------------- processes
    def _popen(self, cmd: list[str], log: Path, env: dict) -> subprocess.Popen:
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "ab") as fh:
            return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                                    start_new_session=True, env=env)

    def _env(self) -> dict:
        # no environment login reaches the runner: each game uses the keychain login of its own profile
        return arena.claude_profile_env("default")

    def start(self, pair: dict, arm: str, key: str, account: str) -> None:
        acct = self.account(account)
        self.ensure_tasks(pair["seed"], pair["rotation"])
        game = schedule_game(self.cfg, pair["rotation"])
        cmd = [sys.executable, "-m", "railhead.multi.arena", "run", str(self.task_dir(key)), str(self.run_dir(key)),
               *players(self.cfg, game), *[str(x) for x in self.cfg["runner"]], "--claude-profile", acct.profile,
               # the sandbox also refuses the other games and tasks of the campaign (the paired arm plays now)
               "--sandbox-deny", f"{self.cfg['runs_dir']},{self.cfg['tasks_dir']}"]
        g = self.game(key)
        sha, dirty = code_version()
        proc = self.launcher(cmd, self.log_path(key), self._env())
        self.procs[key] = proc
        g.update(status="running", account=account, pid=proc.pid, started=self.now(), code_sha=sha,
                 code_dirty=dirty, restarts=0, players=players(self.cfg, game))
        self.last_start = self.now()
        self.save()
        print(json.dumps({"event": "game started", "game": key, "account": account}), flush=True)

    def resume(self, key: str) -> None:
        g = self.game(key)
        if int(g.get("restarts") or 0) >= int(self.cfg["max_restarts"]):
            g["status"] = "failed"  # infrastructure: the pair is replaced (apply_pairing)
            g["exclusion"] = [f"no result.json after {g.get('restarts')} resumes"]
            g["recorded"] = True
            print(json.dumps({"event": "game failed", "game": key}), flush=True)
            return
        cmd = [sys.executable, "-m", "railhead.multi.arena", "resume", str(self.run_dir(key))]
        proc = self.launcher(cmd, self.log_path(key), self._env())
        self.procs[key] = proc
        g.update(status="running", pid=proc.pid, restarts=int(g.get("restarts") or 0) + 1)
        self.last_start = self.now()
        self.save()
        print(json.dumps({"event": "game resumed", "game": key, "restarts": g["restarts"]}), flush=True)

    def log_path(self, key: str) -> Path:
        return self.cfg["state"].parent / "logs" / f"{key}.log"

    # ---------------------------------------------------------------- one step of the loop
    def step(self) -> dict:
        """Update the games, resume the stalled ones, start new ones while the caps allow. Returns a summary;
        ``finished`` is True when nothing runs and nothing more can start."""
        self.refresh()
        self.allocate_seeds()
        self.refresh()
        led = self.ledger()
        reserve = float(self.cfg["reserve_usd"])
        for _, _, key in self.games():  # a stalled game resumes on its own account (from run.json)
            if self.game(key)["status"] == "stalled":
                self.resume(key)
        running = sum(1 for _, _, k in self.games() if self.game(k)["status"] == "running")
        stop = None
        for pair, arm, key in self.games():
            g = self.game(key)
            if g["status"] != "pending":
                continue
            if running >= int(self.cfg["concurrency"]):
                break
            if self.now() - self.last_start < float(self.cfg["stagger_s"]):
                break  # one start per stagger: engines and sinks never start at the same moment
            # a seed starts only when both arms fit under the global cap
            others = [k for a, k in ((a, game_key(self.cfg, self.track, pair["seed"], pair["rotation"], a))
                                     for a in self.cfg["arms"]) if k != key and self.game(k)["status"] == "pending"]
            need = reserve * (1 + len(others))
            cap = self.cfg["usd_cap"]
            if cap is not None and self.total_committed(led) + need > float(cap):
                stop = "global cap"
                break
            acct = self.choose_account(led, reserve)
            if acct is None:
                busy = any(r["running"] >= r["max_games"] for n, r in led.items() if n != "(none)")
                if busy and any(r["headroom"] >= reserve for n, r in led.items() if n != "(none)"):
                    break  # an account with money is busy: wait for its game to end
                stop = "accounts exhausted"
                break
            self.start(pair, arm, key, acct)
            led[acct]["committed"] += reserve
            led[acct]["headroom"] -= reserve
            led[acct]["running"] += 1
            running += 1
        self.save()
        pending = sum(1 for _, _, k in self.games() if self.game(k)["status"] == "pending")
        active = sum(1 for _, _, k in self.games() if self.game(k)["status"] in ("running", "stalled"))
        return {"running": active, "pending": pending, "stop": stop,
                "finished": active == 0 and (pending == 0 or stop is not None)}

    def summary(self) -> dict:
        self.refresh()
        led = self.ledger()
        counts: dict[str, int] = {}
        for _, _, k in self.games():
            st = self.game(k)["status"]
            counts[st] = counts.get(st, 0) + 1
        dropped = sum(1 for g in self.state["games"].values() if g["status"] == "dropped")
        return {"games": counts | ({"dropped": dropped} if dropped else {}),
                "accounts": {n: {k: round(v, 2) if isinstance(v, float) else v for k, v in r.items()}
                             for n, r in led.items() if n != "(none)" or r["spent"] or r["running"]},
                "usd_total": round(self.total_committed(led), 2), "usd_cap": self.cfg["usd_cap"],
                "pairs": [p for p in self.state["pairs"] if p["replaces"] is not None or p["status"] != "active"],
                "rejected_seeds": self.state["rejected_seeds"]}


# ------------------------------------------------------------------------------------------- helpers
def runner_alive(pid: int | None, run_dir: Path) -> bool:
    """A runner of ``run_dir`` still runs as ``pid`` (the command line names the run directory: a reused pid
    of another program does not count)."""
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    r = subprocess.run(["ps", "-p", str(int(pid)), "-o", "command="], capture_output=True, text=True, check=False)
    return "railhead.multi.arena" in r.stdout and str(run_dir) in r.stdout


def code_version() -> tuple[str | None, bool | None]:
    """The commit of the Railhead checkout that runs the games, and whether tracked files differ from it."""
    from railhead.engine import REPO_DIR

    try:
        sha = subprocess.run(["git", "-C", str(REPO_DIR), "rev-parse", "HEAD"], capture_output=True, text=True,
                             check=True, timeout=30).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(REPO_DIR), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, check=True, timeout=30).stdout.strip() != ""
        return sha, dirty
    except (OSError, subprocess.SubprocessError):
        return None, None


def codex_weekly_used(run_dir: Path) -> float | None:
    """The last weekly-window ``used_percent`` that a Codex agent of the run saw (its session files): the
    Codex login is shared, so this tracks its weekly limit. Advisory."""
    last: tuple[float, float] | None = None
    for f in Path(run_dir).glob("agents/*/codex-home/sessions/**/*.jsonl"):
        with contextlib.suppress(OSError):
            for line in f.read_text(errors="replace").splitlines():
                if '"rate_limits"' not in line:
                    continue
                with contextlib.suppress(json.JSONDecodeError, AttributeError, TypeError):
                    doc = json.loads(line)
                    rl = (doc.get("payload") or {}).get("rate_limits") or {}
                    for w in (rl.get("primary"), rl.get("secondary")):
                        if w and int(w.get("window_minutes") or 0) >= 7 * 24 * 60:
                            t = f.stat().st_mtime
                            if last is None or t >= last[0]:
                                last = (t, float(w.get("used_percent") or 0.0))
    return last[1] if last else None


def preflight(accounts: list[Account]) -> list[dict]:
    """A read-only check of each login (no token is printed or stored): is there a keychain item, its plan
    tier, extra usage, and whether two names are the same Claude account (hashes of the account ids)."""
    rows, seen = [], {}
    for a in accounts:
        o = arena.read_claude_login(a.profile)
        cfgdir = arena.claude_profile_dir(a.profile)
        meta = _read(Path(cfgdir) / ".claude.json" if cfgdir else Path.home() / ".claude.json") or {}
        acct = meta.get("oauthAccount") or {}
        ident = hashlib.sha256(f"{acct.get('accountUuid')}/{acct.get('organizationUuid')}".encode()).hexdigest()
        row = {"account": a.name, "login": bool(o.get("accessToken")), "tier": o.get("rateLimitTier"),
               "extra_usage": acct.get("hasExtraUsageEnabled"),
               "expires_in_min": round((float(o.get("expiresAt") or 0) / 1000 - time.time()) / 60) if o else None}
        if acct.get("accountUuid") and ident in seen:
            row["same_account_as"] = seen[ident]
        seen.setdefault(ident, a.name)
        rows.append(row)
    return rows


def usage(account: Account) -> dict:
    """The account's usage windows from the OAuth usage endpoint (advisory: it often rate-limits)."""
    import urllib.error
    import urllib.request

    tok = arena.read_claude_login(account.profile).get("accessToken")
    if not tok:
        return {"error": "no login"}
    req = urllib.request.Request("https://api.anthropic.com/api/oauth/usage",
                                 headers={"Authorization": f"Bearer {tok}", "anthropic-beta": "oauth-2025-04-20"})
    try:
        doc = json.loads(urllib.request.urlopen(req, timeout=20).read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        return {"error": str(getattr(e, "code", "") or e)[:80]}
    return {k: (v or {}).get("utilization") for k, v in doc.items() if isinstance(v, dict) and "utilization" in v}


def plan(cfg: dict) -> dict:
    """The games in order with their task and run state, the accounts, and the projected spend against the
    pool (no change to anything)."""
    c = Campaign(cfg, alive_fn=runner_alive)
    c.refresh()
    games = []
    for pair, arm, key in c.games():
        g = c.game(key)
        games.append({"game": key, "players": players(cfg, schedule_game(cfg, pair["rotation"])),
                      "task": (c.task_dir(key) / "task.json").exists(), "status": g["status"],
                      "account": g.get("account")})
    n = len(games)
    est = float(cfg["game_usd_estimate"])
    pool = sum(a.usd_cap for a in cfg["accounts"])
    return {"games": games, "count": n, "tasks_missing": sum(not g["task"] for g in games),
            "projected_usd": round(n * est, 2), "estimate_per_game": est, "pool_usd": pool,
            "usd_cap": cfg["usd_cap"], "reserve_usd": cfg["reserve_usd"], "concurrency": cfg["concurrency"],
            "pool_covers_projection": pool >= n * est and (cfg["usd_cap"] is None or cfg["usd_cap"] >= n * est),
            "accounts": [{"name": a.name, "usd_cap": a.usd_cap, "max_games": a.max_games} for a in cfg["accounts"]],
            "preflight": preflight(cfg["accounts"])}


# ------------------------------------------------------------------------------------------- CLI
@contextlib.contextmanager
def campaign_lock(state: Path):
    state.parent.mkdir(parents=True, exist_ok=True)
    with open(state.with_name("campaign.lock"), "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SystemExit(f"another campaign process holds {state.with_name('campaign.lock')}") from None
        yield


def run(cfg: dict, campaign: Campaign | None = None, sleep=time.sleep, max_steps: int | None = None) -> dict:
    """The loop: step until finished. Stopping this process (Ctrl-C) leaves the games running in their own
    sessions; a new ``run`` adopts them."""
    c = campaign or Campaign(cfg)
    steps = 0
    with campaign_lock(cfg["state"]):
        while True:
            s = c.step()
            steps += 1
            if s["finished"] or (max_steps is not None and steps >= max_steps):
                break
            sleep(float(cfg["poll_s"]))
    out = c.summary() | {"stop": s["stop"]}
    print(json.dumps(out, indent=1, default=str), flush=True)
    return out


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2 or argv[0] not in ("plan", "run", "status"):
        print(__doc__, file=sys.stderr)
        return 2
    cfg = load_config(Path(argv[1]))
    if argv[0] == "plan":
        print(json.dumps(plan(cfg), indent=1, default=str))
        return 0
    if argv[0] == "status":
        out = Campaign(cfg).summary()
        if "--usage" in argv:
            out["usage"] = {a.name: usage(a) for a in cfg["accounts"]}
        print(json.dumps(out, indent=1, default=str))
        return 0
    run(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
