"""Raw access to the Seaboard's ALSA raw-MIDI device.

``python-rtmidi`` and ``python-evdev`` both need a C compiler, which this
machine does not have, so we open the raw MIDI character device and parse the
byte stream ourselves. The MIDI wire format is small enough that this costs
about a hundred lines and removes every third-party dependency.
"""

from __future__ import annotations

import os
import select
import time
from dataclasses import dataclass


def find_seaboard() -> str:
    """Return the raw-MIDI device node of the attached Seaboard.

    Resolves by card *name* rather than a hardcoded ``hw:4`` so the driver
    keeps working when the device is replugged into a different port.
    """
    try:
        with open("/proc/asound/cards", encoding="utf-8") as fh:
            cards = fh.read()
    except OSError as exc:
        raise RuntimeError(f"cannot read /proc/asound/cards: {exc}") from exc

    for line in cards.splitlines():
        if "Seaboard" not in line and "ROLI" not in line:
            continue
        head = line.split("[", 1)[0].strip()
        if not head.isdigit():
            continue
        node = f"/dev/snd/midiC{head}D0"
        if os.path.exists(node):
            return node
        raise RuntimeError(f"Seaboard is card {head} but {node} does not exist")

    raise RuntimeError(
        "no Seaboard found in /proc/asound/cards — is it plugged in and powered on?"
    )


@dataclass(frozen=True, slots=True)
class MidiMessage:
    """One decoded MIDI message."""

    kind: str
    channel: int | None = None
    note: int | None = None
    velocity: int | None = None
    control: int | None = None
    value: int | None = None
    time: float = 0.0

    def __str__(self) -> str:
        if self.kind in ("note_on", "note_off"):
            return f"{self.kind:10s} ch={self.channel:2d} note={self.note:3d} vel={self.velocity:3d}"
        if self.kind == "cc":
            return f"{self.kind:10s} ch={self.channel:2d} cc={self.control:3d} val={self.value:3d}"
        if self.kind == "pitchwheel":
            return f"{self.kind:10s} ch={self.channel:2d} bend={self.value:+6d}"
        if self.kind in ("aftertouch", "polytouch"):
            note = f" note={self.note:3d}" if self.note is not None else ""
            return f"{self.kind:10s} ch={self.channel:2d}{note} val={self.value:3d}"
        return f"{self.kind:10s} ch={self.channel}"


#: Number of data bytes that follow each status byte, keyed by its high nibble.
_DATA_LEN = {0x8: 2, 0x9: 2, 0xA: 2, 0xB: 2, 0xC: 1, 0xD: 1, 0xE: 2}


class MidiParser:
    """Incremental MIDI byte-stream decoder, including running status."""

    def __init__(self) -> None:
        self._status = 0
        self._data: list[int] = []
        self._in_sysex = False

    def feed(self, data: bytes) -> list[MidiMessage]:
        out: list[MidiMessage] = []
        for byte in data:
            message = self._consume(byte)
            if message is not None:
                out.append(message)
        return out

    def _consume(self, byte: int) -> MidiMessage | None:
        # System realtime bytes may be interleaved anywhere, even inside
        # another message, and must not disturb running status.
        if byte >= 0xF8:
            return None

        if self._in_sysex:
            if byte == 0xF7:
                self._in_sysex = False
            return None

        if byte & 0x80:  # status byte
            if byte == 0xF0:
                self._in_sysex = True
            self._status = 0 if byte >= 0xF0 else byte
            self._data = []
            return None

        if not self._status:
            return None  # data byte with no status yet

        self._data.append(byte)
        if len(self._data) < _DATA_LEN.get(self._status >> 4, 0):
            return None

        data, self._data = self._data, []
        return self._build(self._status, data)

    @staticmethod
    def _build(status: int, data: list[int]) -> MidiMessage:
        nibble, channel = status >> 4, status & 0x0F
        now = time.monotonic()

        if nibble == 0x9:
            # Note-on with velocity 0 is the conventional note-off.
            if data[1] == 0:
                return MidiMessage("note_off", channel, data[0], 0, time=now)
            return MidiMessage("note_on", channel, data[0], data[1], time=now)
        if nibble == 0x8:
            return MidiMessage("note_off", channel, data[0], data[1], time=now)
        if nibble == 0xB:
            return MidiMessage("cc", channel, control=data[0], value=data[1], time=now)
        if nibble == 0xD:
            return MidiMessage("aftertouch", channel, value=data[0], time=now)
        if nibble == 0xA:
            return MidiMessage("polytouch", channel, note=data[0], value=data[1], time=now)
        if nibble == 0xE:
            return MidiMessage(
                "pitchwheel", channel, value=((data[1] << 7) | data[0]) - 8192, time=now
            )
        if nibble == 0xC:
            return MidiMessage("program", channel, value=data[0], time=now)
        return MidiMessage("other", channel, time=now)


class MidiDevice:
    """Blocking-with-timeout reader over an ALSA raw-MIDI character device."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or find_seaboard()
        self._fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK)
        self._parser = MidiParser()

    def read(self, timeout: float = 0.05) -> list[MidiMessage]:
        """Return messages that arrived within ``timeout`` seconds (may be empty)."""
        readable, _, _ = select.select([self._fd], [], [], timeout)
        if not readable:
            return []
        try:
            data = os.read(self._fd, 4096)
        except BlockingIOError:
            return []
        if not data:
            return []
        return self._parser.feed(data)

    def close(self) -> None:
        try:
            os.close(self._fd)
        except OSError:
            pass

    def __enter__(self) -> "MidiDevice":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
