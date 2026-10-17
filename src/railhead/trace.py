"""End-to-end trace bundles: one folder per arena player that joins every layer of one game.

    railhead trace bundle <arena_dir> [--otel traces] [--out traces/bundles/<arena>]

Per player the bundle has:
    llm.jsonl        OTel log events in time order (prompts, LLM requests with tokens and cost, tool calls)
    spans.jsonl      OTel spans in time order (LLM request, tool, tool execution, ...)
    transcript.*     the agent's own stream (``claude -p`` stream-json, or ``codex exec`` text)
    actions.jsonl    the engine-side action log that the verifier replays
    reward.json      the proof-of-play verdict (if the game was verified)
    summary.json     totals of all of the above
and ``index.json`` lists every summary. Identity keys (e-mail, account, organisation, host) are
removed and e-mail addresses and the home path are masked. The default output is under ``traces/``,
which git ignores: a bundle is for local analysis, not for publication.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

from railhead.otel import _attr_value, iter_log_records, summarize_players

PII_KEYS = {"user.email", "user.account_id", "user.account_uuid", "user.id", "organization.id", "host.name"}
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def scrub(x: Any) -> Any:
    """Remove identity keys and mask e-mail addresses and the home path, recursively."""
    if isinstance(x, dict):
        return {k: scrub(v) for k, v in x.items() if k not in PII_KEYS}
    if isinstance(x, list):
        return [scrub(v) for v in x]
    if isinstance(x, str):
        home = os.environ.get("HOME", "")
        if home and home != "/":  # also the dash-encoded form that Claude Code uses for project dirs
            x = x.replace(home, "~").replace(home.replace("/", "-"), "-~")
        return EMAIL.sub("<email>", x)
    return x


def _scrub_line(line: str) -> str:
    try:
        return json.dumps(scrub(json.loads(line)), separators=(",", ":"))
    except json.JSONDecodeError:
        return scrub(line)


CODEX_MARKERS = {"exec", "codex", "thinking", "tokens used", "user", "hook: Stop", "hook: Stop Completed"}


def visible_messages(agent_log: Path) -> list[dict]:
    """The messages a player showed (Claude ``text`` blocks and its final result; Codex ``codex`` blocks),
    in order. Hidden reasoning is never included. The last message is marked ``final``."""
    lines = Path(agent_log).read_text(encoding="utf-8", errors="replace").splitlines()
    out: list[dict] = []
    if lines and lines[0].startswith("{"):  # claude -p stream-json
        for line in lines:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("type") == "assistant":
                out += [{"kind": "message", "text": c["text"]} for c in d.get("message", {}).get("content", [])
                        if c.get("type") == "text" and c.get("text", "").strip()]
            elif d.get("type") == "result" and str(d.get("result", "")).strip():
                out.append({"kind": "final", "text": str(d["result"])})
        return out
    block: list[str] | None = None  # codex exec text: a "codex" line opens a visible message
    for line in lines + ["exec"]:
        if line.strip() in CODEX_MARKERS:
            if block is not None and "\n".join(block).strip():
                out.append({"kind": "message", "text": "\n".join(block).strip()})
            block = [] if line.strip() == "codex" else None
        elif block is not None:
            block.append(line)
    if out:
        out[-1]["kind"] = "final"
    return out


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(json.dumps(r, separators=(",", ":")) + "\n" for r in rows)


def _events_by_player(otel_dir: Path) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for res, attrs, rec in iter_log_records(otel_dir):
        t = int(rec.get("timeUnixNano") or rec.get("observedTimeUnixNano") or 0)
        name = attrs.get("event.name") or rec.get("body", {}).get("stringValue", "")
        out[str(res.get("player", "?"))].append({"t": t, "event": name, "service": res.get("service.name"),
                                                  "attrs": scrub(attrs)})
    for rows in out.values():
        rows.sort(key=lambda r: r["t"])
    return out


def _spans_by_player(otel_dir: Path) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    path = Path(otel_dir) / "traces.jsonl"
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        for rs in json.loads(line).get("body", {}).get("resourceSpans", []):
            res = {a["key"]: _attr_value(a["value"]) for a in rs.get("resource", {}).get("attributes", [])}
            for ss in rs.get("scopeSpans", []):
                for sp in ss.get("spans", []):
                    t0, t1 = int(sp.get("startTimeUnixNano", 0)), int(sp.get("endTimeUnixNano", 0))
                    out[str(res.get("player", "?"))].append({
                        "t": t0, "ms": round((t1 - t0) / 1e6, 3), "name": sp.get("name"), "trace": sp.get("traceId"),
                        "span": sp.get("spanId"), "parent": sp.get("parentSpanId") or None,
                        "attrs": scrub({a["key"]: _attr_value(a["value"]) for a in sp.get("attributes", [])})})
    for rows in out.values():
        rows.sort(key=lambda r: r["t"])
    return out


def bundle(arena_dir: Path, otel_dir: Path, out_dir: Path) -> list[dict]:
    arena_dir, out_dir = Path(arena_dir), Path(out_dir)
    events, spans, usage = _events_by_player(otel_dir), _spans_by_player(otel_dir), summarize_players(otel_dir)
    index = []
    for pdir in sorted(p for p in arena_dir.iterdir() if (p / "state").is_dir()):
        player, dst = pdir.name, out_dir / pdir.name
        dst.mkdir(parents=True, exist_ok=True)
        _write_jsonl(dst / "llm.jsonl", events.get(player, []))
        _write_jsonl(dst / "spans.jsonl", spans.get(player, []))
        log = pdir / "agent.log"
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines() if log.exists() else []
        is_stream = bool(lines) and lines[0].startswith("{")
        (dst / ("transcript.jsonl" if is_stream else "transcript.txt")).write_text(
            "".join(_scrub_line(x) + "\n" for x in lines), encoding="utf-8")
        actions = pdir / "state/run/actions.jsonl"
        n_act = n_wait = 0
        if actions.exists():
            shutil.copyfile(actions, dst / "actions.jsonl")
            for x in actions.read_text(encoding="utf-8").splitlines():
                kind = json.loads(x).get("kind")
                n_act += kind == "act"
                n_wait += kind == "wait"
        reward = json.loads((pdir / "verify/reward.json").read_text()) if (pdir / "verify/reward.json").exists() else {}
        if reward:
            (dst / "reward.json").write_text(json.dumps(reward, indent=1))
        u = usage.get(player, {})
        summary = {"player": player, "model": u.get("model") or None, "llm_events": len(events.get(player, [])),
                   "spans": len(spans.get(player, [])), "requests": u.get("requests", 0),
                   "input_tokens": u.get("input_tokens", 0), "output_tokens": u.get("output_tokens", 0),
                   "cache_read_tokens": u.get("cache_read_tokens", 0), "cost_usd": round(u.get("cost_usd", 0.0), 4),
                   "transcript_lines": len(lines), "game_actions": n_act, "game_waits": n_wait,
                   "net_worth": reward.get("net_worth"), "valid": reward.get("valid"), "reward": reward.get("reward")}
        (dst / "summary.json").write_text(json.dumps(summary, indent=1))
        index.append(summary)
    (out_dir / "index.json").write_text(json.dumps(index, indent=1))
    return index


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] != "bundle":
        print(__doc__)
        return 2
    arena = Path(argv[1])
    otel = Path(argv[argv.index("--otel") + 1]) if "--otel" in argv else Path("traces")
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else Path("traces/bundles") / arena.resolve().name
    for row in bundle(arena, otel, out):
        print(json.dumps(row))
    print(json.dumps({"bundle": str(out.resolve())}))
    return 0
