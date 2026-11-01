from playtest.evaluation import behavior, failures, fun
from playtest.game.profile import GameProfile
from playtest.play.records import RunLog


def run(i, player, seconds, score, end="death", play_again=None, fair=None):
    r = RunLog(index=i, player=player, seconds=seconds, score=score, end=end)
    for k in range(int(score)):
        r.add(seconds * (k + 1) / (score + 1), "score")
    r.inputs = [(0.5, "tap:Space")] * 5
    r.responses = [{"t": 1, "input": "tap:Space", "pixel_change": 0.02, "state_changed": True, "reacted": True}]
    if play_again:
        r.reflection = {"play_again": play_again, "fair": fair, "cause": "hit a pipe"}
    return r


def score_of(signals, name):
    return signals[name]["score"]


def test_skill_beats_random_scores_high():
    runs = [run(i, "random", 2, 0) for i in range(3)] + [run(i, "rules", 30, 12, play_again=4, fair=True) for i in range(3)]
    profile = GameProfile(controls=[])
    fails = failures.analyse(runs)
    beh = behavior.observe(runs, profile, set(), {})
    signals = fun.measure(runs, profile, beh, fails)["signals"]
    assert score_of(signals, "skill_depth") == 5
    assert score_of(signals, "challenge") == 5
    assert score_of(signals, "replay_pull") == 4
    assert score_of(signals, "fairness") == 5


def test_no_rewards_and_instant_deaths_score_low():
    runs = [run(i, "random", 1, 0) for i in range(3)] + [run(i, "rules", 1.5, 0, play_again=1, fair=False) for i in range(3)]
    profile = GameProfile(controls=[])
    fails = failures.analyse(runs)
    signals = fun.measure(runs, profile, behavior.observe(runs, profile, set(), {}), fails)["signals"]
    assert score_of(signals, "reward_cadence") == 1
    assert score_of(signals, "challenge") <= 2
    assert score_of(signals, "fairness") == 1
    assert fails["instant_deaths"] == 6


def test_budget_runs_do_not_count_as_short_survival():
    runs = [run(0, "vision", 25, 3, end="death"), run(1, "vision", 2, 0, end="budget")]
    profile = GameProfile(controls=[])
    fails = failures.analyse(runs)
    sig = fun.measure(runs, profile, behavior.observe(runs, profile, set(), {}), fails)["signals"]["challenge"]
    assert sig["value"]["median_survival_s"] == 25
    assert sig["value"]["runs_cut_by_call_budget"] == 1
