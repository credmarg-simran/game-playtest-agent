"""Command line entry point.

    python -m playtest --source games/clumsy-bird
    python -m playtest --url https://ellisonleao.github.io/clumsy-bird/
    python -m playtest --source games/clumsy-bird --url https://ellisonleao.github.io/clumsy-bird/
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from . import agent
from .llm import make_llm
from .report import writer


def load_env(path=".env"):
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            if value.strip():
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse(argv=None):
    ap = argparse.ArgumentParser(prog="playtest", description="Autonomous playtester for browser games")
    ap.add_argument("--source", help="game project folder (static site or Vite/webpack project)")
    ap.add_argument("--url", help="URL of a game that already runs online")
    ap.add_argument("--runs", type=int, default=5, help="runs per player (default 5)")
    ap.add_argument("--players", help="comma list from random, rules, vision; persona with a colon, "
                                      "e.g. random,vision:competitive. Default: random,rules with code, random,vision without")
    ap.add_argument("--max-run-seconds", type=float, default=90, help="game-time cap per run")
    ap.add_argument("--vision-calls", type=int, default=60, help="model calls per run for the vision player")
    ap.add_argument("--pixels-only", action="store_true", help="URL mode: screenshots only, no DOM/global probing")
    ap.add_argument("--llm", default="auto", choices=["auto", "openai", "mock"])
    ap.add_argument("--model", help="model for frequent calls (default $OPENAI_MODEL or gpt-5-mini)")
    ap.add_argument("--strong-model", help="model for code reading, controller writing and judging "
                                           "(default $OPENAI_STRONG_MODEL or --model)")
    ap.add_argument("--out", default="reports", help="where reports go")
    ap.add_argument("--headed", action="store_true", help="show the browser")
    args = ap.parse_args(argv)
    if not args.source and not args.url:
        ap.error("give --source, --url or both")
    return args


def main(argv=None):
    load_env()
    args = parse(argv)
    llm = make_llm(args.llm, args.model, args.strong_model)
    name = Path(args.source).name if args.source else args.url.rstrip("/").split("/")[-1] or "game"
    out = Path(args.out) / f"{name}-{agent.stamp()}"
    opts = agent.Options(
        runs=args.runs, players=args.players.split(",") if args.players else None,
        max_run_seconds=args.max_run_seconds, vision_calls_per_run=args.vision_calls,
        pixels_only=args.pixels_only, headless=not args.headed,
    )
    models = f"{getattr(llm, 'model', '')} / {getattr(llm, 'strong_model', '')}".strip(" /")
    print(f"model: {llm.name} {models}  ->  {out}")

    started = time.time()
    session = agent.Playtest(llm, out, opts)
    results = []
    if args.source:
        results.append(session.run_mode("code", args.source))
    if args.url:
        results.append(session.run_mode("url", args.url))
    comparison = agent.compare(results) if len(results) > 1 else None
    print("\njudging...")
    for r in results:
        session.evaluate(r, comparison)

    meta = {
        "model": f"{llm.name} {models}".strip(), "llm_calls": llm.usage.calls,
        "input_tokens": llm.usage.input_tokens, "output_tokens": llm.usage.output_tokens,
        "calls_by_task": llm.usage.by_task, "runs": args.runs, "max_run_seconds": args.max_run_seconds,
        "wall_seconds": round(time.time() - started), "when": time.strftime("%Y-%m-%d %H:%M"),
    }
    title, sections = writer.build(results, comparison, meta)
    (out / "report.md").write_text(writer.to_markdown(title, sections))
    (out / "report.html").write_text(writer.to_html(title, sections))
    agent.write_json(out / "report.json", {
        "meta": meta, "comparison": comparison,
        "modes": [{
            "mode": r.mode, "target": r.target, "profile": r.profile.to_dict(), "load": r.load, "hooks": r.hooks,
            "calibration": r.calibration, "problems": r.problems,
            "metrics": r.metrics, "per_player": r.per_player, "failures": r.failures, "behavior": r.behavior,
            "fun": r.fun, "verdict": r.verdict, "build_log": r.build_log, "runs": [x.to_dict() for x in r.runs],
        } for r in results],
    })
    for r in results:
        v = r.verdict
        print(f"{r.mode}: {v.get('verdict', '?')} ({v.get('fun_score', '?')}/10) - {v.get('headline', '')}")
    print(f"report: {out / 'report.html'}")
    print(f"model calls: {llm.usage.calls} {llm.usage.by_task}, tokens in/out {llm.usage.input_tokens}/{llm.usage.output_tokens}")
    return out


if __name__ == "__main__":
    main()
