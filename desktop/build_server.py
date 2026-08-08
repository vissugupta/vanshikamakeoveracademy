"""Build the Flask server into a standalone executable with PyInstaller.

Run this on the target build platform before running an electron-builder
installer build:

    python -m pip install -r ../salon-app/requirements.txt pyinstaller
    python build_server.py

The output is written to desktop/build-resources/ and is intentionally
gitignored because it is platform-specific build output.
"""

from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "build-resources"
WORK = OUTPUT / "pyinstaller-work"
SPEC = ROOT / "server.spec"


def main() -> int:
    pyinstaller = shutil.which("pyinstaller")
    if not pyinstaller:
        print(
            "PyInstaller is required. Install it with:\n"
            "  python -m pip install pyinstaller",
            file=sys.stderr,
        )
        return 1

    OUTPUT.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            pyinstaller,
            "--noconfirm",
            "--clean",
            "--distpath",
            str(OUTPUT),
            "--workpath",
            str(WORK),
            str(SPEC),
        ],
        cwd=ROOT,
        check=True,
    )

    executable = OUTPUT / ("salon-server.exe" if sys.platform == "win32" else "salon-server")
    if not executable.exists():
        print(f"PyInstaller did not create the expected executable: {executable}", file=sys.stderr)
        return 1

    print(f"Built self-contained Flask server: {executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())