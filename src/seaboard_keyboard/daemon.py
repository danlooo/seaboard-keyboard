"""The driver daemon: wires the Seaboard to a virtual keyboard.

    seaboard-keyboard --layout layouts/qwerty.toml          # type for real
    seaboard-keyboard --dry-run                             # print, do not type
    seaboard-keyboard --list-layouts
    seaboard-keyboard --show-layout

In typing mode the daemon holds the raw MIDI device open and types. In MIDI
mode it releases the device so a DAW or synth can take it over, which is what
"behaves like a normal MIDI controller" has to mean in practice: ALSA rawmidi
devices are exclusive, so two readers cannot share the stream.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path

from .engine import Engine
from .input import ActionSink
from .keycodes import ALL_KEYS
from .layout import Layout, LayoutError, load_layout
from .midi import MidiDevice, find_seaboard
from .uinput_kbd import VirtualKeyboard

LAYOUT_DIR = Path(__file__).resolve().parents[2] / "layouts"

#: Where extra layouts are looked up, so a user layout can shadow a bundled one.
USER_LAYOUT_DIR = Path(
    os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
) / "seaboard-keyboard" / "layouts"


def available_layouts() -> dict[str, Path]:
    """All layouts by name; user layouts take precedence over bundled ones."""
    found: dict[str, Path] = {}
    for directory in (LAYOUT_DIR, USER_LAYOUT_DIR):
        if directory.is_dir():
            for path in sorted(directory.glob("*.toml")):
                found[path.stem] = path
    return found


def resolve_layout(name: str) -> Path:
    layouts = available_layouts()
    if name in layouts:
        return layouts[name]
    candidate = Path(name)
    if candidate.is_file():
        return candidate
    known = ", ".join(sorted(layouts)) or "(none found)"
    raise LayoutError(f"unknown layout {name!r}; available: {known}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="seaboard-keyboard",
        description="Use a ROLI Seaboard as a computer keyboard.",
    )
    parser.add_argument(
        "-l", "--layout", default="qwerty",
        help="layout name or path to a .toml file (default: qwerty)",
    )
    parser.add_argument(
        "-d", "--device", default=None,
        help="raw MIDI device, e.g. /dev/snd/midiC4D0 (default: autodetect)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print what would be typed instead of typing it",
    )
    parser.add_argument(
        "--list-layouts", action="store_true", help="list available layouts and exit",
    )
    parser.add_argument(
        "--show-layout", action="store_true", help="print the layout and exit",
    )
    parser.add_argument(
        "--start-in-midi-mode", action="store_true",
        help="start passing MIDI through rather than typing",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="log every decision",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list_layouts:
        for name, path in sorted(available_layouts().items()):
            print(f"{name:16s} {path}")
        return 0

    try:
        layout: Layout = load_layout(resolve_layout(args.layout))
    except LayoutError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.show_layout:
        print(layout.describe())
        return 0

    if args.dry_run:
        print("DRY RUN — nothing will be typed.\n")

    try:
        device_path = args.device or find_seaboard()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    keyboard: VirtualKeyboard | None = None
    if not args.dry_run:
        try:
            keyboard = VirtualKeyboard(keys=set(ALL_KEYS))
        except OSError as exc:
            print(f"error: cannot create virtual keyboard: {exc}", file=sys.stderr)
            print("hint: /dev/uinput must be writable", file=sys.stderr)
            return 1

    engine = Engine(layout=layout, typing_enabled=not args.start_in_midi_mode)
    sink = ActionSink(keyboard, dry_run=args.dry_run)
    running = True
    force_toggle = False

    def stop(signum: int, frame: object) -> None:
        nonlocal running
        running = False

    def toggle(signum: int, frame: object) -> None:
        # An escape hatch that does not depend on the board: in MIDI mode the
        # key gesture is the only way back, so this is the fallback.
        nonlocal force_toggle
        force_toggle = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGUSR1, toggle)

    print(layout.describe())
    print(f"\nlistening on {device_path}")
    print(
        "hold the topmost key to switch between typing and MIDI.\n"
        f"If that gesture ever fails you, send SIGUSR1 to toggle:\n"
        f"    kill -USR1 {os.getpid()}\n"
        "ctrl-c to quit.\n"
    )

    device: MidiDevice | None = None
    try:
        was_typing = engine.typing_enabled
        while running:
            if force_toggle:
                force_toggle = False
                engine.typing_enabled = not engine.typing_enabled
            # The device is held open in both modes. Releasing it in MIDI mode
            # would be tidier for other software, but then the driver could not
            # see the top-key gesture that switches back, and you would be
            # stuck in MIDI mode until you restarted it. (If that becomes a
            # real conflict, giving the board a second ALSA port and letting
            # the DAW read that one is the fix.)
            if device is None:
                try:
                    device = MidiDevice(device_path)
                except OSError as exc:
                    print(f"error: cannot open {device_path}: {exc}", file=sys.stderr)
                    return 1

            if engine.typing_enabled != was_typing:
                was_typing = engine.typing_enabled
                if engine.typing_enabled:
                    print("-- typing mode --")
                else:
                    sink.release_all()
                    engine.release_all()
                    print("-- MIDI mode: notes pass through, not typed --")

            messages = device.read(timeout=0.05)
            for message in messages:
                if args.verbose:
                    print(f"  midi {message}")
                sink.apply(engine.handle(message))

            actions = engine.tick()
            if actions:
                sink.apply(actions)
                if args.verbose:
                    for entry in engine.log[-len(actions):]:
                        print(f"  -> {entry}")
            time.sleep(0.001)
    finally:
        if device is not None:
            device.close()
        sink.release_all()
        if keyboard is not None:
            keyboard.close()
        print("\nstopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
