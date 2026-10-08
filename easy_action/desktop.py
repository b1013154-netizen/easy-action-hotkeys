import base64
import io
import threading
import time
from pathlib import Path
from datetime import datetime
from PIL import Image
from PySide6.QtCore import QObject, Signal, QBuffer, QIODevice
from PySide6.QtGui import QGuiApplication


class ScreenService(QObject):
    requested = Signal(object)

    def __init__(self):
        super().__init__()
        self.requested.connect(self._capture)

    def _capture(self, job):
        try:
            result = []
            for screen in QGuiApplication.screens():
                pixmap = screen.grabWindow(0)
                if pixmap.isNull():
                    raise RuntimeError('画面を読み取れません。Macでは「画面収録」の権限を確認してください。')
                buffer = QBuffer()
                buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                pixmap.save(buffer, 'PNG')
                image = Image.open(io.BytesIO(bytes(buffer.data()))).convert('RGB')
                rect = screen.geometry()
                result.append((image, (rect.x(), rect.y(), rect.width(), rect.height())))
            job['result'] = result
        except Exception as exc:
            job['error'] = exc
        finally:
            job['event'].set()

    def capture(self, cancel=None):
        job = {'event': threading.Event()}
        self.requested.emit(job)
        deadline = time.monotonic() + 15
        while not job['event'].wait(.05):
            if cancel and cancel.is_set():
                raise InterruptedError()
            if time.monotonic() >= deadline:
                raise RuntimeError('画面の読み取りがタイムアウトしました。')
        if 'error' in job:
            raise job['error']
        return job['result']


def image_matches(screens, encoded, threshold):
    import cv2
    import numpy as np
    try:
        with Image.open(io.BytesIO(base64.b64decode(encoded, validate=True))) as raw:
            if raw.width * raw.height > 10_000_000:
                raise ValueError('対象画像が大きすぎます。')
            template = np.asarray(raw.convert('RGB'))
    except Exception as exc:
        raise ValueError('登録した画像が壊れています。画像を選び直してください。') from exc
    height, width = template.shape[:2]
    if width < 8 or height < 8 or np.std(template) < 4:
        raise ValueError('単色の画像では識別できません。文字や輪郭を含めて選んでください。')
    found = []
    for image, (ox, oy, logical_w, logical_h) in screens:
        pixels = np.asarray(image)
        if pixels.shape[0] < height or pixels.shape[1] < width:
            continue
        scores = cv2.matchTemplate(pixels, template, cv2.TM_CCOEFF_NORMED)
        for _ in range(2):
            _, score, _, (x, y) = cv2.minMaxLoc(scores)
            if score < threshold:
                break
            found.append((ox + (x + width / 2) * logical_w / image.width,
                          oy + (y + height / 2) * logical_h / image.height))
            scores[max(0, y-height//2):y+height//2+1, max(0, x-width//2):x+width//2+1] = -1
    if len(found) > 1:
        raise RuntimeError('同じ画像が複数見つかりました。周囲の文字も含めて画像を選び直してください。')
    return found[0] if found else None


def save_screenshot(screens, folder):
    folder = Path(folder).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    paths = []
    for index, (image, _) in enumerate(screens, 1):
        path = folder / f'スクリーンショット-{stamp}-画面{index}.png'
        image.save(path)
        paths.append(str(path))
    return paths
