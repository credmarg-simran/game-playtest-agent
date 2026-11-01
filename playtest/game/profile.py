"""What the agent believes about a game before it plays: controls, goal, where state lives.

Built either from source code (accurate, with state hooks) or from the running page alone
(a screenshot plus whatever the DOM and globals give away).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

from ..browser.session import Input
from ..llm.base import LLM
from .project import Project

PROFILE_SHAPE = """{
  "title": str, "genre": str, "goal": str (one sentence, what the player tries to do),
  "how_you_lose": str, "how_you_win": str ("" if endless),
  "controls": [{"name": short verb like "jump", "key": Playwright key name like "Space" / "ArrowLeft" / "KeyA" (or ""),
                "click": [x, y] as fractions of the canvas (or null), "hold": bool, "does": str}],
  "start": [control names or "key:Enter" / "click:0.5,0.5", pressed in order to get from the title screen into play],
  "restart": [same format, to get from the game over screen back into play],
  "hooks": {name: JS expression},
  "mechanics": [{"name": str, "detail": str, "seen_when": JS expression that is true once the player has experienced it, or ""}],
  "difficulty": str (what makes it hard, numbers from code if any: speeds, gaps, timers),
  "tips": str (advice a good player would follow)
}"""

SOURCE_SYSTEM = f"""You are a game QA engineer. You read the source of a browser game and write a profile
that an automated playtester will use. Be precise: the playtester will press exactly the keys you name and
evaluate your JS expressions in the page every few frames.

Hook rules:
- Expressions run in the page's global scope. A variable named `game` holds the engine instance when one was
  captured (Phaser.Game). For melonJS use globals such as `me.state` and the game's own global object.
- Module-scoped variables in bundled code are NOT reachable. Reach state through `game` (for Phaser use
  `game.scene.getScene('Key')` or `game.scene.getScenes(true)[0]`) or through real globals.
- Expressions must not throw when the object is missing: guard with `&&` or optional chaining.
- Required hooks: "score" (number), "over" (true only on the game over / death state), "playing" (true while
  the player is in control). Add hooks the player needs to play well: player position, nearest obstacle or
  enemy positions, lives, level, timer. Make them decision-ready: give the numbers a player compares, in the
  same coordinate system (e.g. the player's top and bottom edge, and the next gap's top and bottom edge in
  screen pixels, worked out from the sprite sizes in the code). Keep each expression short and cheap.
- Obstacles: an obstacle is still "next" until its far edge is behind the player's near edge, not when a
  score trigger fires. Safe-gap edges come from the solid colliding objects, not from invisible score zones.

Return JSON with exactly this shape:
{PROFILE_SHAPE}"""

BLIND_SYSTEM = f"""You are a game QA engineer looking at a browser game for the first time. You have no source
code: only a screenshot, the page's visible text, its buttons and any global variables with game-like names.
Guess the profile a playtester needs. Prefer what the screen tells you. If a global clearly holds the score
or game state, you may use it as a hook (JS expression in global scope), otherwise leave hooks empty.
Use the standard hook names where you can: "score", "playing" (true only while the player is in control)
and "over" (true only on game over / death).

