# Game Playtest Agent

An agent that plays a browser game by itself, over many runs, and writes a playtest report: gameplay
numbers, why runs ended, what the players did, and a verdict on whether the game is fun, with the
evidence behind it.

It works two ways, and can do both in one session to compare them:

- **With code** (`--source`): point it at the game's project folder. It reads the code to learn the
  controls, the goal and where the game keeps its state (score, death flag, player and obstacle
  positions), verifies that against the running game, then plays with exact state. This is the case for
  games made with tools like Jabali, where the creator has the source.
- **Without code** (`--url`): point it at a game that already runs online. It only has screenshots, the
  page's visible text and whatever global variables the page exposes.

## Setup

```bash
# Python 3.11+
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium
cp .env.example .env        # then put your OpenAI key in .env
```

Without a key everything still runs on an offline mock model (`--llm mock`), which is what the tests use.

## Run

```bash
# with code (the bundled Clumsy Bird clone)
python -m playtest --source games/clumsy-bird

# without code, same game from its public URL
python -m playtest --url https://ellisonleao.github.io/clumsy-bird/

# both, with a side by side comparison in the report
python -m playtest --source games/clumsy-bird --url https://ellisonleao.github.io/clumsy-bird/ --runs 5

# offline, on the tiny bundled sample game
python -m playtest --source games/rock-dodge --llm mock
```

Useful flags: `--runs` (per player), `--players random,rules` or `random,vision:competitive`,
`--max-run-seconds`, `--vision-calls` (model calls per run for the screenshot player), `--pixels-only`
(URL mode without looking at page globals), `--headed` (watch the browser), `--model` and
`--strong-model`.

Each session writes `reports/<game>-<time>/` with `report.html`, `report.md`, `report.json` (every
event, sample and number) and `screenshots/`. A full sample is in [`reports/sample/`](reports/sample/report.md).

Tests: `pytest -q` (about 20 seconds; one of them plays the bundled sample game in headless Chromium).

## How it works

```
 --source folder                               --url
      |                                          |
  project.py   detect engine, build Vite/webpack |
      |        projects, hook the game object    |
      v                                          v
  profile.py   read code -> controls, goal,   screenshot + page text + globals
               state hooks, designed mechanics   -> best-guess profile
      |                                          |
      +-------------------+----------------------+
                          v
  calibrate.py   play a few seconds, check every hook returns live values, repair broken ones
                          v
  runner.py      for each player: start game, observe -> decide -> act -> detect events,
                 detect death/stall/loop, restart, repeat for N runs
                 players: random (control group) | rules (writes its own controller from the code)
                          | vision (plays from screenshots)
                          v
  evaluation/    metrics.py   runs, survival, scores, win rate, actions per minute
                 failures.py  causes of death, repeated failure points, stalls, loops, unresponsive input
                 behavior.py  strategies, score trend, controls and mechanics never used
                 fun.py       measured fun signals -> 1-5 scorecard -> judge model verdict
                          v
  report/writer.py   report.html, report.md, report.json
```

### Playing a fast game with a slow brain

`browser/clock.js` is injected before any game code runs. It replaces `requestAnimationFrame`,
`performance.now`, `Date` and timers with a virtual clock, so the game only moves when the agent steps
it. The agent can stop the game, look, think for as long as the model takes, press a key, and step a few
frames. A reflex game becomes a turn-based one, and all times in the report are game time.

### Observing the game

- **State hooks.** Short JS expressions the agent evaluates in the page every few frames (for example
  `me.state.isCurrent(me.state.GAME_OVER)` for Clumsy Bird). With code, the model writes them from the
  source. Bundled Phaser games hide the game object inside modules, so `project.py` rewrites
  `new Phaser.Game(...)` in a build copy to hand the instance to the agent.
- **Calibration.** Hooks from reading code are often slightly wrong (wrong object, a score trigger
  instead of the real obstacle). Before scoring anything, the agent plays a few seconds, flags hooks
  that never returned a value or never moved, and asks the model to fix them against the source.
- **Pixels.** Screen change between frames (did an input do anything?) and a 64-bit fingerprint of each
  screen (how many different screens has the player seen, and when did new ones stop appearing?).
- **Vision.** Without hooks, a model reads the screenshot for the phase (menu, playing, game over) and
  the score.

### Players

