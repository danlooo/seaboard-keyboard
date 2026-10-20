# AGENTS.md

Guidance for agents working in this repository.

## What this is

Turns a ROLI Seaboard BLOCK M into a computer keyboard. Striking a key types a
character; sliding/gliding on the key supplies the modifiers there are no keys
left for.

`README.md` is the human-facing document and explains the layout and gestures.
This file covers what you need to work on the code without breaking it.

## Commands

```sh
# tests (79) — PYTHONPATH is required; the package is not installed by default
PYTHONPATH=src python -m unittest discover -s tests
PYTHONPATH=src python -m unittest tests.test_engine -v      # one module

# end-to-end with simulated hardware (no board needed, works anywhere)
PYTHONPATH=src python -m seaboard_keyboard.selftest

# the real thing
PYTHONPATH=src python -m seaboard_keyboard.daemon --dry-run  # prints, types nothing
PYTHONPATH=src python -m seaboard_keyboard.daemon --show-layout
PYTHONPATH=src python -m seaboard_keyboard.daemon --verbose

# hardware discovery / calibration (needs the board; exclusive device)
PYTHONPATH=src python -m seaboard_keyboard.probe
PYTHONPATH=src python -m seaboard_keyboard.probe --guided > guided.log
PYTHONPATH=src python -m seaboard_keyboard.calibrate guided.log
```

`python` here is the local `.venv/bin/python` (3.14). `requires-python` is
`>=3.10`.

`--dry-run` is the safe way to exercise the whole path: it runs the real engine
and the real MIDI input, and only skips `/dev/uinput`. Use it instead of
inventing harnesses. Prefer `selftest` in tests and CI.

## Hard constraints

**Do not add third-party dependencies.** This machine has no C compiler, and
Python 3.14 has no prebuilt wheels for `python-rtmidi` or `python-evdev`, so
neither can be installed. That is why `midi.py` parses the ALSA raw-MIDI byte
stream and `uinput_kbd.py` drives `/dev/uinput` via `fcntl.ioctl` directly.
If you need more capability, extend those — do not reach for a library.

**No sudo.** No passwordless sudo is available. The driver needs no root: the
user already has read access to `/dev/snd/midiC4D0` (via the `audio` group) and
write access to `/dev/uinput` (via an ACL). If you propose a udev rule or a
group change, you have probably gone wrong.

**Do not commit without disabling signing.** `commit.gpgsign=true` is set
globally but the configured signing key has no secret key present, so a plain
`git commit` always fails with `gpg: skipped ... No secret key`. Use:

```sh
git -c commit.gpgsign=false commit ...
```

Do not "fix" this by changing the user's global git config or by disabling
signing repository-wide.

## The thing most likely to trip you up

**The board does not behave the way the MPE specification suggests.** A capture
of real hardware (`capture analysed by calibrate.py`) showed:

| Observation | Why it matters |
| --- | --- |
| `CC74` and pitch bend arrive **before** `note_on` | A naive implementation drops the first message of every note. The engine buffers them in `_last_cc74` / `_last_bend` to give each note a baseline. |
| `CC74` sits at **32-42** | MPE's nominal 64, or any absolute threshold, would never fire. |
| A deliberate glide reaches bend **only ~198** | The full 8192 range is not available. |
| Pressure peaks at **~49** | An absolute threshold of 100 would never fire. |
| A plain press emits **wobble** (±6 CC74, ±25 bend, pressure to 41) | Gestures cannot be detected by absolute value. |
| Notes arrive on **separate channels** | Confirms MPE; all per-note state is keyed by **channel**, not note number. |

Therefore, the central invariant:

> Every gesture is detected as **travel away from that note's own baseline**,
> never against an absolute value.

`SLIDE_TRAVEL`, `GLIDE_TRAVEL` and `REPEAT_RISE` in `engine.py` are all
relative. If you find yourself writing `if value > SOME_CONSTANT`, stop and
reconsider — that is the bug this design exists to avoid.

This is also why the layout's thresholds are robust across firmware versions
and across the board's own sensitivity settings. Preserve that property.

## Architecture

The split matters and is deliberate:

