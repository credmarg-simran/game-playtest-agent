"""One playtest session: understand the game, play it with each player, evaluate, report."""

from __future__ import annotations

import contextlib
import io
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageStat

from .browser.server import StaticServer
from .browser.session import BrowserSession
from .evaluation import behavior as behavior_mod
from .evaluation import failures as failures_mod
from .evaluation import fun as fun_mod
from .evaluation import metrics as metrics_mod
from .game.calibrate import calibrate
from .game.profile import GameProfile, profile_from_page, profile_from_source
from .game.project import load_project
from .llm.base import LLM
from .play.records import Event, RunLog
from .play.runner import PlayConfig, Runner
from .policy.random_player import RandomPlayer
from .policy.rule_player import RulePlayer
from .policy.vision_player import VisionPlayer


@dataclass
class Options:
    runs: int = 5
    players: list[str] | None = None
    max_run_seconds: float = 90
    vision_calls_per_run: int = 60
    pixels_only: bool = False  # url mode: ignore DOM/global probes, screenshots only
    headless: bool = True
    viewport: tuple[int, int] = (960, 640)


@dataclass
class ModeResult:
    mode: str  # "code" or "url"
    target: str
    profile: GameProfile
    runs: list[RunLog] = field(default_factory=list)
    hooks: dict = field(default_factory=dict)
    strategies: dict = field(default_factory=dict)
    load: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    per_player: dict = field(default_factory=dict)
    failures: dict = field(default_factory=dict)
    behavior: dict = field(default_factory=dict)
    fun: dict = field(default_factory=dict)
    verdict: dict = field(default_factory=dict)
    build_log: list[str] = field(default_factory=list)
    calibration: list[dict] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # agent-side failures, not game bugs
    mechanics_seen: set[str] = field(default_factory=set)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode, "target": self.target, "profile": self.profile.to_dict(), "load": self.load,
            "hooks": self.hooks, "calibration": self.calibration, "problems": self.problems,
            "strategies": self.strategies, "mechanics_seen": sorted(self.mechanics_seen),
            "metrics": self.metrics, "per_player": self.per_player, "failures": self.failures,
            "behavior": self.behavior, "fun": self.fun, "verdict": self.verdict, "build_log": self.build_log,
            "runs": [x.to_dict() for x in self.runs],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ModeResult":
        """Rebuild a finished mode from report.json, so it can be re-scored without replaying."""
        profile = GameProfile.from_dict(d["profile"], d["profile"].get("source", "blind"))
        profile.hook_check = d["profile"].get("hook_check", {})
        runs = []
        for r in d["runs"]:
            r = dict(r)
            r["events"] = [Event(**e) for e in r["events"]]
            runs.append(RunLog(**r))
        seen = set(d.get("mechanics_seen") or [])
        if not seen:  # older reports only kept the names
            names = set(d.get("behavior", {}).get("mechanics_seen") or [])
            seen = {f"__mech{i}" for i, m in enumerate(profile.mechanics) if m.get("name") in names}
        strategies = d.get("strategies") or {
            name: [{"plan": p} for p in info.get("strategies", [])]
            for name, info in d.get("behavior", {}).get("players", {}).items() if info.get("strategies")
        }
        return cls(mode=d["mode"], target=d["target"], profile=profile, runs=runs, hooks=d.get("hooks", {}),
                   strategies=strategies, load=d.get("load", {}), build_log=d.get("build_log", []),
                   calibration=d.get("calibration", []), problems=d.get("problems", []), mechanics_seen=seen,
                   metrics={"llm_calls": d.get("metrics", {}).get("llm_calls")})


def analyse(result: ModeResult, llm_calls: int | None = None):
    """Everything measured from the runs. No model calls here."""
    calls = llm_calls if llm_calls is not None else result.metrics.get("llm_calls")
    result.metrics = metrics_mod.summarize(result.runs)
    if calls is not None:
        result.metrics["llm_calls"] = calls  # includes profile, repair and controller calls
    result.per_player = metrics_mod.by_player(result.runs)
    result.failures = failures_mod.analyse(result.runs)
    result.behavior = behavior_mod.observe(result.runs, result.profile, result.mechanics_seen, result.strategies)
    result.fun = fun_mod.measure(result.runs, result.profile, result.behavior, result.failures)


def make_player(name: str, persona: str | None, opts: Options):
    if name == "random":
        return RandomPlayer()
    if name == "rules":
        return RulePlayer(persona or "competitive")
    if name == "vision":
        return VisionPlayer(persona or "first_timer", calls_per_run=opts.vision_calls_per_run)
    raise ValueError(f"unknown player {name!r} (use random, rules, vision)")


def _load_check(session: BrowserSession, png: bytes) -> dict:
    img = Image.open(io.BytesIO(png)).convert("L")
    spread = ImageStat.Stat(img).stddev[0]
    return {"blank_screen": spread < 3, "errors_at_load": [e["message"] for e in session.errors()][:5],
            "console_errors": session.console[:5]}


