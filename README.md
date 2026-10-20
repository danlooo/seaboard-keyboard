# seaboard-kbd

Use a ROLI Seaboard BLOCK M as a computer keyboard.

Pressing a key on the Seaboard types a character. Sliding your finger along a
key changes what it types. The board still works as a MIDI instrument when you
want it to; a gesture switches between the two.

## Status

Working and covered by tests, with the gesture detection calibrated against
real captures from the board. What is confirmed:

- The Seaboard is detected (`2af4:0700`, ALSA card 4, `/dev/snd/midiC4D0`)
- The virtual keyboard is created and torn down cleanly via `/dev/uinput`
- Real MIDI has been captured and analysed (see [Measured behaviour](#measured-behaviour))
- 79 unit tests pass, plus 7 end-to-end checks against simulated MIDI

What is still open:

- **The board's true note range is unconfirmed.** Captured notes were 64-65,
  which is consistent with the layout's assumed C3 (48) but also with a base of
  60. If the board is really an octave higher, every key is mapped an octave
  off. Run the guided capture to settle it — this is the first thing to check.
- `SLIDE_TRAVEL` and `REPEAT_RISE` are the least certain thresholds. The
  capture that produced them was a short improvised burst, not deliberate
  gestures, so treat them as provisional until a guided capture replaces them.

## Measured behaviour

The board does **not** behave the way the MPE specification alone would
suggest. A capture of the real device (analysed by `calibrate.py`) showed:

| Observation | Consequence |
| --- | --- |
| `CC74` and pitch bend arrive **before** `note_on` | The engine buffers them so each note has a baseline; a naive implementation drops the first message of every note |
| `CC74` sat at **32-42** | The MPE default of 64, and any absolute threshold, would never fire |
| Bend reached only **198** on a deliberate glide | The full 8192 range is not available; a threshold of 2560 would never fire |
| Pressure peaked at **49** | An absolute threshold of 100 would never fire |
| A plain press still emits bend and CC74 **wobble of ±6 to ±25** | Gestures must be detected as travel away from a baseline, not by absolute value |
| Notes arrive on **separate channels** | Confirms MPE; per-note state is keyed by channel |

Every gesture is therefore detected as a *deviation from that note's own
starting value*. This is what makes the driver robust across firmware versions
and across the Seaboard's own sensitivity settings.

## Requirements

None beyond Python 3.11 and the standard library.

This is deliberate. The usual stack for this job — `python-rtmidi` and
`python-evdev` — both need a C compiler, and this machine has none, and Python
3.14 is new enough that neither ships a prebuilt wheel. Rather than ask for a
toolchain, the two pieces that needed those libraries are implemented directly:

- `midi.py` reads the ALSA raw-MIDI character device and parses MIDI bytes
- `uinput_kbd.py` drives `/dev/uinput` through `fcntl.ioctl`

Both protocols are small, and the result is a driver with no dependencies to
install or break.

Two permissions are needed:

- `/dev/snd/midiC4D0` readable — you have this via the `audio` group
- `/dev/uinput` writable — you have this via an ACL (verified)

No root, no udev rules, no group changes.

## Running

```sh
python -m seaboard_kbd.daemon --show-layout      # print the mapping
python -m seaboard_kbd.daemon --dry-run          # print instead of typing
python -m seaboard_kbd.daemon                    # type for real
python -m seaboard_kbd.daemon --verbose          # log every decision
python -m seaboard_kbd.probe                     # watch raw MIDI from the board
python -m seaboard_kbd.selftest                  # simulate, no hardware needed

# calibration
python -m seaboard_kbd.probe --guided > guided.log   # follow the prompts
python -m seaboard_kbd.calibrate guided.log          # recommended thresholds
```

Start with `--dry-run`: it exercises the whole path but types nothing, so you
can learn the gestures without spraying characters into whatever window has
focus.

Install the console scripts with `pip install -e .` if you want `seaboard-kbd`
and `seaboard-probe` on your `PATH`.

## The layout

`layouts/qwerty.toml`. The Seaboard BLOCK M has 25 keys — 15 white, 10 black —
which is one short of the 26 letters, so the layout is shaped like a QWERTY
keyboard and one letter lives on a layer.

```
BLACK keys = the QWERTY top row (exactly ten of them)

  C# D#    F# G# A#      C# D#    F# G# A#
  q  w     e  r  t       y  u     i  o  p

WHITE keys = home row, then bottom row

  C  D  E  F  G  A  B    C  D  E  F  G  A  B  C
  a  s  d  f  g  h  j    k  l  z  x  c  v  b  n
```

That is 24 letters. `m` is the missing one: it is on the symbol layer, one
slide away, rather than displacing a more common letter. Every letter is
reachable — there is a test asserting exactly that.

## Gestures

Because there are no spare keys for Shift, Space or Enter, those come from the
Seaboard's expressive surface.

| Gesture | Result |
| --- | --- |
| Strike straight down | the plain character |
| Slide up the key | uppercase (Shift) |
| Slide down the key | the symbol layer — digits, punctuation, `m` |
| Glide left | Backspace |
| Glide right | Space |
| Press hard and hold | repeat the last character |
| Hold the topmost key ~0.7 s | toggle between typing and MIDI |

If the toggle gesture ever fails you while in MIDI mode, the daemon prints a
`kill -USR1 <pid>` line at startup; that signal toggles modes without needing
the board.

Glides work by moving sideways off the key rather than by pitch, so they do not
produce a character — which is what makes them usable for Space and Backspace,
the two keys you press most often while typing.

## Design notes

Two decisions are worth knowing about, because they explain the code's shape.

**The decision window.** A strike has to choose a character *before* typing it,
but a slide only becomes visible *after* the strike. The engine therefore holds
each strike for 75 ms (`DECISION_WINDOW`), during which a slide can change its
meaning, and commits when the window closes. Releasing early commits
immediately, so fast typing is not slowed down. A slide is *latched* once
detected and is not undone if your finger drifts back toward the middle before
the window closes.

**MPE channels.** The Seaboard is an MPE controller: every voice gets its own
MIDI channel, and per-note pitch bend and CC74 are addressed to that channel
rather than to a note number. All gesture state is therefore keyed by channel,
not by note. This is what allows a slide on one key to affect only that key
while other fingers keep typing.

A third consequence: ALSA raw-MIDI devices are exclusive, so two readers cannot
share the stream. The daemon therefore holds the device open in *both* modes,
and in MIDI mode simply stops typing — the notes still reach anything listening
on the ALSA sequencer, but they are not echoed as keystrokes.

Holding it open in MIDI mode is deliberate. Releasing it would be tidier for a
DAW, but then the driver could not see the top-key gesture that switches back,
and you would be stuck in MIDI mode until you restarted it. If that contention
becomes a real problem, the fix is to give the board a second ALSA port and let
the DAW read that one.

## Tuning

`src/seaboard_kbd/engine.py`, at the top:

| Constant | Default | Meaning |
| --- | --- | --- |
| `DECISION_WINDOW` | 0.075 s | how long a strike waits for a slide |
| `SLIDE_TRAVEL` | 12 | CC74 travel from the note's baseline that counts as a slide |
| `GLIDE_TRAVEL` | 120 | bend travel from the note's baseline that counts as a glide |
| `REPEAT_RISE` | 45 | pressure rise above baseline that fires a repeat |
| `GESTURE_SETTLE` | 0.03 s | how long a gesture must hold to beat wobble |
| `TOGGLE_HOLD` | 0.7 s | hold time on the top key to switch modes |

All values are *relative* to each note's starting position, so they do not need
retuning when you change the board's sensitivity.

The right way to set these is a guided capture, which labels each gesture so
the tool can separate signal from noise:

```sh
python -m seaboard_kbd.probe --guided > guided.log
python -m seaboard_kbd.calibrate guided.log
```

It prints a table of plain-press noise against each gesture's signal, and
recommends the midpoint for every threshold.

## Layout format

Layouts are data, not code. Copy `layouts/qwerty.toml` to
`~/.config/seaboard-kbd/layouts/` to override one without touching the repo.

```toml
[characters]
white = ["a", "s", ...]   # 15 entries, left to right
black = ["q", "w", ...]   # 10 entries, left to right

[gestures]
slide_up = "shift"
glide_left = "backspace"
```

A layout is validated on load: if the key counts do not match the board, or a
gesture names an unknown action, the daemon refuses to start rather than
half-working.

## Verification

```sh
PYTHONPATH=src python -m unittest discover -s tests   # 79 tests
PYTHONPATH=src python -m seaboard_kbd.selftest        # end-to-end, simulated
```

The unit tests cover the MIDI parser (running status, messages split across
reads, SysEx, realtime bytes), the engine (every gesture, MPE channel
isolation, the decision window, mode toggling) and the calibration analysis.

The gesture tests deliberately replay the sequences the *real* board sends —
CC74 and bend before `note_on`, low absolute values, wobble during a plain
press — rather than the sequences the MPE spec would suggest. Several of them
are regression tests for bugs that only appeared once real data arrived.

`selftest.py` runs synthetic MIDI through the same engine and sink the daemon
uses, and asserts the resulting characters.

None of this proves the real board behaves as assumed for *your* touch. That is
what the guided capture is for.

## Next steps

1. Run the guided capture to confirm the board's note range. This is the
   biggest open question: if the base note is wrong, every key is one octave
   off and the layout will feel scrambled.
2. Re-run guided calibration to replace `SLIDE_TRAVEL` and `REPEAT_RISE` with
   values measured from deliberate gestures.
3. Test `--dry-run` for real, then decide whether the gesture ergonomics
   actually work at typing speed. Space and Backspace as glides are the
   riskiest bet.
4. Add a udev rule or systemd user unit if this should start automatically.
