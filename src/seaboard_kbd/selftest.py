"""End-to-end check with a simulated Seaboard.

Feeds synthetic MIDI through the real engine and sink, so the path exercised is
the same one the daemon uses. Because it never opens the hardware it can run
anywhere, including CI.

The message sequences replay what the *real* board sends — ``CC74`` and pitch
bend before ``note_on``, low absolute values, and wobble during a plain press —
not what the MPE specification alone would suggest. See ``calibrate.py``.

    python -m seaboard_kbd.selftest
"""

from __future__ import annotations

import time

from .daemon import resolve_layout
from .engine import REPEAT_RISE, SLIDE_TRAVEL, Engine
from .input import ActionSink
from .layout import load_layout
from .midi import MidiMessage

#: Values in the range the board actually reports.
Y_REST = 33
PRESS_REST = 4

#: (description, messages, expected typed text, expected named key presses)
SCENARIOS: list[tuple[str, list[MidiMessage], str, list[str]]] = [
    (
        "plain home row",
        [
            MidiMessage("cc", 0, control=74, value=Y_REST),
            MidiMessage("pitchwheel", 0, value=0),
            MidiMessage("note_on", 0, 48, 100),      # a
            MidiMessage("note_off", 0, 48, 0),
            MidiMessage("cc", 1, control=74, value=Y_REST),
            MidiMessage("note_on", 1, 50, 100),      # s
            MidiMessage("note_off", 1, 50, 0),
            MidiMessage("cc", 2, control=74, value=Y_REST),
            MidiMessage("note_on", 2, 52, 100),      # d
            MidiMessage("note_off", 2, 52, 0),
        ],
        "asd",
        [],
    ),
    (
        "qwerty top row on the black keys",
        [
            MidiMessage("cc", 0, control=74, value=Y_REST),
            MidiMessage("note_on", 0, 49, 100),      # q
            MidiMessage("note_off", 0, 49, 0),
            MidiMessage("cc", 1, control=74, value=Y_REST),
            MidiMessage("note_on", 1, 51, 100),      # w
            MidiMessage("note_off", 1, 51, 0),
            MidiMessage("cc", 2, control=74, value=Y_REST),
            MidiMessage("note_on", 2, 54, 100),      # e
            MidiMessage("note_off", 2, 54, 0),
        ],
        "qwe",
        [],
    ),
    (
        "wobble during a press is not a gesture",
        [
            MidiMessage("cc", 0, control=74, value=Y_REST),
            MidiMessage("pitchwheel", 0, value=0),
            MidiMessage("note_on", 0, 48, 100),
            MidiMessage("cc", 0, control=74, value=Y_REST + 6),
            MidiMessage("pitchwheel", 0, value=-25),
            MidiMessage("aftertouch", 0, value=41),
            MidiMessage("note_off", 0, 48, 0),
        ],
        "a",
        [],
    ),
    (
        "slide up gives upper case",
        [
            MidiMessage("cc", 0, control=74, value=Y_REST),
            MidiMessage("pitchwheel", 0, value=0),
            MidiMessage("note_on", 0, 48, 100),
            MidiMessage("cc", 0, control=74, value=Y_REST + SLIDE_TRAVEL + 5),
            MidiMessage("note_off", 0, 48, 0),
        ],
        "A",
        [],
    ),
    (
        "slide down gives the symbol layer",
        [
            MidiMessage("cc", 0, control=74, value=Y_REST),
            MidiMessage("pitchwheel", 0, value=0),
            MidiMessage("note_on", 0, 49, 100),      # black #0 -> '1' on symbols
            MidiMessage("cc", 0, control=74, value=Y_REST - SLIDE_TRAVEL - 5),
            MidiMessage("note_off", 0, 49, 0),
        ],
        "1",
        [],
    ),
    (
        "glide right presses space",
        [
            MidiMessage("pitchwheel", 0, value=0),
            MidiMessage("note_on", 0, 48, 100),
            MidiMessage("pitchwheel", 0, value=198),   # the real measured peak
            MidiMessage("note_off", 0, 48, 0),
        ],
        "",
        ["space"],
    ),
    (
        "glide left presses backspace",
        [
            MidiMessage("pitchwheel", 0, value=0),
            MidiMessage("note_on", 0, 48, 100),
            MidiMessage("pitchwheel", 0, value=-198),
            MidiMessage("note_off", 0, 48, 0),
        ],
        "",
        ["backspace"],
    ),
    (
        "two voices on separate MPE channels",
        [
            MidiMessage("cc", 1, control=74, value=Y_REST),
            MidiMessage("note_on", 1, 48, 100),       # a
            MidiMessage("cc", 2, control=74, value=Y_REST),
            MidiMessage("note_on", 2, 50, 100),       # s
            MidiMessage("cc", 2, control=74, value=Y_REST + SLIDE_TRAVEL + 5),
            MidiMessage("note_off", 2, 50, 0),        # -> S, resolved on release
            MidiMessage("note_off", 1, 48, 0),        # -> a
        ],
        # Each voice commits when its own note is released, so the slid voice
        # lands first even though it was struck second.
        "Sa",
        [],
    ),
]


def _drain(engine: Engine, sink: ActionSink, messages: list[MidiMessage], window: float) -> None:
    """Feed messages, letting the decision window close between notes."""
    for index, message in enumerate(messages):
        sink.apply(engine.handle(message))
        if message.kind == "note_off":
            time.sleep(window)
            sink.apply(engine.tick())
    time.sleep(window)
    sink.apply(engine.tick())


def run() -> int:
    """Run every scenario and report which passed."""
    layout = load_layout(resolve_layout("qwerty"))
    window = 0.08
    failures = 0

    print("seaboard-kbd self test (simulated hardware)\n")

    for description, messages, expected, expected_presses in SCENARIOS:
        engine = Engine(layout=layout)
        engine.decision_window = window / 2
        sink = ActionSink(keyboard=None, dry_run=True, echo=lambda _t: None)
        _drain(engine, sink, messages, window)

        actual = sink.text
        presses = list(sink.pressed)
        ok = actual == expected and presses == expected_presses
        if not ok:
            failures += 1
        detail = f"{actual!r}"
        if expected_presses or presses:
            detail += f" + presses {presses}"
        if not ok:
            detail += f"   expected {expected!r} + {expected_presses}"
        print(f"  {'ok  ' if ok else 'FAIL'} {description:44s} {detail}")

    # Repeat needs a prior character and a deliberate push.
    engine = Engine(layout=layout)
    engine.decision_window = window / 2
    sink = ActionSink(keyboard=None, dry_run=True, echo=lambda _t: None)
    _drain(
        engine,
        sink,
        [
            MidiMessage("cc", 0, control=74, value=Y_REST),
            MidiMessage("note_on", 0, 48, 100),
            MidiMessage("note_off", 0, 48, 0),
            MidiMessage("aftertouch", 0, value=PRESS_REST),
        ],
        window,
    )
    time.sleep(0.2)
    sink.apply(engine.handle(MidiMessage("aftertouch", 0, value=PRESS_REST + REPEAT_RISE + 5)))
    ok = sink.text == "aa"
    if not ok:
        failures += 1
    print(
        f"  {'ok  ' if ok else 'FAIL'} "
        f"{'a hard press repeats the last character':44s} {sink.text!r}"
        + ("" if ok else "   expected 'aa'")
    )

    print(f"\n{'all checks passed' if not failures else f'{failures} check(s) failed'}")
    return 1 if failures else 0


def main() -> int:
    """Entry point for the ``seaboard-kbd selftest`` subcommand."""
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