Return JSON with exactly this shape:
{PROFILE_SHAPE}"""


@dataclass
class Control:
    name: str
    key: str = ""
    click: list[float] | None = None
    hold: bool = False
    does: str = ""

    def to_input(self) -> Input:
        if self.key or not self.click:
            return Input("key", key=self.key or "Space", hold=self.hold)
        return Input("click", x=float(self.click[0]), y=float(self.click[1]))


@dataclass
class GameProfile:
    title: str = "Unknown game"
    genre: str = ""
    goal: str = ""
    how_you_lose: str = ""
    how_you_win: str = ""
    controls: list[Control] = field(default_factory=list)
    start: list[str] = field(default_factory=list)
    restart: list[str] = field(default_factory=list)
    hooks: dict[str, str] = field(default_factory=dict)
    mechanics: list[dict] = field(default_factory=list)
    difficulty: str = ""
    tips: str = ""
    source: str = "blind"  # "code" or "blind"
    hook_check: dict[str, str] = field(default_factory=dict)
    code: str = field(default="", repr=False)  # source the profile was built from, for later calls

    @classmethod
    def from_dict(cls, d: dict, source: str) -> "GameProfile":
        controls = []
        for c in d.get("controls") or []:
            if isinstance(c, dict) and c.get("name"):
                controls.append(Control(
                    name=str(c["name"]), key=c.get("key") or "", click=c.get("click") or None,
                    hold=bool(c.get("hold")), does=c.get("does") or "",
                ))
        hooks = {k: v for k, v in (d.get("hooks") or {}).items() if isinstance(v, str) and v.strip()}
        return cls(
            title=d.get("title") or "Unknown game", genre=d.get("genre") or "", goal=d.get("goal") or "",
            how_you_lose=d.get("how_you_lose") or "", how_you_win=d.get("how_you_win") or "",
            controls=controls, start=list(d.get("start") or []), restart=list(d.get("restart") or []),
            hooks=hooks, mechanics=[m for m in d.get("mechanics") or [] if isinstance(m, dict)],
            difficulty=d.get("difficulty") or "", tips=d.get("tips") or "", source=source,
        )

    def control(self, name: str) -> Control | None:
        return next((c for c in self.controls if c.name == name), None)

    def resolve(self, step: str) -> Input | None:
        """Turn "jump", "key:Enter" or "click:0.5,0.6" into an Input.

        Models sometimes write alternatives ("key:Space or click:0.5,0.5"); take the first one.
        """
        step = re.split(r"\s+or\s+|\s*\|\s*|\s*/\s*", str(step).strip())[0].strip()
        if step.startswith("key:"):
            return Input("key", key=step[4:].strip() or "Space")
        if step.startswith("click:"):
            try:
                x, y = (float(v) for v in step[6:].split(","))
                return Input("click", x=x, y=y)
            except ValueError:
                return Input("click")
        c = self.control(step)
        return c.to_input() if c else None

    @property
    def has_hooks(self) -> bool:
        return {"score", "over"} <= set(self.hooks)

    def summary(self) -> str:
        controls = ", ".join(f"{c.name} ({c.key or c.click}{', hold' if c.hold else ''})" for c in self.controls)
        return (f"{self.title} ({self.genre}). Goal: {self.goal} Lose: {self.how_you_lose} "
                f"Win: {self.how_you_win or 'endless'}. Controls: {controls}.")

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("code")
        return d


def _format_sources(project: Project) -> str:
    parts = [f"Engine guess: {project.engine}"]
    for path, text in project.notes.items():
        parts.append(f"--- design note: {path}\n{text[:4000]}")
    for path, text in project.files.items():
        parts.append(f"--- {path}\n{text}")
    return "\n\n".join(parts)


def profile_from_source(project: Project, llm: LLM) -> GameProfile:
    code = _format_sources(project)
    data = llm.ask_json("profile", SOURCE_SYSTEM, code)
    profile = GameProfile.from_dict(data, source="code")
    profile.code = code
    return profile


REFINE_SYSTEM = f"""You wrote a playtest profile for a browser game from its title screen, without source code.
The game is now in play. You get a screenshot, the visible text, and the engine's live state: the active
scenes with their simple fields (numbers, flags, arrays and objects with positions). A variable `game` in the
page's global scope holds the engine instance, so a scene field is `game.scene.getScene('Key').field`.

Rewrite the profile. Use the live state for hooks: "score", "over" (true only on game over), "playing" (true
only while the player is in control), plus what a player needs to decide (player position, hazards, lives,
timers). Guard every expression so it never throws (`&&` / optional chaining). Keep the controls and start
steps unless the screen shows they are wrong.

Return JSON with exactly this shape:
{PROFILE_SHAPE}"""


def _page_info(probe: dict, use_globals: bool) -> dict:
    rect = probe.get("canvas") or None
    buttons = []
    for b in probe.get("buttons") or []:
        # give positions in the same canvas fractions that controls use
        if rect and rect["w"] and rect["h"]:
            buttons.append({"text": b["text"], "click": [round((b["x"] - rect["x"]) / rect["w"], 3),
                                                         round((b["y"] - rect["y"]) / rect["h"], 3)]})
    info = {"title": probe.get("title"), "engine_hint": probe.get("engine"), "visible_text": probe.get("text"),
            "buttons": buttons}
    if use_globals:
        info["globals"] = probe.get("globals")
        info["engine_state"] = probe.get("engine_state")
    return info


def profile_from_page(probe: dict, screenshot: bytes, llm: LLM, use_globals=True) -> GameProfile:
    info = _page_info(probe, use_globals)
    data = llm.ask_json("profile", BLIND_SYSTEM, json.dumps(info, default=str)[:12_000], images=[screenshot])
    profile = GameProfile.from_dict(data, source="blind")
    if not use_globals:
        profile.hooks = {}
    return profile


def refine_from_play(profile: GameProfile, probe: dict, screenshot: bytes, llm: LLM) -> GameProfile:
    """Second look, once the game is running: the play scene and its state only exist now."""
    info = {"current_profile": profile.to_dict(), **_page_info(probe, True)}
    data = llm.ask_json("profile", REFINE_SYSTEM, json.dumps(info, default=str)[:16_000], images=[screenshot])
    if not data.get("controls"):
        return profile
    refined = GameProfile.from_dict(data, source="blind")
    # later calls (hook repair, controller writing) get the runtime state in place of source code
    refined.code = "No source code. Live engine state seen while playing:\n" + json.dumps(probe.get("engine_state"), default=str)
    return refined


