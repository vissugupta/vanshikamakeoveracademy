# Vanshika Makeover Academy — Python Desktop App

The desktop application uses the existing Flask screens inside a native
`pywebview` window. It is packaged with PyInstaller, so a salon owner's Windows
computer does not need Python, Flask, Node, or Electron installed.

## How it works

1. `launcher.py` starts the existing Flask application on local port `5001`.
2. Flask uses a separate writable SQLite database for each installation.
3. `pywebview` displays the existing customer, staff, and admin screens in a
   native desktop window.
4. Closing the window stops the local Flask server.

The salon owner can continue to change the salon name, tagline, logo, colors,
contact details, and other settings from the existing admin screens.

## Development

From the project root:

```bash
python desktop/launcher.py
```

To check the Flask lifecycle without opening a graphical window:

```bash
python desktop/launcher.py --self-test
```

The Replit **Desktop App** workflow uses this self-test in environments where
the Linux VNC webview backend is unavailable. On Windows, run the launcher
normally to open the native window.

## Build the Windows executable

Build Windows executables on a native Windows runner:

```powershell
python -m pip install -r salon-app/requirements.txt -r desktop/requirements.txt
python -m PyInstaller --noconfirm --clean desktop/pywebview.spec
```

The executable is created at:

```text
desktop\dist\Vanshika Makeover Academy.exe
```

The GitHub Actions workflow at `.github/workflows/desktop-release.yml` performs
this build on `windows-latest` and uploads the artifact
`vanshika-makeover-windows`.

To build manually:

1. Open the repository's **Actions** tab.
2. Select **Desktop Release**.
3. Click **Run workflow**.
4. Choose the branch containing the latest desktop code.
5. Download `vanshika-makeover-windows` after the run succeeds.

## Local data and diagnostics

The packaged application keeps salon data outside the executable:

```text
%APPDATA%\Vanshika Makeover Academy\salon-data\
```

This contains the salon's SQLite database, uploaded logo, and feedback
uploads. It is not replaced when a new executable is installed.

Startup diagnostics are written to:

```text
%APPDATA%\Vanshika Makeover Academy\logs\desktop-startup.log
```

If the app cannot start, the packaged executable displays an error dialog with
the log path. The log helps identify missing WebView2, blocked executables,
database errors, port conflicts, and Flask startup failures.

## Windows requirements

The Windows build uses the Microsoft Edge WebView2 runtime through pywebview.
Current supported Windows versions normally include WebView2. If it is missing,
install the official Microsoft WebView2 Runtime once, then relaunch the app.

## Product architecture

- One shared Flask/Python codebase
- One local SQLite database per salon installation
- Runtime salon branding
- Feature toggles and owner-controlled permissions
- Optional custom branded executable for premium clients
- Versioned executable releases for fixes and new features

The Python launcher is intentionally thin. New salon features should be added
to the existing Flask modules and templates, not duplicated in desktop code.