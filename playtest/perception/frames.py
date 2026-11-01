"""Cheap pixel signals: how much the screen changed, and a fingerprint for 'have I seen this screen'."""

from __future__ import annotations

import io

from PIL import Image, ImageChops, ImageStat


def _small(png: bytes, size=(96, 64)) -> Image.Image:
    return Image.open(io.BytesIO(png)).convert("L").resize(size)


def change(a: bytes, b: bytes) -> float:
    """Mean absolute pixel difference between two screenshots, 0..1."""
    diff = ImageChops.difference(_small(a), _small(b))
    return ImageStat.Stat(diff).mean[0] / 255


def fingerprint(png: bytes) -> int:
    """64-bit difference hash. Similar screens give hashes a few bits apart."""
    img = Image.open(io.BytesIO(png)).convert("L").resize((9, 8))
    px = img.tobytes()
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
    return bits


def distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


class ScreenMemory:
    """Remembers distinct screens seen so far, to measure visual variety over time."""

    def __init__(self, threshold=10):
        self.threshold = threshold
        self.seen: list[int] = []

    def add(self, fp: int) -> bool:
        if all(distance(fp, s) > self.threshold for s in self.seen):
            self.seen.append(fp)
            return True
        return False
