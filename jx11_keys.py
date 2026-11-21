"""
Config-driven key mapping for the JX-11 ring (macOS).

Each ring event is bound to an action in a JSON keymap:

    {
      "scroll_forward": {"keys": "down"},
      "scroll_back":    {"keys": "up"},
      "left":           {"keys": "cmd+left"},
      "middle":         {"keys": "space"},
      "top":            {"text": "hello"},
      "middle_double":  {"shell": "open -a Safari"}
    }

Event names: scroll_forward, scroll_back, top, left, right, middle,
middle_double, left_double, right_double. Unbound events do nothing.

Action kinds (exactly one per binding):
  keys   "mod+mod+key" — modifiers cmd/shift/alt(option)/ctrl, key from KEY_CODES
         or a single printable character. A list of chords runs in order.
  text   type a literal string.
  shell  run a command with /bin/sh (your own config only; never load a
         keymap you did not write).

Keystrokes go through `osascript` (System Events), so the terminal or Python
running this needs Accessibility permission: System Settings → Privacy &
Security → Accessibility.

Run:
    python jx11_keys.py --config keymap.json            # drive the ring
    python jx11_keys.py --config keymap.json --check    # validate only
    python jx11_keys.py --config keymap.json --dry-run  # print, don't press
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger("jx11_keys")

EVENTS = (
    "scroll_forward",
    "scroll_back",
    "top",
    "left",
    "right",
    "middle",
    "middle_double",
    "left_double",
    "right_double",
)

MODIFIERS = {
    "cmd": "command down",
    "command": "command down",
    "shift": "shift down",
    "alt": "option down",
    "option": "option down",
    "opt": "option down",
    "ctrl": "control down",
    "control": "control down",
}

# macOS virtual key codes (Carbon kVK_*) for keys that `keystroke` can't send.
KEY_CODES = {
    "return": 36, "enter": 76, "tab": 48, "space": 49, "delete": 51,
    "backspace": 51, "forwarddelete": 117, "escape": 53, "esc": 53,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97,
    "f7": 98, "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
}


class KeymapError(ValueError):
    pass


def _applescript_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def chord_script(chord: str) -> str:
    """One AppleScript System Events statement for a chord like "cmd+shift+t"."""
    if not isinstance(chord, str) or not chord.strip():
        raise KeymapError("empty key chord")
    parts = [p.strip() for p in chord.split("+")]
    # "cmd++" means cmd and the plus key.
    if chord.endswith("++"):
        parts = parts[:-2] + ["+"]
    *mods, key = parts
    using = []
    for m in mods:
        name = m.lower()
        if name not in MODIFIERS:
            raise KeymapError(f"unknown modifier {m!r} in {chord!r}")
        if MODIFIERS[name] not in using:
            using.append(MODIFIERS[name])
    if not key:
        raise KeymapError(f"missing key in {chord!r}")
    lower = key.lower()
    if lower in KEY_CODES:
        stmt = f"key code {KEY_CODES[lower]}"
    elif len(key) == 1 and key.isprintable():
        stmt = f"keystroke {_applescript_string(key)}"
    else:
        raise KeymapError(f"unknown key {key!r} in {chord!r}")
    if using:
        stmt += " using {" + ", ".join(using) + "}"
    return stmt


@dataclass(frozen=True)
class Action:
    kind: str      # "keys" | "text" | "shell"
    value: tuple   # tuple of chords, or (text,), or (command,)

    def describe(self) -> str:
        return f"{self.kind}: {' , '.join(self.value)}"


def parse_action(event: str, raw) -> Action:
    if not isinstance(raw, dict):
        raise KeymapError(f"{event}: binding must be an object like {{\"keys\": \"down\"}}")
    kinds = [k for k in ("keys", "text", "shell") if k in raw]
    unknown = set(raw) - {"keys", "text", "shell"}
    if unknown:
        raise KeymapError(f"{event}: unknown field(s) {sorted(unknown)}")
    if len(kinds) != 1:
        raise KeymapError(f"{event}: use exactly one of keys / text / shell")
    kind = kinds[0]
    value = raw[kind]
    if kind == "keys":
        chords = [value] if isinstance(value, str) else value
        if not isinstance(chords, list) or not chords or not all(isinstance(c, str) for c in chords):
            raise KeymapError(f"{event}: keys must be a chord string or a list of chords")
        for c in chords:
            chord_script(c)  # validate early
        return Action("keys", tuple(chords))
    if not isinstance(value, str) or not value:
        raise KeymapError(f"{event}: {kind} must be a non-empty string")
    return Action(kind, (value,))


def parse_keymap(data) -> dict[str, Action]:
    if not isinstance(data, dict):
        raise KeymapError("keymap must be a JSON object of event → binding")
    keymap = {}
    for event, raw in data.items():
        if event.startswith("_"):   # "_comment" keys are allowed
            continue
        if event not in EVENTS:
            raise KeymapError(f"unknown event {event!r}; expected one of {', '.join(EVENTS)}")
        keymap[event] = parse_action(event, raw)
    return keymap


def load_keymap(path: str) -> dict[str, Action]:
    with open(path, encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError as e:
            raise KeymapError(f"{path}: invalid JSON: {e}") from e
    return parse_keymap(data)


def osascript_runner(script: str) -> None:
    subprocess.run(["osascript", "-e", f'tell application "System Events" to {script}'],
                   check=False, timeout=5)


def shell_runner(command: str) -> None:
    subprocess.Popen(["/bin/sh", "-c", command])


class Dispatcher:
    """Turns ring events into actions. I/O is injected so it is testable."""

    def __init__(self, keymap: dict[str, Action],
                 run_script: Callable[[str], None] = osascript_runner,
                 run_shell: Callable[[str], None] = shell_runner):
        self.keymap = keymap
        self._run_script = run_script
        self._run_shell = run_shell

    def handle(self, event: str) -> None:
        action = self.keymap.get(event)
        if action is None:
            log.debug("unbound event %s", event)
            return
        log.info("%s → %s", event, action.describe())
        if action.kind == "keys":
            for chord in action.value:
                self._run_script(chord_script(chord))
        elif action.kind == "text":
            self._run_script(f"keystroke {_applescript_string(action.value[0])}")
        elif action.kind == "shell":
            self._run_shell(action.value[0])

    def on_scroll(self, steps: int) -> None:
        self.handle("scroll_forward" if steps > 0 else "scroll_back")

    def attach(self, ring) -> None:
        ring.on("scroll", self.on_scroll)
        for event in EVENTS:
            if not event.startswith("scroll_"):
                ring.on(event, lambda e=event: self.handle(e))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Map JX-11 ring buttons to keys (macOS).")
    ap.add_argument("--config", default="keymap.json", help="keymap JSON (default: keymap.json)")
    ap.add_argument("--check", action="store_true", help="validate the keymap and exit")
    ap.add_argument("--dry-run", action="store_true", help="log actions instead of performing them")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(message)s")
    try:
        keymap = load_keymap(args.config)
    except (OSError, KeymapError) as e:
        print(f"keymap error: {e}", file=sys.stderr)
        return 2
    if args.check:
        for event in EVENTS:
            if event in keymap:
                print(f"{event:15} {keymap[event].describe()}")
        print(f"ok: {len(keymap)} binding(s)")
        return 0
    if args.dry_run:
        dispatcher = Dispatcher(keymap,
                                run_script=lambda s: log.info("  would run: %s", s),
                                run_shell=lambda c: log.info("  would shell: %s", c))
    else:
        dispatcher = Dispatcher(keymap)

    from jx11 import Ring   # needs `hid` + hidapi; imported late so tests don't

    ring = Ring()
    dispatcher.attach(ring)
    ring.on("connect", lambda: log.info("ring connected"))
    ring.on("disconnect", lambda: log.info("ring disconnected"))
    ring.start()
    log.info("running — Ctrl+C to stop")
    try:
        import threading
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        ring.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
