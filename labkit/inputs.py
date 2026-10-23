"""Synthetic input for the game window through posted Win32 messages.

GLFW turns WM_MOUSEMOVE / WM_*BUTTON* / WM_KEY* / WM_CHAR into its cursor, button, key and char
callbacks, and the engine reads input from exactly those (org.lwjglx.input.Mouse.addMoveEvent,
glfwGetMouseButton/glfwGetKey caches). So the real cursor and keyboard are untouched and the
window needs no focus. Coordinates are client pixels == the game's UI coordinates (getMouseX/Y).
"""

import time

from . import win32

WM_KEYDOWN, WM_KEYUP, WM_CHAR = 0x0100, 0x0101, 0x0102
WM_MOUSEMOVE, WM_MOUSEWHEEL = 0x0200, 0x020A
BUTTONS = {
    # name: (down msg, up msg, MK_ flag)
    "left": (0x0201, 0x0202, 0x0001),
    "right": (0x0204, 0x0205, 0x0002),
    "middle": (0x0207, 0x0208, 0x0010),
}

# Virtual-key codes; True marks extended keys (affects the scancode flag GLFW reads).
KEYS = {
    "esc": (0x1B, False), "escape": (0x1B, False), "enter": (0x0D, False), "return": (0x0D, False),
    "tab": (0x09, False), "space": (0x20, False), "backspace": (0x08, False),
    "shift": (0xA0, False), "lshift": (0xA0, False), "rshift": (0xA1, False),
    "ctrl": (0xA2, False), "lctrl": (0xA2, False), "rctrl": (0xA3, True),
    "alt": (0xA4, False), "lalt": (0xA4, False), "ralt": (0xA5, True),
    "up": (0x26, True), "down": (0x28, True), "left": (0x25, True), "right": (0x27, True),
    "insert": (0x2D, True), "delete": (0x2E, True), "home": (0x24, True), "end": (0x23, True),
    "pageup": (0x21, True), "pagedown": (0x22, True),
    "minus": (0xBD, False), "equals": (0xBB, False), "comma": (0xBC, False), "period": (0xBE, False),
    "slash": (0xBF, False), "semicolon": (0xBA, False), "quote": (0xDE, False),
    "lbracket": (0xDB, False), "rbracket": (0xDD, False), "backslash": (0xDC, False), "grave": (0xC0, False),
}
for _i in range(26):
    KEYS[chr(ord("a") + _i)] = (0x41 + _i, False)
for _i in range(10):
    KEYS[str(_i)] = (0x30 + _i, False)
    KEYS["num" + str(_i)] = (0x60 + _i, False)
for _i in range(1, 13):
    KEYS["f" + str(_i)] = (0x6F + _i, False)


def _lparam_xy(x, y):
    return ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)


class Input:
    def __init__(self, hwnd, frame_s=1 / 30):
        self.hwnd = hwnd
        self.frame_s = frame_s  # a pause long enough for the game to poll at least once
        self.buttons = 0
        self.pos = (0, 0)

    def _post(self, msg, wparam, lparam):
        win32.post(self.hwnd, msg, wparam, lparam)

    # Mouse ---------------------------------------------------------------------------------

    def move(self, x, y):
        self.pos = (int(x), int(y))
        self._post(WM_MOUSEMOVE, self.buttons, _lparam_xy(x, y))

    def down(self, button="left", x=None, y=None):
        if x is not None:
            self.move(x, y)
        msg_down, _, flag = BUTTONS[button]
        self.buttons |= flag
        self._post(msg_down, self.buttons, _lparam_xy(*self.pos))

    def up(self, button="left", x=None, y=None):
        if x is not None:
            self.move(x, y)
        _, msg_up, flag = BUTTONS[button]
        self.buttons &= ~flag
        self._post(msg_up, self.buttons, _lparam_xy(*self.pos))

    def click(self, x, y, button="left", hold_s=None, double=False, modifiers=()):
        """modifiers: key names held around the click, e.g. ("alt",) or ("ctrl", "shift")."""
        held = self.press_modifiers(modifiers)
        try:
            self.move(x, y)
            time.sleep(self.frame_s)
            for _ in range(2 if double else 1):
                self.down(button)
                time.sleep(hold_s if hold_s is not None else self.frame_s * 2)
                self.up(button)
                time.sleep(self.frame_s)
        finally:
            self.release_modifiers(held)

    def drag(self, x1, y1, x2, y2, button="left", steps=10, modifiers=()):
        held = self.press_modifiers(modifiers)
        try:
            self.move(x1, y1)
            time.sleep(self.frame_s)
            self.down(button)
            time.sleep(self.frame_s * 2)
            for i in range(1, steps + 1):
                self.move(x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps)
                time.sleep(self.frame_s)
            time.sleep(self.frame_s)
            self.up(button)
            time.sleep(self.frame_s)
        finally:
            self.release_modifiers(held)

    def wheel(self, x, y, notches):
        self.move(x, y)
        time.sleep(self.frame_s)
        sx, sy = win32.client_to_screen(self.hwnd, x, y)
        delta = int(notches * 120) & 0xFFFF
        self._post(WM_MOUSEWHEEL, (delta << 16) | self.buttons, _lparam_xy(sx, sy))
        time.sleep(self.frame_s)

    # Keyboard ------------------------------------------------------------------------------

    @staticmethod
    def _key(name):
        key = KEYS.get(name.lower())
        if key is None:
            raise ValueError("unknown key '%s' (known: %s)" % (name, ", ".join(sorted(KEYS))))
        return key

    def key_down(self, name):
        vk, ext = self._key(name)
        lparam = 1 | (win32.scancode(vk) << 16) | ((1 if ext else 0) << 24)
        self._post(WM_KEYDOWN, vk, lparam)

    def key_up(self, name):
        vk, ext = self._key(name)
        lparam = 1 | (win32.scancode(vk) << 16) | ((1 if ext else 0) << 24) | (1 << 30) | (1 << 31)
        self._post(WM_KEYUP, vk, lparam)

    def press_modifiers(self, modifiers):
        """Presses each named key (a name or a sequence of names); returns them for
        release_modifiers. The game sees them held from the next frame on."""
        names = [modifiers] if isinstance(modifiers, str) else list(modifiers or ())
        for name in names:
            self._key(name)  # validate all before pressing any
        for name in names:
            self.key_down(name)
            time.sleep(self.frame_s)
        return names

    def release_modifiers(self, names):
        for name in reversed(names):
            self.key_up(name)
            time.sleep(self.frame_s)

    def tap(self, name, hold_s=None):
        self.key_down(name)
        time.sleep(hold_s if hold_s is not None else self.frame_s * 2)
        self.key_up(name)
        time.sleep(self.frame_s)

    def chord(self, *names, hold_s=None):
        """Press keys in order, release in reverse (e.g. chord('ctrl', 'a'))."""
        for name in names:
            self.key_down(name)
            time.sleep(self.frame_s)
        time.sleep(hold_s if hold_s is not None else self.frame_s * 2)
        for name in reversed(names):
            self.key_up(name)
            time.sleep(self.frame_s)

    def type_text(self, text):
        for ch in text:
            self._post(WM_CHAR, ord(ch), 1)
            time.sleep(self.frame_s / 2)
