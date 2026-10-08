"""Native accessible objects. Only the foreground application's visible objects
are considered; ambiguous matches stop rather than guessing a click location.
"""
import sys
import psutil
from concurrent.futures import ThreadPoolExecutor
from functools import wraps


def _init_windows_thread():
    import pythoncom
    pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)


# pywinauto stores a COM singleton. All UIA calls must stay on its owning thread.
_windows_thread = ThreadPoolExecutor(max_workers=1, initializer=_init_windows_thread) if sys.platform == 'win32' else None


def _native_thread(function):
    @wraps(function)
    def call(*args, **kwargs):
        if _windows_thread is not None:
            return _windows_thread.submit(function, *args, **kwargs).result(timeout=20)
        return function(*args, **kwargs)
    return call


def _win_context():
    from pywinauto import Desktop
    import ctypes
    window = Desktop(backend='uia').window(handle=ctypes.windll.user32.GetForegroundWindow()).wrapper_object()
    return window, psutil.Process(window.process_id()).name().lower()


def _foreground_windows_app():
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
    return psutil.Process(pid.value).name().lower() if pid.value else None


def _win_signature(obj, app):
    info = obj.element_info
    return {'platform': 'win32', 'app': app, 'name': info.name or '',
            'role': info.control_type, 'identifier': info.automation_id or ''}


def _ax_get(obj, attr):
    import ApplicationServices as ax
    error, value = ax.AXUIElementCopyAttributeValue(obj, attr, None)
    return value if error == 0 else None


def _mac_context():
    import ApplicationServices as ax
    from AppKit import NSWorkspace
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    return ax.AXUIElementCreateApplication(app.processIdentifier()), app.bundleIdentifier()


def _mac_signature(obj, app):
    return {'platform': 'darwin', 'app': app,
            'name': str(_ax_get(obj, 'AXTitle') or _ax_get(obj, 'AXDescription') or ''),
            'role': str(_ax_get(obj, 'AXRole') or ''),
            'identifier': str(_ax_get(obj, 'AXIdentifier') or '')}


@_native_thread
def capture_target(x, y):
    if sys.platform == 'win32':
        from pywinauto import Desktop
        obj = Desktop(backend='uia').from_point(int(x), int(y))
        app = psutil.Process(obj.process_id()).name().lower()
        for _ in range(5):
            signature = _win_signature(obj, app)
            if signature['name'] or signature['identifier']:
                return signature
            obj = obj.parent()
    elif sys.platform == 'darwin':
        import ApplicationServices as ax
        if not ax.AXIsProcessTrusted():
            raise RuntimeError('システム設定でこのアプリの「アクセシビリティ」を許可してください。')
        error, obj = ax.AXUIElementCopyElementAtPosition(ax.AXUIElementCreateSystemWide(), x, y, None)
        if error:
            raise RuntimeError('この場所のボタン情報を読み取れません。画像指定を使ってください。')
        error, pid = ax.AXUIElementGetPid(obj, None)
        if error:
            raise RuntimeError('対象アプリを読み取れません。')
        from AppKit import NSRunningApplication
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid).bundleIdentifier()
        for _ in range(5):
            signature = _mac_signature(obj, app)
            if signature['name'] or signature['identifier']:
                return signature
            obj = _ax_get(obj, 'AXParent')
            if obj is None:
                break
    else:
        raise RuntimeError('ボタン認識はWindowsとMacに対応しています。')
    raise RuntimeError('ボタンを識別できません。画像指定を使ってください。')


def visible_objects():
    """Returns (signature, native object) for the foreground application."""
    if sys.platform == 'win32':
        root, app = _win_context()
        nodes = [root] + root.descendants()
        if len(nodes) > 6000:
            raise RuntimeError('画面の要素が多すぎます。画像指定を使ってください。')
        return [(_win_signature(obj, app), obj) for obj in nodes if obj.is_visible()]
    if sys.platform == 'darwin':
        root, app = _mac_context()
        stack, result, visited = [root], [], 0
        while stack:
            obj = stack.pop()
            visited += 1
            if visited > 6000:
                raise RuntimeError('画面の要素が多すぎます。画像指定を使ってください。')
            if _ax_get(obj, 'AXPosition') is not None:
                result.append((_mac_signature(obj, app), obj))
            stack.extend(_ax_get(obj, 'AXChildren') or [])
        return result
    raise RuntimeError('このOSのボタン認識には対応していません。')


def matching(signature, target):
    if signature['platform'] != target.get('platform') or signature['app'] != target.get('app'):
        return False
    if signature['role'] != target.get('role'):
        return False
    identifier = target.get('identifier')
    return signature['identifier'] == identifier if identifier else bool(target.get('name')) and signature['name'] == target['name']


def _find_object(target):
    if target.get('platform') != sys.platform:
        raise RuntimeError('別のOSで登録したボタンです。このPCで選び直してください。')
    if sys.platform == 'win32' and _foreground_windows_app() != target.get('app'):
        return None
    found = [obj for signature, obj in visible_objects() if matching(signature, target)]
    if len(found) > 1:
        raise RuntimeError('同じ名前のボタンが複数あります。画像指定で区別してください。')
    return found[0] if found else None


@_native_thread
def find_target(target):
    obj = _find_object(target)
    return center(obj) if obj is not None else None


@_native_thread
def check_state(target):
    obj = _find_object(target)
    return checked(obj) if obj is not None else None


@_native_thread
def checkbox_snapshot():
    result = []
    for signature, obj in visible_objects():
        if signature['role'] in ('CheckBox', 'AXCheckBox'):
            if not signature['name'] and not signature['identifier']:
                raise RuntimeError('名前のないチェック項目があります。個別に登録してください。')
            result.append({'kind': 'check', 'target': signature, 'checked': checked(obj), 'seconds': 10})
    if not result:
        raise RuntimeError('チェック項目が見つかりません。対象の画面を手前に出してください。')
    return result


def center(obj):
    if sys.platform == 'win32':
        rect = obj.rectangle()
        if rect.width() <= 0 or rect.height() <= 0 or not obj.is_enabled():
            raise RuntimeError('このボタンは現在操作できません。')
        return (rect.left + rect.width() / 2, rect.top + rect.height() / 2)
    import ApplicationServices as ax
    position, size = _ax_get(obj, 'AXPosition'), _ax_get(obj, 'AXSize')
    if position is None or size is None:
        raise RuntimeError('ボタンの場所を取得できません。')
    ok1, point = ax.AXValueGetValue(position, ax.kAXValueCGPointType, None)
    ok2, extent = ax.AXValueGetValue(size, ax.kAXValueCGSizeType, None)
    if not ok1 or not ok2 or extent.width <= 0 or extent.height <= 0:
        raise RuntimeError('ボタンの場所を取得できません。')
    return point.x + extent.width / 2, point.y + extent.height / 2


def checked(obj):
    if sys.platform == 'win32':
        state = obj.get_toggle_state()
        if state not in (0, 1):
            raise RuntimeError('チェックの状態を確認できません。')
        return bool(state)
    value = _ax_get(obj, 'AXValue')
    if value not in (0, 1, False, True):
        raise RuntimeError('チェックの状態を確認できません。')
    return bool(value)
