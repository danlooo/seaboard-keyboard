"""Piano geometry: which physical key is which, and where it sits.

The Seaboard reports absolute MIDI note numbers, but a layout should be
described in terms that survive transposition — "the third white key" rather
than "note 52". This module converts between the two.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Semitone offsets of the white keys within an octave, in pitch order.
WHITE_DEGREES: tuple[tuple[str, int], ...] = (
    ("C", 0), ("D", 2), ("E", 4), ("F", 5), ("G", 7), ("A", 9), ("B", 11),
)

#: Semitone offsets of the black keys within an octave, in pitch order.
BLACK_DEGREES: tuple[tuple[str, int], ...] = (
    ("C#", 1), ("D#", 3), ("F#", 6), ("G#", 8), ("A#", 10),
)


@dataclass(frozen=True, slots=True)
class KeyPosition:
    """Where a key sits on the board, in layout terms."""

    colour: str  # "white" or "black"
    index: int  # position among keys of that colour, left to right
    octave: int  # octave number relative to the board's lowest C
    degree: str  # e.g. "C", "F#"

    def __str__(self) -> str:
        return f"{self.degree}{self.octave} ({self.colour} #{self.index})"


class PianoGeometry:
    """A keyboard spanning ``octaves`` octaves starting at a given MIDI note.

    With ``octaves=2`` this yields 15 white and 10 black keys — the shape of a
    Seaboard Block M / original Block.
    """

    def __init__(self, base_note: int = 48, octaves: int = 2) -> None:
        self.base_note = base_note
        self.octaves = octaves

        self.whites: list[int] = []  # note numbers, left to right
        self.blacks: list[int] = []
        self._positions: dict[int, KeyPosition] = {}

        for octave in range(octaves):
            for name, semitone in WHITE_DEGREES:
                note = base_note + octave * 12 + semitone
                self._positions[note] = KeyPosition(
                    "white", len(self.whites), octave, name
                )
                self.whites.append(note)
            for name, semitone in BLACK_DEGREES:
                note = base_note + octave * 12 + semitone
                self._positions[note] = KeyPosition(
                    "black", len(self.blacks), octave, name
                )
                self.blacks.append(note)

        # The board's top C is a white key with no black keys above it.
        top_c = base_note + octaves * 12
        self._positions[top_c] = KeyPosition("white", len(self.whites), octaves, "C")
        self.whites.append(top_c)

        self.notes = frozenset(self._positions)

    @property
    def white_count(self) -> int:
        return len(self.whites)

    @property
    def black_count(self) -> int:
        return len(self.blacks)

    def position(self, note: int) -> KeyPosition | None:
        """Layout position of a MIDI note, or None if it is off the board."""
        return self._positions.get(note)

    def is_on_board(self, note: int) -> bool:
        return note in self._positions

    def describe(self) -> str:
        return (
            f"base={self.base_note} octaves={self.octaves} "
            f"notes={self.base_note}..{self.base_note + self.octaves * 12} "
            f"({self.white_count} white, {self.black_count} black)"
        )
