'use strict';

const { app, BrowserWindow, ipcMain, dialog, shell, Menu, Notification } = require('electron');
const path  = require('path');
const http  = require('http');
const { spawn } = require('child_process');

// ─── Auto-updater ─────────────────────────────────────────────────────────────

/**
 * How often (ms) to re-check for updates after the initial launch check.
 * Default: every 4 hours.
 */
const UPDATE_CHECK_INTERVAL_MS = 4 * 60 * 60 * 1000;

/**
 * Set up electron-updater for silent background updates.
 * Downloads happen automatically; the user is notified only when a restart
 * is required to apply the installed update.
 *
 * This function is a no-op in development (app.isPackaged === false) so that
 * the dev workflow is unaffected.
 */
function setupAutoUpdater() {
  if (!app.isPackaged) {
    console.log('[updater] Skipping auto-update setup in development mode.');
    return;
  }

  let { autoUpdater } = require('electron-updater');

  // Silent downloads — never interrupt the user
  autoUpdater.autoDownload    = true;
  autoUpdater.autoInstallOnAppQuit = true;

  autoUpdater.logger = {
    info:  (msg) => console.log(`[updater] ${msg}`),
    warn:  (msg) => console.warn(`[updater] ${msg}`),
    error: (msg) => console.error(`[updater] ${msg}`),
    debug: (msg) => console.log(`[updater] ${msg}`),
  };

  autoUpdater.on('checking-for-update', () => {
    console.log('[updater] Checking for update…');
  });

  autoUpdater.on('update-available', (info) => {
    console.log(`[updater] Update available: ${info.version} — downloading in background…`);
  });

  autoUpdater.on('update-not-available', () => {
    console.log('[updater] App is up to date.');
  });

  autoUpdater.on('download-progress', (progress) => {
    console.log(
      `[updater] Download progress: ${Math.round(progress.percent)}% ` +
      `(${Math.round(progress.bytesPerSecond / 1024)} KB/s)`,
    );
  });

  autoUpdater.on('update-downloaded', (info) => {
    console.log(`[updater] Update ${info.version} downloaded — will apply on next restart.`);
    showUpdateReadyNotification(info.version);
  });

  autoUpdater.on('error', (err) => {
    console.error(`[updater] Error: ${err.message}`);
  });

  // Initial check on launch (slight delay so the app finishes loading first)
  setTimeout(() => {
    autoUpdater.checkForUpdates().catch((err) => {
      console.error(`[updater] checkForUpdates error: ${err.message}`);
    });
  }, 10_000);

  // Periodic re-check
  setInterval(() => {
    autoUpdater.checkForUpdates().catch((err) => {
      console.error(`[updater] Periodic checkForUpdates error: ${err.message}`);
    });
  }, UPDATE_CHECK_INTERVAL_MS);
}

/**
 * Show a subtle OS notification telling the user a restart will apply the
 * downloaded update. Clicking the notification triggers a restart.
 */
function showUpdateReadyNotification(version) {
  if (!Notification.isSupported()) return;

  const notif = new Notification({
    title: 'Update ready',
    body:  `Version ${version} has been downloaded. Restart the app to apply it.`,
    silent: true,
  });

  notif.on('click', () => {
    const { autoUpdater } = require('electron-updater');
    autoUpdater.quitAndInstall(false, true);
  });

  notif.show();

  // Also offer a dialog if the main window is open
  if (mainWindow) {
    mainWindow.webContents.executeJavaScript(`
      (function () {
        const banner = document.createElement('div');
        banner.id = 'updater-banner';
        banner.style.cssText = [
          'position:fixed','bottom:0','left:0','right:0','z-index:99999',
          'background:#1e293b','color:#f8fafc','font-size:13px',
          'padding:10px 16px','display:flex','align-items:center','gap:12px',
          'border-top:1px solid #334155',
        ].join(';');
        banner.innerHTML =
          '<span>✦ Update ready — <strong>restart to apply v${version}</strong></span>' +
          '<button onclick="window.__restartForUpdate()" style="' +
            'margin-left:auto;padding:4px 14px;border-radius:6px;' +
            'background:#3b82f6;color:#fff;border:none;cursor:pointer;font-size:12px' +
          '">Restart now</button>' +
          '<button onclick="document.getElementById(\\\'updater-banner\\\').remove()" style="' +
            'padding:4px 10px;border-radius:6px;background:transparent;' +
            'color:#94a3b8;border:1px solid #475569;cursor:pointer;font-size:12px' +
          '">Later</button>';
        document.body.appendChild(banner);
      })();
    `).catch(() => {});
  }
}

