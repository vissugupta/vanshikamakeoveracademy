---
name: Windows desktop startup diagnostics
description: Packaged Windows startup failures need user-visible errors and a persistent Electron log because no terminal is available.
---

Packaged desktop builds must keep startup diagnostics in a writable per-user
location and show the failure reason before exiting; otherwise a missing or
blocked bundled server appears to the customer as an app that does nothing.

**Why:** Windows GUI launches do not expose Electron or PyInstaller stdout, and
the main window is intentionally hidden while the local Flask server starts.

**How to apply:** Preserve the startup log and visible error path whenever
changing Electron launch, PyInstaller packaging, or the packaged data directory.