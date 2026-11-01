"""Plays a game for several runs with one player and records what happened."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..browser.session import BrowserSession
from ..game.profile import GameProfile
from ..llm.base import LLM
from ..perception.frames import ScreenMemory, change, distance, fingerprint
from ..policy.base import Player, View
from ..policy.vision_player import VisionPlayer
from .records import RunLog

LOOK_SYSTEM = """You check a screenshot of a browser game. Return JSON:
{"phase": "menu" | "playing" | "over" | "loading", "score": number or null (as shown on screen),
 "how_to_continue": "input that would start or restart play, e.g. key:Space or click:0.5,0.6, or ''"}"""


@dataclass
class PlayConfig:
    runs: int = 5
    max_run_seconds: float = 90
    start_attempts: int = 6
    sample_every: int = 15  # frames between recorded state samples
    screen_every: int = 30  # frames between screen fingerprints
    response_every: int = 6  # check every Nth input for a visible reaction
    stall_seconds: float = 20
    look_every: int = 120  # frames between screenshot checks when nothing else can tell us the phase
    settle_frames: int = 150  # frames to let a death animation finish before restarting
    keep_screens: int = 4  # mid-run screenshots kept per run


class Runner:
    def __init__(self, session: BrowserSession, profile: GameProfile, player: Player,
                 llm: LLM | None, cfg: PlayConfig, shots: Path):
        self.s = session
        self.profile = profile
        self.player = player
        self.llm = llm
        self.cfg = cfg
        self.shots = shots
        self.best = None
        self.memory = ScreenMemory()
        self.hook_seen: dict[str, set] = {name: set() for name in profile.hooks}
        self.mechanics_seen: set[str] = set()
        self.reopen = None  # reloads the page when a run ends without a game over screen
        self.mechanic_reads = {f"__mech{i}": m["seen_when"] for i, m in enumerate(profile.mechanics) if m.get("seen_when")}
        player.bind(session, profile, llm)

    # helpers

    def state(self) -> dict:
        # hooks can grow during a session (the rules player adds the ones it finds it needs)
        values = self.s.read({**self.profile.hooks, **self.mechanic_reads})
        for name, value in values.items():
            if name.startswith("__"):
                continue
            seen = self.hook_seen.setdefault(name, set())
            if value is not None and len(seen) < 50:
                seen.add(str(value)[:60])
        return values

    def look(self, png: bytes) -> dict:
        if not self.llm:
            return {}
        return self.llm.ask_json("look", LOOK_SYSTEM, "What is on screen?", images=[png])

    def shot(self, run: RunLog, label: str, t: float) -> bytes:
        name = f"run{run.index + 1:02d}-{run.player}-{label}.png"
        png = self.s.screenshot(self.shots / name)
        run.screenshots.append({"t": round(t, 1), "path": f"screenshots/{name}", "label": label})
        return png

    def _playing(self, state: dict) -> bool | None:
        if "playing" in self.profile.hooks and state.get("playing") is not None:
            return bool(state["playing"]) and not state.get("over")
        if "over" in self.profile.hooks and state.get("over") is not None:
            return not state["over"]
        return None

    # getting into play

    def enter_play(self, run: RunLog, steps: list[str]) -> bool:
        """Press the start/restart sequence until the game says we are in play."""
        steps = steps or self.profile.start or ["key:Space"]
        start = self.s.frame
        hooked = "playing" in self.profile.hooks or "over" in self.profile.hooks
        for attempt in range(self.cfg.start_attempts):
            if hooked and self._playing(self.state()):
                break
            if not hooked and isinstance(self.player, VisionPlayer):
                break  # the vision player sorts out menus itself
            for step in steps:
                inp = self.profile.resolve(step) or self.profile.resolve("key:Space")
                self.s.tap(inp)
                self.s.step(30)
            if hooked:
                # countdowns and "get ready" screens: wait before deciding the press did nothing
                for _ in range(16):
                    if self._playing(self.state()):
                        break
                    self.s.step(15)
            else:
                seen = self.look(self.s.screenshot())
                run.llm_calls += 1
                if seen.get("phase") == "playing":
                    break
                if seen.get("how_to_continue"):
                    steps = [seen["how_to_continue"]]
        else:
            run.start_seconds = self.s.seconds(self.s.frame - start)
            return False
        run.start_seconds = self.s.seconds(self.s.frame - start)
        if attempt > 1:
            run.add(0, "start", f"pressed {steps} {attempt} times before play began")
        return True

    # one run

    def play_run(self, index: int, first: bool) -> RunLog:
        run = RunLog(index=index, player=self.player.name)
        if not self.enter_play(run, self.profile.start if first else self.profile.restart):
            run.end = "no_start"
            self.shot(run, "stuck-at-start", 0)
            run.add(0, "no_start", "could not get into play")
            return run

        cfg, s, hooks = self.cfg, self.s, self.profile.hooks
        begin = s.frame
        state = self.state()
        self.player.begin_run(index, state)
        if self.player.uses_llm and index == 0:
            run.llm_calls += 1
        self.shot(run, "start", 0)
        run.add(0, "start")

        score = state.get("score") if isinstance(state.get("score"), (int, float)) else 0
        last_score_t, last_sample, last_screen, last_look = 0.0, -999, -999, s.frame
        decisions = 0
        lost_at, before_loss, was_playing, death_png = None, [], False, None
        # without a game-over hook the vision player decides when play starts and ends
        seen_play = "over" in hooks or not isinstance(self.player, VisionPlayer)
        waiting_turns = 0
        recent_inputs: list[str] = []
        saved_screens = 0

        while True:
            frame = s.frame
            t = s.seconds(frame - begin)
            state = self.state()

            png = None
            if self.player.wants_screenshot() or frame - last_screen >= cfg.screen_every:
                png = s.screenshot()
                fp = fingerprint(png)
                run.screens.append((round(t, 2), fp))
                if self.memory.add(fp):
                    run.add(t, "new_screen")
                last_screen = frame
                if t > 5 and saved_screens < cfg.keep_screens and t // 10 > saved_screens:
                    self.shot(run, f"t{int(t)}s", t)
                    saved_screens += 1

            if frame - last_sample >= cfg.sample_every:
                run.samples.append({"t": round(t, 2), **self._public(state)})
                last_sample = frame
            for key, value in state.items():
                if key.startswith("__mech") and value and key not in self.mechanics_seen:
                    self.mechanics_seen.add(key)
                    run.add(t, "mechanic", self.profile.mechanics[int(key[6:])].get("name", key))

            new_score = state.get("score")
            if isinstance(new_score, (int, float)) and new_score != score:
                run.add(t, "score", f"{score} -> {new_score}")
                score, last_score_t = new_score, t

            # Many games play a death animation before the game over screen. Survival ends when
            # control is lost, and the state just before that is what explains the death.
            playing = self._playing(state)
            if playing:
                was_playing = True
                lost_at = None
                if not before_loss or t - before_loss[-1]["t"] >= 0.1:
                    before_loss = (before_loss + [{"t": round(t, 2), **self._public(state)}])[-15:]
            elif was_playing and playing is False and lost_at is None:
                lost_at = t
                death_png = self.shot(run, "death", t)

            # is the run over?
            over = bool(state.get("over")) if "over" in hooks and state.get("over") is not None else False
            if lost_at is not None and t - lost_at > 6:
                over = True  # control never came back
            if "over" not in hooks and not self.player.wants_screenshot() and frame - last_look >= cfg.look_every:
                seen = self.look(png or s.screenshot())
                run.llm_calls += 1
                last_look = frame
                over = seen.get("phase") == "over"
                if isinstance(seen.get("score"), (int, float)) and seen["score"] != score:
                    run.add(t, "score", f"{score} -> {seen['score']}")
                    score, last_score_t = seen["score"], t
            if over:
                run.end = "death"
                break
            if t >= cfg.max_run_seconds:
                run.end = "timeout"
                break
            if t - last_score_t > cfg.stall_seconds and self._screen_static(run):
                run.end = "stall"
                run.add(t, "stall", f"no score change for {t - last_score_t:.0f}s and the screen stopped changing")
                break

            decision = self.player.decide(View(t=t, frame=frame, state=state, screenshot=png))
            decisions += 1
            if self.player.uses_llm and isinstance(self.player, VisionPlayer):
                run.llm_calls += 1
                if decision.score is not None and "score" not in hooks and decision.score != score:
                    run.add(t, "score", f"{score} -> {decision.score}")
                    score, last_score_t = decision.score, t
                if not seen_play:
                    if decision.phase == "playing":
                        seen_play, begin = True, frame  # the clock for this run starts now
                    else:
                        waiting_turns += 1
                        if waiting_turns > 15:
                            run.end = "no_start"
                            run.add(t, "no_start", f"still on '{decision.phase}' after 15 looks")
                            break
                elif decision.phase == "over" and "over" not in hooks:
                    run.end = "death"
                    break
                if self.player.out_of_budget:
                    run.end = "budget"
                    break
            if decision.note and decision.note.startswith(("ReferenceError", "TypeError", "SyntaxError")):
                run.add(t, "controller_error", decision.note[:120])

            names = [i.name for i in decision.inputs]
            for name in names:
                run.inputs.append((round(t, 2), name))
            recent_inputs.append(",".join(names))
            if len(recent_inputs) >= 12 and len(set(recent_inputs[-12:])) == 1 and recent_inputs[-1] \
                    and t - last_score_t > 5 and run.count("loop") < 3:
                run.add(t, "loop", f"same input {recent_inputs[-1]} 12 times with no progress")

            if decision.inputs and decisions % cfg.response_every == 0:
                self._check_response(run, t, decision.inputs, state)
            else:
                used = s.perform(decision.inputs)
                s.step(max(1, decision.wait - used))

        s.release_all()
        run.seconds = round(lost_at if lost_at is not None and run.end == "death" else s.seconds(s.frame - begin), 2)
        run.score = score or 0
        run.death = {"t": run.seconds, "state": before_loss[-1] if before_loss else self._public(state),
                     "last_moments": before_loss}
        final = self.shot(run, run.end, run.seconds)
        if run.end == "death":
            s.step(cfg.settle_frames)
            final = self.shot(run, "after-death", run.seconds)

        if self.best is None or run.score > self.best:
            if self.best is not None:
                run.add(run.seconds, "new_best", str(run.score))
            self.best = run.score
        run.errors = [f"{e['where']}: {e['message']}" for e in s.errors(begin)][:20]
        for e in run.errors[:5]:
            run.add(run.seconds, "error", e[:120])

        if self.player.uses_llm and self.llm:
            run.reflection = self.player.reflect(run, death_png or final)
            run.llm_calls += 1
        return run

    @staticmethod
    def _public(state: dict) -> dict:
        return {k: v for k, v in state.items() if not k.startswith("__")}

    def _screen_static(self, run: RunLog) -> bool:
        recent = [fp for _, fp in run.screens[-6:]]
        return len(recent) >= 6 and max(distance(recent[0], fp) for fp in recent) <= 2

    def _check_response(self, run: RunLog, t: float, inputs, state_before: dict):
        """Did the game visibly react to this input within a few frames?"""
        before = self.s.screenshot()
        used = self.s.perform(inputs)
        self.s.step(max(1, 8 - used))
        after = self.s.screenshot()
        state_after = self.state()
        moved = change(before, after)
        state_changed = any(state_after.get(k) != state_before.get(k) for k in self.profile.hooks)
        run.responses.append({
            "t": round(t, 2), "input": inputs[0].name, "pixel_change": round(moved, 4),
            "state_changed": state_changed, "reacted": moved > 0.004 or state_changed,
        })

    def play(self) -> list[RunLog]:
        runs = []
        fresh = True
        for i in range(self.cfg.runs):
            run = self.play_run(i, first=fresh)
            runs.append(run)
            print(f"  {self.player.name} run {i + 1}: {run.end} after {run.seconds:.1f}s, score {run.score}")
            if run.end == "no_start" and i > 0 and runs[-2].end == "no_start":
                break
            # Timeouts, stalls and budget stops leave the game mid-play, so start clean.
            fresh = run.end not in ("death", "win")
            if fresh and self.reopen:
                self.reopen()
        return runs

    def hook_report(self) -> dict:
        report = {}
        for name, values in self.hook_seen.items():
            if not values:
                report[name] = "never returned a value"
            elif len(values) == 1:
                report[name] = f"constant ({next(iter(values))})"
            else:
                report[name] = f"live ({len(values)}+ distinct values)"
        return report
