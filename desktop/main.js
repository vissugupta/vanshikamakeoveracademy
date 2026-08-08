'use strict';

const { app, BrowserWindow, ipcMain, dialog, shell, Menu } = require('electron');
const path  = require('path');
const http  = require('http');
const { spawn } = require('child_process');

// ─── Configuration ────────────────────────────────────────────────────────────

/** Port Flask will listen on. Chosen to avoid conflict with the dev server. */
const FLASK_PORT = 5001;
const FLASK_URL  = `http://127.0.0.1:${FLASK_PORT}`;

/** Maximum seconds to wait for Flask to become ready before giving up. */
const FLASK_TIMEOUT_SECONDS = 60;

/**
 * Writable per-user data directory for the packaged app.
 * In development (not packaged) we defer to Flask's own defaults so that the
 * existing salon.db / uploads / logos are used unchanged.
 *
 * In a packaged build this is e.g.
 *   Windows : C:\Users\<user>\AppData\Roaming\Vanshika Makeover Academy\salon-data
 *   macOS   : ~/Library/Application Support/Vanshika Makeover Academy/salon-data
 *   Linux   : ~/.config/Vanshika Makeover Academy/salon-data
 */
function getSalonDataDir() {
  if (!app.isPackaged) return null; // dev mode: let Flask use its own defaults
  return path.join(app.getPath('userData'), 'salon-data');
}

/**
 * On first launch of the packaged app, copy the seed database from the
 * read-only resources into the writable data directory so Flask can open
 * and mutate it.  Subsequent launches skip the copy.
 */
function initUserDataDir(dataDir) {
  const fs = require('fs');

  // Create sub-directories Flask expects
  for (const sub of ['uploads/feedback', 'logos']) {
    fs.mkdirSync(path.join(dataDir, sub), { recursive: true });
  }

  const destDb = path.join(dataDir, 'salon.db');
  if (!fs.existsSync(destDb)) {
    // Copy the bundled (seed) database so the user starts with a clean slate
    const srcDb = path.join(getSalonAppDir(), 'salon.db');
    if (fs.existsSync(srcDb)) {
      fs.copyFileSync(srcDb, destDb);
      console.log(`[desktop] Seeded salon.db into ${dataDir}`);
    } else {
      console.log(`[desktop] No seed database found; Flask will create a fresh one`);
    }
  }
}

// ─── State ────────────────────────────────────────────────────────────────────

let mainWindow   = null;
let loadingWin   = null;
let flaskProcess = null;
let isQuitting   = false;

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * Resolve the path to the salon-app directory.
 * In development  → ../salon-app  (relative to desktop/)
 * In packaged app → <resourcesPath>/salon-app (extraResources copies it there)
 */
function getSalonAppDir() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'salon-app');
  }
  return path.join(__dirname, '..', 'salon-app');
}

/**
 * Find the Python executable. Prefers python3, falls back to python.
 * In a packaged app you would bundle a Python runtime; for now we rely on the
 * system Python that the owner already has installed.
 */
function findPython() {
  // On Windows the launcher is usually just "python"
  if (process.platform === 'win32') return 'python';
  return 'python3';
}

// ─── Flask lifecycle ──────────────────────────────────────────────────────────

function startFlask() {
  const salonDir  = getSalonAppDir();
  const python    = findPython();
  const dataDir   = getSalonDataDir();

  if (dataDir) {
    initUserDataDir(dataDir);
  }

  console.log(`[desktop] Starting Flask from ${salonDir} using ${python}`);
  if (dataDir) {
    console.log(`[desktop] User data dir: ${dataDir}`);
  }

  flaskProcess = spawn(python, ['wsgi.py'], {
    cwd: salonDir,
    env: {
      ...process.env,
      FLASK_PORT:     String(FLASK_PORT),
      FLASK_RUN_PORT: String(FLASK_PORT),
      // Tell Flask/Werkzeug to bind on the fixed port
      PORT: String(FLASK_PORT),
      // Switch Flask from HTTPS-proxy session config to plain HTTP cookies so
      // sessions persist correctly when served over the local loopback interface.
      FLASK_DESKTOP_MODE: '1',
      // Tell Flask where to store mutable data (db, uploads, logos).
      // Only set in packaged mode; dev mode keeps using Flask's own defaults.
      ...(dataDir ? { SALON_DATA_DIR: dataDir } : {}),
    },
    // Pipe stdout/stderr so we can log them
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  flaskProcess.stdout.on('data', (data) => {
    process.stdout.write(`[flask] ${data}`);
  });

  flaskProcess.stderr.on('data', (data) => {
    process.stderr.write(`[flask] ${data}`);
  });

  flaskProcess.on('exit', (code, signal) => {
    if (!isQuitting) {
      console.error(`[desktop] Flask exited unexpectedly (code=${code}, signal=${signal})`);
      dialog.showErrorBox(
        'Server crashed',
        `The Flask server stopped unexpectedly (exit code ${code}).\n` +
        'Please restart the application. If the problem persists, check that ' +
        'Python and all dependencies are installed correctly.',
      );
      app.quit();
    }
  });

  flaskProcess.on('error', (err) => {
    console.error('[desktop] Failed to spawn Flask:', err.message);
    dialog.showErrorBox(
      'Cannot start server',
      `Could not launch Python.\n\nError: ${err.message}\n\n` +
      'Please make sure Python 3 is installed and available on your PATH.',
    );
    app.quit();
  });
}

/**
 * Poll http://127.0.0.1:FLASK_PORT/ until it returns a 2xx or 3xx response,
 * then resolve. Rejects after FLASK_TIMEOUT_SECONDS.
 */
function waitForFlask() {
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + FLASK_TIMEOUT_SECONDS * 1000;

    function probe() {
      if (Date.now() > deadline) {
        return reject(new Error(`Flask did not start within ${FLASK_TIMEOUT_SECONDS} s`));
      }

      const req = http.get(FLASK_URL, (res) => {
        // Any HTTP response means the server is up
        if (res.statusCode >= 200 && res.statusCode < 500) {
          res.resume(); // drain
          resolve();
        } else {
          // Unexpected status — try again
          res.resume();
          setTimeout(probe, 500);
        }
      });

      req.on('error', () => {
        // Connection refused — Flask isn't ready yet
        setTimeout(probe, 500);
      });

      req.setTimeout(2000, () => {
        req.destroy();
        setTimeout(probe, 500);
      });
    }

    probe();
  });
}

