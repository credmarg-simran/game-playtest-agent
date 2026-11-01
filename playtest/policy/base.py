from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..browser.session import BrowserSession, Input
from ..game.profile import GameProfile
from ..llm.base import LLM
from ..play.records import RunLog

PERSONAS = {
    "first_timer": "You are a curious first-time player. You have never seen this game. You learn by trying "
                   "things and you notice when the game does not explain itself.",
    "competitive": "You are a competitive player who wants the highest score. You think about timing and risk.",
}

REFLECT_SYSTEM = """You just finished one run of a browser game as a playtester. Look at the facts of the run
and the screenshot from the moment you lost control, then answer honestly as the player. Keep two things
apart: problems with the game itself, and problems with your own play or your controller (bad timing,
missing state data). Only the first kind belongs in "frustration" and "confusing". Return JSON:
{"cause": "what ended the run, concretely", "fair": bool (could a careful player have avoided it?),
 "learned": "one thing you now understand about the game", "fun_moment": "best moment, or ''",
 "frustration": "worst moment, or ''", "confusing": "anything unclear about the rules or UI, or ''",
 "play_again": 1-5 (how much you want another run), "why": "short reason"}"""


@dataclass
class View:
    t: float
    frame: int
    state: dict = field(default_factory=dict)
    screenshot: bytes | None = None


@dataclass
class Decision:
    inputs: list[Input] = field(default_factory=list)
    wait: int = 6
    phase: str | None = None  # what the player thinks is on screen: menu, playing, over
    score: float | None = None
    note: str = ""


class Player:
    name = "player"
    uses_llm = False

    def bind(self, session: BrowserSession, profile: GameProfile, llm: LLM | None):
        self.session = session
        self.profile = profile
        self.llm = llm

    def wants_screenshot(self) -> bool:
        return False

    def begin_run(self, index: int, first_state: dict):
        pass

    def decide(self, view: View) -> Decision:
        raise NotImplementedError

    def reflect(self, run: RunLog, final_png: bytes) -> dict:
        return {}

    def _reflect(self, run: RunLog, final_png: bytes, extra: str = "") -> dict:
        facts = {
            "game": self.profile.summary(),
            "run": run.index + 1,
            "seconds_survived": round(run.seconds, 1),
            "score": run.score,
            "ended_by": run.end,
            "inputs_used": len(run.inputs),
            "last_inputs": run.inputs[-8:],
            "state_in_the_last_moments_before_losing_control": run.death.get("last_moments") or run.samples[-5:],
            "events": [f"{e.t}s {e.kind} {e.detail}".strip() for e in run.events[-12:]],
        }
        prompt = json.dumps(facts, default=str) + extra
        return self.llm.ask_json("reflect", REFLECT_SYSTEM, prompt, images=[final_png])
