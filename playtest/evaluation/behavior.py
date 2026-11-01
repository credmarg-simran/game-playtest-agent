"""What the players did: strategies found, mechanics ignored, improvement across runs."""

from __future__ import annotations

from collections import Counter

from ..game.profile import GameProfile
from ..play.records import RunLog


def _trend(values: list[float]) -> float | None:
    """Least-squares slope per run."""
    n = len(values)
    if n < 3:
        return None
    xs = range(n)
    mx, my = (n - 1) / 2, sum(values) / n
    den = sum((x - mx) ** 2 for x in xs)
    return round(sum((x - mx) * (y - my) for x, y in zip(xs, values)) / den, 3)


def observe(runs: list[RunLog], profile: GameProfile, mechanics_seen: set[str], strategies: dict[str, list]) -> dict:
    out: dict = {"players": {}}
    for name in sorted({r.player for r in runs}):
        mine = [r for r in runs if r.player == name and r.end != "no_start"]
        used = Counter(inp for r in mine for _, inp in r.inputs)
        scores = [r.score for r in mine]
        out["players"][name] = {
            "inputs_used": dict(used.most_common(8)),
            "score_by_run": scores,
            "survival_by_run": [r.seconds for r in mine],
            "score_trend_per_run": _trend(scores),
            "learned": [r.reflection.get("learned") for r in mine if r.reflection.get("learned")],
            "fun_moments": [r.reflection.get("fun_moment") for r in mine if r.reflection.get("fun_moment")],
            "frustrations": [r.reflection.get("frustration") for r in mine if r.reflection.get("frustration")],
            "play_again": [r.reflection.get("play_again") for r in mine if r.reflection.get("play_again")],
        }
        if strategies.get(name):
            out["players"][name]["strategies"] = [
                "; ".join(map(str, s["plan"])) if isinstance(s["plan"], list) else str(s["plan"]).replace("\n", " ")
                for s in strategies[name]
            ]

    used_anywhere = {inp for r in runs if r.player != "random" for _, inp in r.inputs}
    menu_only = set(profile.start) | set(profile.restart)
    out["controls_never_used"] = [
        c.name for c in profile.controls if c.to_input().name not in used_anywhere and c.name not in menu_only
    ] if any(r.player != "random" for r in runs) else []

    with_check = [(i, m) for i, m in enumerate(profile.mechanics) if m.get("seen_when")]
    out["mechanics_checked"] = len(with_check)
    out["mechanics_seen"] = [m.get("name") for i, m in with_check if f"__mech{i}" in mechanics_seen]
    out["mechanics_never_seen"] = [m.get("name") for i, m in with_check if f"__mech{i}" not in mechanics_seen]

    out["stuck_runs"] = sum(1 for r in runs if r.end in ("stall", "no_start"))
    return out
