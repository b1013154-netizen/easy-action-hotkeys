"""Render local GUI screenshots without operating other applications."""
import os
# On Windows the native platform supplies the installed Japanese fonts.
if os.name != 'nt':
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['PYNPUT_BACKEND'] = 'dummy'
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from easy_action.ui import MainWindow, StepDialog, STYLE
from easy_action.model import Store, new_macro

app = QApplication([])
app.setStyleSheet(STYLE)
folder = Path(__file__).parent
window = MainWindow(Store(folder / 'render-data' / 'actions.json'), start_listeners=False)
macro = new_macro()
macro.update(name='お知らせを入力する', hotkey='ctrl+alt+f10', steps=[
    {'kind': 'click', 'x': 450, 'y': 320},
    {'kind': 'text', 'text': 'お世話になっております。\n確認をお願いします。'},
    {'kind': 'keys', 'keys': 'ctrl+s'},
    {'kind': 'screenshot', 'folder': 'Pictures/EasyActionHotkeys'},
])
window.macros = [macro]
window.refresh_macros()
window.load_draft(macro)
window.show()
app.processEvents()
window.grab().save(str(folder / 'main-window.png'))
dialog = StepDialog(window, {'kind': 'text', 'text': 'お世話になっております。\n確認をお願いします。'})
dialog.show()
app.processEvents()
dialog.grab().save(str(folder / 'text-dialog.png'))
dialog.close()
window.close()
