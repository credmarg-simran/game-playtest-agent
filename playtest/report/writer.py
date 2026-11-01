"""Builds the report once as a list of sections, then renders it to Markdown and HTML."""

from __future__ import annotations

import html
from dataclasses import dataclass, field

from ..agent import ModeResult
from ..evaluation.fun import DIMENSIONS


@dataclass
class Table:
    head: list[str]
    rows: list[list]


@dataclass
class Images:
    items: list[tuple[str, str]]  # (path, caption)


@dataclass
class Section:
    title: str
    blocks: list = field(default_factory=list)  # str (paragraph), list[str] (bullets), Table, Images

    def add(self, block):
        if block:
            self.blocks.append(block)
        return self


def _fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.2f}".rstrip("0").rstrip(".")
    if isinstance(v, dict):
        return ", ".join(f"{k}: {_fmt(x)}" for k, x in v.items())
    if isinstance(v, list):
        return ", ".join(_fmt(x) for x in v)
    return str(v)


def _mode_sections(r: ModeResult, multi: bool) -> list[Section]:
    tag = f" ({'with code' if r.mode == 'code' else 'URL only'})" if multi else ""
    v = r.verdict or {}
    out = []

    head = Section(f"Verdict{tag}")
    head.add(f"**{_fmt(v.get('verdict', '?')).upper()}**, fun score {_fmt(v.get('fun_score'))}/10. {v.get('headline', '')}")
    head.add(f"Measured scorecard average: {_fmt(r.fun.get('measured_score'))}/5 across {len(r.fun.get('signals', {}))} dimensions.")
    out.append(head)

    card = Section(f"Fun scorecard{tag}")
    rows = []
    judged = v.get("dimensions") or {}
    for dim, sig in r.fun.get("signals", {}).items():
        j = judged.get(dim) or {}
        rows.append([dim, sig["score"], j.get("score", "-"), _fmt(sig["value"]), j.get("why", ""), sig["basis"]])
    card.add(Table(["Dimension", "Measured", "Judge", "Signal", "Why", "Scale"], rows))
    missing = [d for d in DIMENSIONS if d not in r.fun.get("signals", {})]
    if missing:
        card.add(f"Not measurable in this session: {', '.join(missing)}.")
    out.append(card)

    m = Section(f"Gameplay metrics{tag}")
    keys = ["runs", "avg_survival_s", "median_survival_s", "best_survival_s", "avg_score", "best_score",
            "win_rate", "actions_per_minute", "avg_time_to_start_s", "llm_calls", "ends"]
    players = list(r.per_player)
    m.add(Table(["Metric", "All"] + players,
                [[k, _fmt(r.metrics.get(k))] + [_fmt(r.per_player[p].get(k)) for p in players] for k in keys]))
    out.append(m)

    f = r.failures
    fs = Section(f"Failure analysis{tag}")
    causes = v.get("death_causes") or []
    if causes:
        fs.add(Table(["Cause of death (grouped)", "Count"], [[c.get("cause"), c.get("count")] for c in causes]))
    ends = [d for d in f.get("ends", []) if d.get("cause")]
    if ends:
        fs.add(Table(["Run", "Player", "Ended", "At (s)", "Score", "Player's account", "Avoidable?"],
                     [[d["run"], d["player"], d["end"], d["t"], d["score"], d["cause"], _fmt(d["fair"])] for d in ends]))
    bullets = []
    if f.get("repeated_death_scores"):
        bullets.append("Repeated failure points (same score at death): " + _fmt([f"score {x['score']} x{x['deaths']}" for x in f["repeated_death_scores"]]))
    if f.get("repeated_death_positions"):
        bullets.append("Repeated death positions: " + _fmt([f"{x['where']} x{x['deaths']}" for x in f["repeated_death_positions"]]))
    if f.get("death_time_windows"):
        bullets.append("Deaths by time into run: " + _fmt([f"{x['seconds']}s x{x['deaths']}" for x in f["death_time_windows"]]))
    bullets.append(f"Instant deaths (under 3s): {f.get('instant_deaths', 0)}")
    bullets.append(f"Stalls: {len(f.get('stalls', []))}; agent loops: {len(f.get('loops', []))}; runs that never started: {f.get('no_start_runs', 0)}")
    ur = f.get("unresponsive_inputs", {})
    if ur.get("checked"):
        bullets.append(f"Inputs with no visible reaction: {ur['no_reaction']} of {ur['checked']} checked")
    for c in f.get("ui_confusion", []):
        bullets.append(f"UI confusion: {c}")
    for e in f.get("start_retries", []):
        bullets.append(f"Start screen: {e}")
    for e in f.get("controller_errors", []):
        bullets.append(f"Agent controller error: {e}")
    for e in f.get("js_errors", []):
        bullets.append(f"JS error: {e}")
    if r.load.get("blank_screen"):
        bullets.append("The page looked blank after loading.")
    fs.add(bullets)
    out.append(fs)

    b = Section(f"Behavioural observations{tag}")
    b.add(v.get("observations") or [])
    lines = []
    for name, info in r.behavior.get("players", {}).items():
        lines.append(f"{name}: scores by run {info['score_by_run']}, trend {_fmt(info['score_trend_per_run'])}/run, "
                     f"inputs {_fmt(info['inputs_used'])}")
        for i, plan in enumerate(info.get("strategies", []), 1):
            lines.append(f"{name} strategy v{i}: {plan}")
        for x in info.get("learned", [])[:5]:
            lines.append(f"{name} learned: {x}")
    if r.behavior.get("controls_never_used"):
        lines.append("Controls the thinking players never used: " + ", ".join(r.behavior["controls_never_used"]))
    if r.behavior.get("mechanics_never_seen"):
        lines.append("Coded mechanics never reached in play: " + ", ".join(r.behavior["mechanics_never_seen"]))
    if r.behavior.get("mechanics_seen"):
        lines.append("Coded mechanics reached: " + ", ".join(r.behavior["mechanics_seen"]))
    b.add(lines)
    out.append(b)

    sug = Section(f"Suggested improvements{tag}")
    sug.add([f"**{s.get('area', '')}**: {s.get('change', '')} *Why:* {s.get('why', '')}" for s in v.get("suggestions") or []])
    out.append(sug)

    p = r.profile
    prof = Section(f"What the agent understood about the game{tag}")
    prof.add(f"Profile built from **{'source code' if p.source == 'code' else 'the running page only'}**. {p.summary()}")
    prof.add([f"How you lose: {p.how_you_lose}", f"Difficulty notes: {p.difficulty}", f"Tips: {p.tips}"])
    if p.hooks:
        prof.add(Table(["State hook", "Expression", "During play"],
                       [[k, f"`{e}`", r.hooks.get(k, "-")] for k, e in p.hooks.items()]))
    if p.mechanics:
        prof.add(Table(["Designed mechanic", "Detail"], [[m.get("name"), m.get("detail")] for m in p.mechanics]))
    out.append(prof)

    runs = Section(f"Runs{tag}")
    runs.add(Table(["#", "Player", "Ended", "Seconds", "Score", "Inputs", "Play again?", "Why"],
                   [[x.index + 1, x.player, x.end, x.seconds, x.score, len(x.inputs),
                     x.reflection.get("play_again", "-"), x.reflection.get("why", "")] for x in r.runs]))
    pics = [(sc["path"], f"{x.player} run {x.index + 1}: {sc['label']} @ {sc['t']}s")
            for x in r.runs for sc in x.screenshots if sc["label"] in ("start", "after-death", "death", "stall", "timeout")]
    runs.add(Images(pics[:12]))
    out.append(runs)
    return out


