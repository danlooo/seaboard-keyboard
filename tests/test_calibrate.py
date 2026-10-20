"""Tests for the capture analysis used to calibrate the driver."""

from __future__ import annotations

import unittest

from seaboard_keyboard.calibrate import parse, report

SAMPLE = """listening on /dev/snd/midiC4D0
press / slide / wiggle keys on the Seaboard. ctrl-c to stop.

[  1.001] note_on    ch= 1 note= 48 vel= 92
[  1.010] cc         ch= 1 cc= 74 val= 12
[  1.090] cc         ch= 1 cc= 74 val=127
[  1.120] pitchwheel ch= 1 bend=  +120
[  1.150] note_off   ch= 1 note= 48 vel=  0
[  1.300] note_on    ch= 2 note= 60 vel=100
[  1.400] aftertouch ch= 2 val= 88
"""


class TestParse(unittest.TestCase):
    def test_counts_messages(self) -> None:
        capture = parse(SAMPLE)
        self.assertEqual(capture.total, 7)

    def test_collects_notes(self) -> None:
        capture = parse(SAMPLE)
        self.assertEqual(capture.note_range, (48, 60))
        self.assertEqual(capture.note_on_count, 2)

    def test_collects_channels(self) -> None:
        capture = parse(SAMPLE)
        self.assertEqual(capture.channels, {1, 2})

    def test_collects_cc_values(self) -> None:
        capture = parse(SAMPLE)
        self.assertEqual(capture.cc_values[74], [12, 127])

    def test_parses_signed_bend_with_padded_plus(self) -> None:
        """Probe prints bends as e.g. 'bend=  +120'; the sign must survive."""
        capture = parse(SAMPLE)
        self.assertEqual(capture.bends, [120])

    def test_parses_negative_bend(self) -> None:
        capture = parse("[  1.0] pitchwheel ch= 1 bend= -4000\n")
        self.assertEqual(capture.bends, [-4000])

    def test_collects_pressure(self) -> None:
        capture = parse(SAMPLE)
        self.assertEqual(capture.pressures, [88])

    def test_ignores_headers_and_blank_lines(self) -> None:
        capture = parse("listening on x\n\n\n")
        self.assertEqual(capture.total, 0)


class TestReport(unittest.TestCase):
    def test_empty_capture_explains_itself(self) -> None:
        text = report(parse("listening on x\n"))
        self.assertIn("No MIDI messages", text)

    def test_flags_multiple_channels_as_mpe(self) -> None:
        text = report(parse(SAMPLE))
        self.assertIn("MPE", text)

    def test_flags_single_channel_as_not_mpe(self) -> None:
        text = report(parse("[  1.0] note_on    ch= 0 note= 48 vel= 90\n"))
        self.assertIn("NOT MPE", text)

    def test_warns_when_slide_cc_missing(self) -> None:
        text = report(parse("[  1.0] note_on    ch= 1 note= 48 vel= 90\n"))
        self.assertIn("slide gestures did not send CC", text)

    def test_reports_cc74_travel(self) -> None:
        text = report(parse(SAMPLE))
        self.assertIn("CC74", text)
        self.assertIn("12..127", text)

    def test_reports_bend_deflection(self) -> None:
        text = report(parse(SAMPLE))
        self.assertIn("120", text)


if __name__ == "__main__":
    unittest.main()


GUIDED = """# SECTION plain_press
[  1.000] cc         ch= 1 cc= 74 val= 30
[  1.010] cc         ch= 1 cc= 74 val= 34
[  1.020] pitchwheel ch= 1 bend=    +4
[  1.030] pitchwheel ch= 1 bend=    -4
[  1.040] aftertouch ch= 1 val=  4
# SECTION slide_up
[  2.000] note_on    ch= 2 note= 60 vel= 99
[  2.010] cc         ch= 2 cc= 74 val= 30
[  2.020] cc         ch= 2 cc= 74 val= 48
# SECTION glide_side
[  3.000] note_on    ch= 3 note= 62 vel= 99
[  3.010] pitchwheel ch= 3 bend=   +190
# SECTION pressure
[  4.000] aftertouch ch= 4 val=  5
[  4.010] aftertouch ch= 4 val= 70
# END
"""


class TestGuidedSections(unittest.TestCase):
    def test_sections_are_labelled(self) -> None:
        capture = parse(GUIDED)
        names = [section.name for section in capture.sections]
        self.assertEqual(names, ["plain_press", "slide_up", "glide_side", "pressure"])

    def test_messages_land_in_the_right_section(self) -> None:
        capture = parse(GUIDED)
        by_name = {s.name: s for s in capture.sections}
        self.assertEqual(by_name["slide_up"].notes, [60])
        self.assertEqual(by_name["glide_side"].bends, [190])
        self.assertEqual(by_name["pressure"].pressures, [5, 70])

    def test_plain_press_noise_is_measured(self) -> None:
        by_name = {s.name: s for s in parse(GUIDED).sections}
        self.assertEqual(by_name["plain_press"].travel(), 4)
        self.assertEqual(by_name["plain_press"].deflection(), 4)

    def test_report_recommends_thresholds_between_noise_and_signal(self) -> None:
        text = report(parse(GUIDED))
        self.assertIn("RECOMMENDED CHANGES", text)
        self.assertIn("SLIDE_TRAVEL", text)
        self.assertIn("GLIDE_TRAVEL", text)
        self.assertIn("REPEAT_RISE", text)

    def test_report_flags_a_gesture_that_never_arrived(self) -> None:
        """A gesture weaker than plain-press noise cannot be used."""
        text = report(parse("# SECTION plain_press\n"
                            "[  1.010] pitchwheel ch= 1 bend=    +80\n"
                            "# SECTION glide_side\n"
                            "[  2.010] pitchwheel ch= 2 bend=    +10\n"))
        self.assertIn("did not exceed plain-press noise", text)

    def test_unguided_capture_still_reports(self) -> None:
        capture = parse(SAMPLE)
        self.assertEqual(capture.sections, [])
        self.assertIn("CC74", report(capture))
