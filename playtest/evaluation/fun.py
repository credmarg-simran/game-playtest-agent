"""Judging fun. Measure first, then let a model reason over the measurements.

Each dimension gets a raw signal from the logs and a 1-5 score from fixed thresholds, so the
scorecard is reproducible and the model cannot talk a boring game up. The judge then reads the
numbers, the players' own reflections and a few screenshots and writes the verdict, and it has to
cite numbers for every claim.
"""

from __future__ import annotations

import json
from statistics import mean, median

from ..game.profile import GameProfile
from ..llm.base import LLM
from ..play.records import RunLog

DIMENSIONS = {
    "clarity": "Can a new player tell what to do and get going quickly?",
    "feedback": "Does the game visibly react to the player's inputs?",
    "challenge": "Is the difficulty in a range where runs are neither instant deaths nor endless?",
    "skill_depth": "Does deliberate play beat button mashing by a clear margin?",
    "learnability": "Does a player get better across runs?",
    "reward_cadence": "How often does the player get a reward (points, progress)?",
    "variety": "Does the game keep showing new things, or is it the same screen forever?",
    "fairness": "When the player loses, could they have avoided it?",
    "replay_pull": "Do players want another run?",
    "delivers_design": "Do the mechanics in the code actually show up in play?",
}


def _scale(value, steps: list[tuple[float, int]], default=1) -> int:
    """steps: [(threshold, score)] checked in order, first value >= threshold wins."""
    for threshold, score in steps:
        if value >= threshold:
            return score
    return default


def _signal(value, score, basis):
    return {"value": value, "score": score, "basis": basis}


