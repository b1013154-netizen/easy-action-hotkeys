"""Distribution startup/import check. Does not send keys or capture the desktop."""
import json
import os
from pathlib import Path
import sys
from PySide6.QtWidgets import QApplication
from .ui import MainWindow, StepDialog, STYLE
from .model import Store, KINDS, data_folder


def diagnose():
    folder = Path(os.environ.get('EASY_ACTION_DATA', str(data_folder())))
    folder.mkdir(parents=True, exist_ok=True)
    result = {'passed': False, 'platform': sys.platform}
    window = None
    try:
        import cv2
        import numpy
        from PIL import Image
        app = QApplication([])
        app.setStyleSheet(STYLE)
        window = MainWindow(Store(folder / 'diagnostic-actions.json'), start_listeners=False)
        window.show()
        app.processEvents()
        window.grab().save(str(folder / 'diagnostic-window.png'))
        for index in range(len(KINDS)):
            dialog = StepDialog(window)
            dialog.kind.setCurrentIndex(index)
            dialog.close()
        if sys.platform == 'win32':
            from .accessibility import _windows_thread
            def load_uia():
                from pywinauto import Desktop
                return Desktop(backend='uia') is not None
            result['uia_import'] = _windows_thread.submit(load_uia).result(timeout=20)
        elif sys.platform == 'darwin':
            import ApplicationServices
            import AppKit
            result['accessibility_import'] = True
        result.update(passed=True, action_types=len(KINDS), opencv=cv2.__version__)
    except Exception as exc:
        import traceback
        result['error'] = str(exc)
        result['traceback'] = traceback.format_exc()
    finally:
        if window is not None:
            window.close()
        (folder / 'diagnostic-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    raise SystemExit(0 if result['passed'] else 1)
