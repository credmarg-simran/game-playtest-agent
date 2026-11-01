"""Plays the bundled rock-dodge game for real (headless Chromium) with the offline mock model."""

import json
from pathlib import Path

from playtest.cli import main

GAME = Path(__file__).parent.parent / "games" / "rock-dodge"


def test_full_session_with_mock_model(tmp_path):
    out = main(["--source", str(GAME), "--llm", "mock", "--runs", "2", "--max-run-seconds", "12", "--out", str(tmp_path)])
    report = json.loads((out / "report.json").read_text())
    mode = report["modes"][0]

    assert (out / "report.html").exists() and (out / "report.md").exists()
    assert mode["profile"]["title"] == "Rock Dodge"
    assert {r["player"] for r in mode["runs"]} == {"random", "rules"}
    assert all(r["end"] in ("death", "timeout") for r in mode["runs"])
    # the controller actually played (pressed keys and kept the player alive for a while)
    rules = [r for r in mode["runs"] if r["player"] == "rules"]
    assert any(r["inputs"] for r in rules)
    assert max(r["seconds"] for r in rules) > 3
    assert mode["hooks"]["score"].startswith("live")
    assert "skill_depth" in mode["fun"]["signals"]
    assert mode["verdict"]["verdict"] in ("fun", "mixed", "not fun")
    assert any(s["label"] == "start" for r in mode["runs"] for s in r["screenshots"])

    # the saved report can be re-scored without replaying
    again = main(["--llm", "mock", "--rescore", str(out / "report.json")])
    rescored = json.loads((again / "report.json").read_text())
    assert rescored["meta"]["rescored"]
    assert rescored["modes"][0]["metrics"]["runs"] == mode["metrics"]["runs"]
