"""Live view of everything the Seaboard sends.

Run this, then press, slide and wiggle a few keys — it prints each MIDI
message as it arrives, which is how we discover what the hardware actually
does rather than guessing.

    python -m seaboard_kbd.probe              # watch everything
    python -m seaboard_kbd.probe --guided     # walk through each gesture

``--guided`` is what calibration needs: it prompts for one gesture at a time
and marks the boundaries in the output, so ``calibrate.py`` can tell a
deliberate slide from the wobble of an ordinary press. Without those labels
the two are indistinguishable, and every threshold would be a guess.
"""

from __future__ import annotations

import argparse
import sys
import time

from .midi import MidiDevice

#: (heading, instruction, seconds) — the guided calibration script.
GUIDED_STEPS: tuple[tuple[str, str, float], ...] = (
    ("rest", "Do NOT touch the board.", 3.0),
    (
        "lowest_key",
        "Press ONLY the very lowest key on the board (the far left end), "
        "then let go.",
        8.0,
    ),
    (
        "highest_key",
        "Press ONLY the very highest key on the board (the far right end), "
        "then let go.",
        8.0,
    ),
    (
        "plain_press",
        "Press several keys normally, one at a time — straight down, "
        "no sliding, no sideways movement.",
        12.0,
    ),
    (
        "slide_up",
        "Press a key and, while holding it, slide your finger UP the key "
        "towards the back of the board.",
        12.0,
    ),
    (
        "slide_down",
        "Press a key and, while holding it, slide your finger DOWN the key "
        "towards the front edge.",
        12.0,
    ),
    (
        "glide_side",
        "Press a key and move your finger SIDEWAYS along the board "
        "(left and right, along the keybed).",
        12.0,
    ),
    (
        "pressure",
        "Press a key and push HARD into the surface, holding the pressure.",
        10.0,
    ),
)


def _run_guided(device: MidiDevice) -> int:
    print("Guided calibration capture.")
    print("Follow each instruction exactly; the labels are what makes")
    print("calibration possible.\n")
    print("Press ctrl-c at any point to stop early.\n")

    start = time.monotonic()
    try:
        for heading, instruction, seconds in GUIDED_STEPS:
            print(f"--- {heading} " + "-" * max(0, 52 - len(heading)))
            print(f"    {instruction}")
            print(f"    starting now, {seconds:.0f}s ...", flush=True)
            print(f"# SECTION {heading}")

            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                for message in device.read(timeout=0.1):
                    elapsed = message.time - start
                    print(f"[{elapsed:7.3f}] {message}", flush=True)
            print()
    except KeyboardInterrupt:
        print("\nstopped early.")
    print("# END")
    print("\nCapture complete. Run:")
    print("    python -m seaboard_kbd.calibrate <this file>")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="seaboard-probe",
        description="Print the MIDI the Seaboard sends, for discovery and calibration.",
    )
    parser.add_argument(
        "-g", "--guided", action="store_true",
        help="walk through each gesture in turn, labelling the output for calibrate.py",
    )
    parser.add_argument(
        "-d", "--device", default=None,
        help="raw MIDI device, e.g. /dev/snd/midiC4D0 (default: autodetect)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    guided = args.guided
    path = args.device

    try:
        device = MidiDevice(path)
    except (RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        if guided:
            return _run_guided(device)

        print(f"listening on {device.path}")
        print("press / slide / wiggle keys on the Seaboard. ctrl-c to stop.\n")
        start = time.monotonic()
        try:
            while True:
                for message in device.read(timeout=0.1):
                    elapsed = message.time - start
                    print(f"[{elapsed:7.3f}] {message}", flush=True)
        except KeyboardInterrupt:
            print("\nstopped.")
    finally:
        device.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