// IPC handler so the renderer can trigger a restart-to-update
ipcMain.handle('restart-for-update', () => {
  if (!app.isPackaged) return;
  const { autoUpdater } = require('electron-updater');
  autoUpdater.quitAndInstall(false, true);
});

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
 * On first launch of the packaged app, create the writable directories Flask
 * expects. Flask creates salon.db itself on first start. We intentionally do
 * not copy a database from the read-only installer: each sold installation
 * must start with its own fresh local database.
 */
function initUserDataDir(dataDir) {
  const fs = require('fs');

  // Create sub-directories Flask expects
  for (const sub of ['uploads/feedback', 'logos']) {
    fs.mkdirSync(path.join(dataDir, sub), { recursive: true });
  }

  console.log(`[desktop] Writable salon data directory ready: ${dataDir}`);
}

// ─── State ────────────────────────────────────────────────────────────────────

let mainWindow   = null;
let loadingWin   = null;
let flaskProcess = null;
let isQuitting   = false;

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * Resolve the path to the salon-app directory for development-only fallback.
 */
function getSalonAppDir() {
  return path.join(__dirname, '..', 'salon-app');
}

/**
 * Locate the PyInstaller server binary.
 *
 * Packaged installers always use this binary. In development, the same
 * build-resources binary is preferred, but Python remains a convenient
 * fallback so the Desktop App workflow works before the first server build.
 */
function getBundledServerPath() {
  const filename = process.platform === 'win32' ? 'salon-server.exe' : 'salon-server';
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'server', filename);
  }
  return path.join(__dirname, 'build-resources', filename);
}

function getServerCommand() {
  const fs = require('fs');
  const bundledServer = getBundledServerPath();
  if (fs.existsSync(bundledServer)) {
    return { command: bundledServer, args: [], cwd: path.dirname(bundledServer), bundled: true };
  }

  if (app.isPackaged) {
    throw new Error(
      `The bundled server executable is missing at ${bundledServer}. ` +
      'Rebuild the installer after running "npm run build:server".',
    );
  }

  // Development fallback only. This path is never used by an installer.
  const python = process.platform === 'win32' ? 'python' : 'python3';
  return { command: python, args: ['wsgi.py'], cwd: getSalonAppDir(), bundled: false };
}

// ─── Flask lifecycle ──────────────────────────────────────────────────────────

function startFlask() {
  const dataDir   = getSalonDataDir();
  let server;

  if (dataDir) {
    initUserDataDir(dataDir);
  }

  try {
    server = getServerCommand();
  } catch (error) {
    dialog.showErrorBox('Desktop server is missing', error.message);
    app.quit();
    return;
  }

  console.log(
    `[desktop] Starting Flask ${server.bundled ? 'binary' : 'development fallback'} ` +
    `from ${server.command}`,
  );
  if (dataDir) {
    console.log(`[desktop] User data dir: ${dataDir}`);
  }

  flaskProcess = spawn(server.command, server.args, {
    cwd: server.cwd,
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
      'Please restart the application. If the problem persists, reinstall the app ' +
      'or contact support.',
      );
      app.quit();
    }
  });

  flaskProcess.on('error', (err) => {
    console.error('[desktop] Failed to spawn Flask:', err.message);
    dialog.showErrorBox(
      'Cannot start server',
      `Could not launch the bundled server.\n\nError: ${err.message}\n\n` +
      'Please reinstall the application or contact support.',
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
  // Set up silent background updates (packaged builds only)
  setupAutoUpdater();

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
      'Please reinstall the application or contact support.',
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
