"""Tests for the gesture engine.

These drive the engine with synthetic MIDI so every gesture path is exercised
without hardware: plain strikes, slides, glides, repeats and the mode toggle.

The message sequences here deliberately mirror what the real board sends,
which was measured with ``probe.py`` and is not what the MPE specification
alone would suggest:

* ``CC74`` and pitch bend arrive **before** ``note_on``, not after it.
* The absolute values are low — CC74 sat at 32-42, bend reached at most 198
  for a deliberate glide, and pressure peaked at 49.
* A plain press still produces a continuous stream of small bend and CC74
  wobble, so gestures have to be detected as travel away from a baseline
  rather than against fixed thresholds.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from seaboard_keyboard.engine import (
    GLIDE_TRAVEL,
    REPEAT_RISE,
    SLIDE_TRAVEL,
    Engine,
    PressKey,
    ToggleMode,
    TypeChar,
)
from seaboard_keyboard.geometry import PianoGeometry
from seaboard_keyboard.layout import LayoutError, load_layout
from seaboard_keyboard.midi import MidiMessage

LAYOUT = Path(__file__).resolve().parent.parent / "layouts" / "qwerty.toml"

#: Values in the range the board actually reports.
Y_REST = 33
PRESS_REST = 4


def note_on(note: int, channel: int = 0) -> MidiMessage:
    return MidiMessage("note_on", channel, note, 100, time=0.0)


def note_off(note: int, channel: int = 0) -> MidiMessage:
    return MidiMessage("note_off", channel, note, 0, time=0.0)


def cc(control: int, value: int, channel: int = 0) -> MidiMessage:
    return MidiMessage("cc", channel, control=control, value=value, time=0.0)


def bend(value: int, channel: int = 0) -> MidiMessage:
    return MidiMessage("pitchwheel", channel, value=value, time=0.0)


def aftertouch(value: int, channel: int = 0) -> MidiMessage:
    return MidiMessage("aftertouch", channel, value=value, time=0.0)


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class EngineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.layout = load_layout(LAYOUT)
        self.clock = FakeClock()
        self.engine = Engine(layout=self.layout, clock=self.clock)

    def settle(self, *messages: MidiMessage) -> list[object]:
        """Feed messages, then let the decision window close, and collect."""
        out: list[object] = []
        for message in messages:
            out += self.engine.handle(message)
        self.clock.advance(self.engine.decision_window + 0.001)
        out += self.engine.tick()
        return out

    def press(self, note: int, channel: int = 0) -> list[object]:
        """A realistic plain press, as the board actually sends it."""
        return self.settle(
            cc(74, Y_REST, channel),          # Y baseline, before the note
            bend(0, channel),                 # bend baseline, before the note
            note_on(note, channel),
            cc(74, Y_REST + 2, channel),      # small wobble during the press
            bend(-6, channel),
            note_off(note, channel),
        )


class TestGeometry(unittest.TestCase):
    def test_two_octaves_has_25_keys(self) -> None:
        geometry = PianoGeometry(48, 2)
        self.assertEqual(geometry.white_count, 15)
        self.assertEqual(geometry.black_count, 10)
        self.assertEqual(len(geometry.notes), 25)

    def test_lowest_note_is_white_c(self) -> None:
        position = PianoGeometry(48, 2).position(48)
        self.assertEqual((position.colour, position.degree, position.index), ("white", "C", 0))

    def test_black_key_positions(self) -> None:
        position = PianoGeometry(48, 2).position(49)
        self.assertEqual((position.colour, position.index, position.degree), ("black", 0, "C#"))

    def test_top_c_is_white_and_last(self) -> None:
        position = PianoGeometry(48, 2).position(72)
        self.assertEqual((position.colour, position.index), ("white", 14))

    def test_off_board_note(self) -> None:
        geometry = PianoGeometry(48, 2)
        self.assertIsNone(geometry.position(47))
        self.assertIsNone(geometry.position(73))


class TestLayoutLoading(unittest.TestCase):
    def test_loads_and_validates(self) -> None:
        self.assertEqual(len(load_layout(LAYOUT).characters), 25)

    def test_qwerty_top_row_on_black_keys(self) -> None:
        layout = load_layout(LAYOUT)
        blacks = "".join(layout.characters[("black", i)] for i in range(10))
        self.assertEqual(blacks, "qwertyuiop")

    def test_white_keys_carry_home_then_bottom_row(self) -> None:
        layout = load_layout(LAYOUT)
        whites = "".join(layout.characters[("white", i)] for i in range(15))
        self.assertEqual(whites, "asdfghjklzxcvbn")

    def test_m_lives_on_symbol_layer(self) -> None:
        layout = load_layout(LAYOUT)
        self.assertIn("m", [layout.symbols[("white", i)] for i in range(15)])

    def test_every_letter_reachable(self) -> None:
        layout = load_layout(LAYOUT)
        reachable = set(layout.characters.values()) | set(layout.symbols.values())
        for letter in "abcdefghijklmnopqrstuvwxyz":
            self.assertIn(letter, reachable, f"{letter} is unreachable")

    def test_wrong_key_count_is_rejected(self) -> None:
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as fh:
            fh.write(
                '[board]\nbase_note = 48\noctaves = 2\n'
                '[characters]\nwhite = ["a"]\nblack = []\n'
                '[modifiers]\nwhite = ["A"]\nblack = []\n'
                '[symbols]\nwhite = ["a"]\nblack = []\n'
            )
            path = fh.name
        with self.assertRaises(LayoutError):
            load_layout(path)


class TestPlainTyping(EngineTestCase):
    def test_lowest_white_key_types_a(self) -> None:
        self.assertIn(TypeChar("a", "plain"), self.press(48))

    def test_black_key_types_q(self) -> None:
        self.assertIn(TypeChar("q", "plain"), self.press(49))

    def test_second_white_key_types_s(self) -> None:
        self.assertIn(TypeChar("s", "plain"), self.press(50))

    def test_typing_waits_for_the_decision_window(self) -> None:
        self.engine.handle(note_on(48))
        self.assertEqual(self.engine.tick(), [], "must not type before the window closes")
        self.clock.advance(0.1)
        self.assertEqual(self.engine.tick(), [TypeChar("a", "plain")])

    def test_off_board_note_types_nothing(self) -> None:
        self.assertEqual(self.settle(note_on(40), note_off(40)), [])

    def test_release_before_the_window_resolves_immediately(self) -> None:
        self.engine.handle(note_on(48))
        self.clock.advance(0.01)
        self.assertEqual(self.engine.handle(note_off(48)), [TypeChar("a", "plain")])

    def test_a_strike_types_once_only(self) -> None:
        self.engine.handle(note_on(48))
        self.clock.advance(0.1)
        self.assertEqual(len(self.engine.tick()), 1)
        self.clock.advance(0.1)
        self.assertEqual(self.engine.tick(), [])

    def test_wobble_during_a_plain_press_does_not_trigger_a_gesture(self) -> None:
        """The board emits bend/CC74 noise on every press; it must not gesture."""
        out = self.press(48)
        self.assertEqual([a for a in out if isinstance(a, TypeChar)], [TypeChar("a", "plain")])
        self.assertEqual([a for a in out if isinstance(a, PressKey)], [])


class TestSlideGestures(EngineTestCase):
    def test_cc74_before_note_on_sets_the_baseline(self) -> None:
        """The board sends CC74 first; using it as the baseline is the fix."""
        baseline = self.engine.handle(cc(74, Y_REST))
        self.assertEqual(baseline, [])
        self.engine.handle(note_on(48))
        # A slide is travel from the baseline, not an absolute value.
        self.engine.handle(cc(74, Y_REST + SLIDE_TRAVEL))
        self.clock.advance(self.engine.decision_window + 0.001)
        self.assertEqual(self.engine.tick(), [TypeChar("A", "shift")])

    def test_slide_up_shifts(self) -> None:
        out = self.settle(
            cc(74, Y_REST), bend(0), note_on(48),
            cc(74, Y_REST + SLIDE_TRAVEL + 3), note_off(48),
        )
        self.assertIn(TypeChar("A", "shift"), out)

    def test_slide_down_uses_the_symbol_layer(self) -> None:
        out = self.settle(
            cc(74, Y_REST), bend(0), note_on(48),
            cc(74, Y_REST - SLIDE_TRAVEL - 3), note_off(48),
        )
        self.assertIn(TypeChar(",", "symbol"), out)

    def test_slide_just_under_the_threshold_does_not_trigger(self) -> None:
        out = self.settle(
            cc(74, Y_REST), bend(0), note_on(48),
            cc(74, Y_REST + SLIDE_TRAVEL - 1), note_off(48),
        )
        self.assertIn(TypeChar("a", "plain"), out)

    def test_measured_wobble_stays_below_the_threshold(self) -> None:
        """Real captures showed +-6 of CC74 drift on a plain press."""
        out = self.settle(
            cc(74, 32), bend(0), note_on(48),
            cc(74, 38), note_off(48),
        )
        self.assertIn(TypeChar("a", "plain"), out)

    def test_cc1_also_counts_as_slide(self) -> None:
        out = self.settle(
            cc(1, Y_REST), note_on(48), cc(1, Y_REST + SLIDE_TRAVEL), note_off(48),
        )
        self.assertIn(TypeChar("A", "shift"), out)

    def test_slide_does_not_double_type(self) -> None:
        out = self.settle(
            cc(74, Y_REST), bend(0), note_on(48),
            cc(74, Y_REST + SLIDE_TRAVEL + 3), note_off(48),
        )
        typed = [a for a in out if isinstance(a, TypeChar)]
        self.assertEqual(len(typed), 1)

    def test_slide_down_reaches_every_white_key(self) -> None:
        for index in range(self.layout.geometry.white_count):
            note = self.layout.geometry.whites[index]
            engine = Engine(layout=self.layout, clock=self.clock)
            out: list[object] = []
            out += engine.handle(cc(74, Y_REST))
            out += engine.handle(bend(0))
            out += engine.handle(note_on(note))
            out += engine.handle(cc(74, Y_REST - SLIDE_TRAVEL - 5))
            out += engine.handle(note_off(note))
            self.clock.advance(0.2)
            out += engine.tick()
            self.assertTrue(
                any(isinstance(a, TypeChar) for a in out),
                f"white key {index} (note {note}) typed nothing on slide down",
            )


class TestGlideGestures(EngineTestCase):
    def test_glide_is_measured_from_the_notes_baseline(self) -> None:
        """Measured: a deliberate glide reached +198; a plain press stayed +-25."""
        out = self.settle(
            bend(0), note_on(48),
            bend(GLIDE_TRAVEL + 78),
            note_off(48),
        )
        self.assertIn(PressKey("space", "glide"), out)

    def test_plain_press_bend_noise_is_not_a_glide(self) -> None:
        out = self.settle(
            bend(0), note_on(48),
            bend(-25),            # the largest wobble seen in a real press
            note_off(48),
        )
        self.assertIn(TypeChar("a", "plain"), out)
        self.assertEqual([a for a in out if isinstance(a, PressKey)], [])

    def test_glide_left_deletes(self) -> None:
        out = self.settle(bend(0), note_on(48), bend(-GLIDE_TRAVEL - 78), note_off(48))
        self.assertIn(PressKey("backspace", "glide"), out)


class TestRepeatGesture(EngineTestCase):
    def test_pressure_rise_repeats_the_last_character(self) -> None:
        self.press(48)  # types 'a'
        self.clock.advance(0.2)
        out = self.settle(aftertouch(PRESS_REST), aftertouch(PRESS_REST + REPEAT_RISE + 5))
        self.assertIn(TypeChar("a", "repeat"), out)

    def test_a_real_press_pressure_does_not_repeat(self) -> None:
        """Measured pressure during a plain press peaked at 41 from a base of ~2."""
        self.press(48)
        self.clock.advance(0.2)
        out = self.settle(aftertouch(2), aftertouch(20), aftertouch(41))
        self.assertEqual([a for a in out if isinstance(a, TypeChar) and a.source == "repeat"], [])

    def test_nothing_to_repeat(self) -> None:
        self.assertEqual(self.settle(aftertouch(0), aftertouch(80)), [])

    def test_holding_pressure_does_not_spray_characters(self) -> None:
        self.press(48)
        self.clock.advance(0.2)
        first = self.engine.handle(aftertouch(0))
        first += self.engine.handle(aftertouch(REPEAT_RISE + 20))
        self.assertEqual(len(first), 1)
        # Holding at the same pressure must not keep repeating.
        self.clock.advance(0.2)
        self.assertEqual(self.engine.handle(aftertouch(REPEAT_RISE + 20)), [])

    def test_repeat_is_rate_limited(self) -> None:
        self.press(48)
        self.clock.advance(0.2)
        push = REPEAT_RISE + 20
        self.engine.handle(aftertouch(0))
        self.assertEqual(len(self.engine.handle(aftertouch(push))), 1)
        # Easing off and pushing again immediately is still too soon.
        self.engine.handle(aftertouch(0))
        self.assertEqual(self.engine.handle(aftertouch(push)), [], "too soon to repeat again")
        # After the interval, and with a fresh push, it fires again.
        self.clock.advance(0.2)
        self.engine.handle(aftertouch(0))
        self.assertEqual(len(self.engine.handle(aftertouch(push))), 1)


class TestModeToggle(EngineTestCase):
    def test_holding_the_top_key_toggles_mode(self) -> None:
        self.engine.handle(note_on(72))
        self.clock.advance(self.engine.toggle_hold + 0.01)
        out = self.engine.tick()
        self.assertTrue(any(isinstance(a, ToggleMode) for a in out))
        self.assertFalse(self.engine.typing_enabled)

    def test_toggle_suppresses_the_key_itself(self) -> None:
        self.engine.handle(note_on(72))
        self.clock.advance(self.engine.toggle_hold + 0.01)
        out = self.engine.tick()
        self.assertEqual([a for a in out if isinstance(a, TypeChar)], [])

    def test_short_tap_of_the_top_key_types_normally(self) -> None:
        self.engine.handle(note_on(72))
        self.clock.advance(0.05)
        self.assertEqual(self.engine.handle(note_off(72)), [TypeChar("n", "plain")])

    def test_notes_played_in_midi_mode_stay_silent(self) -> None:
        """Switching to MIDI must not make the next notes type characters."""
        self.engine.typing_enabled = False
        out = self.settle(cc(74, Y_REST), bend(0), note_on(60), note_off(60))
        self.assertEqual(out, [])

    def test_toggle_back_to_typing(self) -> None:
        for _ in range(2):
            self.engine.handle(note_on(72))
            self.clock.advance(self.engine.toggle_hold + 0.01)
            self.engine.tick()
            self.engine.handle(note_off(72))
            self.clock.advance(0.01)
            self.engine.tick()
        self.assertTrue(self.engine.typing_enabled)


class TestMpeChannels(EngineTestCase):
    def test_simultaneous_notes_on_separate_channels(self) -> None:
        out = self.settle(
            cc(74, Y_REST, 1), note_on(48, 1),
            cc(74, Y_REST, 2), note_on(50, 2),
        )
        chars = {a.char for a in out if isinstance(a, TypeChar)}
        self.assertEqual(chars, {"a", "s"})

    def test_a_slide_only_affects_its_own_channel(self) -> None:
        out = self.settle(
            cc(74, Y_REST, 1), bend(0, 1), note_on(48, 1),
            cc(74, Y_REST, 2), bend(0, 2), note_on(50, 2),
            cc(74, Y_REST + SLIDE_TRAVEL + 5, 2),
            note_off(50, 2),
        )
        chars = sorted(a.char for a in out if isinstance(a, TypeChar))
        self.assertEqual(chars, ["S", "a"])

    def test_bend_on_another_channel_is_ignored(self) -> None:
        out = self.settle(
            bend(0, 1), note_on(48, 1),
            bend(GLIDE_TRAVEL + 500, 9),   # a different voice
            note_off(48, 1),
        )
        self.assertIn(TypeChar("a", "plain"), out)


class TestLog(unittest.TestCase):
    def test_log_is_bounded(self) -> None:
        layout = load_layout(LAYOUT)
        clock = FakeClock()
        engine = Engine(layout=layout, clock=clock)
        for _ in range(200):
            engine.handle(note_on(48))
            clock.advance(0.1)
            engine.tick()
        self.assertLessEqual(len(engine.log), 40)


if __name__ == "__main__":
    unittest.main()
