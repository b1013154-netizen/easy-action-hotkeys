import threading
import time
from PySide6.QtCore import QObject, Signal
from pynput import keyboard, mouse
from .model import normalize_hotkey, MODIFIERS, NAMED_KEYS


def key_name(key):
    if isinstance(key, keyboard.Key):
        name = key.name
        return {'ctrl_l': 'ctrl', 'ctrl_r': 'ctrl', 'alt_l': 'alt', 'alt_r': 'alt',
                'alt_gr': 'alt', 'shift_l': 'shift', 'shift_r': 'shift',
                'cmd_l': 'cmd', 'cmd_r': 'cmd'}.get(name, name)
    char = key.char
    if char and len(char) == 1:
        if 1 <= ord(char) <= 26:
            return chr(ord(char) + 96)
        return 'plus' if char == '+' else char.lower()
    vk = getattr(key, 'vk', None)
    if vk and 48 <= vk <= 90:
        return chr(vk).lower()
    return None


def native_key(name):
    if name == 'plus':
        return '+'
    if len(name) == 1:
        return name
    return getattr(keyboard.Key, name)


class InputHub(QObject):
    triggered = Signal(str)
    stop_requested = Signal()
    recording_finished = Signal()
    recorded = Signal(object)
    checkpoint_requested = Signal()
    failed = Signal(str)

    def __init__(self):
        super().__init__()
        self.lock = threading.RLock()
        self.pressed = set()
        self.bindings = {}
        self.suspended = True
        self.recording = False
        self.keyboard_listener = None
        self.mouse_listener = None
        self.last_event = 0
        self.last_move = 0
        self.mouse_down = {}
        self.recorded_keys = set()
        self.record_limit = 1900
        self.event_count = 0

    def start(self):
        try:
            self.keyboard_listener = keyboard.Listener(on_press=self._press, on_release=self._release)
            self.mouse_listener = mouse.Listener(on_move=self._move, on_click=self._click, on_scroll=self._scroll)
            self.keyboard_listener.start()
            self.mouse_listener.start()
            if not getattr(self.keyboard_listener, 'IS_TRUSTED', True):
                self.failed.emit('キーボードを読み取る権限がありません。Macの「入力監視」と「アクセシビリティ」を許可してください。')
        except Exception as exc:
            self.failed.emit(f'キー監視を開始できません：{exc}')

    def close(self):
        for listener in (self.keyboard_listener, self.mouse_listener):
            if listener:
                listener.stop()

    def set_bindings(self, macros):
        with self.lock:
            self.bindings = {normalize_hotkey(m['hotkey']): m['id'] for m in macros if m['enabled']}

    def begin_record(self, limit=1900):
        with self.lock:
            self.recording = True
            self.suspended = True
            self.last_event = time.monotonic()
            self.mouse_down.clear()
            self.recorded_keys.clear()
            self.record_limit = limit
            self.event_count = 0

    def end_record(self):
        with self.lock:
            if not self.recording:
                return
            for name in sorted(self.recorded_keys):
                self.recorded.emit({'kind': 'key_up', 'keys': name})
            for (button, (x, y)) in self.mouse_down.items():
                self.recorded.emit({'kind': 'click', 'x': x, 'y': y, 'button': button, 'count': 1})
            self.recording = False
            self.recorded_keys.clear()
            self.mouse_down.clear()
            self.recording_finished.emit()

    def _emit(self, step):
        # Reserve room to release held keys cleanly at the end.
        if self.event_count >= self.record_limit:
            self.end_record()
            return
        now = time.monotonic()
        delay = now - self.last_event
        if delay > .07:
            self.recorded.emit({'kind': 'wait', 'seconds': round(min(delay, 3600), 3)})
            self.event_count += 1
        self.last_event = now
        self.recorded.emit(step)
        self.event_count += 1

    def _press(self, key):
        with self.lock:
            name = key_name(key)
            if not name:
                return
            repeated = name in self.pressed
            self.pressed.add(name)
            if name == 'esc':
                self.stop_requested.emit()
                self.end_record()
                return
            if name == 'f8' and self.recording:
                self.end_record()
                return
            if name == 'f7' and self.recording:
                if not repeated:
                    self.checkpoint_requested.emit()
                return
            if self.recording:
                if not repeated and (len(name) == 1 or name in NAMED_KEYS):
                    self.recorded_keys.add(name)
                    self._emit({'kind': 'key_down', 'keys': name})
                return
            if not self.suspended and not repeated:
                try:
                    chord = normalize_hotkey('+'.join(sorted(self.pressed)))
                except ValueError:
                    return
                if chord in self.bindings:
                    self.triggered.emit(self.bindings[chord])

    def _release(self, key):
        with self.lock:
            name = key_name(key)
            self.pressed.discard(name)
            if self.recording and name in self.recorded_keys:
                self.recorded_keys.remove(name)
                self._emit({'kind': 'key_up', 'keys': name})

    def _move(self, x, y):
        with self.lock:
            if self.recording and not self.mouse_down and time.monotonic() - self.last_move >= .15:
                self.last_move = time.monotonic()
                self._emit({'kind': 'move', 'x': int(x), 'y': int(y), 'duration': .1})

    def _click(self, x, y, button, pressed):
        with self.lock:
            if not self.recording:
                return
            name = button.name
            if name not in ('left', 'right', 'middle'):
                return
            if pressed:
                self.mouse_down[name] = (int(x), int(y))
                return
            start = self.mouse_down.pop(name, (int(x), int(y)))
            if abs(start[0] - x) + abs(start[1] - y) > 8:
                self._emit({'kind': 'drag', 'x': start[0], 'y': start[1],
                            'to_x': int(x), 'to_y': int(y), 'duration': .5})
            else:
                self._emit({'kind': 'click', 'x': int(x), 'y': int(y), 'button': name, 'count': 1})

    def _scroll(self, x, y, dx, dy):
        with self.lock:
            if self.recording and dy:
                self._emit({'kind': 'move', 'x': int(x), 'y': int(y), 'duration': .05})
                self._emit({'kind': 'scroll', 'amount': int(dy)})