| Module | Role |
| --- | --- |
| `engine.py` | **All decisions, no I/O.** MIDI messages in, actions out. The heart of the project. |
| `layout.py`, `geometry.py` | Layout as **data** (TOML), validated on load. |
| `midi.py` | ALSA raw-MIDI device + incremental byte parser (running status, SysEx, realtime bytes). |
| `uinput_kbd.py` | Virtual keyboard over `/dev/uinput`. |
| `input.py` | Executes actions against the virtual keyboard. Only place that touches the device. |
| `daemon.py` | Wiring, CLI, mode toggling. |
| `probe.py`, `calibrate.py` | Hardware discovery and threshold calibration. |

Because `engine.py` does no I/O and takes an injectable `clock`, it is fully
testable without hardware. Keep it that way — new gesture logic belongs there,
not in the daemon.

### Two design decisions worth understanding before changing them

**The decision window.** A strike must choose a character *before* typing it, but
a slide only becomes visible *after* the strike. So the engine holds each strike
for `DECISION_WINDOW` (75 ms) and commits when it closes. Releasing early
commits immediately, so fast typing is not slowed. A slide is *latched* once
detected and is not undone if the finger drifts back before the window closes.

**The settle check.** Because a plain press emits wobble, a gesture must hold for
`GESTURE_SETTLE` (30 ms) to count; a brief spike falls through and types the
plain character instead. This is what stops accidental Shift while typing.

## Working with the layouts

The layout is data, not code. To change the mapping, edit
`layouts/qwerty.toml` — not Python. A layout is validated on load: mismatched
key counts or an unknown gesture action make the daemon refuse to start rather
than half-work.

There are 25 keys (15 white, 10 black) but 26 letters, so one letter must live
on a layer. Currently `m` does. `tests/test_engine.py` asserts every letter is
reachable — keep that test passing if you rearrange things.

## Testing expectations

- **Gesture tests replay sequences the real board sends**, including CC74/bend
  before `note_on`, low absolute values, and press wobble. Several are
  regression tests for bugs that only surfaced with real data.
- **Do not "correct" these tests to match the MPE spec.** If a test looks wrong
  because it contradicts the spec, the test is right and the spec is not the
  contract here — the hardware is.
- Add a test for any new gesture, and for any bug you fix. The engine's pure
  design makes this cheap.
- `selftest.py` is the end-to-end check; keep its scenarios in sync with the
  engine's real behaviour.

## Verification before you finish

```sh
PYTHONPATH=src python -m unittest discover -s tests   # expect 79 passed
PYTHONPATH=src python -m seaboard_keyboard.selftest   # expect 9 checks passed
```

Both must pass. `selftest` covers the wiring that unit tests do not.

## Open questions

These are unresolved, not oversights — do not assume the current behaviour is
correct:

1. **The board's true note range is unconfirmed.** Captured notes were 64-65,
   consistent both with the layout's assumed base of C3 (48) *and* with a base
   of 60. If it is the latter, every key is mapped an octave off. The `--guided`
   capture presses the lowest and highest keys to settle this. **Check this
   before tuning anything else.**
2. **`SLIDE_TRAVEL` (12) and `REPEAT_RISE` (45) are provisional.** They come
   from a short improvised burst, not deliberate gestures. In one capture the
   CC74 span was only 10, which would put slides out of reach at
   `SLIDE_TRAVEL = 12`. A guided capture should replace them.
3. **RAW MIDI is exclusive**, so the daemon holds the device open in both modes
   (needed for the toggle gesture to work at all) and may keep a DAW from seeing
   the board. The intended fix is a second ALSA port for the DAW.

Calibrate with a guided capture rather than guessing:

```sh
PYTHONPATH=src python -m seaboard_keyboard.probe --guided > guided.log
PYTHONPATH=src python -m seaboard_keyboard.calibrate guided.log
```

It contrasts each gesture's signal against plain-press noise and recommends the
midpoint for each threshold. All of this needs a human at the board; no agent
can perform it.

## Style

Match the surrounding code: type hints throughout, `from __future__ import
annotations`, dataclasses for value types, `slots=True` where useful, and
comments that explain *why* rather than restating the code. Docstrings explain
rationale, especially where behaviour is counterintuitive — that is the house
style and it is load-bearing here, because most of the surprises are invisible
in the source.
