# Vanshika Makeover Academy — Desktop App

Electron wrapper that turns the Flask salon ERP into a native installable
desktop application for Windows, macOS, and Linux.

## How it works

1. `main.js` spawns `salon-app/wsgi.py` as a child process on port 5001.
2. A loading screen (`loading.html`) is shown while Flask initialises.
3. Once Flask responds, a full-screen `BrowserWindow` loads the app UI.
4. Closing the window also shuts down the Flask child process cleanly.

## Development

```bash
# From the desktop/ directory
npm start          # launches Electron + Flask immediately
```

Or use the **Desktop App** workflow in Replit which does the same thing.

## Build distributable installers

Make sure you have the target platform's build tools, then:

```bash
npm run dist:win   # → dist/*.exe  (NSIS installer)
npm run dist:mac   # → dist/*.dmg  (macOS disk image)
npm run dist:linux # → dist/*.AppImage + *.deb
```

> **Python bundling note:** The installers bundle the salon-app Python source
> files, but they rely on the end-user having Python 3 installed on their
> machine. For a fully self-contained installer that ships its own Python
> runtime, add PyInstaller (or a similar tool) to build a standalone
> `wsgi` executable and reference that in `main.js` instead of spawning
> `python wsgi.py`.

## File layout

```
desktop/
├── main.js          ← Electron main process (spawns Flask, opens window)
├── preload.js       ← Context bridge for renderer ↔ main IPC
├── loading.html     ← Splash screen shown during startup
├── package.json     ← npm / electron-builder config
└── README.md
```
