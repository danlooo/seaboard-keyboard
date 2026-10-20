"""Analyse a probe capture to calibrate the driver against real hardware.

Every threshold in ``engine.py`` is currently a guess. This reads a capture
from ``probe.py`` and reports what the board actually sent, so the guesses can
be replaced with measurements.

    python -m seaboard_keyboard.probe > capture.log     # then play the board
    python -m seaboard_keyboard.calibrate capture.log
"""

from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .engine import GLIDE_TRAVEL, REPEAT_RISE, SLIDE_TRAVEL

_LINE = re.compile(
    r"^\[\s*(?P<t>[\d.]+)\]\s+(?P<kind>\w+)\s+ch=\s*(?P<ch>\d+)"
    r"(?:\s+note=\s*(?P<note>\d+))?"
    r"(?:\s+cc=\s*(?P<cc>\d+))?"
    r"(?:\s+bend=\s*(?P<bend>[+-]?\d+))?"
    r"(?:\s+val=\s*(?P<val>\d+))?"
)


@dataclass
class Section:
    """One labelled gesture from a guided capture."""

    name: str
    cc74: list[int] = field(default_factory=list)
    bends: list[int] = field(default_factory=list)
    pressures: list[int] = field(default_factory=list)
    notes: list[int] = field(default_factory=list)

    def travel(self) -> int:
        return (max(self.cc74) - min(self.cc74)) if self.cc74 else 0

    def deflection(self) -> int:
        return max((abs(b) for b in self.bends), default=0)

    def rise(self) -> int:
        return (max(self.pressures) - min(self.pressures)) if self.pressures else 0


@dataclass
class Capture:
    """Aggregated facts about one probe capture."""

    sections: list[Section] = field(default_factory=list)
    notes: Counter[int] = field(default_factory=Counter)
    channels: set[int] = field(default_factory=set)
    cc_numbers: Counter[int] = field(default_factory=Counter)
    cc_values: dict[int, list[int]] = field(default_factory=lambda: defaultdict(list))
    bends: list[int] = field(default_factory=list)
    pressures: list[int] = field(default_factory=list)
    note_on_count: int = 0
    total: int = 0

    @property
    def note_range(self) -> tuple[int, int] | None:
        if not self.notes:
            return None
        return min(self.notes), max(self.notes)


def parse(text: str) -> Capture:
    """Read probe output into a :class:`Capture`.

    Understands the ``# SECTION <name>`` markers a guided capture emits, which
    is what distinguishes a deliberate gesture from ordinary wobble.
    """
    capture = Capture()
    current: Section | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# SECTION "):
            current = Section(stripped[len("# SECTION "):].strip())
            capture.sections.append(current)
            continue
        if stripped.startswith("#"):
            continue
        match = _LINE.match(stripped)
        if not match:
            continue
        if current is not None:
            _accumulate_section(current, match)
        capture.total += 1
        kind = match["kind"]
        if match["ch"]:
            capture.channels.add(int(match["ch"]))

        if kind == "note_on":
            capture.note_on_count += 1
            if match["note"]:
                capture.notes[int(match["note"])] += 1
        elif kind == "cc":
            if match["cc"]:
                number = int(match["cc"])
                capture.cc_numbers[number] += 1
                if match["val"]:
                    capture.cc_values[number].append(int(match["val"]))
        elif kind == "pitchwheel" and match["bend"]:
            capture.bends.append(int(match["bend"]))
        elif kind in ("aftertouch", "polytouch") and match["val"]:
            capture.pressures.append(int(match["val"]))
    return capture


def _accumulate_section(section: Section, match: re.Match[str]) -> None:
    """Add one parsed message to the current guided section."""
    kind = match["kind"]
    if kind == "note_on" and match["note"]:
        section.notes.append(int(match["note"]))
    elif kind == "cc" and match["cc"] and int(match["cc"]) in (74, 1) and match["val"]:
        section.cc74.append(int(match["val"]))
    elif kind == "pitchwheel" and match["bend"]:
        section.bends.append(int(match["bend"]))
    elif kind in ("aftertouch", "polytouch") and match["val"]:
        section.pressures.append(int(match["val"]))


