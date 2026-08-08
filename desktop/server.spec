# PyInstaller spec for the self-contained Flask server.
#
# Build with:
#   python build_server.py
#
# The application data is embedded in the executable so the end user's
# computer does not need Python, Flask, or the project source files.
from pathlib import Path

from PyInstaller.building.build_main import Analysis, EXE, PYZ


ROOT = Path(SPECPATH).resolve()
SALON_APP = ROOT.parent / "salon-app"

datas = [
    (str(SALON_APP / "templates"), "templates"),
    (str(SALON_APP / "static"), "static"),
]

a = Analysis(
    [str(SALON_APP / "wsgi.py")],
    pathex=[str(SALON_APP)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "notifications",
        "apscheduler",
        "twilio",
        "requests",
    ],
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
    name="salon-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)