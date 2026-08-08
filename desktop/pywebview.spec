# PyInstaller spec for the native Python + pywebview desktop application.
from pathlib import Path

from PyInstaller.building.build_main import Analysis, EXE, PYZ
from PyInstaller.utils.hooks import collect_all


ROOT = Path(SPECPATH).resolve()
SALON_APP = ROOT.parent / "salon-app"

webview_datas, webview_binaries, webview_hiddenimports = collect_all("webview")

datas = [
    (str(SALON_APP / "templates"), "templates"),
    (str(SALON_APP / "static"), "static"),
    *webview_datas,
]

hiddenimports = [
    "app",
    "notifications",
    "apscheduler",
    "twilio",
    "requests",
    *webview_hiddenimports,
]

a = Analysis(
    [str(ROOT / "launcher.py")],
    pathex=[str(ROOT), str(SALON_APP)],
    binaries=webview_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Vanshika Makeover Academy",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)