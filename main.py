import sys

if sys.platform == 'win32':
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass

from easy_action.ui import main

if __name__ == '__main__':
    if '--diagnose' in sys.argv:
        from easy_action.diagnostics import diagnose
        diagnose()
    else:
        main()
