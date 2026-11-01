"""Plays with source access. The model reads the code and the state hooks and writes a small JS
controller that runs every few frames. After each run it studies how it died and rewrites the
controller, and may add state hooks it found it was missing.

This is how a person with the code would play a fast game: work out the rule, then react quickly.
"""

from __future__ import annotations

import json

from .base import PERSONAS, Decision, Player, View

STRATEGY_SYSTEM = """You write the controller for an automated player of a browser game. The controller is the
BODY of a JavaScript function `(s, mem) => string[]`:
- `s` holds the current values of the game's state hooks (see example state).
- `mem` is an object kept between calls during a run; use it for timers or previous values.
- Return the names of the controls to use this tick ([] to do nothing). Controls marked hold stay pressed
  while you keep returning them; other controls are tapped once per tick you return them.
It runs every `every` frames (60 frames = 1 second). Work out the real geometry and timing from the
source code (sizes, speeds, gravity), keep it defensive against null values, and keep it short.
Prefer simple threshold rules over long physics predictions. After a tap the game needs a few frames
to show its effect, so use `mem` to avoid tapping again before the state has reacted.

If the hooks lack something you need (for example the obstacle you are still overlapping), add hooks:
JS expressions evaluated in the page's global scope, guarded so they never throw.

Return JSON: {"plan": "one or two sentences", "every": int 1-10, "body": "JS function body",
"hooks": {"name": "JS expression"} (only new ones, or {})}"""

REVISE_NOTE = """

You are revising a controller. The best one so far (result: {best_result}):
{best_body}
Its plan: {best_plan}
The run above used controller {current_version}.

After answering the questions, also include "plan", "every", "body" and "hooks" for an improved controller.
"hooks" is only for NEW state expressions the controller reads as s.<name> (JS, global scope), or {{}}.
Start from the best controller, fix what ended the run above, and keep what worked.
"body" is the statements inside the function (ending in a return), not a function declaration."""


# Runs the controller in the page. Models sometimes return a whole function instead of a body, so accept
# a body, a function expression or an arrow function. Compiled once per version.
CONTROLLER_JS = """([body, s, reset]) => {
    if (reset || !window.__ptMem) window.__ptMem = {};
    let ctl = window.__ptCtl;
    if (!ctl || ctl.body !== body) {
        let fn = null, err = null;
        try { fn = Function('s', 'mem', body); } catch (e) { err = e; }
        if (!fn) {
            try { const f = (0, eval)('(' + body + ')'); if (typeof f === 'function') fn = f; } catch (e) { }
        }
        ctl = window.__ptCtl = { body, fn, err: fn ? null : String(err && err.name + ': ' + err.message) };
    }
    if (!ctl.fn) return [[], ctl.err];
    try {
        let r = ctl.fn(s, window.__ptMem);
        if (typeof r === 'function') r = r(s, window.__ptMem);
        return [Array.isArray(r) ? r : [], null];
    } catch (e) { return [[], String(e.name + ': ' + e.message)]; }
}"""


class RulePlayer(Player):
    name = "rules"
    uses_llm = True

    def __init__(self, persona="competitive"):
        self.persona = persona
        self.body = "return [];"
        self.plan = ""
        self.every = 3
        self.history: list[dict] = []  # every controller version and how it did
        self.version = 0
        self.mem_reset = True

    def begin_run(self, index, first_state):
        if index == 0:
            prompt = json.dumps({
                "persona": PERSONAS[self.persona],
                "game": self.profile.summary(),
                "difficulty": self.profile.difficulty,
                "tips": self.profile.tips,
                "controls": [c.__dict__ for c in self.profile.controls],
                "hooks": self.profile.hooks,
                "example_state": first_state,
            }, default=str)
            if self.profile.code:
                prompt += "\n\nGame source:\n" + self.profile.code[:40_000]
            self._use(self.llm.ask_json("strategy", STRATEGY_SYSTEM, prompt))
            self._ensure_works(first_state)
        self.mem_reset = True

    def _dry_run(self, state: dict) -> str | None:
        names, error = self.session.page.evaluate(CONTROLLER_JS, [self.body, state, True])
        if error:
            return error
        unknown = [n for n in names if not self.profile.control(str(n))]
        if unknown:
            return f"returned unknown controls {unknown}; valid names are {[c.name for c in self.profile.controls]}"
        return None

    def _ensure_works(self, state: dict):
        """Run a new controller once against real state; if it throws, give the model one chance to fix it."""
        problem = self._dry_run(state)
        if not problem:
            return
        print(f"  controller v{self.version} failed a dry run: {problem[:150]}")
        fixed = self.llm.ask_json("strategy", STRATEGY_SYSTEM, "This controller fails when run. Fix it.\n" + json.dumps({
            "body": self.body, "problem": problem, "controls": [c.__dict__ for c in self.profile.controls],
            "hooks": self.profile.hooks, "example_state": state,
        }, default=str))
        if fixed.get("body"):
            self.body = self.history[-1]["body"] = fixed["body"]
            self.history[-1]["fixed_after_dry_run"] = problem[:200]
            still = self._dry_run(state)
            if still:
                print(f"  controller v{self.version} still fails: {still[:150]}")

    def _use(self, data: dict):
        if not data.get("body"):
            return
        self.body = data["body"]
        self.plan = data.get("plan", "")
        try:
            self.every = max(1, min(10, int(data.get("every", 3))))
        except (TypeError, ValueError):
            self.every = 3
        hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
        added = {k: v for k, v in hooks.items() if isinstance(v, str) and v.strip()}
        self.profile.hooks.update(added)
        self.version = len(self.history) + 1
        self.history.append({"version": self.version, "plan": self.plan, "body": self.body, "every": self.every,
                             "added_hooks": list(added), "results": []})

    def _best(self) -> dict:
        tried = [h for h in self.history if h["results"]]
        if not tried:
            return self.history[-1]
        return max(tried, key=lambda h: max((r["score"], r["seconds"]) for r in h["results"]))

    def decide(self, view: View) -> Decision:
        names, error = self.session.page.evaluate(CONTROLLER_JS, [self.body, view.state, self.mem_reset])
        self.mem_reset = False
        controls = [self.profile.control(str(n)) for n in names]
        if not error and None in controls:
            error = f"ControllerError: unknown control in {names}"
        inputs = [c.to_input() for c in controls if c]
        return Decision(inputs=inputs, wait=self.every, note=error or "")

    def reflect(self, run, final_png):
        if self.history:
            self.history[-1]["results"].append({"run": run.index + 1, "score": run.score, "seconds": run.seconds})
        best = self._best()
        best_result = ", ".join(f"run {r['run']}: score {r['score']} in {r['seconds']}s" for r in best["results"]) or "untested"
        current = f"v{self.version}" + (" (the best one)" if best is self.history[-1] else "")
        extra = REVISE_NOTE.format(best_result=best_result, best_body=best["body"], best_plan=best["plan"],
                                   current_version=current)
        data = self._reflect(run, final_png, extra)
        before = self.version
        self._use(data)
        if self.version != before:
            self._ensure_works(run.death.get("state") or {})
        return {k: v for k, v in data.items() if k not in ("body", "every", "hooks")}
