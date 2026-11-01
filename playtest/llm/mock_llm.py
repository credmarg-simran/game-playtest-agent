"""Deterministic stand-in for a real model, so the pipeline and tests run offline.

It is not smart: it returns well-formed answers of the right shape, with a little awareness of the
bundled rock-dodge sample so the test runs exercise real state hooks.
"""

from __future__ import annotations

import json
import random

from .base import LLM

ROCK_DODGE_PROFILE = {
    "title": "Rock Dodge", "genre": "arcade dodger", "goal": "Dodge falling rocks for as long as possible.",
    "how_you_lose": "A rock hits the player.", "how_you_win": "",
    "controls": [
        {"name": "left", "key": "ArrowLeft", "hold": True, "does": "move left"},
        {"name": "right", "key": "ArrowRight", "hold": True, "does": "move right"},
        {"name": "start", "key": "Space", "hold": False, "does": "start or restart"},
    ],
    "start": ["start"], "restart": ["start"],
    "hooks": {
        "score": "window.state && state.score", "over": "window.state && state.over",
        "playing": "window.state && state.playing", "player_x": "window.state && state.playerX",
        "rocks": "window.state && state.rocks.filter(r => r.y > 200).map(r => [Math.round(r.x), Math.round(r.y)])",
    },
    "mechanics": [{"name": "speed ramps up", "detail": "rocks fall faster with score", "seen_when": "window.state && state.score >= 15"}],
    "difficulty": "Rock speed is 2 + score/15 px per frame.", "tips": "Stay away from rocks that are low on screen.",
}

GENERIC_PROFILE = {
    "title": "Unknown game", "genre": "unknown", "goal": "Unknown.", "how_you_lose": "", "how_you_win": "",
    "controls": [{"name": "action", "key": "Space", "hold": False, "does": "primary action"},
                 {"name": "tap", "click": [0.5, 0.5], "hold": False, "does": "click the middle"}],
    "start": ["action"], "restart": ["action"], "hooks": {}, "mechanics": [], "difficulty": "", "tips": "",
}

DODGE_RULE = """
const rocks = s.rocks || [];
const x = s.player_x || 240;
const near = rocks.filter(r => Math.abs(r[0] - x) < 40);
if (!near.length) return [];
const threat = near.reduce((a, b) => (b[1] > a[1] ? b : a));
return threat[0] > x ? ['left'] : ['right'];
"""


class MockLLM(LLM):
    name = "mock"

    def __init__(self, seed=1):
        super().__init__()
        self.rng = random.Random(seed)

    def ask_json(self, task, system, prompt, images=()):
        self.usage.add(task, len(system + prompt) // 4 + 85 * len(images), 120)
        if task == "profile":
            return json.loads(json.dumps(ROCK_DODGE_PROFILE if "state.rocks" in prompt or "Rock Dodge" in prompt else GENERIC_PROFILE))
        if task == "strategy":
            return {"plan": "Step away from the lowest rock above me.", "every": 3, "body": DODGE_RULE, "hooks": {}}
        if task == "play":
            return {"phase": "playing", "score": None, "inputs": [self.rng.choice(["hold:ArrowLeft", "hold:ArrowRight", "key:Space"])],
                    "wait": 12, "thought": "mock move"}
        if task == "look":
            return {"phase": "playing", "score": None, "how_to_continue": "key:Space"}
        if task == "reflect":
            out = {"cause": "hit by an obstacle", "fair": True, "learned": "obstacles speed up", "fun_moment": "",
                   "frustration": "", "confusing": "", "play_again": 3, "why": "mock answer"}
            if "improved controller" in prompt:
                out.update({"plan": "Same rule.", "every": 3, "body": DODGE_RULE})
            return out
        if task == "judge":
            data = json.loads(prompt[: prompt.rfind("}") + 1]) if prompt.strip().startswith("{") else {}
            signals = data.get("fun_signals", {})
            dims = {k: {"score": v["score"], "why": f"measured: {json.dumps(v['value'])[:120]}"} for k, v in signals.items()}
            avg = sum(d["score"] for d in dims.values()) / len(dims) if dims else 3
            return {"fun_score": round(avg * 2), "verdict": "fun" if avg >= 3.5 else "mixed" if avg >= 2.5 else "not fun",
                    "headline": "Mock verdict built from the measured scorecard only.", "dimensions": dims,
                    "death_causes": [], "observations": [], "suggestions": [], "with_vs_without_code": ""}
        return {}
