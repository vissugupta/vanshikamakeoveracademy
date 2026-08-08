"""Windows desktop launcher for the salon application.

The UI remains the existing Flask application. This process owns the local
Flask server and displays it in a native pywebview window, so the customer
does not need Python, Flask, Node, or Electron installed.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import time
from pathlib import Path
from urllib.request import urlopen


APP_NAME = "Vanshika Makeover Academy"
APP_VERSION = "1.1.0"
FLASK_PORT = 5001


def _user_data_root() -> Path:
    """Return a writable per-user directory on every supported desktop OS."""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP_NAME


USER_DATA_ROOT = _user_data_root()
SALON_DATA_DIR = USER_DATA_ROOT / "salon-data"
LOG_DIR = USER_DATA_ROOT / "logs"
LOG_FILE = LOG_DIR / "desktop-startup.log"


def _configure_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("salon-desktop")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


logger = _configure_logging()


def _show_error(title: str, message: str) -> None:
    """Show a useful error even when the executable was built without a console."""
    full_message = f"{message}\n\nDiagnostic log:\n{LOG_FILE}"
    logger.error("%s: %s", title, message)
    if os.name == "nt":
        try:
            ctypes.windll.user32.MessageBoxW(None, full_message, title, 0x10)
            return
        except Exception:
            pass
    print(f"{title}: {full_message}", file=sys.stderr)


def _wait_for_flask(url: str, timeout_seconds: float = 60) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = "connection refused"
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=2) as response:
                if 200 <= response.status < 500:
                    return
                last_error = f"HTTP {response.status}"
        except Exception as error:
            last_error = str(error)
        time.sleep(0.25)
    raise RuntimeError(f"Flask did not start within {timeout_seconds:.0f} seconds ({last_error})")


def _start_flask():
    """Import the existing app and serve it without Flask's development runner."""
    # In a PyInstaller build app.py is extracted beside this launcher. In
    # development it lives in the sibling salon-app directory.
    root = Path(__file__).resolve().parent
    candidates = [root / "salon-app", root.parent / "salon-app", root]
    salon_app_dir = next((candidate for candidate in candidates if candidate.exists()), root)
    sys.path.insert(0, str(salon_app_dir))

    # These must be set before importing app.py because it calculates its
    # database, logo, and upload paths during module import.
    SALON_DATA_DIR.mkdir(parents=True, exist_ok=True)
    os.environ["SALON_DATA_DIR"] = str(SALON_DATA_DIR)
    os.environ["FLASK_DESKTOP_MODE"] = "1"
    os.environ["PORT"] = str(FLASK_PORT)
    os.environ["FLASK_PORT"] = str(FLASK_PORT)

    from werkzeug.serving import make_server
    from app import app as flask_app

    server = make_server("127.0.0.1", FLASK_PORT, flask_app, threaded=True)
    thread = threading.Thread(
        target=server.serve_forever,
        name="salon-flask-server",
        daemon=True,
    )
    thread.start()
    logger.info("Flask server started on http://127.0.0.1:%s", FLASK_PORT)
    return server, thread


def _run_self_test() -> int:
    server, thread = _start_flask()
    try:
        _wait_for_flask(f"http://127.0.0.1:{FLASK_PORT}/")
        logger.info("Self-test passed")
        print("pywebview desktop self-test passed")
        return 0
    finally:
        server.shutdown()
        thread.join(timeout=5)


def main() -> int:
    logger.info("Launching %s version %s", APP_NAME, APP_VERSION)
    logger.info("Writable salon data directory: %s", SALON_DATA_DIR)

    if "--self-test" in sys.argv:
        try:
            return _run_self_test()
        except Exception as error:
            logger.exception("Self-test failed")
            _show_error("Desktop self-test failed", str(error))
            return 1

    server = None
    server_thread = None
    try:
        server, server_thread = _start_flask()
        flask_url = f"http://127.0.0.1:{FLASK_PORT}"
        _wait_for_flask(flask_url)

        import webview

        webview_data_dir = USER_DATA_ROOT / "webview"
        webview_data_dir.mkdir(parents=True, exist_ok=True)
        webview.create_window(
            APP_NAME,
            flask_url,
            width=1280,
            height=820,
            min_size=(900, 600),
            background_color="#0a0a0a",
            confirm_close=True,
        )
        logger.info("Opening pywebview window")
        webview.start(
            debug=False,
            private_mode=False,
            storage_path=str(webview_data_dir),
        )
        logger.info("pywebview window closed")
        return 0
    except Exception as error:
        logger.exception("Desktop startup failed")
        _show_error("Could not start Vanshika Makeover Academy", str(error))
        return 1
    finally:
        if server is not None:
            logger.info("Stopping Flask server")
            server.shutdown()
        if server_thread is not None:
            server_thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())