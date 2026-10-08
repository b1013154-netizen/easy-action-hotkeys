$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11以上をインストールしてください。' }
}
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt pyinstaller
if ($LASTEXITCODE -ne 0) { throw '依存ライブラリのインストールに失敗しました。' }
& '.\.venv\Scripts\python.exe' -m PyInstaller --noconfirm --windowed --onedir --name EasyActionHotkeys --collect-submodules pynput --collect-submodules pywinauto --hidden-import comtypes.stream main.py
if ($LASTEXITCODE -ne 0) { throw 'ビルドに失敗しました。' }
& '.\.venv\Scripts\python.exe' prepare_release.py
if ($LASTEXITCODE -ne 0) { throw '配布ファイルの作成に失敗しました。' }
