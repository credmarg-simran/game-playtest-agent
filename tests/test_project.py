from pathlib import Path

from playtest.game.project import GAME_CTOR, detect_engine, load_project, read_sources

GAMES = Path(__file__).parent.parent / "games"


def test_engine_detection():
    assert detect_engine(GAMES / "clumsy-bird") == "melonjs"
    assert detect_engine(GAMES / "rock-dodge") == "canvas"


def test_sources_skip_minified_libraries():
    code, _ = read_sources(GAMES / "clumsy-bird", budget=200_000)
    assert "js/entities/entities.js" in code
    assert not any("melonJS-min" in path or "clumsy-min" in path for path in code)


def test_static_project_is_served_as_is(tmp_path):
    project = load_project(GAMES / "rock-dodge", tmp_path)
    assert not project.built
    assert (project.serve_dir / "index.html").exists()


def test_game_constructor_rewrite():
    src = "const game = new Phaser.Game(config);\nexport default new Game({ ...cfg, parent });"
    out = GAME_CTOR.sub(lambda m: f"globalThis.__ptNew({m.group(1)}, ", src)
    assert out == "const game = globalThis.__ptNew(Phaser.Game, config);\nexport default globalThis.__ptNew(Game, { ...cfg, parent });"
