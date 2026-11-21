import json

import pytest

from jx11_keys import (
    EVENTS,
    Dispatcher,
    KeymapError,
    chord_script,
    load_keymap,
    parse_keymap,
)


def test_named_keys_use_key_codes():
    assert chord_script("down") == "key code 125"
    assert chord_script("Return") == "key code 36"


def test_modifiers_and_characters():
    assert chord_script("cmd+shift+t") == 'keystroke "t" using {command down, shift down}'
    assert chord_script("alt+left") == "key code 123 using {option down}"
    assert chord_script("cmd++") == 'keystroke "+" using {command down}'


def test_quotes_are_escaped():
    assert chord_script('"') == 'keystroke "\\""'


@pytest.mark.parametrize("bad", ["", "hyper+a", "cmd+", "cmd+nosuchkey"])
def test_bad_chords_are_rejected(bad):
    with pytest.raises(KeymapError):
        chord_script(bad)


def test_parse_rejects_unknown_event_and_mixed_kinds():
    with pytest.raises(KeymapError, match="unknown event"):
        parse_keymap({"wheel": {"keys": "down"}})
    with pytest.raises(KeymapError, match="exactly one"):
        parse_keymap({"top": {"keys": "a", "text": "b"}})
    with pytest.raises(KeymapError, match="unknown field"):
        parse_keymap({"top": {"key": "a"}})


def test_comment_keys_are_ignored():
    assert parse_keymap({"_comment": "x", "top": {"keys": "a"}}).keys() == {"top"}


def test_example_keymap_is_valid():
    keymap = load_keymap("keymap.example.json")
    assert set(keymap) == set(EVENTS)


def _dispatcher(data):
    scripts, shells = [], []
    d = Dispatcher(parse_keymap(data), run_script=scripts.append, run_shell=shells.append)
    return d, scripts, shells


def test_dispatch_keys_text_shell_and_scroll_direction():
    d, scripts, shells = _dispatcher({
        "scroll_forward": {"keys": "down"},
        "scroll_back": {"keys": ["up", "up"]},
        "top": {"text": 'hi "x"'},
        "middle_double": {"shell": "echo ok"},
    })
    d.on_scroll(1)
    d.on_scroll(-1)
    d.handle("top")
    d.handle("middle_double")
    d.handle("left")  # unbound: nothing
    assert scripts == ["key code 125", "key code 126", "key code 126", 'keystroke "hi \\"x\\""']
    assert shells == ["echo ok"]


def test_attach_registers_every_event():
    class FakeRing:
        def __init__(self):
            self.handlers = {}

        def on(self, event, handler):
            self.handlers[event] = handler

    d, scripts, _ = _dispatcher({"right_double": {"keys": "cmd+right"}, "scroll_forward": {"keys": "down"}})
    ring = FakeRing()
    d.attach(ring)
    assert set(ring.handlers) == {"scroll"} | {e for e in EVENTS if not e.startswith("scroll_")}
    ring.handlers["right_double"]()
    ring.handlers["scroll"](1)
    assert scripts == ["key code 124 using {command down}", "key code 125"]


def test_load_keymap_reports_invalid_json(tmp_path):
    p = tmp_path / "k.json"
    p.write_text("{nope")
    with pytest.raises(KeymapError, match="invalid JSON"):
        load_keymap(str(p))
    p.write_text(json.dumps({"top": {"keys": "a"}}))
    assert load_keymap(str(p))["top"].value == ("a",)
