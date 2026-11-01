"""The one interface the rest of the code uses to talk to a model."""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass, field

from PIL import Image


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    by_task: dict[str, int] = field(default_factory=dict)

    def add(self, task: str, inp: int, out: int):
        self.calls += 1
        self.input_tokens += inp
        self.output_tokens += out
        self.by_task[task] = self.by_task.get(task, 0) + 1


class LLM:
    """Ask for JSON back. `task` names the call so usage can be broken down in the report."""

    name = "base"

    def __init__(self):
        self.usage = Usage()

    def ask_json(self, task: str, system: str, prompt: str, images: list[bytes] = ()) -> dict:
        raise NotImplementedError


def shrink(png: bytes, width=512, quality=70) -> bytes:
    """Downscale a screenshot and re-encode as JPEG to keep image tokens low."""
    img = Image.open(io.BytesIO(png)).convert("RGB")
    if img.width > width:
        img = img.resize((width, round(img.height * width / img.width)))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def data_url(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
