"""Mashes buttons. The control group: if this scores like a thinking player, the game has no skill in it."""

from __future__ import annotations

import random

from .base import Decision, Player, View


class RandomPlayer(Player):
    name = "random"

    def __init__(self, seed=7):
        self.rng = random.Random(seed)
        self.holding = []

    def decide(self, view: View) -> Decision:
        controls = self.profile.controls
        if not controls or self.rng.random() < 0.35:
            return Decision(wait=self.rng.randint(4, 20))
        c = self.rng.choice(controls)
        inp = c.to_input()
        if c.click and self.rng.random() < 0.5:
            inp.x, inp.y = self.rng.uniform(0.1, 0.9), self.rng.uniform(0.1, 0.9)
        if inp.hold:
            # keep holding for a while, like someone leaning on a key
            self.holding = [inp] if self.rng.random() < 0.7 else []
            return Decision(inputs=self.holding, wait=self.rng.randint(6, 30))
        return Decision(inputs=[inp] + self.holding, wait=self.rng.randint(4, 20))