def _spread(values: list[int]) -> str:
    if not values:
        return "none"
    return f"min={min(values)} max={max(values)} n={len(values)}"


def report_sections(capture: Capture) -> str:
    """Compare the guided gestures and recommend thresholds.

    This is the part that actually matters: by contrasting a deliberate
    gesture against the wobble of a plain press, it separates signal from
    noise in a way no single threshold value can.
    """
    by_name = {section.name: section for section in capture.sections}
    if not by_name:
        return ""

    lines = ["GUIDED SECTIONS", ""]
    lines.append(f"  {'section':<14}{'notes':>6}{'CC74 span':>11}{'bend max':>10}{'press span':>12}")
    for name in (s[0] for s in [(sec.name,) for sec in capture.sections]):
        section = by_name[name]
        lines.append(
            f"  {name:<14}{len(section.notes):>6}{section.travel():>11}"
            f"{section.deflection():>10}{section.rise():>12}"
        )
    lines.append("")

    # The board's actual note range decides the layout's base_note. Getting
    # this wrong shifts every key by an octave, so it is checked first.
    lowest = by_name.get("lowest_key")
    highest = by_name.get("highest_key")
    if lowest is not None and lowest.notes and highest is not None and highest.notes:
        low = min(lowest.notes)
        high = max(highest.notes)
        span = high - low
        lines.append(f"  range from the end keys: {low}..{high} ({span} semitones)")
        if span == 24:
            lines.append("    -> 25 keys, matching the assumed C-to-C board")
        else:
            lines.append(
                f"    -> {span + 1} keys, which does NOT match the assumed "
                f"25-key C-to-C board; the geometry needs revisiting"
            )
        if low % 12 != 0:
            lines.append(f"    -> lowest note {low} is not a C; check the layout's base_note")
        else:
            lines.append(f"    -> set base_note = {low} in the layout (currently 48)")
        lines.append("")

    plain = by_name.get("plain_press")
    recommendations: list[str] = []

    if plain is not None:
        noise_cc = plain.travel()
        noise_bend = plain.deflection()
        noise_press = plain.rise()
        lines.append(
            f"  plain-press noise: CC74 +-{noise_cc}, bend +-{noise_bend}, "
            f"pressure {noise_press}"
        )
        lines.append("")

        for gesture, field, current, noun in (
            ("slide_up", "travel", SLIDE_TRAVEL, "SLIDE_TRAVEL"),
            ("slide_down", "travel", SLIDE_TRAVEL, "SLIDE_TRAVEL"),
            ("glide_side", "deflection", GLIDE_TRAVEL, "GLIDE_TRAVEL"),
            ("pressure", "rise", REPEAT_RISE, "REPEAT_RISE"),
        ):
            section = by_name.get(gesture)
            if section is None:
                continue
            signal = getattr(section, field)()
            noise = {
                "travel": noise_cc,
                "deflection": noise_bend,
                "rise": noise_press,
            }[field]
            if signal <= noise:
                recommendations.append(
                    f"{noun}: gesture signal ({signal}) did not exceed plain-press "
                    f"noise ({noise}). The gesture may not be reaching the driver — "
                    f"check the board's own sensitivity settings."
                )
            else:
                midpoint = (signal + noise) // 2
                verdict = "ok" if noise < current < signal else "CHANGE"
                recommendations.append(
                    f"{noun} [{verdict}]: noise {noise} < threshold < signal {signal} "
                    f"-> recommended {midpoint} (currently {current})"
                )

    if recommendations:
        lines.append("  RECOMMENDED CHANGES")
        for item in recommendations:
            lines.append(f"    {item}")
        lines.append("")

    return "\n".join(lines)


