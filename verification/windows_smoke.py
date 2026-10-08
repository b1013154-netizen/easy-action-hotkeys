"""Opt-in native Windows smoke test, operating ONLY a disposable test window.
Run from the repository root with the virtual-environment Python.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLineEdit, QPushButton, QLabel, QCheckBox

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FOLDER = Path(__file__).parent / 'native-results'
FOLDER.mkdir(exist_ok=True)

if '--fixture' in sys.argv:
    app = QApplication([])
    window = QWidget()
    window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
    window.setWindowTitle('EasyAction disposable verification fixture')
    window.setGeometry(150, 150, 560, 250)
    layout = QVBoxLayout(window)
    field = QLineEdit()
    field.setAccessibleName('EasyAction test text input')
    action = QPushButton('EasyAction test button')
    counter = QLabel('0')
    checkbox = QCheckBox('EasyAction test checkbox')
    layout.addWidget(field)
    layout.addWidget(action)
    layout.addWidget(counter)
    layout.addWidget(checkbox)
    def write_result():
        (FOLDER / 'fixture-result.json').write_text(json.dumps({'text': field.text(), 'clicks': int(counter.text()), 'checked': checkbox.isChecked()}, ensure_ascii=False), encoding='utf-8')
    def click():
        counter.setText(str(int(counter.text())+1))
        write_result()
    action.clicked.connect(click)
    field.textChanged.connect(write_result)
    checkbox.toggled.connect(write_result)
    window.show()
    window.raise_()
    window.activateWindow()
    def ready():
        point = field.mapToGlobal(field.rect().center())
        target = action.mapToGlobal(action.rect().center())
        check_point = checkbox.mapToGlobal(checkbox.rect().topLeft())
        (FOLDER / 'fixture-ready.json').write_text(json.dumps({'input': [point.x(), point.y()], 'button': [target.x(), target.y()], 'checkbox': [check_point.x()+10, check_point.y()+10]}))
        write_result()
    QTimer.singleShot(1000, ready)
    QTimer.singleShot(35000, app.quit)
    sys.exit(app.exec())

if sys.platform != 'win32':
    raise SystemExit('This smoke test requires an interactive Windows desktop.')

from easy_action.accessibility import capture_target
from easy_action.desktop import ScreenService
from easy_action.input import InputHub
from easy_action.runner import Runner
from pynput import keyboard

ready_file = FOLDER / 'fixture-ready.json'
# Remove only an individual stale verification file, never a directory.
if ready_file.exists():
    ready_file.unlink()
fixture = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--fixture'], creationflags=subprocess.CREATE_NO_WINDOW)
try:
    deadline = time.monotonic() + 10
    while not ready_file.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError('Fixture did not become ready')
        time.sleep(.1)
    app = QApplication([])
    geometry = json.loads(ready_file.read_text())
    input_target = capture_target(*geometry['input'])
    button_target = capture_target(*geometry['button'])
    checkbox_target = capture_target(*geometry['checkbox'])
    if not input_target['name'].startswith('EasyAction test') or not button_target['name'].startswith('EasyAction test'):
        raise RuntimeError('Fixture is obscured. Test stopped without sending input.')
    screens = ScreenService()
    hub = InputHub()
    hotkeys, recorded = [], []
    hub.bindings = {'ctrl+alt+f10': 'fixture-hotkey'}
    hub.triggered.connect(hotkeys.append)
    hub.recorded.connect(recorded.append)
    hub.start()
    runner = Runner(screens, hub)
    results = []
    def finished(message, ok):
        results.append({'ok': ok, 'message': message})
        hub.suspended = False
        def trigger_hotkey():
            controller = keyboard.Controller()
            for key in (keyboard.Key.ctrl, keyboard.Key.alt, keyboard.Key.f10):
                controller.press(key)
            for key in (keyboard.Key.f10, keyboard.Key.alt, keyboard.Key.ctrl):
                controller.release(key)
            QTimer.singleShot(300, record_input)
        def record_input():
            hub.begin_record()
            controller = keyboard.Controller()
            controller.press('r')
            controller.release('r')
            controller.press(keyboard.Key.f8)
            controller.release(keyboard.Key.f8)
            QTimer.singleShot(300, app.quit)
        QTimer.singleShot(300, trigger_hotkey)
    runner.finished.connect(finished)
    runner.start([
        # The verified fixture's input is clicked first to give it OS focus.
        {'kind': 'click', 'x': geometry['input'][0], 'y': geometry['input'][1]},
        {'kind': 'text', 'text': '日本語 test'},
        {'kind': 'element', 'target': button_target, 'seconds': 5},
        {'kind': 'check', 'target': checkbox_target, 'checked': True, 'seconds': 5},
        {'kind': 'check', 'target': checkbox_target, 'checked': True, 'seconds': 5},
        {'kind': 'screenshot', 'folder': str(FOLDER)},
    ])
    QTimer.singleShot(25000, lambda: (runner.stop(), app.quit()))
    app.exec()
    outcome = json.loads((FOLDER / 'fixture-result.json').read_text(encoding='utf-8'))
    result = {'runner': results, 'fixture': outcome,
              'hotkeys': hotkeys, 'recorded': recorded,
              'passed': bool(results and results[0]['ok'] and outcome['text'] == '日本語 test' and outcome['clicks'] == 1 and outcome['checked'] and hotkeys == ['fixture-hotkey'] and any(s.get('keys') == 'r' for s in recorded) and not hub.recording)}
    (FOLDER / 'smoke-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    if not result['passed']:
        raise SystemExit(1)
finally:
    if 'hub' in globals():
        hub.close()
    fixture.terminate()
    fixture.wait(timeout=5)
