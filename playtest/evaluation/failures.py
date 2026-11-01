"""Why runs ended, where players kept failing, and where the agent or the UI got stuck."""

from __future__ import annotations

from collections import Counter

from ..play.records import RunLog

POSITION_KEYS = ("player_x", "player_y", "x", "y", "level", "stage", "wave")


def _bucket(value: float, size: float) -> str:
    lo = int(value // size * size)
    return f"{lo}-{lo + size:g}"


def analyse(runs: list[RunLog]) -> dict:
    deaths = []
    for r in runs:
        if r.end not in ("death", "stall", "no_start"):
            continue
        refl = r.reflection or {}
        where = {k: r.death.get("state", {}).get(k) for k in POSITION_KEYS if r.death.get("state", {}).get(k) is not None}
        deaths.append({
            "run": r.index + 1, "player": r.player, "end": r.end, "t": r.seconds, "score": r.score,
            "cause": refl.get("cause", ""), "fair": refl.get("fair"), "where": where,
        })

    # The same score at death across runs means players keep failing at the same point.
    at_score = Counter(d["score"] for d in deaths if d["end"] == "death")
    repeated_scores = [{"score": s, "deaths": n} for s, n in at_score.most_common() if n >= 2]

    # Same idea with survival time: lots of deaths in one window points at a spike.
    times = Counter(_bucket(d["t"], 5) for d in deaths if d["end"] == "death")
    death_windows = [{"seconds": w, "deaths": n} for w, n in times.most_common(4)]

    positions = Counter()
    for d in deaths:
        for k, v in d["where"].items():
            if isinstance(v, (int, float)):
                positions[f"{k} {_bucket(v, 50 if abs(v) > 20 else 1)}"] += 1
    repeated_positions = [{"where": w, "deaths": n} for w, n in positions.most_common(4) if n >= 2]

    responses = [x for r in runs for x in r.responses]
    no_reaction = [x for x in responses if not x["reacted"]]
    confusing = [r.reflection.get("confusing") for r in runs if r.reflection and r.reflection.get("confusing")]

    return {
        "ends": deaths,
        "repeated_death_scores": repeated_scores,
        "death_time_windows": death_windows,
        "repeated_death_positions": repeated_positions,
        "instant_deaths": sum(1 for d in deaths if d["end"] == "death" and d["t"] < 3),
        "stalls": [{"run": r.index + 1, "player": r.player, "detail": e.detail} for r in runs for e in r.events if e.kind == "stall"],
        "loops": [{"run": r.index + 1, "player": r.player, "detail": e.detail} for r in runs for e in r.events if e.kind == "loop"],
        "no_start_runs": sum(r.end == "no_start" for r in runs),
        "start_retries": [e.detail for r in runs for e in r.events if e.kind == "start" and e.detail],
        "unresponsive_inputs": {"checked": len(responses), "no_reaction": len(no_reaction),
                                "examples": no_reaction[:5]},
        "ui_confusion": confusing,
        "controller_errors": [e.detail for r in runs for e in r.events if e.kind == "controller_error"][:5],
        "js_errors": sorted({e for r in runs for e in r.errors})[:10],
    }
