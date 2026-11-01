from playtest.game.profile import GameProfile


def make():
    return GameProfile.from_dict({
        "title": "T", "controls": [
            {"name": "jump", "key": "Space", "click": [0.5, 0.5]},
            {"name": "left", "key": "ArrowLeft", "hold": True},
            {"name": "tap", "click": [0.2, 0.8]},
        ],
        "hooks": {"score": "window.s", "over": "window.o", "empty": ""},
        "start": ["jump"],
    }, source="code")


def test_from_dict_drops_empty_hooks():
    p = make()
    assert set(p.hooks) == {"score", "over"}
    assert p.has_hooks


def test_key_wins_over_click_and_holds_are_kept():
    p = make()
    assert p.control("jump").to_input().name == "tap:Space"
    assert p.control("left").to_input().name == "hold:ArrowLeft"
    assert p.control("tap").to_input().name == "click(0.20,0.80)"


def test_resolve_raw_steps():
    p = make()
    assert p.resolve("key:Enter").key == "Enter"
    inp = p.resolve("click:0.25,0.75")
    assert (inp.kind, inp.x, inp.y) == ("click", 0.25, 0.75)
    assert p.resolve("nope") is None


def test_to_dict_leaves_out_source():
    p = make()
    p.code = "x" * 1000
    assert "code" not in p.to_dict()
