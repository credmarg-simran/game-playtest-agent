"""Gameplay numbers, per player and overall."""

from __future__ import annotations

from statistics import mean, median

from ..play.records import RunLog


def _round(x, n=2):
    return round(x, n) if isinstance(x, (int, float)) else x


def summarize(runs: list[RunLog]) -> dict:
    played = [r for r in runs if r.end != "no_start"]
    if not runs:
        return {"runs": 0}
    seconds = [r.seconds for r in played] or [0]
    scores = [r.score for r in played] or [0]
    play_minutes = sum(seconds) / 60
    ends = {}
    for r in runs:
        ends[r.end] = ends.get(r.end, 0) + 1
    finished = [r for r in played if r.end in ("death", "win")]
    return {
        "runs": len(runs),
        "ends": ends,
        "avg_survival_s": _round(mean(seconds)),
        "median_survival_s": _round(median(seconds)),
        "best_survival_s": _round(max(seconds)),
        "avg_score": _round(mean(scores)),
        "best_score": _round(max(scores)),
        "win_rate": _round(sum(r.end == "win" for r in finished) / len(finished)) if finished else None,
        "actions_per_minute": _round(sum(len(r.inputs) for r in played) / play_minutes, 1) if play_minutes else 0,
        "avg_time_to_start_s": _round(mean(r.start_seconds for r in runs)),
        "play_seconds_total": _round(sum(seconds), 1),
        "llm_calls": sum(r.llm_calls for r in runs),
    }


def by_player(runs: list[RunLog]) -> dict[str, dict]:
    names = sorted({r.player for r in runs})
    return {n: summarize([r for r in runs if r.player == n]) for n in names}