def build(results: list[ModeResult], comparison: dict | None, meta: dict) -> tuple[str, list[Section]]:
    title = f"Playtest report: {results[0].profile.title}"
    intro = Section("Session")
    intro.add([
        f"Targets: " + "; ".join(f"{r.mode}: {r.target}" for r in results),
        f"Model: {meta['model']} ({meta['llm_calls']} calls, {meta['input_tokens']:,} input / {meta['output_tokens']:,} output tokens)",
        f"Runs per player: {meta['runs']}, max {meta['max_run_seconds']}s of game time each. Wall time {meta['wall_seconds']}s.",
        f"Generated {meta['when']}",
    ])
    sections = [intro]
    multi = len(results) > 1
    if comparison:
        cmp = Section("With code vs URL only")
        keys = list(next(iter(comparison.values())).keys())
        cmp.add(Table(["", *comparison.keys()], [[k, *(_fmt(comparison[m][k]) for m in comparison)] for k in keys]))
        note = next((r.verdict.get("with_vs_without_code") for r in reversed(results) if r.verdict.get("with_vs_without_code")), "")
        cmp.add(note)
        sections.append(cmp)
    for r in results:
        sections += _mode_sections(r, multi)
    return title, sections


def to_markdown(title: str, sections: list[Section]) -> str:
    out = [f"# {title}\n"]
    for s in sections:
        out.append(f"## {s.title}\n")
        for b in s.blocks:
            if isinstance(b, str):
                out.append(b + "\n")
            elif isinstance(b, list):
                out.append("\n".join(f"- {x}" for x in b) + "\n")
            elif isinstance(b, Table):
                out.append("| " + " | ".join(b.head) + " |")
                out.append("|" + "---|" * len(b.head))
                for row in b.rows:
                    out.append("| " + " | ".join(_fmt(c).replace("|", "\\|").replace("\n", " ") for c in row) + " |")
                out.append("")
            elif isinstance(b, Images):
                out.append("\n".join(f"![{cap}]({path})" for path, cap in b.items) + "\n")
    return "\n".join(out)


