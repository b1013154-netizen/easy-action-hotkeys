#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt pyinstaller
.venv/bin/python -m PyInstaller --noconfirm --windowed --onedir --name EasyActionHotkeys \
  --collect-submodules pynput --hidden-import ApplicationServices --hidden-import AppKit \
  --osx-bundle-identifier local.easy-action-hotkeys main.py
/usr/libexec/PlistBuddy -c 'Add :NSInputMonitoringUsageDescription string Keyboard shortcuts and recording user actions' dist/EasyActionHotkeys.app/Contents/Info.plist
/usr/libexec/PlistBuddy -c 'Add :NSScreenCaptureUsageDescription string Screenshots and finding selected images' dist/EasyActionHotkeys.app/Contents/Info.plist
.venv/bin/python -c 'from prepare_release import copy_notices; from pathlib import Path; copy_notices(Path("dist/EasyActionHotkeys.app/Contents/Resources/licenses"))'
cp README.md THIRD_PARTY_NOTICES.md dist/EasyActionHotkeys.app/Contents/Resources/
ditto -c -k --keepParent dist/EasyActionHotkeys.app dist/EasyActionHotkeys-macOS.zip
.venv/bin/python prepare_release.py
