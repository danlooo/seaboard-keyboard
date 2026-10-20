"""Keyboard layout definition and loading.

A layout is deliberately data, not code: it lives in a TOML file so the
mapping can be retuned by editing text rather than by patching the driver.

Structure::

    [board]
    base_note = 48
    octaves = 2

    [characters]          # what a plain strike produces, by key position
    white = ["a", "s", ...]   # 15 entries, left to right
    black = ["q", "w", ...]   # 10 entries, left to right

    [modifiers]           # what a character becomes on the shift layer
    white = ["A", "S", ...]
    black = ["Q", "W", ...]

    [symbols]             # what a key produces when slid downwards
    white = [...]
    black = ["1", "2", ...]

    [gestures]            # non-character actions
    slide_up = "shift"
    slide_down = "symbol"
    press = "repeat"
    glide_left = "backspace"
    glide_right = "space"
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .geometry import PianoGeometry


class LayoutError(ValueError):
    """Raised when a layout file is malformed or inconsistent with the board."""


@dataclass(slots=True)
class Layout:
    geometry: PianoGeometry
    #: key position index -> the character a plain strike produces
    characters: dict[tuple[str, int], str] = field(default_factory=dict)
    #: key position index -> the character produced with the shift layer active
    shifted: dict[tuple[str, int], str] = field(default_factory=dict)
    #: key position index -> the character produced with the symbol layer active
    symbols: dict[tuple[str, int], str] = field(default_factory=dict)
    #: gesture name -> action name
    gestures: dict[str, str] = field(default_factory=dict)
    name: str = "unnamed"

    def character_for(self, colour: str, index: int, layer: str = "plain") -> str | None:
        table = {
            "plain": self.characters,
            "shift": self.shifted,
            "symbol": self.symbols,
        }.get(layer)
        if table is None:
            raise LayoutError(f"unknown layer {layer!r}")
        return table.get((colour, index))

    def validate(self) -> None:
        """Check the layout actually covers the board it claims to describe."""
        expected_white = self.geometry.white_count
        expected_black = self.geometry.black_count

        for layer, table in (
            ("characters", self.characters),
            ("shifted", self.shifted),
            ("symbols", self.symbols),
        ):
            whites = [k for k in table if k[0] == "white"]
            blacks = [k for k in table if k[0] == "black"]
            if len(table) != len(whites) + len(blacks):
                raise LayoutError(f"{layer}: unknown key colour")
            if len(whites) != expected_white or len(blacks) != expected_black:
                raise LayoutError(
                    f"{layer} layer describes {len(whites)} white and "
                    f"{len(blacks)} black keys, but the board has "
                    f"{expected_white} white and {expected_black} black"
                )

    def describe(self) -> str:
        lines = [f"layout: {self.name}", f"  {self.geometry.describe()}"]
        for layer, table in (
            ("plain", self.characters),
            ("shift", self.shifted),
            ("symbol", self.symbols),
        ):
            whites = "".join(
                (table.get(("white", i)) or "?") for i in range(self.geometry.white_count)
            )
            blacks = "".join(
                (table.get(("black", i)) or "?") for i in range(self.geometry.black_count)
            )
            lines.append(f"  {layer:7s} white: {whites}")
            lines.append(f"  {layer:7s} black: {blacks}")
        if self.gestures:
            lines.append("  gestures:")
            for gesture, action in sorted(self.gestures.items()):
                lines.append(f"    {gesture:12s} -> {action}")
        return "\n".join(lines)


def _as_char(value: object, where: str) -> str:
    """Normalise a layout entry to a single character.

    Multi-character entries are allowed only for named keys ("enter", "space"),
    which the input layer resolves separately.
    """
    if not isinstance(value, str):
        raise LayoutError(f"{where}: expected a string, got {type(value).__name__}")
    if value == "":
        raise LayoutError(f"{where}: empty string is not a valid key")
    return value


def _table(raw: dict, section: str, colour: str) -> dict[tuple[str, int], str]:
    values = raw.get(section, {})
    if not isinstance(values, dict):
        raise LayoutError(f"[{section}] must be a table")
    entries = values.get(colour, [])
    if not isinstance(entries, list):
        raise LayoutError(f"[{section}].{colour} must be a list")
    out: dict[tuple[str, int], str] = {}
    for index, value in enumerate(entries):
        if value is None:
            continue  # explicitly unassigned
        out[(colour, index)] = _as_char(value, f"[{section}].{colour}[{index}]")
    return out


def load_layout(path: str | Path) -> Layout:
    """Parse a layout TOML file and return a validated :class:`Layout`."""
    path = Path(path)
    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    except FileNotFoundError:
        raise LayoutError(f"layout file not found: {path}") from None
    except tomllib.TOMLDecodeError as exc:
        raise LayoutError(f"{path}: invalid TOML: {exc}") from exc

    board = raw.get("board", {})
    geometry = PianoGeometry(
        base_note=int(board.get("base_note", 48)),
        octaves=int(board.get("octaves", 2)),
    )

    layout = Layout(
        geometry=geometry,
        characters={**_table(raw, "characters", "white"), **_table(raw, "characters", "black")},
        shifted={**_table(raw, "modifiers", "white"), **_table(raw, "modifiers", "black")},
        symbols={**_table(raw, "symbols", "white"), **_table(raw, "symbols", "black")},
        gestures=dict(raw.get("gestures", {})),
        name=path.stem,
    )
    layout.validate()
    return layout
