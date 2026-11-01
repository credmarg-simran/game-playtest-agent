"""Check the state hooks against the running game before playing for real, and repair broken ones.

Reading code gets the hooks mostly right. Mostly is not enough for a controller that steers by them,
so we play a few seconds, look at what every hook actually returned, and ask the model to fix the
ones that are dead or stuck.
"""

from __future__ import annotations

import json
import re

from ..browser.session import BrowserSession
from ..llm.base import LLM
from .profile import GameProfile

REPAIR_SYSTEM = """Some state hooks for a browser game playtester are not working. Each hook is a JS expression
evaluated in the page's global scope every few frames. You get the game source, the current hooks, and
what each hook returned over a few seconds of play (the player was getting into the game and pressing the
main control now and then).

Fix hooks that returned null the whole time or stayed stuck on a placeholder value while the thing they
describe was clearly changing. Leave hooks that are legitimately constant (e.g. a player x that never
moves). Check object names, property paths and how the engine stores its objects in the source.
Obstacle hooks should describe the obstacle the player has not fully passed yet, with edges taken from the
solid colliding objects (not score triggers).

Return JSON: {"hooks": {"name": "fixed JS expression"}, "notes": "what was wrong"}"""


# Things that should move while someone plays. A constant value here means the hook is broken.
MOVING = {"x", "y", "dx", "dy", "top", "bottom", "left", "right", "gap", "pipe", "enemy", "enemies", "obstacle",
          "distance", "dist", "pos", "position", "center", "centre", "bird", "ball", "next"}


def _words(name: str) -> set[str]:
    """playerTop, player_top, gap-center-y -> {"player", "top"}, {"gap", "center", "y"}"""
    return {w.lower() for w in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", name)}


def _should_move(name: str) -> bool:
    return bool(_words(name) & MOVING)


def _status(values: list) -> str:
    seen = {json.dumps(v, sort_keys=True, default=str) for v in values if v is not None}
    if not seen:
        return "null"
    if len(seen) == 1:
        return f"constant {next(iter(seen))[:40]}"
    return "live"


def _sample(session: BrowserSession, profile: GameProfile, seconds: float) -> dict[str, list]:
    session.open()
    for step in profile.start or ["key:Space"]:
        inp = profile.resolve(step)
        if inp:
            session.tap(inp)
            session.step(30)
    main = profile.controls[0].to_input() if profile.controls else None
    values: dict[str, list] = {name: [] for name in profile.hooks}
    frames = int(seconds * session.fps)
    for i in range(0, frames, 10):
        state = session.read(profile.hooks)
        for name in values:
            values[name].append(state.get(name))
        if main and i % 30 == 0 and not main.hold:
            session.tap(main)
        session.step(10)
    return values


def calibrate(session: BrowserSession, profile: GameProfile, llm: LLM, seconds=5.0, rounds=2) -> list[dict]:
    """Returns one entry per round: hook statuses and what was repaired."""
    log = []
    for round_no in range(rounds + 1):
        values = _sample(session, profile, seconds)
        status = {name: _status(v) for name, v in values.items()}
        suspect = [n for n, st in status.items() if st == "null" or (st.startswith("constant") and _should_move(n))]
        entry = {"round": round_no + 1, "status": status, "repaired": []}
        log.append(entry)
        if not suspect or round_no == rounds or not profile.code:
            break
        prompt = json.dumps({
            "hooks": profile.hooks,
            "observed": {n: {"status": status[n], "values": values[n][::3][:12]} for n in profile.hooks},
            "suspect": suspect,
        }, default=str) + "\n\nGame source:\n" + profile.code[:40_000]
        fixed = llm.ask_json("repair_hooks", REPAIR_SYSTEM, prompt).get("hooks")
        fixed = {k: v for k, v in fixed.items() if k in suspect and isinstance(v, str) and v.strip()} if isinstance(fixed, dict) else {}
        if not fixed:
            break
        profile.hooks.update(fixed)
        entry["repaired"] = list(fixed)
    profile.hook_check = log[-1]["status"]
    return log
