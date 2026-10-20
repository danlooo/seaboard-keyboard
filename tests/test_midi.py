"""Tests for the MIDI byte-stream parser.

The parser sees a stream of raw bytes and has to reconstruct messages,
including running status, which the Seaboard uses heavily for its continuous
per-note streams.
"""

from __future__ import annotations

import unittest

from seaboard_keyboard.midi import MidiParser


class TestMidiParser(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = MidiParser()

    def test_single_note_on(self) -> None:
        msgs = self.parser.feed(bytes([0x90, 60, 100]))
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].kind, "note_on")
        self.assertEqual(msgs[0].note, 60)
        self.assertEqual(msgs[0].velocity, 100)
        self.assertEqual(msgs[0].channel, 0)

    def test_note_on_zero_velocity_is_note_off(self) -> None:
        msgs = self.parser.feed(bytes([0x90, 60, 0]))
        self.assertEqual(msgs[0].kind, "note_off")
        self.assertEqual(msgs[0].note, 60)

    def test_explicit_note_off(self) -> None:
        self.parser.feed(bytes([0x90, 60, 100]))
        msgs = self.parser.feed(bytes([0x80, 60, 64]))
        self.assertEqual(msgs[0].kind, "note_off")
        self.assertEqual(msgs[0].note, 60)

    def test_running_status(self) -> None:
        # One status byte followed by three note payloads.
        msgs = self.parser.feed(bytes([0x90, 60, 100, 62, 101, 64, 102]))
        self.assertEqual([m.note for m in msgs], [60, 62, 64])
        self.assertTrue(all(m.kind == "note_on" for m in msgs))

    def test_split_across_reads(self) -> None:
        # Bytes arriving in pieces must still form whole messages.
        msgs = self.parser.feed(bytes([0x90, 60]))
        self.assertEqual(msgs, [])
        msgs = self.parser.feed(bytes([100]))
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].note, 60)

    def test_split_between_status_and_running_data(self) -> None:
        msgs = self.parser.feed(bytes([0x90, 60, 100, 62]))
        self.assertEqual(len(msgs), 1)
        msgs = self.parser.feed(bytes([101]))
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].note, 62)

    def test_control_change(self) -> None:
        msgs = self.parser.feed(bytes([0xB0, 74, 127]))
        self.assertEqual(msgs[0].kind, "cc")
        self.assertEqual(msgs[0].control, 74)
        self.assertEqual(msgs[0].value, 127)

    def test_channel_extracted_from_status(self) -> None:
        msgs = self.parser.feed(bytes([0x9F, 60, 100]))
        self.assertEqual(msgs[0].channel, 15)

    def test_pitchwheel_is_signed(self) -> None:
        msgs = self.parser.feed(bytes([0xE0, 0x00, 0x40]))  # centre
        self.assertEqual(msgs[0].kind, "pitchwheel")
        self.assertEqual(msgs[0].value, 0)
        msgs = self.parser.feed(bytes([0xE0, 0x00, 0x00]))  # full down
        self.assertEqual(msgs[0].value, -8192)
        msgs = self.parser.feed(bytes([0xE0, 0x7F, 0x7F]))  # full up
        self.assertEqual(msgs[0].value, 8191)

    def test_channel_aftertouch(self) -> None:
        msgs = self.parser.feed(bytes([0xD0, 80]))
        self.assertEqual(msgs[0].kind, "aftertouch")
        self.assertEqual(msgs[0].value, 80)

    def test_poly_aftertouch(self) -> None:
        msgs = self.parser.feed(bytes([0xA0, 60, 55]))
        self.assertEqual(msgs[0].kind, "polytouch")
        self.assertEqual(msgs[0].note, 60)
        self.assertEqual(msgs[0].value, 55)

    def test_realtime_bytes_do_not_disturb_running_status(self) -> None:
        # Clock (0xF8) can arrive between any two bytes and must be ignored
        # without clearing running status.
        msgs = self.parser.feed(bytes([0x90, 60, 100, 0xF8, 62, 101]))
        self.assertEqual([m.note for m in msgs], [60, 62])

    def test_sysex_is_skipped(self) -> None:
        msgs = self.parser.feed(bytes([0xF0, 0x7E, 0x00, 0x06, 0x01, 0xF7, 0x90, 60, 100]))
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].note, 60)

    def test_data_byte_without_status_is_ignored(self) -> None:
        msgs = self.parser.feed(bytes([60, 100]))
        self.assertEqual(msgs, [])

    def test_system_common_resets_status(self) -> None:
        self.parser.feed(bytes([0x90, 60, 100]))
        self.parser.feed(bytes([0xF1, 0x00]))  # MTC quarter frame
        msgs = self.parser.feed(bytes([62, 101]))  # stray data, no status
        self.assertEqual(msgs, [])


if __name__ == "__main__":
    unittest.main()
