"""Executes engine actions against the virtual keyboard.

Separated from the engine so the engine stays pure logic and this stays the
only place that touches ``/dev/uinput``.
"""

from __future__ import annotations

from .engine import Action, PressKey, ToggleMode, TypeChar
from .keycodes import CHAR_TO_KEY, NAMED_KEYS
from .uinput_kbd import VirtualKeyboard


class ActionSink:
    """Applies actions to a device, or prints them when in dry-run mode."""

    def __init__(self, keyboard: VirtualKeyboard | None, dry_run: bool = False, echo=None) -> None:
        self.keyboard = keyboard
        self.dry_run = dry_run
        self._echo = echo or (lambda text: print(text, flush=True))
        self.typed: list[str] = []
        self.pressed: list[str] = []

    def apply(self, actions: list[Action]) -> None:
        for action in actions:
            if isinstance(action, TypeChar):
                self._type(action)
            elif isinstance(action, PressKey):
                self._press(action)
            elif isinstance(action, ToggleMode):
                # The daemon owns mode switching; the sink only reports it.
                self._echo("  -- mode toggled --")

    def _type(self, action: TypeChar) -> None:
        entry = CHAR_TO_KEY.get(action.char)
        if entry is None:
            self._echo(f"  !! no keycode for {action.char!r}")
            return
        code, shift = entry
        self.typed.append(action.char)
        if self.dry_run:
            self._echo(f"  type {action.char!r}  [{action.source}]")
        elif self.keyboard is not None:
            self.keyboard.press(code, shift=shift)

    def _press(self, action: PressKey) -> None:
        code = NAMED_KEYS.get(action.name)
        if code is None:
            self._echo(f"  !! unknown named key {action.name!r}")
            return
        self.pressed.append(action.name)
        if self.dry_run:
            self._echo(f"  press {action.name}  [{action.source}]")
        elif self.keyboard is not None:
            self.keyboard.tap(code)

    @property
    def text(self) -> str:
        """Everything typed so far, for dry-run verification."""
        return "".join(self.typed)

    def release_all(self) -> None:
        if self.keyboard is not None and not self.dry_run:
            self.keyboard.release_all()
