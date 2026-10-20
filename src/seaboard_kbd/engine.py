"""The mapping engine: MIDI messages in, keyboard actions out.

This module holds all the interesting decisions and no I/O, so the behaviour
can be tested exhaustively without a Seaboard attached. ``daemon.py`` wires it
to real devices.

The central difficulty is that a key strike must choose *which* character to
type before the character is typed, but the gesture that changes that choice
(a slide) only becomes visible *after* the strike. The engine therefore holds
each strike for a short decision window, during which a slide or glide can
upgrade it, and only then emits.

Seaboard notes arrive as MPE: every voice gets its own MIDI channel, and
per-note pitch bend and CC74 are addressed to that channel rather than to a
note number. All gesture state is therefore keyed by channel.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .keycodes import CHAR_TO_KEY, NAMED_KEYS
from .layout import Layout
from .midi import MidiMessage

# --- tuning knobs -----------------------------------------------------------
# These were derived from a capture of the real board (see calibrate.py), not
# guessed. The board reports every channel at a *low* absolute value: in the
# reference capture CC74 sat at 32-42, bend peaked at 198 and pressure at 49.
# Absolute thresholds tuned elsewhere (the MPE default of 64 for CC74, or the
# full 8192 bend range) would never fire, which is why every gesture below is
# detected as a *deviation from that note's own starting value* rather than
# against a fixed number. That also makes this robust across firmware and
# across the Seaboard's own sensitivity settings.

#: How long after a strike we wait for a slide before committing the character.
DECISION_WINDOW = 0.075
#: A slide counts once CC74 moves this far from where the note started.
SLIDE_TRAVEL = 12
#: A glide counts once pitch bend moves this far from where the note started.
#: Measured: a deliberate sideways glide reached 198, while a plain press
#: stayed within +-25, so this sits well clear of both.
GLIDE_TRAVEL = 120
#: Pressure must rise this far above its starting value to fire a repeat.
#: Measured: an ordinary press peaked at 41 above a base of ~2, so this has to
#: sit clear of that or simply playing a note would repeat characters. The
#: margin is narrow because the board's pressure range is small, so this is the
#: least certain value here and the first thing to recalibrate.
REPEAT_RISE = 45
#: How long the top key must be held to toggle between typing and MIDI.
TOGGLE_HOLD = 0.7
#: Minimum gap between repeats, mimicking keyboard auto-repeat.
REPEAT_INTERVAL = 0.05
#: A note released sooner than this after its last gesture cannot have been an
#: intentional gesture, so it is treated as a character strike instead. This
#: suppresses the noise in the board's continuous streams during a plain press.
GESTURE_SETTLE = 0.03


# --- actions ----------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class TypeChar:
    """Type a single character."""

    char: str
    source: str = ""  # human-readable provenance, for the debug log


@dataclass(frozen=True, slots=True)
class PressKey:
    """Press a named key such as "backspace" or "enter"."""

    name: str
    source: str = ""


@dataclass(frozen=True, slots=True)
class ToggleMode:
    """Switch between typing mode and pass-through MIDI mode."""

    source: str = ""


#: Anything the engine can ask the input layer to do.
Action = TypeChar | PressKey | ToggleMode


#: Named actions a layout's ``[gestures]`` table may use.
GESTURE_ACTIONS = frozenset(
    {"shift", "symbol", "repeat", "backspace", "space", "enter", "tab", "esc", "toggle"}
)


@dataclass(slots=True)
class _Pending:
    """A strike being held open while we decide what it means."""

    channel: int
    note: int
    colour: str
    index: int
    layer: str = "plain"
    deadline: float = 0.0
    resolved: bool = False
    glide: str | None = None
    #: true if this note was struck while the board was already in MIDI mode,
    #: in which case it is a performance gesture and must not type anything
    silent: bool = False
    #: CC74 value at the moment of the strike, used as the slide's zero point
    y_base: int | None = None
    #: pitch-bend value at the moment of the strike, the glide's zero point
    bend_base: int = 0
    #: channel pressure at the moment of the strike, the repeat's zero point
    pressure_base: int = 0
    #: when the note last produced a gesture, for the settle check
    last_gesture: float = 0.0


@dataclass(slots=True)
class Engine:
    """Maps Seaboard gestures to keyboard actions.

    Feed it events with :meth:`handle` and call :meth:`tick` regularly; both
    return a list of actions for the caller to execute.
    """

    layout: Layout
    typing_enabled: bool = True
    decision_window: float = DECISION_WINDOW
    toggle_hold: float = TOGGLE_HOLD
    clock: object = time.monotonic

    #: channel -> note currently sounding on it (MPE)
    _channels: dict[int, int] = field(default_factory=dict)
    #: channel -> (note, pending) for strikes still inside the decision window
    _pending: dict[int, _Pending] = field(default_factory=dict)
    #: channel -> most recent CC74, which the board sends *before* note_on so
    #: a strike always has a Y baseline to measure its slide against
    _last_cc74: dict[int, int] = field(default_factory=dict)
    #: channel -> most recent pitch bend before the note started
    _last_bend: dict[int, int] = field(default_factory=dict)
    #: channel -> pressure baseline for this note
    _pressure: dict[int, int] = field(default_factory=dict)
    #: channel -> whether a fresh push may fire (re-armed when pressure drops),
    #: so holding a finger down repeats once rather than continuously
    _repeat_armed: dict[int, bool] = field(default_factory=dict)
    #: when the last repeat fired, for rate limiting
    _last_repeat: float = 0.0
    #: strike time of the top key, if it is being held as a toggle candidate
    _top_key_since: float | None = None
    _top_key_consumed: bool = False
    #: last typed character, for the repeat gesture
    last_char: str | None = None
    #: rolling log of decisions, most recent last (for the debug overlay)
    log: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        gestures = set(self.layout.gestures.values())
        unknown = gestures - GESTURE_ACTIONS
        if unknown:
            raise ValueError(f"layout uses unknown gesture actions: {sorted(unknown)}")

    # -- helpers -------------------------------------------------------------
    @property
    def top_white_index(self) -> int:
        return self.layout.geometry.white_count - 1

    def _note(self) -> float:
        return self.clock()  # type: ignore[operator]

    def _record(self, text: str) -> None:
        self.log.append(text)
        del self.log[:-40]

    def _gesture_name(self, key: str) -> str | None:
        return self.layout.gestures.get(key)

    # -- event intake --------------------------------------------------------
    def handle(self, message: MidiMessage) -> list[Action]:
        now = self._note()
        out: list[Action] = []

        if message.kind == "note_on":
            out.extend(self._on_note_on(message, now))
        elif message.kind == "note_off":
            out.extend(self._on_note_off(message))
        elif message.kind == "cc":
            out.extend(self._on_cc(message))
        elif message.kind == "pitchwheel":
            out.extend(self._on_bend(message))
        elif message.kind == "aftertouch":
            out.extend(self._on_aftertouch(message))

        return out

    def _on_note_on(self, msg: MidiMessage, now: float) -> list[Action]:
        if msg.note is None or msg.channel is None:
            return []
        position = self.layout.geometry.position(msg.note)
        if position is None:
            self._record(f"off-board note {msg.note}")
            return []

        self._channels[msg.channel] = msg.note
        self._pressure.pop(msg.channel, None)

        pending = _Pending(
            channel=msg.channel,
            note=msg.note,
            colour=position.colour,
            index=position.index,
            deadline=now + self.decision_window,
            # The board sends CC74 and bend *before* note_on, so the value we
            # last saw on this channel is this note's starting position.
            y_base=self._last_cc74.get(msg.channel),
            bend_base=self._last_bend.get(msg.channel, 0),
            silent=not self.typing_enabled,
        )
        self._pending[msg.channel] = pending

        # The top key doubles as the mode toggle when held.
        if position.colour == "white" and position.index == self.top_white_index:
            self._top_key_since = now
            self._top_key_consumed = False

        return []

    def _on_note_off(self, msg: MidiMessage) -> list[Action]:
        if msg.channel is None:
            return []
        self._channels.pop(msg.channel, None)
        self._pressure.pop(msg.channel, None)

        pending = self._pending.get(msg.channel)
        if pending is None:
            return []

        # A glide is a gesture, not a character: it presses a named key
        # (space, backspace, ...) and types nothing. It fires on release, so
        # the gesture is complete before it acts.
        if pending.glide is not None:
            self._pending.pop(msg.channel, None)
            self._record(f"{pending.glide} (glide)")
            return [PressKey(pending.glide, "glide")]

        # Releasing early simply resolves the strike immediately.
        return self._resolve(pending, self._note())

    def _on_cc(self, msg: MidiMessage) -> list[Action]:
        if msg.control is None or msg.channel is None:
            return []

        # MPE "slide" along the key is CC74; some firmware uses CC1 for it.
        if msg.control not in (74, 1):
            return []

        value = msg.value or 0
        # Record unconditionally: this stream arrives before note_on and is
        # what gives the coming strike its Y baseline.
        previous_cc74 = self._last_cc74.get(msg.channel)
        self._last_cc74[msg.channel] = value

        pending = self._pending.get(msg.channel)
        if pending is None:
            return []
        if pending.y_base is None:
            pending.y_base = previous_cc74 if previous_cc74 is not None else value

        # Slide is measured as travel away from where the note started, not
        # against an absolute value — the board's own range is compressed.
        travel = value - pending.y_base
        action = None
        if travel >= SLIDE_TRAVEL:
            action = self._gesture_name("slide_up")
        elif travel <= -SLIDE_TRAVEL:
            action = self._gesture_name("slide_down")
        if action is None:
            return []
        pending.last_gesture = self._note()

        if action == "shift":
            pending.layer = "shift"
        elif action == "symbol":
            pending.layer = "symbol"
        elif action in ("backspace", "space", "enter", "tab", "esc"):
            pending.glide = action
        elif action == "repeat":
            return []

        # Latch the gesture but let the decision window resolve it, so a value
        # that merely spikes during the strike transient is not acted on until
        # it has proven stable. The latch itself is not undone if the finger
        # drifts back towards the middle before the window closes.
        return []

    def _on_bend(self, msg: MidiMessage) -> list[Action]:
        if msg.channel is None or msg.value is None:
            return []

        # Record unconditionally: bend also arrives before note_on, and gives
        # the note its glide baseline.
        self._last_bend[msg.channel] = msg.value

        pending = self._pending.get(msg.channel)
        if pending is None:
            return []

        deflection = msg.value - pending.bend_base
        if deflection >= GLIDE_TRAVEL:
            action = self._gesture_name("glide_right")
        elif deflection <= -GLIDE_TRAVEL:
            action = self._gesture_name("glide_left")
        else:
            return []
        if action is None:
            return []

        if action in ("backspace", "space", "enter", "tab", "esc"):
            pending.glide = action
        elif action == "repeat":
            return []
        pending.last_gesture = self._note()
        return []

    def _on_aftertouch(self, msg: MidiMessage) -> list[Action]:
        if msg.channel is None:
            return []

        value = msg.value or 0
        # The first pressure value after the strike is this note's baseline;
        # like slide and glide this is measured as a rise, not an absolute.
        base = self._pressure.setdefault(msg.channel, value)

        # Re-arm once the finger eases off, so each deliberate push repeats
        # once. Without this, resting a finger would repeat forever.
        if value - base < REPEAT_RISE // 2:
            self._repeat_armed[msg.channel] = True

        if self._gesture_name("press") != "repeat":
            return []
        pending = self._pending.get(msg.channel)
        if pending is not None and pending.silent:
            return []
        if self.last_char is None:
            return []
        if not self._repeat_armed.get(msg.channel, True):
            return []

        # A repeat needs a deliberate rise above the baseline, not merely the
        # pressure an ordinary press produces.
        if value - base < REPEAT_RISE:
            return []
        now = self._note()
        if now - self._last_repeat < REPEAT_INTERVAL:
            return []
        self._last_repeat = now
        self._repeat_armed[msg.channel] = False
        if pending is not None:
            pending.last_gesture = now
        self._record(f"repeat {self.last_char!r}")
        return [TypeChar(self.last_char, "repeat")]

    # -- emission ------------------------------------------------------------
    def _resolve(self, pending: _Pending, now: float | None = None) -> list[Action]:
        """Turn a strike into a character, if it is not being used as a gesture."""
        if pending.resolved:
            return []
        pending.resolved = True
        self._pending.pop(pending.channel, None)

        # A note struck while the board was already in MIDI mode is a musical
        # gesture, not typing, and must stay silent.
        if pending.silent:
            self._record(f"silent (midi mode) note {pending.note}")
            return []

        if pending.glide is not None:
            # The board's continuous streams wobble during a plain press, so a
            # deflection only counts as a glide if it was held briefly. A
            # momentary spike falls through and types the plain character.
            now = self._note() if now is None else now
            if now - pending.last_gesture >= GESTURE_SETTLE:
                name = pending.glide
                self._record(f"{name} (glide)")
                return [PressKey(name, "glide")]
            self._record(f"glide {pending.glide} too brief, ignoring")

        char = self.layout.character_for(pending.colour, pending.index, pending.layer)
        if char is None:
            self._record(f"unassigned {pending.colour} #{pending.index}")
            return []

        # Named keys are pressed rather than typed.
        if char in NAMED_KEYS:
            self._record(f"{char} (named)")
            return [PressKey(char, pending.layer)]
        if char not in CHAR_TO_KEY:
            self._record(f"no keycode for {char!r}")
            return []

        self.last_char = char
        self._record(f"{char!r} ({pending.layer})")
        return [TypeChar(char, pending.layer)]

    def tick(self, now: float | None = None) -> list[Action]:
        """Resolve strikes whose decision window has closed, and check the toggle."""
        now = self._note() if now is None else now
        out: list[Action] = []

        if self._top_key_since is not None and not self._top_key_consumed:
            if now - self._top_key_since >= self.toggle_hold:
                self._top_key_consumed = True
                self.typing_enabled = not self.typing_enabled
                state = "typing" if self.typing_enabled else "midi"
                self._record(f"mode -> {state}")
                out.append(ToggleMode(f"hold {self.toggle_hold:.1f}s"))
                # Suppress whatever that key was going to type.
                for pending in list(self._pending.values()):
                    if (
                        pending.colour == "white"
                        and pending.index == self.top_white_index
                    ):
                        pending.resolved = True
                        self._pending.pop(pending.channel, None)

        for pending in list(self._pending.values()):
            if not pending.resolved and now >= pending.deadline:
                out.extend(self._resolve(pending, now))
        return out

    def release_all(self) -> None:
        """Forget every held note. Call when the input stream is interrupted."""
        self._pending.clear()
        self._channels.clear()
        self._pressure.clear()
        self._repeat_armed.clear()
        self._top_key_since = None
        self._top_key_consumed = False