def measure(runs: list[RunLog], profile: GameProfile, behavior: dict, failures: dict) -> dict:
    played = [r for r in runs if r.end != "no_start"]
    skilled = [r for r in played if r.player != "random"]
    baseline = [r for r in played if r.player == "random"]
    signals: dict[str, dict] = {}

    # clarity: time from title screen into play, and to the first point, for the first thinking run
    first = next((r for r in runs if r.player != "random"), runs[0] if runs else None)
    if first:
        first_score = next((e.t for e in first.events if e.kind == "score"), None)
        penalty = len(failures["start_retries"]) + 2 * failures["no_start_runs"] + len(failures["ui_confusion"])
        if first_score is None:
            to_point, score = "none in first run", 1
        else:
            to_point = round(first.start_seconds + first_score, 1)
            score = _scale(-to_point, [(-5, 5), (-10, 4), (-20, 3), (-40, 2)]) - min(2, penalty)
        signals["clarity"] = _signal(
            {"seconds_to_first_point": to_point, "start_retries": len(failures["start_retries"]),
             "runs_that_never_started": failures["no_start_runs"], "confusion_notes": len(failures["ui_confusion"])},
            max(1, score), "first point within 5s = 5, 10s = 4, 20s = 3, 40s = 2, none = 1; minus retries/confusion")

    # feedback: share of sampled inputs that changed the screen or the state within 8 frames
    responses = [x for r in played for x in r.responses]
    if responses:
        share = sum(x["reacted"] for x in responses) / len(responses)
        signals["feedback"] = _signal(
            {"inputs_checked": len(responses), "reacted_share": round(share, 2)},
            _scale(share, [(0.9, 5), (0.75, 4), (0.5, 3), (0.3, 2)]), "reacted share >= .9 = 5, .75 = 4, .5 = 3, .3 = 2")

    # challenge: median survival of thinking players, punished at both ends
    if skilled:
        # runs cut short by the call budget say nothing about difficulty, so leave them out
        judged = [r for r in skilled if r.end != "budget"] or skilled
        surv = median(r.seconds for r in judged)
        instant = sum(r.seconds < 3 for r in judged if r.end == "death") / len(judged)
        timeouts = sum(r.end == "timeout" for r in judged) / len(judged)
        if timeouts >= 0.8:
            score = 2
        else:
            score = _scale(surv, [(20, 5), (10, 4), (5, 3), (2, 2)])
            score -= 1 if instant > 0.3 else 0
        signals["challenge"] = _signal(
            {"median_survival_s": round(surv, 1), "instant_death_share": round(instant, 2), "timeout_share": round(timeouts, 2),
             "runs_cut_by_call_budget": sum(r.end == "budget" for r in skilled)},
            max(1, score), "median survival >= 20s = 5, 10s = 4, 5s = 3, 2s = 2; -1 if >30% die in 3s; 2 if almost all runs time out")

    # skill depth: thinking players versus the random control group
    if skilled and baseline:
        s_avg, b_avg = mean(r.score for r in skilled), mean(r.score for r in baseline)
        s_surv, b_surv = mean(r.seconds for r in skilled), mean(r.seconds for r in baseline)
        ratio = (s_avg + 1) / (b_avg + 1)
        surv_ratio = s_surv / max(b_surv, 0.1)
        signals["skill_depth"] = _signal(
            {"skilled_avg_score": round(s_avg, 2), "random_avg_score": round(b_avg, 2), "score_ratio": round(ratio, 2),
             "survival_ratio": round(surv_ratio, 2)},
            _scale(max(ratio, surv_ratio * 0.8), [(3, 5), (2, 4), (1.3, 3), (1.05, 2)]),
            "(skilled+1)/(random+1) score ratio >= 3 = 5, 2 = 4, 1.3 = 3, 1.05 = 2")

    # learnability: did the thinking player's later runs beat its early ones
    for name, info in behavior["players"].items():
        scores = info["score_by_run"]
        if name != "random" and len(scores) >= 3:
            half = len(scores) // 2
            early, late = mean(scores[:half]), mean(scores[half:])
            gain = (late - early) / (abs(early) + 1)
            signals["learnability"] = _signal(
                {"player": name, "early_avg": round(early, 2), "late_avg": round(late, 2),
                 "trend_per_run": info["score_trend_per_run"]},
                _scale(gain, [(1, 5), (0.4, 4), (0.1, 3), (-0.1, 2)]), "relative gain late vs early runs >= 1 = 5, .4 = 4, .1 = 3, flat = 2")
            break

    # reward cadence: gaps between score changes while playing
    gaps = []
    for r in played:
        ts = [0.0] + r.times("score") + [r.seconds]
        gaps += [b - a for a, b in zip(ts, ts[1:]) if b > a]
    if played:
        rewarded = sum(r.count("score") for r in played)
        med_gap = median(gaps) if gaps and rewarded else None
        score = 1 if not rewarded else _scale(-med_gap, [(-3, 5), (-6, 4), (-12, 3), (-25, 2)])
        signals["reward_cadence"] = _signal(
            {"score_events": rewarded, "median_gap_s": round(med_gap, 2) if med_gap else None,
             "longest_gap_s": round(max(gaps), 1) if gaps else None},
            score, "median gap between rewards <= 3s = 5, 6s = 4, 12s = 3, 25s = 2; none = 1")

    # variety: distinct screens per minute of play, and how late new ones still appear
    if played:
        minutes = sum(r.seconds for r in played) / 60 or 1
        new_screens = [e.t for r in played for e in r.events if e.kind == "new_screen"]
        per_min = len(new_screens) / minutes
        longest = max(played, key=lambda r: r.seconds)
        late_new = [e.t for e in longest.events if e.kind == "new_screen" and e.t > longest.seconds / 2]
        score = _scale(per_min, [(6, 5), (3, 4), (1.5, 3), (0.5, 2)])
        signals["variety"] = _signal(
            {"distinct_screens": len(new_screens), "per_minute": round(per_min, 2),
             "new_screens_in_second_half_of_longest_run": len(late_new)},
            score, "distinct screens per minute >= 6 = 5, 3 = 4, 1.5 = 3, .5 = 2")

    # fairness: the players' own judgement of each death
    judged = [r.reflection.get("fair") for r in played if r.end == "death" and isinstance(r.reflection.get("fair"), bool)]
    if judged:
        share = sum(judged) / len(judged)
        signals["fairness"] = _signal(
            {"deaths_judged": len(judged), "avoidable_share": round(share, 2), "instant_deaths": failures["instant_deaths"]},
            _scale(share, [(0.9, 5), (0.7, 4), (0.5, 3), (0.3, 2)]), "share of deaths the player says were avoidable")

    # replay pull: the players' 1-5 answer to "play again?"
    votes = [v for info in behavior["players"].values() for v in info["play_again"] if isinstance(v, (int, float))]
    if votes:
        avg = mean(votes)
        signals["replay_pull"] = _signal({"answers": votes, "average": round(avg, 2)}, round(avg), "average answer to 'play again?'")

    # delivers design: mechanics from the code that players actually ran into
    if behavior["mechanics_checked"]:
        share = len(behavior["mechanics_seen"]) / behavior["mechanics_checked"]
        signals["delivers_design"] = _signal(
            {"seen": behavior["mechanics_seen"], "never_seen": behavior["mechanics_never_seen"]},
            _scale(share, [(0.9, 5), (0.7, 4), (0.5, 3), (0.25, 2)]), "share of coded mechanics reached in play")

    scores = [s["score"] for s in signals.values()]
    return {"signals": signals, "measured_score": round(mean(scores), 2) if scores else None}


