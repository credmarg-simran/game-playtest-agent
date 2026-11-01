"""Turn a game folder into something we can serve, and pick the source files worth reading."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

CODE_EXT = {".js", ".ts", ".jsx", ".tsx", ".mjs", ".html", ".gd"}
NOTE_EXT = {".md", ".json", ".txt"}
SKIP_DIRS = {"node_modules", "dist", "build", ".git", "out", ".vite", "coverage", "assets", "data", "public"}
SKIP_FILES = {"package-lock.json", "tsconfig.json", "yarn.lock", "pnpm-lock.yaml"}
ENGINES = [
    ("phaser", re.compile(r"\bphaser\b", re.I)),
    ("melonjs", re.compile(r"melonjs|\bme\.state\b", re.I)),
    ("three", re.compile(r"\bthree\b|THREE\.", re.I)),
    ("pixi", re.compile(r"pixi\.js|\bPIXI\.")),
    ("kaboom", re.compile(r"kaboom|kaplay")),
    ("godot", re.compile(r"godot|\.pck\b", re.I)),
]
# `new Phaser.Game(cfg)` -> `__ptNew(Phaser.Game, cfg)`, so the clock script can keep a handle on
# the game instance even when the bundler hides it inside a module.
GAME_CTOR = re.compile(r"\bnew\s+((?:Phaser\.)?Game)\s*\(")


@dataclass
class Project:
    root: Path
    serve_dir: Path
    engine: str
    built: bool
    files: dict[str, str] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)
    log: list[str] = field(default_factory=list)

    @property
    def isolate(self) -> bool:
        return self.engine == "godot"


def _looks_minified(text: str) -> bool:
    lines = text.splitlines() or [""]
    return len(text) > 20_000 and len(text) / len(lines) > 300


def _walk(root: Path):
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if p.is_file() and not any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts[:-1]):
            yield p, rel


def detect_engine(root: Path) -> str:
    pkg = root / "package.json"
    hay = pkg.read_text(errors="ignore") if pkg.exists() else ""
    for p, rel in _walk(root):
        if p.suffix in {".html", ".pck", ".js", ".ts"} and len(hay) < 400_000:
            hay += "\n" + rel.name + "\n" + p.read_text(errors="ignore")[:4000]
    for name, pattern in ENGINES:
        if pattern.search(hay):
            return name
    return "canvas"


def read_sources(root: Path, budget: int) -> tuple[dict[str, str], dict[str, str]]:
    """Game code within a character budget, most relevant files first, plus design notes."""
    code, notes = {}, {}
    candidates = []
    for p, rel in _walk(root):
        if p.name in SKIP_FILES or p.stat().st_size > 400_000:
            continue
        if p.suffix in NOTE_EXT:
            if p.name != "package.json" and p.stat().st_size < 20_000:
                notes[str(rel)] = p.read_text(errors="ignore")
            continue
        if p.suffix not in CODE_EXT:
            continue
        text = p.read_text(errors="ignore")
        if _looks_minified(text) or ".min." in p.name:
            continue
        rank = 0 if re.search(r"main|game|scene|play|player|level|config|index", str(rel), re.I) else 1
        candidates.append((rank, len(text), str(rel), text))

    used = 0
    for _, size, rel, text in sorted(candidates):
        if used >= budget:
            break
        code[rel] = text[: budget - used]
        used += min(size, budget - used)
    return code, notes


def _run(cmd: list[str], cwd: Path, log: list[str]):
    log.append("$ " + " ".join(cmd))
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=600)
    if res.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed:\n{res.stdout[-2000:]}\n{res.stderr[-2000:]}")


def _build(root: Path, work: Path, log: list[str]) -> Path:
    """Copy a bundled project (Vite, webpack...), hook the game constructor, build it."""
    dest = work / "build"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(root, dest, ignore=shutil.ignore_patterns("node_modules", "dist", ".git"))

    patched = 0
    for p in dest.rglob("*"):
        if p.suffix in {".js", ".ts", ".jsx", ".tsx", ".mjs"} and "node_modules" not in p.parts:
            text = p.read_text(errors="ignore")
            cast = "(globalThis as any)" if p.suffix in {".ts", ".tsx"} else "globalThis"
            new = GAME_CTOR.sub(lambda m: f"{cast}.__ptNew({m.group(1)}, ", text)
            new = new.replace(".__ptNew(Game, )", ".__ptNew(Game)").replace(".__ptNew(Phaser.Game, )", ".__ptNew(Phaser.Game)")
            if new != text:
                p.write_text(new)
                patched += 1
    log.append(f"hooked game constructor in {patched} file(s)")

    _run(["npm", "ci" if (dest / "package-lock.json").exists() else "install", "--no-audit", "--no-fund"], dest, log)
    _run(["npm", "run", "build"], dest, log)
    for name in ("dist", "build", "out", "public"):
        if (dest / name / "index.html").exists():
            return dest / name
    raise RuntimeError("build finished but no index.html found in dist/, build/ or out/")


def load_project(path: str | Path, work: Path, source_budget=60_000) -> Project:
    root = Path(path).resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    engine = detect_engine(root)
    code, notes = read_sources(root, source_budget)
    log: list[str] = []

    pkg = root / "package.json"
    pkg_text = pkg.read_text() if pkg.exists() else "{}"
    has_build = "build" in json.loads(pkg_text).get("scripts", {})
    needs_build = has_build and bool(re.search(r"vite|webpack|parcel|esbuild|rollup", pkg_text))
    if needs_build:
        serve_dir = _build(root, work, log)
    else:
        index = min(root.rglob("index.html"), key=lambda p: len(p.parts), default=None)
        if index is None:
            raise FileNotFoundError(f"no index.html under {root}")
        serve_dir = index.parent
    return Project(root=root, serve_dir=serve_dir, engine=engine, built=needs_build, files=code, notes=notes, log=log)
