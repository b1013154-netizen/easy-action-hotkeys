import copy
import threading
import time
import sys
import logging
from PySide6.QtCore import QObject, Signal
from pynput import keyboard, mouse
from .input import native_key
from .model import describe, key_tokens, validate_step
from .desktop import image_matches, save_screenshot
from . import accessibility


class Runner(QObject):
    progress = Signal(int, str)
    finished = Signal(str, bool)

    def __init__(self, screens, hub):
        super().__init__()
        self.screens = screens
        self.hub = hub
        self.cancel = threading.Event()
        self.thread = None

    @property
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, steps):
        if self.busy:
            return False
        for step in steps:
            validate_step(step)
        self.cancel.clear()
        self.hub.suspended = True
        self.thread = threading.Thread(target=self._run, args=(copy.deepcopy(steps),), daemon=True)
        self.thread.start()
        return True

    def stop(self):
        self.cancel.set()

    def wait(self, seconds):
        if self.cancel.wait(seconds):
            raise InterruptedError()

    def _move(self, destination, seconds=.25):
        start = self.mouse.position
        if start is None:
            raise RuntimeError('マウスを操作できません。通常のデスクトップで起動してください。')
        count = max(1, int(seconds / .02))
        for i in range(1, count + 1):
            self.wait(seconds / count)
            self.mouse.position = tuple(a + (b-a)*i/count for a, b in zip(start, destination))

    def _click(self, point, button='left', count=1):
        self._move(point, .1)
        for _ in range(count):
            self.wait(.05)
            self.mouse.click(getattr(mouse.Button, button))
            self.wait(.12)

    def _keys(self, value):
        pressed = []
        try:
            for token in key_tokens(value):
                self.wait(.03)
                key = native_key(token)
                self.keys.press(key)
                pressed.append(key)
        finally:
            for key in reversed(pressed):
                self.keys.release(key)

    def _resolve(self, step):
        deadline = time.monotonic() + step.get('seconds', 10)
        while True:
            self.wait(.01)
            if step['kind'] in ('element', 'check'):
                point = accessibility.find_target(step['target'])
            else:
                point = image_matches(self.screens.capture(self.cancel), step['png'], step.get('threshold', .92))
            if point is not None:
                return point
            if time.monotonic() >= deadline:
                raise RuntimeError('対象が見つかりません。対象のアプリを手前に出すか、画像・ボタンを選び直してください。')
            self.wait(.3)

    def _run(self, steps):
        held = set()
        drag_held = False
        message, ok = 'すべての動作が完了しました。', True
        initialized_com = False
        try:
            if sys.platform == 'win32':
                import pythoncom
                # UI Automation's event machinery requires an MTA worker.
                pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
                initialized_com = True
            self.keys, self.mouse = keyboard.Controller(), mouse.Controller()
            deadline = time.monotonic() + 5
            while self.hub.pressed:
                if time.monotonic() >= deadline:
                    raise RuntimeError('呼び出しキーを離してから実行してください。')
                self.wait(.05)
            for remaining in (3, 2, 1):
                self.progress.emit(-1, f'{remaining}秒後に開始します。操作する画面を手前に出してください。Escで停止できます。')
                self.wait(1)
            for index, step in enumerate(steps):
                self.wait(.01)
                self.progress.emit(index, describe(step))
                kind = step['kind']
                if kind == 'wait':
                    self.wait(step['seconds'])
                elif kind == 'move':
                    self._move((step['x'], step['y']), step.get('duration', .3))
                elif kind == 'click':
                    self._click((step['x'], step['y']), step.get('button', 'left'), step.get('count', 1))
                elif kind == 'drag':
                    self._move((step['x'], step['y']), .1)
                    self.mouse.press(mouse.Button.left)
                    drag_held = True
                    try:
                        self._move((step['to_x'], step['to_y']), step.get('duration', .5))
                    finally:
                        self.mouse.release(mouse.Button.left)
                        drag_held = False
                elif kind == 'scroll':
                    self.mouse.scroll(0, step['amount'])
                elif kind == 'text':
                    for char in step['text']:
                        self.wait(.006)
                        if char == '\n':
                            self._keys('enter')
                        elif char == '\t':
                            self._keys('tab')
                        else:
                            self.keys.type(char)
                elif kind == 'keys':
                    self._keys(step['keys'])
                elif kind == 'key_down':
                    key = native_key(step['keys'])
                    self.keys.press(key)
                    held.add(key)
                elif kind == 'key_up':
                    key = native_key(step['keys'])
                    self.keys.release(key)
                    held.discard(key)
                elif kind in ('image', 'element'):
                    self._click(self._resolve(step), step.get('button', 'left'), step.get('count', 1))
                elif kind == 'check':
                    point = self._resolve(step)
                    state = accessibility.check_state(step['target'])
                    if state is None:
                        raise RuntimeError('チェック項目が見つかりません。')
                    if state != step['checked']:
                        self._click(point)
                        self.wait(.3)
                    if accessibility.check_state(step['target']) != step['checked']:
                        raise RuntimeError('チェックの状態を変更できませんでした。')
                elif kind == 'screenshot':
                    paths = save_screenshot(self.screens.capture(self.cancel), step['folder'])
                    message = '完了しました。画像の保存先：' + paths[-1]
        except InterruptedError:
            message, ok = '停止しました。', False
        except Exception as exc:
            logging.exception('Automation stopped')
            message, ok = f'動作を停止しました：{exc}', False
        finally:
            for key in held:
                try:
                    self.keys.release(key)
                except Exception:
                    pass
            if drag_held:
                self.mouse.release(mouse.Button.left)
            if initialized_com:
                import pythoncom
                pythoncom.CoUninitialize()
            self.finished.emit(message, ok)
