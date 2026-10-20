"""A virtual keyboard built on /dev/uinput, using only the standard library.

``python-evdev`` is the usual way to do this, but it needs a C compiler and
Python 3.14 has no prebuilt wheel. The uinput ABI is small enough to drive
directly with ``fcntl.ioctl`` and ``struct``, which keeps this project free of
any third-party dependency.

The device this creates looks to the rest of the system exactly like a USB
keyboard, so it works under Wayland, X11 and on a bare TTY.
"""

from __future__ import annotations

import ctypes
import fcntl
import os
import struct
import time

# --- ioctl encoding (asm-generic/ioctl.h) -----------------------------------
_IOC_NRBITS = 8
_IOC_TYPEBITS = 8
_IOC_SIZEBITS = 14
_IOC_DIRBITS = 2
_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
_IOC_DIRSHIFT = _IOC_SIZESHIFT + _IOC_SIZEBITS
_IOC_NONE = 0
_IOC_WRITE = 1


def _IOC(dir_: int, type_: str, nr: int, size: int) -> int:
    return (
        (dir_ << _IOC_DIRSHIFT)
        | (ord(type_) << _IOC_TYPESHIFT)
        | (nr << _IOC_NRSHIFT)
        | (size << _IOC_SIZESHIFT)
    )


def _IO(type_: str, nr: int) -> int:
    return _IOC(_IOC_NONE, type_, nr, 0)


def _IOW(type_: str, nr: int, size: int) -> int:
    return _IOC(_IOC_WRITE, type_, nr, size)


# --- kernel structs (linux/uinput.h) ----------------------------------------
UINPUT_MAX_NAME_SIZE = 80


class _InputId(ctypes.Structure):
    _fields_ = [
        ("bustype", ctypes.c_uint16),
        ("vendor", ctypes.c_uint16),
        ("product", ctypes.c_uint16),
        ("version", ctypes.c_uint16),
    ]


class _UinputSetup(ctypes.Structure):
    _fields_ = [
        ("id", _InputId),
        ("name", ctypes.c_char * UINPUT_MAX_NAME_SIZE),
        ("ff_effects_max", ctypes.c_uint32),
    ]


UI_DEV_CREATE = _IO("U", 1)
UI_DEV_DESTROY = _IO("U", 2)
UI_DEV_SETUP = _IOW("U", 3, ctypes.sizeof(_UinputSetup))
UI_SET_EVBIT = _IOW("U", 100, ctypes.sizeof(ctypes.c_int))
UI_SET_KEYBIT = _IOW("U", 101, ctypes.sizeof(ctypes.c_int))

BUS_USB = 0x03

#: ``struct input_event``: struct timeval (two longs) + type + code + value.
_EVENT_FMT = "llHHi"
EVENT_SIZE = struct.calcsize(_EVENT_FMT)

EV_SYN = 0x00
EV_KEY = 0x01
SYN_REPORT = 0

_destroyed = False


class VirtualKeyboard:
    """A uinput-backed keyboard device.

    Use as a context manager, or call :meth:`close` explicitly — the kernel
    keeps the device alive until ``UI_DEV_DESTROY`` is issued or the fd closes.
    """

    def __init__(
        self,
        name: str = "Seaboard Virtual Keyboard",
        keys: set[int] | frozenset[int] = frozenset(),
        vendor: int = 0x5EAB,
        product: int = 0x0001,
    ) -> None:
        self.name = name
        self._fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        self._closed = False
        self._down: set[int] = set()
        try:
            fcntl.ioctl(self._fd, UI_SET_EVBIT, EV_KEY)
            fcntl.ioctl(self._fd, UI_SET_EVBIT, EV_SYN)
            for code in sorted(set(keys)):
                fcntl.ioctl(self._fd, UI_SET_KEYBIT, int(code))

            setup = _UinputSetup()
            setup.id.bustype = BUS_USB
            setup.id.vendor = vendor
            setup.id.product = product
            setup.id.version = 1
            setup.name = name.encode()[: UINPUT_MAX_NAME_SIZE - 1]
            fcntl.ioctl(self._fd, UI_DEV_SETUP, setup)
            fcntl.ioctl(self._fd, UI_DEV_CREATE)
        except Exception:
            os.close(self._fd)
            self._closed = True
            raise
        # The kernel needs a moment before the node is usable.
        time.sleep(0.15)

    # -- event emission ------------------------------------------------------
    def _emit(self, type_: int, code: int, value: int) -> None:
        os.write(self._fd, struct.pack(_EVENT_FMT, 0, 0, type_, code, value))

    def _syn(self) -> None:
        self._emit(EV_SYN, SYN_REPORT, 0)

    def key_down(self, code: int) -> None:
        """Press a key and flush, suppressing duplicates."""
        if code in self._down:
            return
        self._down.add(code)
        self._emit(EV_KEY, code, 1)
        self._syn()

    def key_up(self, code: int) -> None:
        if code not in self._down:
            return
        self._down.discard(code)
        self._emit(EV_KEY, code, 0)
        self._syn()

    def tap(self, code: int) -> None:
        """Press and release a key in one report."""
        self._emit(EV_KEY, code, 1)
        self._emit(EV_KEY, code, 0)
        self._syn()

    def press(self, code: int, shift: bool = False, ctrl: bool = False, alt: bool = False) -> None:
        """Press a key with optional modifiers held around it."""
        from .keycodes import KEY_LEFTALT, KEY_LEFTCTRL, KEY_LEFTSHIFT

        mods = []
        if ctrl:
            mods.append(KEY_LEFTCTRL)
        if alt:
            mods.append(KEY_LEFTALT)
        if shift:
            mods.append(KEY_LEFTSHIFT)
        for m in mods:
            self._emit(EV_KEY, m, 1)
        self._emit(EV_KEY, code, 1)
        self._emit(EV_KEY, code, 0)
        for m in reversed(mods):
            self._emit(EV_KEY, m, 0)
        self._syn()

    def release_all(self) -> None:
        """Lift every key we are currently holding. Safe to call at any time."""
        held = sorted(self._down)
        self._down.clear()
        for code in held:
            self._emit(EV_KEY, code, 0)
        if held:
            self._syn()

    @property
    def held(self) -> frozenset[int]:
        return frozenset(self._down)

    # -- lifecycle -----------------------------------------------------------
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.release_all()
            fcntl.ioctl(self._fd, UI_DEV_DESTROY)
        except OSError:
            pass
        finally:
            os.close(self._fd)

    def __enter__(self) -> "VirtualKeyboard":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
