"""Copy notices and prepare distribution/source ZIPs without user data."""
import importlib.metadata
from pathlib import Path
import shutil
import zipfile
import hashlib
import json

ROOT = Path(__file__).resolve().parent
DIST = ROOT / 'dist'
APP = DIST / 'EasyActionHotkeys'


def fix_windows_runtime(application):
    # Qt 6.11 uses the Windows system ICU API (unversioned exports). A host PATH
    # entry for Poppler can cause PyInstaller to collect ICU with versioned
    # exports instead. Windows supplies its ICU API; don't bundle that other ICU.
    internal = application / '_internal'
    for name in ('icuuc.dll', 'icudt78.dll'):
        path = internal / name
        if path.exists():
            path.unlink()


def copy_notices(destination):
    destination.mkdir(parents=True, exist_ok=True)
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get('Name', 'package')
        for relative in distribution.files or []:
            lower = str(relative).lower()
            if any(part in lower for part in ('license', 'copying', 'notice')):
                original = distribution.locate_file(relative)
                if original.is_file():
                    target = destination / name / str(relative).replace('..', '_parent_')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(original, target)


def prepare():
    if APP.exists() and __import__('sys').platform == 'win32':
        fix_windows_runtime(APP)
        copy_notices(APP / 'licenses')
        for name in ('README.md', 'THIRD_PARTY_NOTICES.md'):
            shutil.copy2(ROOT / name, APP / name)
        shutil.copytree(ROOT / 'docs', APP / 'docs', dirs_exist_ok=True)
        with zipfile.ZipFile(DIST / 'EasyActionHotkeys-Windows.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
            for path in APP.rglob('*'):
                if path.is_file():
                    archive.write(path, path.relative_to(DIST))

    source_files = ['main.py', 'requirements.txt', '.gitignore', 'README.md',
                    'THIRD_PARTY_NOTICES.md', 'build_windows.ps1', 'build_macos.sh',
                    'prepare_release.py']
    with zipfile.ZipFile(DIST / 'easy-action-hotkeys-source.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in source_files:
            archive.write(ROOT / name, name)
        for folder in ('easy_action', 'tests', 'docs', '.github'):
            for path in (ROOT / folder).rglob('*'):
                if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
                    archive.write(path, path.relative_to(ROOT))
        for name in ('render_ui.py', 'windows_smoke.py'):
            path = ROOT / 'verification' / name
            archive.write(path, path.relative_to(ROOT))

    checksums = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in DIST.glob('*.zip')}
    (DIST / 'SHA256SUMS.json').write_text(json.dumps(checksums, indent=2), encoding='utf-8')
    print(json.dumps({path.name: round(path.stat().st_size / 1024 / 1024, 1) for path in DIST.glob('*.zip')}, indent=2))


if __name__ == '__main__':
    prepare()
