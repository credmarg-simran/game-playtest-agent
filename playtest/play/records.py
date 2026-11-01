"""Plain data records of what happened. Everything downstream (metrics, report) reads these."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Event:
    t: float  # seconds into the run (game time)
    kind: str  # start, score, new_best, death, win, stall, loop, new_screen, error, mechanic, no_response
    detail: str = ""


@dataclass
class RunLog:
    index: int
    player: str
    start_seconds: float = 0.0  # time spent getting from the menu into play
    seconds: float = 0.0  # time in play
    score: float = 0.0
    end: str = ""  # death, win, timeout, stall, no_start
    inputs: list[tuple[float, str]] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    samples: list[dict] = field(default_factory=list)  # {"t": .., **hook values}
    screens: list[tuple[float, int]] = field(default_factory=list)  # (t, fingerprint)
    responses: list[dict] = field(default_factory=list)  # sampled input -> did anything react
    screenshots: list[dict] = field(default_factory=list)  # {"t", "path", "label"}
    death: dict = field(default_factory=dict)  # last state, position, what the player saw
    reflection: dict = field(default_factory=dict)  # the player's own account of the run
    errors: list[str] = field(default_factory=list)
    llm_calls: int = 0

    def add(self, t: float, kind: str, detail: str = ""):
        self.events.append(Event(round(t, 2), kind, detail))

    def count(self, kind: str) -> int:
        return sum(1 for e in self.events if e.kind == kind)

    def times(self, kind: str) -> list[float]:
        return [e.t for e in self.events if e.kind == kind]

    def to_dict(self) -> dict:
        return asdict(self)