class Playtest:
    def __init__(self, llm: LLM, out: Path, opts: Options):
        self.llm = llm
        self.out = out
        self.opts = opts
        (out / "screenshots").mkdir(parents=True, exist_ok=True)

    def _session(self, url: str) -> BrowserSession:
        return BrowserSession(url, viewport=self.opts.viewport, headless=self.opts.headless)

    def run_mode(self, mode: str, target: str) -> ModeResult:
        print(f"\n== {mode} mode: {target}")
        calls_before = self.llm.usage.calls
        with contextlib.ExitStack() as stack:
            build_log: list[str] = []
            if mode == "code":
                project = load_project(target, self.out / "work")
                build_log = project.log
                server = stack.enter_context(StaticServer(project.serve_dir, isolate=project.isolate))
                url = server.url
                print(f"  engine {project.engine}, {len(project.files)} source files, serving {project.serve_dir}")
                profile = profile_from_source(project, self.llm)
            else:
                url = target
                profile = None

            # A first look at the page: does it load, what is on screen.
            with self._session(url) as s:
                s.open()
                png = s.screenshot(self.out / "screenshots" / f"{mode}-title.png")
                load = _load_check(s, png)
                probe = s.probe()
                if profile is None:
                    profile = profile_from_page(probe, png, self.llm, use_globals=not self.opts.pixels_only)
                calibration = calibrate(s, profile, self.llm) if profile.hooks else []
                for c in calibration:
                    dead = {k: v for k, v in c["status"].items() if v != "live"}
                    print(f"  hook check {c['round']}: {len(c['status']) - len(dead)}/{len(c['status'])} live, not live {dead}"
                          + (f", repaired {c['repaired']}" if c["repaired"] else ""))
            print(f"  profile: {profile.title} / {profile.genre}; controls {[c.name for c in profile.controls]}; "
                  f"hooks {list(profile.hooks)}")

            result = ModeResult(mode=mode, target=target, profile=profile, load=load, build_log=build_log,
                                calibration=calibration)
            names = self.opts.players or (["random", "rules"] if mode == "code" and profile.has_hooks else ["random", "vision"])
            mechanics_seen: set[str] = set()
            for spec in names:
                name, _, persona = spec.partition(":")
                if name == "rules" and not profile.has_hooks:
                    print("  skipping rules player: no score/over hooks")
                    continue
                player = make_player(name, persona or None, self.opts)
                cfg = PlayConfig(runs=self.opts.runs, max_run_seconds=self.opts.max_run_seconds)
                with self._session(url) as s:
                    s.open()
                    runner = Runner(s, profile, player, self.llm, cfg, self.out / "screenshots")
                    runner.reopen = s.open
                    print(f"  playing as {player.name}")
                    try:
                        runner.play()
                    except Exception as e:  # keep the runs we have and still write a report
                        print(f"  {player.name} stopped early: {e.__class__.__name__}: {str(e)[:200]}")
                        result.problems.append(f"{player.name} stopped early: {e.__class__.__name__}: {str(e)[:300]}")
                    result.runs += runner.runs
                    for k, v in runner.hook_report().items():
                        result.hooks[k] = v if result.hooks.get(k, "never").startswith(("never", "constant")) else result.hooks[k]
                    mechanics_seen |= runner.mechanics_seen
                if isinstance(player, RulePlayer):
                    result.strategies[player.name] = player.history
                    print(f"  controller versions: {[(h['version'], h['results'], h['added_hooks']) for h in player.history]}")

        result.mechanics_seen = mechanics_seen
        analyse(result, llm_calls=self.llm.usage.calls - calls_before)
        return result

    def evaluate(self, result: ModeResult, comparison: dict | None):
        shots = []
        for r in result.runs:
            if r.player != "random":
                for sc in r.screenshots:
                    if sc["label"] in ("start", "after-death", "death") and len(shots) < 3:
                        shots.append((self.out / sc["path"]).read_bytes())
        result.verdict = fun_mod.judge(self.llm, result.profile, {"overall": result.metrics, "per_player": result.per_player},
                                       result.failures, result.behavior, result.fun, result.runs, shots, comparison)


def compare(results: list[ModeResult]) -> dict:
    """Side by side numbers for code access versus URL only."""
    rows = {}
    for r in results:
        thinking = {k: v for k, v in r.per_player.items() if k != "random"}
        best = max(thinking.values(), key=lambda m: m.get("avg_score", 0), default={})
        rows[r.mode] = {
            "profile_from": r.profile.source,
            "state_hooks": len(r.profile.hooks),
            "live_hooks": sum(1 for v in r.hooks.values() if v.startswith("live")),
            "players": list(r.per_player),
            "best_thinking_avg_score": best.get("avg_score"),
            "best_thinking_avg_survival_s": best.get("avg_survival_s"),
            "best_score": r.metrics.get("best_score"),
            "llm_calls": r.metrics.get("llm_calls"),
            "measured_fun": r.fun.get("measured_score"),
        }
    return rows


def write_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2, default=str))


def stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")