def _inline(text: str) -> str:
    """Escape, then support the tiny bit of markdown we emit: **bold**, *italic*, `code`."""
    import re

    t = html.escape(text)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"\*(.+?)\*", r"<i>\1</i>", t)
    return re.sub(r"`(.+?)`", r"<code>\1</code>", t)


CSS = """
body{font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;max-width:1100px;margin:32px auto;padding:0 16px;color:#1d2330;background:#fafbfc}
h1{font-size:26px}h2{font-size:19px;margin-top:36px;border-bottom:1px solid #dde1e7;padding-bottom:6px}
table{border-collapse:collapse;width:100%;margin:10px 0;font-size:13.5px;background:#fff}
th,td{border:1px solid #dde1e7;padding:6px 8px;text-align:left;vertical-align:top}th{background:#f0f2f5}
code{background:#eef0f3;padding:1px 4px;border-radius:3px;font-size:12px;word-break:break-all}
.shots{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px}
figure{margin:0}figure img{width:100%;border:1px solid #dde1e7;border-radius:4px}figcaption{font-size:12px;color:#5b6475}
@media (prefers-color-scheme: dark){body{background:#14171d;color:#e3e6ea}table{background:#1b1f27}th{background:#232834}th,td{border-color:#2e3442}code{background:#262b36}h2{border-color:#2e3442}}
"""


def to_html(title: str, sections: list[Section]) -> str:
    out = [f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body><h1>{html.escape(title)}</h1>"]
    for s in sections:
        out.append(f"<h2>{html.escape(s.title)}</h2>")
        for b in s.blocks:
            if isinstance(b, str):
                out.append(f"<p>{_inline(b)}</p>")
            elif isinstance(b, list):
                out.append("<ul>" + "".join(f"<li>{_inline(str(x))}</li>" for x in b) + "</ul>")
            elif isinstance(b, Table):
                head = "".join(f"<th>{html.escape(h)}</th>" for h in b.head)
                rows = "".join("<tr>" + "".join(f"<td>{_inline(_fmt(c))}</td>" for c in row) + "</tr>" for row in b.rows)
                out.append(f"<table><tr>{head}</tr>{rows}</table>")
            elif isinstance(b, Images):
                figs = "".join(f"<figure><a href='{p}'><img src='{p}' loading='lazy'></a><figcaption>{html.escape(c)}</figcaption></figure>"
                               for p, c in b.items)
                out.append(f"<div class='shots'>{figs}</div>")
    out.append("</body></html>")
    return "".join(out)
