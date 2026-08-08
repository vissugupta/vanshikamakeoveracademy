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

## Building Windows installers with GitHub Actions

The workflow at `.github/workflows/desktop-release.yml` builds the Windows
installer on a native Windows runner, so you do not need a Windows computer or
Wine in the Replit workspace.

### Manual build (recommended)

1. Push this project to the GitHub repository connected to the desktop release
   workflow.
2. Open the repository's **Actions** tab.
3. Select **Desktop Release**.
4. Click **Run workflow**, choose the branch, and click **Run workflow** again.
5. Open the completed workflow run.
6. Download the artifact named **`vanshika-makeover-windows`**.
7. Extract the artifact and run the `.exe` installer on Windows.

The Windows job builds a native `salon-server.exe`, places it inside the
installer, and uploads the NSIS `.exe` as an artifact. Python is not required
on the Windows customer's computer.

### If the installed Windows app does not open

Install the newest Windows installer and launch it again. Version `1.0.1` and
later write startup diagnostics to:

```text
%APPDATA%\Vanshika Makeover Academy\logs\desktop-startup.log
```

If the bundled server cannot start, the app now keeps its loading window
visible and shows an error dialog with the reason and log path. Send that log
when reporting a startup problem; it identifies missing files, blocked
executables, port conflicts, and Flask/database startup errors.

The workflow also runs automatically when a version tag such as `v1.0.0` is
pushed and uploads the Linux packages as a separate artifact.

### Publishing a GitHub Release (optional)

Publishing installers to the Releases page is optional. If you want the
workflow to upload a release instead of only creating downloadable artifacts,
configure the repository's `GH_TOKEN` secret with Contents read/write access
and use the existing `npm run release` process.

### Step 1 — Create a GitHub personal access token

You only need to do this once.

**Option A — Fine-grained token (recommended)**

1. Go to **GitHub → Settings → Developer settings → Personal access tokens →
   Fine-grained tokens**.
2. Click **Generate new token**.
3. Set a descriptive name, e.g. `vanshika-desktop-releases`.
4. Under **Repository access**, choose **Only select repositories** and pick
   this repository.
5. Under **Permissions → Repository permissions**, set **Contents** to
   **Read and write**. No other permissions are needed.
6. Click **Generate token** and copy the value immediately (it is shown only once).

**Option B — Classic token**

1. Go to **GitHub → Settings → Developer settings → Personal access tokens →
   Tokens (classic)**.
2. Click **Generate new token (classic)**.
3. Give it a name and tick only the **`repo`** scope (this includes contents
   write access).
4. Click **Generate token** and copy the value.

### Step 2 — Add the token as a repository secret

1. Open the repository on GitHub and go to
   **Settings → Secrets and variables → Actions**.
2. Click **New repository secret**.
3. Set the name to exactly **`GH_TOKEN`** (case-sensitive).
4. Paste the token value and click **Add secret**.

The workflow reads this secret as `${{ secrets.GH_TOKEN }}` — the name must
match exactly.

### Step 3 — Tag a release to trigger the workflow

The workflow runs whenever a tag that matches `v*.*.*` is pushed:

```bash
# In the repository root, tag the commit you want to release
git tag v1.0.0
git push origin v1.0.0
```

Use [semantic versioning](https://semver.org/): `v<major>.<minor>.<patch>`.
Examples: `v1.0.0` for the first release, `v1.0.1` for a small bug-fix,
`v1.1.0` for a new feature.

### Step 4 — Verify a successful release

1. Go to the repository on GitHub and click the **Actions** tab.
2. Find the run called **Desktop Release** that was triggered by your tag.
3. Both the `Build Windows installer` and `Build Linux packages` jobs should
   show a green ✓. If either shows a red ✗, click the job name to read the
   build log and find the error.
4. Once both jobs pass, open the **Releases** page of the repository
   (right-hand sidebar or `https://github.com/<owner>/<repo>/releases`).
5. A new release named after your tag (e.g. `v1.0.0`) should appear there,
   with the Windows `.exe` installer and Linux `.AppImage` / `.deb` files
   attached as downloadable assets.

> **Tip:** If the release appears but assets are missing, or if the workflow
> fails with a `403` or "resource not accessible by integration" error, double-
> check that:
> - The secret is named exactly `GH_TOKEN`.
> - The token has **Contents: Read and write** (fine-grained) or the **`repo`**
>   scope (classic).
> - The token has not expired.

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
