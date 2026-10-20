"""Linux input-event key codes and a character -> (keycode, shift) table.

Values come from ``linux/input-event-codes.h``. We only list what a keyboard
layout can plausibly emit.
"""

from __future__ import annotations

# --- modifier / control keys ------------------------------------------------
KEY_ESC = 1
KEY_BACKSPACE = 14
KEY_TAB = 15
KEY_ENTER = 28
KEY_LEFTCTRL = 29
KEY_LEFTSHIFT = 42
KEY_RIGHTSHIFT = 54
KEY_LEFTALT = 56
KEY_SPACE = 57
KEY_CAPSLOCK = 58
KEY_RIGHTCTRL = 97
KEY_RIGHTALT = 100
KEY_LEFTMETA = 125

# --- letters ----------------------------------------------------------------
KEY_A, KEY_B, KEY_C, KEY_D, KEY_E = 30, 48, 46, 32, 18
KEY_F, KEY_G, KEY_H, KEY_I, KEY_J = 33, 34, 35, 23, 36
KEY_K, KEY_L, KEY_M, KEY_N, KEY_O = 37, 38, 50, 49, 24
KEY_P, KEY_Q, KEY_R, KEY_S, KEY_T = 25, 16, 19, 31, 20
KEY_U, KEY_V, KEY_W, KEY_X, KEY_Y = 22, 47, 17, 45, 21
KEY_Z = 44

# --- digits and the unshifted punctuation row -------------------------------
KEY_1, KEY_2, KEY_3, KEY_4 = 2, 3, 4, 5
KEY_5, KEY_6, KEY_7, KEY_8 = 6, 7, 8, 9
KEY_9, KEY_0 = 10, 11
KEY_MINUS = 12
KEY_EQUAL = 13
KEY_LEFTBRACE = 26
KEY_RIGHTBRACE = 27
KEY_SEMICOLON = 39
KEY_APOSTROPHE = 40
KEY_GRAVE = 41
KEY_BACKSLASH = 43
KEY_COMMA = 51
KEY_DOT = 52
KEY_SLASH = 53

EV_SYN = 0
EV_KEY = 1
SYN_REPORT = 0

#: Everything a layout may press. Registered with uinput at device-creation time.
ALL_KEYS: frozenset[int] = frozenset(
    v for k, v in list(globals().items()) if k.startswith("KEY_") and isinstance(v, int)
)

#: ``character -> (keycode, needs_shift)`` for the US layout.
CHAR_TO_KEY: dict[str, tuple[int, bool]] = {}

_CHAR_CODE = {
    "a": KEY_A, "b": KEY_B, "c": KEY_C, "d": KEY_D, "e": KEY_E, "f": KEY_F,
    "g": KEY_G, "h": KEY_H, "i": KEY_I, "j": KEY_J, "k": KEY_K, "l": KEY_L,
    "m": KEY_M, "n": KEY_N, "o": KEY_O, "p": KEY_P, "q": KEY_Q, "r": KEY_R,
    "s": KEY_S, "t": KEY_T, "u": KEY_U, "v": KEY_V, "w": KEY_W, "x": KEY_X,
    "y": KEY_Y, "z": KEY_Z,
    "1": KEY_1, "2": KEY_2, "3": KEY_3, "4": KEY_4, "5": KEY_5,
    "6": KEY_6, "7": KEY_7, "8": KEY_8, "9": KEY_9, "0": KEY_0,
    "-": KEY_MINUS, "=": KEY_EQUAL, "[": KEY_LEFTBRACE, "]": KEY_RIGHTBRACE,
    ";": KEY_SEMICOLON, "'": KEY_APOSTROPHE, "`": KEY_GRAVE, "\\": KEY_BACKSLASH,
    ",": KEY_COMMA, ".": KEY_DOT, "/": KEY_SLASH, " ": KEY_SPACE,
}
_SHIFTED_CODE = {
    "!": KEY_1, "@": KEY_2, "#": KEY_3, "$": KEY_4, "%": KEY_5,
    "^": KEY_6, "&": KEY_7, "*": KEY_8, "(": KEY_9, ")": KEY_0,
    "_": KEY_MINUS, "+": KEY_EQUAL, "{": KEY_LEFTBRACE, "}": KEY_RIGHTBRACE,
    ":": KEY_SEMICOLON, '"': KEY_APOSTROPHE, "~": KEY_GRAVE, "|": KEY_BACKSLASH,
    "<": KEY_COMMA, ">": KEY_DOT, "?": KEY_SLASH,
}

for _c, _code in _CHAR_CODE.items():
    CHAR_TO_KEY[_c] = (_code, False)
for _c, _code in _SHIFTED_CODE.items():
    CHAR_TO_KEY[_c] = (_code, True)
for _c, _code in _CHAR_CODE.items():
    if _c.isalpha():
        CHAR_TO_KEY[_c.upper()] = (_code, True)

#: Named keys a layout may reference by name instead of by character.
NAMED_KEYS: dict[str, int] = {
    "esc": KEY_ESC,
    "backspace": KEY_BACKSPACE,
    "tab": KEY_TAB,
    "enter": KEY_ENTER,
    "space": KEY_SPACE,
    "leftctrl": KEY_LEFTCTRL,
    "rightctrl": KEY_RIGHTCTRL,
    "leftshift": KEY_LEFTSHIFT,
    "rightshift": KEY_RIGHTSHIFT,
    "leftalt": KEY_LEFTALT,
    "rightalt": KEY_RIGHTALT,
    "leftmeta": KEY_LEFTMETA,
    "capslock": KEY_CAPSLOCK,
}
