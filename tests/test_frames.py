import io

from PIL import Image, ImageDraw

from playtest.perception.frames import ScreenMemory, change, distance, fingerprint


def png(draw=None, color=(20, 20, 30)):
    img = Image.new("RGB", (200, 120), color)
    if draw:
        draw(ImageDraw.Draw(img))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_change_is_zero_for_identical_frames():
    a = png()
    assert change(a, a) == 0


def test_change_sees_a_moving_box():
    a = png(lambda d: d.rectangle([10, 10, 50, 50], fill="white"))
    b = png(lambda d: d.rectangle([120, 60, 160, 100], fill="white"))
    assert change(a, b) > 0.05


def test_fingerprint_groups_similar_screens():
    a = png(lambda d: d.rectangle([10, 10, 100, 60], fill="white"))
    b = png(lambda d: d.rectangle([11, 10, 101, 60], fill="white"))
    c = png(lambda d: d.ellipse([120, 20, 190, 110], fill="red"))
    assert distance(fingerprint(a), fingerprint(b)) <= 4
    memory = ScreenMemory()
    assert memory.add(fingerprint(a))
    assert not memory.add(fingerprint(b))
    assert memory.add(fingerprint(c))
