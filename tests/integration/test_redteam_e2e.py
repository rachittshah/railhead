"""Red-team attacks end to end (real engine, GameScript and verifier). Threat model M1: the agent can rewrite
its log after play, but it cannot run its own engine. See tests/unit/test_redteam.py for the fake-engine set."""

# ruff: noqa: F811 - the honest fixture is imported from test_integrity_e2e and used as a parameter
from __future__ import annotations

import json

import pytest

from railhead.actionlog import meta_digest, read_log
from tests.integration.test_integrity_e2e import TASK, _verify, honest  # noqa: F401 - fixture used by name

pytestmark = pytest.mark.integration

# The GameScript checksum step (main.nut H_checksum `mix`) and meta fold (H_note), copied as an attacker would.
K, MASK = 1000003, (1 << 47) - 1
K_INV = pow(K, -1, 1 << 47)  # K is odd, so each mix step can be undone


def _mix(h: int, v: int) -> int:
    return ((h * K) ^ (v & 0xFFFFFFFF)) & MASK


def _unmix(h: int, v: int) -> int:
    return ((h ^ (v & 0xFFFFFFFF)) * K_INV) & MASK


def _chain(metas: list[dict]) -> int:
    md = 0
    for r in metas:
        md = ((md * K) ^ (meta_digest(r["m"], r.get("a") or {}) & 0x7FFFFFFF)) & MASK or 1
    return md


def _rechecksum(recs: list[dict], old: list[dict]) -> None:
    """Give each wait of ``recs`` the checksum it would have with its meta records, from the logged
    checksum of the same wait in ``old`` alone: strip the old meta fold, add the new one. No engine."""
    old_waits = iter([i, r] for i, r in enumerate(old) if r["kind"] == "wait")
    for i, r in enumerate(recs):
        if r["kind"] != "wait":
            continue
        j, o = next(old_waits)
        md_old = _chain([m for m in old[:j] if m["kind"] == "meta"])
        md_new = _chain([m for m in recs[:i] if m["kind"] == "meta"])
        h = o["checksum"]["h"]
        if md_old:
            h = _unmix(_unmix(h, md_old >> 32), md_old)
        if md_new:
            h = _mix(_mix(h, md_new), md_new >> 32)
        r["checksum"] = dict(o["checksum"], h=h)


def test_attack_hindsight_promise_with_recomputed_checksums_is_rejected(honest, tmp_path):
    """The agent plays, sees its delivery, then changes its promise (before the bid deadline, before the
    first wait) to the delivery it saw, and recomputes every later wait checksum from the log alone.
    This defeats only a forger that has the log alone: with the digest mixed first, an agent that recorded
    every world value in the checksum at each wait can still recompute it (Partly in VERIFICATION.md)."""
    log, out = honest
    assert out["rejected"] == 0 and out["bid"] < out["delivered"]
    old = [r.raw for r in read_log(log)]
    recs = json.loads(json.dumps(old))
    promise = next(r for r in recs if r["kind"] == "meta" and r["m"] == "promise")
    promise["a"]["quantity"] = int(out["delivered"])
    _rechecksum(recs, old)
    forged = tmp_path / "forged.jsonl"
    forged.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in recs))
    res = _verify(forged, tmp_path)
    assert res["rejected"] == 1 and res["reward"] == 0.0, (res, out)


def test_rechecksum_is_the_identity_on_an_unchanged_log(honest, tmp_path):
    """Control for the attack above: the forger's arithmetic reproduces the honest checksums exactly."""
    log, _ = honest
    old = [r.raw for r in read_log(log)]
    recs = json.loads(json.dumps(old))
    _rechecksum(recs, old)
    assert recs == old


def test_honest_interrupted_wait_and_fork_at_the_horizon_verify(tmp_path):
    """Honest side of the strict v2 rules: a wait cut short by a wake condition (live steps in days, the
    replay in ticks), a meta record between waits, and a fork made on the horizon date."""
    from railhead.actionlog import ActionLog
    from railhead.task import TaskSpec
    from railhead.world import World

    spec = TaskSpec.load(TASK)
    log_path = tmp_path / "actions.jsonl"
    log = ActionLog(log_path)
    with World.load(tmp_path / "live", TASK / "start.sav") as w:
        w.attach_log(log)
        start = w.state()["date"]
        out = w.wait(days=45, until=["new_month"])
        assert out["reason"] == "new_month" and out["days"] < 45
        w.note("flag", note="map looks odd")
        w.wait(days=start + spec.horizon_days - w.state()["date"])
        with w.fork(tmp_path / "fork", reseed=7) as f:
            f.step(days=5)
    log.close()
    res = _verify(log_path, tmp_path)
    assert res["valid"] == 1 and res["rejected"] == 0 and res["divergences"] == 0 and res["waits"] == 2
