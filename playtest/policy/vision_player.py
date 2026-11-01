"""Plays from screenshots, the way a person would. Works with or without source code.

The game is paused while the model looks, so even a twitchy game is playable; the cost is one model
call per look, which is why each run has a call budget.
"""

from __future__ import annotations

import json

from ..browser.session import Input
from .base import PERSONAS, Decision, Player, View

PLAY_SYSTEM = """You are playing a browser game through screenshots. The game is paused while you decide.
{persona}
Each turn you see the current screen. Choose inputs to press right now and how many frames to let pass
before you look again (60 frames = 1 second; use small waits like 6-15 when things move fast).

Inputs can be control names from the game profile, or raw ones: "key:<Playwright key>" such as "key:Space",
"key:ArrowLeft", or "click:x,y" with x and y as fractions of the game area (0..1). Add "hold:" in front of a
key input to keep it held until your next turn.

Return JSON: {{"phase": "menu" | "playing" | "over", "score": number or null (as shown on screen),
"inputs": [str], "wait": int, "thought": "short reason"}}"""


class VisionPlayer(Player):
    name = "vision"
    uses_llm = True

    def __init__(self, persona="first_timer", calls_per_run=40):
        self.persona = persona
        self.calls_per_run = calls_per_run
        self.recent: list[str] = []
        self.calls = 0

    def wants_screenshot(self):
        return True

    def begin_run(self, index, first_state):
        self.calls = 0
        self.recent = []

    @property
    def out_of_budget(self) -> bool:
        return self.calls >= self.calls_per_run

    def _parse(self, raw: str) -> Input | None:
        raw = str(raw).strip()
        hold = raw.startswith("hold:")
        if hold:
            raw = raw[5:]
        inp = self.profile.resolve(raw)
        if inp and hold and inp.kind == "key":
            inp.hold = True
        return inp

    def decide(self, view: View) -> Decision:
        self.calls += 1
        prompt = json.dumps({
            "game_profile": self.profile.summary() if self.profile.controls else "unknown, figure it out",
            "time_s": round(view.t, 1),
            "state": view.state or None,
            "your_last_turns": self.recent[-4:],
        }, default=str)
        system = PLAY_SYSTEM.format(persona=PERSONAS[self.persona])
        data = self.llm.ask_json("play", system, prompt, images=[view.screenshot] if view.screenshot else [])
        inputs = [i for i in (self._parse(r) for r in data.get("inputs") or []) if i]
        try:
            wait = max(2, min(90, int(data.get("wait", 10))))
        except (TypeError, ValueError):
            wait = 10
        thought = str(data.get("thought", ""))[:160]
        self.recent.append(f"t={view.t:.1f}s saw {data.get('phase')}, pressed {[i.name for i in inputs]}: {thought}")
        score = data.get("score")
        return Decision(
            inputs=inputs, wait=wait, phase=data.get("phase"),
            score=score if isinstance(score, (int, float)) else None, note=thought,
        )

    def reflect(self, run, final_png):
        return self._reflect(run, final_png)