JUDGE_SYSTEM = """You are a senior game designer reviewing an automated playtest. You get the game profile,
gameplay metrics, failure analysis, behaviour observations, measured fun signals with 1-5 scores, the
players' own reflections, and a few screenshots.

Decide whether this game is fun for a human, and why. Rules:
- Every claim must cite a number or a quote from the data. No generic advice.
- You may move a dimension score by at most 1 from the measured score, and only with a stated reason.
- The players are bots: separate "the game is hard/boring" from "the bot is bad at it".
- Suggestions must be concrete changes a developer can make (a value, a screen, a rule), tied to evidence.
- Only quote internal values (speeds, gravity, sizes) that appear in the profile or data. Without source code,
  describe changes relative to what was observed instead of inventing numbers.
- A stall or loop by the random player usually means the bot sat on a menu or mashed a useless key. Call it a
  game bug only if a thinking player hit it too or the data shows the game itself froze.

Return JSON:
{"fun_score": 1-10, "verdict": "fun" | "mixed" | "not fun", "headline": "one sentence",
 "dimensions": {"<dimension>": {"score": 1-5, "why": "cites numbers"}},
 "death_causes": [{"cause": "grouped cause", "count": int}],
 "observations": ["behavioural observation with evidence"],
 "suggestions": [{"area": "difficulty|balance|ux|tutorial|feedback|camera|content|bug", "change": "...", "why": "evidence"}],
 "with_vs_without_code": "what code access changed in this playtest, or ''"}"""


def judge(llm: LLM, profile: GameProfile, metrics: dict, failures: dict, behavior: dict, fun: dict,
          runs: list[RunLog], images: list[bytes], comparison: dict | None = None) -> dict:
    reflections = [{"player": r.player, "run": r.index + 1, **r.reflection} for r in runs if r.reflection]
    payload = {
        "game": profile.to_dict() | {"hook_check": None},
        "metrics": metrics,
        "failures": {k: v for k, v in failures.items() if k != "ends"} | {"ends": failures["ends"][:20]},
        "behavior": behavior,
        "fun_signals": fun["signals"],
        "dimension_meaning": {k: v for k, v in DIMENSIONS.items() if k in fun["signals"]},
        "reflections": reflections[:20],
        "mode_comparison": comparison,
    }
    return llm.ask_json("judge", JUDGE_SYSTEM, json.dumps(payload, default=str)[:60_000], images=images[:4])
