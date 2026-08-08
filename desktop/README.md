# Vanshika Makeover Academy — Desktop App

Electron wrapper that turns the Flask salon ERP into a native installable
desktop application for Windows, macOS, and Linux.

## How it works

1. `main.js` spawns a self-contained PyInstaller server executable on port 5001.
2. A loading screen (`loading.html`) is shown while Flask initialises.
3. Once Flask responds, a full-screen `BrowserWindow` loads the app UI.
4. Closing the window also shuts down the Flask child process cleanly.

## Development

```bash
# From the desktop/ directory
npm start          # launches Electron + Python fallback in development
```

Or use the **Desktop App** workflow in Replit which does the same thing.

## Build distributable installers

The installer does not require Python on the salon owner's computer. Build the
server executable first on the platform you are packaging for, then build the
Electron installer:

```bash
# From the desktop/ directory
python -m pip install -r ../salon-app/requirements.txt pyinstaller
npm run build:server
```

This creates `build-resources/salon-server` (or `salon-server.exe` on Windows).
The executable contains Flask, the templates, static assets, and notification
dependencies. It is platform-specific, so build Windows installers on Windows
(or with a compatible cross-build tool), and macOS installers on macOS.

```bash
npm run dist:win   # → dist/*.exe  (NSIS installer)
npm run dist:mac   # → dist/*.dmg  (native architecture of the build Mac)
npm run dist:linux # → dist/*.AppImage + *.deb
```

The `dist:*` commands automatically run `npm run build:server` first, so the
two commands can also be combined after PyInstaller is installed. Python is
needed only on the build machine, never on the customer's computer.

### macOS architecture note

PyInstaller creates a native server binary. `npm run dist:mac` therefore
detects the build Mac's architecture and produces one matching DMG:

- Apple Silicon Mac → arm64 DMG with an arm64 server
- Intel Mac → x64 DMG with an x64 server

Do not use one Mac build to advertise both architectures. Run the command on
each architecture when both DMGs are needed.

## File layout

```
desktop/
├── main.js          ← Electron main process (spawns bundled Flask, opens window)
├── build_server.py  ← PyInstaller build command
├── server.spec      ← PyInstaller data/dependency specification
├── preload.js       ← Context bridge for renderer ↔ main IPC
├── loading.html     ← Splash screen shown during startup
├── package.json     ← npm / electron-builder config
└── README.md
```
