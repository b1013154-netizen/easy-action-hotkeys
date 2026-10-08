import copy
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from easy_action.ui import MainWindow, StepDialog, STYLE
from easy_action.model import Store
from easy_action.input import InputHub
from easy_action.runner import Runner
from pynput import keyboard


APP = QApplication.instance() or QApplication([])
APP.setStyleSheet(STYLE)


class FakeKeyboard:
    def __init__(self):
        self.held = set()
        self.events = []
    def press(self, key):
        self.held.add(key)
        self.events.append(('press', key))
    def release(self, key):
        self.held.discard(key)
        self.events.append(('release', key))
    def type(self, value):
        self.events.append(('type', value))


class FakeMouse:
    position = (0, 0)
    def __init__(self):
        self.held = set()
        self.events = []
    def press(self, key):
        self.held.add(key)
    def release(self, key):
        self.held.discard(key)
    def click(self, key):
        self.events.append(('click', self.position, key))
    def scroll(self, x, y):
        self.events.append(('scroll', x, y))


class GuiTests(unittest.TestCase):
    def test_create_save_edit_and_reload(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'actions.json')
            window = MainWindow(store, start_listeners=False)
            self.assertTrue(window.new())
            window.name.setText('文章を入力する')
            window.hotkey.setText('ctrl+alt+f10')
            dialog = StepDialog(window, {'kind': 'text', 'text': '日本語の文章\n次の行'})
            dialog.save()
            window.draft['steps'].append(dialog.result_step)
            self.assertTrue(window.save())
            self.assertEqual(store.load()[0]['steps'][0]['text'], '日本語の文章\n次の行')
            self.assertFalse(window.dirty)
            self.assertEqual(window.macro_list.count(), 1)
            window.close()

    def test_all_action_forms_construct_and_screenshot_template(self):
        with tempfile.TemporaryDirectory() as folder:
            window = MainWindow(Store(Path(folder) / 'actions.json'), start_listeners=False)
            window.screenshot_template()
            window.hotkey.setText('ctrl+alt+f11')
            self.assertTrue(window.save())
            for index in range(len(__import__('easy_action.model', fromlist=['KINDS']).KINDS)):
                dialog = StepDialog(window)
                dialog.kind.setCurrentIndex(index)
                self.assertTrue(dialog.widgets is not None)
                dialog.close()
            window.close()


class RunnerTests(unittest.TestCase):
    def run_steps(self, steps, cancel=False):
        hub = InputHub()
        runner = Runner(None, hub)
        keys, mouse = FakeKeyboard(), FakeMouse()
        results = []
        runner.finished.connect(lambda message, ok: results.append((message, ok)))
        calls = [0]
        def immediate_wait(seconds):
            calls[0] += 1
            if cancel and calls[0] >= 6:
                raise InterruptedError()
        runner.wait = immediate_wait
        with patch('easy_action.runner.keyboard.Controller', return_value=keys), patch('easy_action.runner.mouse.Controller', return_value=mouse):
            runner._run(steps)
        return keys, mouse, results

    def test_order_and_unicode_input(self):
        keys, mouse, results = self.run_steps([
            {'kind': 'click', 'x': 20, 'y': 30}, {'kind': 'text', 'text': '日本語\n'},
            {'kind': 'keys', 'keys': 'ctrl+s'}, {'kind': 'scroll', 'amount': -3}])
        self.assertEqual(mouse.events[0][1], (20, 30))
        self.assertEqual([e[1] for e in keys.events if e[0] == 'type'], ['日', '本', '語'])
        self.assertFalse(keys.held)
        self.assertTrue(results[0][1])

    def test_cancel_releases_held_modifier(self):
        keys, mouse, results = self.run_steps([
            {'kind': 'key_down', 'keys': 'ctrl'}, {'kind': 'wait', 'seconds': 30},
            {'kind': 'key_up', 'keys': 'ctrl'}], cancel=True)
        self.assertFalse(keys.held)
        self.assertFalse(results[0][1])

    def test_checkbox_action_sets_state_without_toggling_twice(self):
        step = {'kind': 'check', 'target': {}, 'checked': True, 'seconds': 1}
        with patch('easy_action.runner.accessibility.find_target', return_value=(20, 30)), patch('easy_action.runner.accessibility.check_state', side_effect=[False, True, True, True]):
            keys, mouse, results = self.run_steps([step, step])
        self.assertEqual(len([e for e in mouse.events if e[0] == 'click']), 1)
        self.assertTrue(results[0][1])


class InputTests(unittest.TestCase):
    def test_hotkey_fires_once_until_released(self):
        hub = InputHub()
        hub.bindings = {'ctrl+alt+f10': 'sample'}
        hub.suspended = False
        fired = []
        hub.triggered.connect(fired.append)
        # The dummy backend aliases Key enum members; exercise state logic with
        # canonical names. Native key conversion is checked by windows_smoke.py.
        with patch('easy_action.input.key_name', side_effect=lambda key: key):
            for key in ('ctrl', 'alt', 'f10', 'f10'):
                hub._press(key)
            hub._release('f10')
            hub._press('f10')
        self.assertEqual(fired[:1], ['sample'])
        self.assertEqual(fired, ['sample', 'sample'])

    def test_recording_stop_releases_modifier_and_excludes_stop_key(self):
        hub = InputHub()
        steps, completed = [], []
        hub.recorded.connect(steps.append)
        hub.recording_finished.connect(lambda: completed.append(True))
        hub.begin_record()
        with patch('easy_action.input.key_name', side_effect=lambda key: key):
            hub._press('ctrl')
            hub._press('f8')
        self.assertFalse(hub.recording)
        self.assertEqual([s for s in steps if s['kind'] != 'wait'], [
            {'kind': 'key_down', 'keys': 'ctrl'}, {'kind': 'key_up', 'keys': 'ctrl'}])
        self.assertEqual(completed, [True])


if __name__ == '__main__':
    unittest.main()