def report(capture: Capture) -> str:
    """Turn a capture into a human-readable calibration report."""
    lines: list[str] = []

    if capture.total == 0:
        return (
            "No MIDI messages in this capture.\n\n"
            "The board was not played, or the notes played were outside the\n"
            "range the probe listens for. Play some keys while the probe runs\n"
            "and try again."
        )

    lines.append(f"{capture.total} messages, {capture.note_on_count} note-ons")
    lines.append("")

    guided = report_sections(capture)
    if guided:
        lines.append(guided)

    # --- note range: the single most important thing to confirm ------------
    span = capture.note_range
    if span:
        low, high = span
        lines.append("NOTES")
        lines.append(f"  range: {low}..{high}  ({high - low + 1} semitones)")
        lines.append(f"  distinct notes: {len(capture.notes)}")
        expected = set(range(48, 73))
        actual = set(capture.notes)
        extra = sorted(actual - expected)
        if not extra:
            lines.append("  every note played is within the layout's assumed 48..72")
        else:
            lines.append(f"  seen outside the assumed 48..72: {extra}")
            lines.append(
                f"  -> if {low} is the board's lowest C, set base_note = {low}"
            )
        lines.append(
            "  note: only notes actually played appear here, so this does not "
            "yet prove the full span"
        )
        lines.append("")

    # --- channels: confirms whether this really is MPE ---------------------
    lines.append("CHANNELS")
    lines.append(f"  used: {sorted(capture.channels)}")
    if len(capture.channels) > 1:
        lines.append("  multiple channels -> MPE, per-note state is keyed by channel")
    elif capture.channels:
        lines.append("  a single channel -> NOT MPE; gesture handling needs revisiting")
    lines.append("")

    # --- controllers: which CC is the slide? -------------------------------
    lines.append("CONTINUOUS CONTROLLERS")
    if not capture.cc_numbers:
        lines.append("  none seen")
        lines.append("  -> slide gestures did not send CC; check whether the board")
        lines.append("     needs MPE mode enabled, or whether slide is off")
    for number, count in sorted(capture.cc_numbers.items()):
        values = capture.cc_values[number]
        label = ""
        if number == 74:
            label = "  <- MPE slide (Y), what the engine expects"
        elif number == 1:
            label = "  <- modulation, also accepted as slide"
        lines.append(f"  CC{number}: {count} messages, {_spread(values)}{label}")
        if values and len(set(values)) > 1:
            low, high = min(values), max(values)
            span = high - low
            lines.append(f"    observed travel {low}..{high} (span {span})")
            lines.append(
                f"    -> SLIDE_TRAVEL must be below {span} to be reachable; "
                f"currently {SLIDE_TRAVEL}"
            )
    lines.append("")

    # --- pitch bend: the glide ---------------------------------------------
    lines.append("PITCH BEND")
    if not capture.bends:
        lines.append("  none seen")
        lines.append("  -> vertical glides may not send bend, or glide is disabled")
    else:
        lines.append(f"  {_spread(capture.bends)}")
        strongest = max(abs(b) for b in capture.bends)
        lines.append(f"  strongest deflection: {strongest}")
        lines.append(
            f"  -> GLIDE_TRAVEL must sit above the wobble of a plain press "
            f"and below {strongest}; currently {GLIDE_TRAVEL}"
        )
        lines.append(
            "  note: a large deflection here may be a deliberate glide or may "
            "be the board's own response to a hard strike — compare against a "
            "capture of plain presses only"
        )
    lines.append("")

    # --- pressure: the repeat ----------------------------------------------
    lines.append("PRESSURE (aftertouch)")
    if not capture.pressures:
        lines.append("  none seen")
        lines.append("  -> the repeat gesture cannot work without pressure messages")
    else:
        lines.append(f"  {_spread(capture.pressures)}")
        span = max(capture.pressures) - min(capture.pressures)
        lines.append(f"  span: {span}")
        lines.append(
            f"  -> REPEAT_RISE must exceed the pressure an ordinary press "
            f"produces and stay below {span}; currently {REPEAT_RISE}"
        )
    lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(__doc__)
        return 2
    try:
        with open(argv[0], encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(report(parse(text)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
