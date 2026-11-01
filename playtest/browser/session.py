"""A Playwright page with the virtual clock installed, plus the few input helpers we need."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import sync_playwright

CLOCK_JS = (Path(__file__).parent / "clock.js").read_text()


@dataclass
class Input:
    """One thing the player does: tap or hold a key, or click a point.

    x and y are fractions of the game canvas (0..1), so they survive any scaling.
    """

    kind: str  # "key" or "click"
    key: str = ""
    x: float = 0.5
    y: float = 0.5
    hold: bool = False

    @property
    def name(self) -> str:
        if self.kind == "click":
            return f"click({self.x:.2f},{self.y:.2f})"
        return f"{'hold' if self.hold else 'tap'}:{self.key}"


class BrowserSession:
    def __init__(self, url: str, viewport=(960, 640), headless=True, fps=60, frames_per_tick=20):
        self.url = url
        self.viewport = viewport
        self.headless = headless
        self.clock_cfg = {"fps": fps, "framesPerTick": frames_per_tick}
        self.fps = fps
        self.console: list[str] = []
        self.held: set[str] = set()

    def __enter__(self):
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(
            headless=self.headless,
            args=["--autoplay-policy=no-user-gesture-required", "--mute-audio"],
        )
        context = self.browser.new_context(viewport={"width": self.viewport[0], "height": self.viewport[1]})
        context.add_init_script(f"window.__PLAYTEST_CFG = {json.dumps(self.clock_cfg)};\n{CLOCK_JS}")
        self.page = context.new_page()
        self.page.on("console", lambda m: m.type == "error" and self.console.append(m.text[:300]))
        self.page.on("pageerror", lambda e: self.console.append(f"pageerror: {str(e)[:300]}"))
        return self

    def __exit__(self, *exc):
        self.browser.close()
        self._pw.stop()

    def open(self, warmup_frames=120):
        self._load(self.url)
        if self._unwrap_iframe():
            self._load(self.url)
        self.page.evaluate("window.__playtest.pause()")
        self.step(warmup_frames)

    def _load(self, url: str):
        self.page.goto(url, wait_until="load", timeout=60_000)
        try:
            self.page.wait_for_load_state("networkidle", timeout=15_000)  # assets still loading
        except Exception:
            pass

    def _unwrap_iframe(self) -> bool:
        """Game portals often embed the game in an iframe. Play the iframe's page directly so keys,
        clicks and state reads all reach the game."""
        if self.page.evaluate("!!document.querySelector('canvas')"):
            return False
        for frame in self.page.frames[1:]:
            try:
                if frame.url.startswith("http") and frame.evaluate("!!document.querySelector('canvas')"):
                    self.url = frame.url
                    return True
            except Exception:
                continue
        return False

    # clock

    def step(self, frames: int) -> int:
        return self.page.evaluate("n => window.__playtest.step(n)", int(frames))

    @property
    def frame(self) -> int:
        return self.page.evaluate("window.__playtest.frame()")

    def seconds(self, frames: int) -> float:
        return frames / self.fps

    # observing

    def screenshot(self, path: str | Path | None = None) -> bytes:
        return self.page.screenshot(path=str(path) if path else None, type="png")

    def read(self, exprs: dict[str, str]) -> dict:
        if not exprs:
            return {}
        return self.page.evaluate("e => window.__playtest.read(e)", exprs)

    def probe(self) -> dict:
        """Everything we can learn about the page without knowing the code."""
        return self.page.evaluate(
            """() => ({
                engine: window.__playtest.engine(),
                globals: window.__playtest.scanGlobals(),
                text: window.__playtest.visibleText(),
                buttons: window.__playtest.buttons(),
                canvas: window.__playtest.canvasRect(),
                title: document.title,
            })"""
        )

    def errors(self, since_frame=0) -> list[dict]:
        return self.page.evaluate("f => window.__playtest.errors(f)", since_frame)

    # acting

    def _point(self, fx: float, fy: float) -> tuple[float, float]:
        rect = self.page.evaluate("window.__playtest.canvasRect()") or {
            "x": 0, "y": 0, "w": self.viewport[0], "h": self.viewport[1],
        }
        return rect["x"] + fx * rect["w"], rect["y"] + fy * rect["h"]

    def tap(self, inp: Input, frames_down=2):
        """Press and release, letting a couple of frames pass so polling games see it."""
        if inp.kind == "click":
            x, y = self._point(inp.x, inp.y)
            self.page.mouse.move(x, y)
            self.page.mouse.down()
            self.step(frames_down)
            self.page.mouse.up()
        else:
            self.page.keyboard.down(inp.key)
            self.step(frames_down)
            self.page.keyboard.up(inp.key)

    def hold_only(self, keys: set[str]):
        """Make exactly these keys held down."""
        for k in self.held - keys:
            self.page.keyboard.up(k)
        for k in keys - self.held:
            self.page.keyboard.down(k)
        self.held = set(keys)

    def release_all(self):
        self.hold_only(set())

    def perform(self, inputs: list[Input]) -> int:
        """Apply one decision. Returns frames that already passed while doing it."""
        self.hold_only({i.key for i in inputs if i.kind == "key" and i.hold})
        used = 0
        for inp in inputs:
            if not inp.hold:
                self.tap(inp)
                used += 2
        return used
