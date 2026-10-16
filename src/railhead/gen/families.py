"""Task families: objective selection on a generated map (deterministic for a given world)."""

from __future__ import annotations

from railhead.world import World


def freight_cargos(world: World) -> set[int]:
    return {int(c["id"]) for c in world.call("cargos") if c.get("freight", c.get("is_freight"))}


def pick_deliver_pair(world: World, dmin: int = 12, dmax: int = 60, target: int = 24,
                      skip: int = 0, revenue: bool = False) -> tuple[dict, dict, int] | None:
    """(source industry, accepting industry, cargo) at a road-sized distance; ``skip`` picks the n-th best.

    ``revenue=True`` ranks by production x distance (a profit proxy) and skips sources whose output can
    only decline (temperate oil wells) — used for tycoon baselines that must actually make money.
    """
    freight = freight_cargos(world)
    inds = world.call("industries")
    cands = []
    for s in inds:
        if revenue and "Oil Wells" in str(s.get("type_name", "")):
            continue
        for p in s.get("produces", []):
            c = int(p["cargo"])
            if c not in freight:
                continue
            prod = int(p.get("last_month", 0))
            for d in inds:
                if d["id"] == s["id"] or c not in [int(x) for x in d.get("accepts", [])]:
                    continue
                dist = abs(s["x"] - d["x"]) + abs(s["y"] - d["y"])
                if dmin <= dist <= dmax:
                    key = -prod * dist if revenue else abs(dist - target)
                    cands.append((key, s["id"], d["id"], c, s, d))
    cands.sort(key=lambda t: t[:4])
    if len(cands) <= skip:
        return None
    _, _, _, c, s, d = cands[skip]
    return s, d, c