function stopFlask() {
  if (flaskProcess && !flaskProcess.killed) {
    console.log('[desktop] Stopping Flask…');
    flaskProcess.kill('SIGTERM');

    // Give it 3 s then force-kill
    setTimeout(() => {
      if (flaskProcess && !flaskProcess.killed) {
        flaskProcess.kill('SIGKILL');
      }
    }, 3000);
  }
}

// ─── Window creation ──────────────────────────────────────────────────────────

function createLoadingWindow() {
  loadingWin = new BrowserWindow({
    width:           480,
    height:          340,
    frame:           false,
    transparent:     false,
    resizable:       false,
    center:          true,
    skipTaskbar:     true,
    backgroundColor: '#0a0a0a',
    webPreferences:  { nodeIntegration: false, contextIsolation: true },
  });

  loadingWin.loadFile(path.join(__dirname, 'loading.html'));
  loadingWin.on('closed', () => { loadingWin = null; });
}

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width:           1280,
    height:          820,
    minWidth:        900,
    minHeight:       600,
    show:            false,            // shown once Flask is ready
    backgroundColor: '#0a0a0a',
    title:           'Vanshika Makeover Academy',
    webPreferences: {
      preload:          path.join(__dirname, 'preload.js'),
      nodeIntegration:  false,
      contextIsolation: true,
      // Allow the embedded Flask app to use session storage etc.
      webSecurity:      true,
    },
  });

  // Build a minimal application menu
  const menuTemplate = [
    {
      label: 'Application',
      submenu: [
        { label: 'Reload', accelerator: 'CmdOrCtrl+R',
          click: () => mainWindow && mainWindow.webContents.reload() },
        { type: 'separator' },
        { label: 'Quit', accelerator: 'CmdOrCtrl+Q',
          click: () => app.quit() },
      ],
    },
    {
      label: 'View',
      submenu: [
        { label: 'Zoom In',  accelerator: 'CmdOrCtrl+Plus',
          click: () => { if (mainWindow) mainWindow.webContents.setZoomLevel(mainWindow.webContents.getZoomLevel() + 0.5); } },
        { label: 'Zoom Out', accelerator: 'CmdOrCtrl+-',
          click: () => { if (mainWindow) mainWindow.webContents.setZoomLevel(mainWindow.webContents.getZoomLevel() - 0.5); } },
        { label: 'Reset Zoom', accelerator: 'CmdOrCtrl+0',
          click: () => { if (mainWindow) mainWindow.webContents.setZoomLevel(0); } },
        { type: 'separator' },
        { label: 'Toggle Developer Tools', accelerator: 'CmdOrCtrl+Shift+I',
          click: () => mainWindow && mainWindow.webContents.toggleDevTools() },
      ],
    },
  ];

  Menu.setApplicationMenu(Menu.buildFromTemplate(menuTemplate));

  // Open external links in the system browser, not in the app
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (!url.startsWith(FLASK_URL)) {
      shell.openExternal(url);
      return { action: 'deny' };
    }
    return { action: 'allow' };
  });

  mainWindow.on('closed', () => { mainWindow = null; });
}

// ─── IPC handlers ─────────────────────────────────────────────────────────────

ipcMain.handle('get-flask-url', () => FLASK_URL);

// ─── App lifecycle ────────────────────────────────────────────────────────────

app.whenReady().then(async () => {
  // Show the loading screen immediately
  createLoadingWindow();

  // Start Flask in the background
  startFlask();

  // Pre-create the main window (hidden) while we wait
  createMainWindow();

  try {
    await waitForFlask();
    console.log('[desktop] Flask is ready — loading UI');

    // Load the Flask app into the main window
    await mainWindow.loadURL(FLASK_URL);

    // Fade out loading, show main
    mainWindow.show();
    if (loadingWin) {
      loadingWin.close();
    }
  } catch (err) {
    console.error('[desktop] Flask failed to start:', err.message);
    dialog.showErrorBox(
      'Startup timeout',
      `The application server did not start in time.\n\nDetails: ${err.message}\n\n` +
      'Please check that Python and all requirements are installed, then try again.',
    );
    app.quit();
  }
});

app.on('window-all-closed', () => {
  // On macOS it's conventional to keep the process running until Cmd+Q
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('activate', () => {
  // Re-open the window on macOS when clicking the dock icon
  if (mainWindow === null && !isQuitting) {
    createMainWindow();
    if (flaskProcess && !flaskProcess.killed) {
      mainWindow.loadURL(FLASK_URL).then(() => mainWindow.show());
    }
  }
});

app.on('before-quit', () => {
  isQuitting = true;
  stopFlask();
});