- `random`: mashes the game's controls. It is the control group: if it scores like a thinking player,
  the game has little skill in it.
- `rules`: reads the source and writes a small JS controller `(state, memory) => [controls]` that runs
  every few frames. After each death it looks at the last 1.5 seconds of state and a screenshot of the
  moment of death, then rewrites the controller, starting from the best version so far. It can also add
  hooks it found it was missing.
- `vision`: plays from screenshots only, like a person would. Personas `first_timer` (default) and
  `competitive`.

After every run the thinking players answer a short questionnaire: what ended the run, could it have been
avoided, what was confusing, best and worst moment, and "play again?" from 1 to 5.

### Judging fun

The main question is whether the game is fun, so this part does not rely on the model's taste alone.
`fun.py` measures ten signals from the logs and maps each to a 1-5 score with fixed thresholds that are
printed in the report:

| Dimension | Signal |
|---|---|
| clarity | seconds from title screen to first point; start retries; confusion notes |
| feedback | share of sampled inputs that changed the screen or the state within 8 frames |
| challenge | median survival of thinking players; share of deaths under 3 s; share of runs that never end |
| skill depth | thinking players' score and survival versus the random control group |
| learnability | later runs versus earlier runs |
| reward cadence | median and longest gap between score changes |
| variety | distinct screens per minute, and whether new ones still appear late in a run |
| fairness | share of deaths the player judged avoidable |
| replay pull | average "play again?" answer |
| delivers design | share of mechanics found in the code that actually showed up in play (code mode) |

A judge model then gets the scorecard, metrics, failure analysis, behaviour notes, the players' answers
and a few screenshots. It writes the verdict, may move a dimension by at most one point with a reason,
must cite numbers for every claim, and is told to separate "the game is hard" from "the bot is bad at it".

### Model use and cost

All model calls go through one small interface (`llm/base.py`) with an OpenAI implementation and an
offline mock. Frequent calls (vision moves, screen checks) use a small model with minimal reasoning.
The few calls that need real thought (reading code, repairing hooks, writing the controller,
reflections, the verdict) can go to a stronger model through `OPENAI_STRONG_MODEL`. A session with code
makes about 10-12 calls; the vision player adds up to `--vision-calls` per run. Token counts per task are
in the report.

## Code map

```
playtest/
  cli.py                  command line, writes the report files
  agent.py                one session: load, profile, calibrate, play each player, evaluate
  browser/clock.js        virtual clock, error capture, state reads, engine hooks (runs in the page)
  browser/session.py      Playwright page + key/click helpers
  browser/server.py       local static server for project folders
  game/project.py         engine detection, Vite/webpack builds, picking source files to read
  game/profile.py         what the agent believes about the game, from code or from the page
  game/calibrate.py       checks hooks against the running game and repairs them
  perception/frames.py    pixel change and screen fingerprints
  policy/                 random, rules and vision players
  play/runner.py          the play loop: start, observe, decide, act, detect, restart
  play/records.py         RunLog and Event records
  evaluation/             metrics, failures, behavior, fun
  llm/                    model interface, OpenAI, offline mock
  report/writer.py        report sections rendered to Markdown and HTML
games/
  clumsy-bird/            open source Flappy Bird clone (MIT, github.com/ellisonleao/clumsy-bird)
  rock-dodge/             tiny sample game used by the tests
```

Adding a player means one class with `decide()` (and optionally `reflect()`). Adding a fun dimension is a
block in `fun.measure()`. Other engines work as long as their state is reachable from the page or the
screen.

## Results on Clumsy Bird

See [`reports/sample/report.md`](reports/sample/report.md) for the full report. In short:

- **With code**, the agent found the game's real state (menu, play and game-over states, bird position,
  the 176 px gap between pipes) and its rules player got through pipes, where button mashing never did.
- **Without code**, it still found the score through a page global, but not the death state, and the
  screenshot player could not react fast enough for a one-button reflex game within its call budget.

## Limits

- The bots are not good players. They play like a novice, which is useful for clarity and first-session
  difficulty, less so for judging late-game balance.
- Fairness, replay pull and confusion come from the bots' own answers. They are bot opinions, measured
  consistently, not human opinions.
- Canvas-only games without reachable state depend on the vision model to tell the phase and the score.
- Godot web exports load, but their state is inside WebAssembly, so they run in pixels-only mode.
