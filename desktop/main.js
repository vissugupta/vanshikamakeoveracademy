'use strict';

const { app, BrowserWindow, ipcMain, dialog, shell, Menu, Notification } = require('electron');
const path  = require('path');
const http  = require('http');
const { spawn } = require('child_process');
const fs = require('fs');

// Keep a startup log in the user's writable app-data folder. This is
// especially important on Windows, where launching an installed GUI app does
// not expose a terminal if the bundled server fails before the main window is
// shown.
let logFile = null;
let startupFailureShown = false;
function initStartupLog() {
  try {
    const dir = app.getPath('logs');
    fs.mkdirSync(dir, { recursive: true });
    logFile = path.join(dir, 'desktop-startup.log');
    fs.appendFileSync(logFile, `\n--- ${new Date().toISOString()} ---\n`, 'utf8');
  } catch {
    logFile = null;
  }
}
function writeStartupLog(message) {
  const line = `[${new Date().toISOString()}] ${message}`;
  console.log(line);
  if (logFile) {
    try { fs.appendFileSync(logFile, `${line}\n`, 'utf8'); } catch {}
  }
}
function showStartupFailure(title, message) {
  if (startupFailureShown) return;
  startupFailureShown = true;
  writeStartupLog(`${title}: ${message}`);

  const logHint = logFile ? `\n\nA diagnostic log was saved here:\n${logFile}` : '';
  if (loadingWin && !loadingWin.isDestroyed()) {
    loadingWin.setSkipTaskbar(false);
    loadingWin.show();
    loadingWin.webContents.executeJavaScript(`
      (() => {
        const status = document.getElementById('status-text');
        if (status) status.textContent = ${JSON.stringify(`${title}: ${message}`)};
      })();
    `).catch(() => {});
  }
  dialog.showErrorBox(title, `${message}${logHint}`);
}
process.on('uncaughtException', (error) => {
  writeStartupLog(`Uncaught exception: ${error.stack || error.message}`);
  if (app.isReady()) {
    showStartupFailure('Application startup failed', error.message);
    app.quit();
  }
});
process.on('unhandledRejection', (reason) => {
  const message = reason && reason.stack ? reason.stack : String(reason);
  writeStartupLog(`Unhandled rejection: ${message}`);
  if (app.isReady()) {
    showStartupFailure('Application startup failed', message);
    app.quit();
  }
});

// ─── Auto-updater ─────────────────────────────────────────────────────────────

/**
 * How often (ms) to re-check for updates after the initial launch check.
 * Default: every 4 hours.
 */
const UPDATE_CHECK_INTERVAL_MS = 4 * 60 * 60 * 1000;

/**
 * Default update preferences.
 *
 *   mode            – 'on-quit'     : install when the app next closes (default)
 *                   – 'on-download' : install immediately after download
 *   maintenanceHour – null          : disabled
 *                   – 0-23          : silently restart at this hour if an update
 *                                     is waiting (only used when mode='on-quit')
 */
const DEFAULT_UPDATE_PREFS = {
  mode: 'on-quit',
  maintenanceHour: null,
};

/**
 * Resolve the path to the per-user update-preferences JSON file.
 * app.getPath('userData') is available only after the app is ready.
 */
function getUpdatePrefsPath() {
  return path.join(app.getPath('userData'), 'update-prefs.json');
}

/** Read preferences from disk; fall back to defaults on any error. */
function loadUpdatePrefs() {
  const fs = require('fs');
  try {
    const raw = fs.readFileSync(getUpdatePrefsPath(), 'utf8');
    return { ...DEFAULT_UPDATE_PREFS, ...JSON.parse(raw) };
  } catch {
    return { ...DEFAULT_UPDATE_PREFS };
  }
}

/** Persist preferences to disk. */
function saveUpdatePrefs(prefs) {
  const fs = require('fs');
  try {
    fs.writeFileSync(getUpdatePrefsPath(), JSON.stringify(prefs, null, 2), 'utf8');
  } catch (e) {
    console.warn('[updater] Could not save update prefs:', e.message);
  }
}

// Track whether a downloaded update is waiting to be applied.
let pendingUpdateVersion = null;
// Reference to the maintenance-window interval so we can cancel it if needed.
let maintenanceIntervalId = null;

/**
 * Start (or restart) the maintenance-window checker.
 * Runs every minute; fires quitAndInstall when the clock enters the
 * configured hour and an update is waiting.
 */
function startMaintenanceWindowChecker() {
  if (maintenanceIntervalId) {
    clearInterval(maintenanceIntervalId);
    maintenanceIntervalId = null;
  }

  maintenanceIntervalId = setInterval(() => {
    if (!pendingUpdateVersion) return;
    const prefs = loadUpdatePrefs();
    if (prefs.mode !== 'on-quit' || prefs.maintenanceHour === null) return;

    const now = new Date();
    if (now.getHours() === prefs.maintenanceHour) {
      console.log(
        `[updater] Maintenance window reached (${prefs.maintenanceHour}:xx) — ` +
        `installing update ${pendingUpdateVersion} silently.`,
      );
      const { autoUpdater } = require('electron-updater');
      autoUpdater.quitAndInstall(false, true);
    }
  }, 60_000); // check every minute
}

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

  // Silent downloads — never interrupt the user mid-session
  autoUpdater.autoDownload = true;
  // We manage install-on-quit ourselves so that the owner's preference is honoured.
  autoUpdater.autoInstallOnAppQuit = false;

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
    console.log(`[updater] Update ${info.version} downloaded.`);
    pendingUpdateVersion = info.version;

    const prefs = loadUpdatePrefs();

    if (prefs.mode === 'on-download') {
      // Owner chose "install immediately" — apply right away
      console.log('[updater] mode=on-download — installing now.');
      autoUpdater.quitAndInstall(false, true);
      return;
    }

    // mode === 'on-quit': respect autoInstallOnAppQuit via our own before-quit hook
    autoUpdater.autoInstallOnAppQuit = true;
    console.log('[updater] mode=on-quit — will install on next quit.');

    // Start maintenance-window checker in case a scheduled hour is configured
    startMaintenanceWindowChecker();

    showUpdateReadyNotification(info.version, prefs);
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
 * Show a subtle OS notification and an in-app banner telling the user a
 * downloaded update is ready. The banner copy adapts to the owner's preference.
 */
function showUpdateReadyNotification(version, prefs) {
  prefs = prefs || loadUpdatePrefs();
  if (!Notification.isSupported()) return;

  const bodyText = prefs.maintenanceHour !== null
    ? `Version ${version} is ready. It will install automatically at ${prefs.maintenanceHour}:00, or restart now.`
    : `Version ${version} has been downloaded. Restart the app to apply it.`;

  const notif = new Notification({
    title: 'Update ready',
    body:  bodyText,
    silent: true,
  });

  notif.on('click', () => {
    const { autoUpdater } = require('electron-updater');
    autoUpdater.quitAndInstall(false, true);
  });

  notif.show();

  // Also inject a banner into the main window
  if (mainWindow) {
    const remindLabel = prefs.maintenanceHour !== null
      ? `Auto-installs at ${prefs.maintenanceHour}:00`
      : 'Later';

    mainWindow.webContents.executeJavaScript(`
      (function () {
        if (document.getElementById('updater-banner')) return;
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
          '">${remindLabel}</button>';
        document.body.appendChild(banner);
      })();
    `).catch(() => {});
  }
}

// ─── IPC: restart-for-update ──────────────────────────────────────────────────

// Triggered by the banner "Restart now" button
ipcMain.handle('restart-for-update', () => {
  if (!app.isPackaged) return;
  const { autoUpdater } = require('electron-updater');
  autoUpdater.quitAndInstall(false, true);
});

// ─── IPC: update preferences ──────────────────────────────────────────────────

ipcMain.handle('get-update-prefs', () => loadUpdatePrefs());

ipcMain.handle('set-update-prefs', (_event, prefs) => {
  // Validate and sanitise before saving
  const mode = prefs.mode === 'on-download' ? 'on-download' : 'on-quit';
  let maintenanceHour = null;
  if (typeof prefs.maintenanceHour === 'number') {
    const h = Math.round(prefs.maintenanceHour);
    if (h >= 0 && h <= 23) maintenanceHour = h;
  }
  const cleaned = { mode, maintenanceHour };
  saveUpdatePrefs(cleaned);

  // If a download is already waiting, re-evaluate behaviour immediately
  if (pendingUpdateVersion) {
    if (mode === 'on-download') {
      const { autoUpdater } = require('electron-updater');
      autoUpdater.quitAndInstall(false, true);
    } else {
      startMaintenanceWindowChecker();
    }
  }

  console.log('[updater] Preferences saved:', JSON.stringify(cleaned));
  return cleaned;
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
    writeStartupLog(`Bundled server lookup failed: ${error.stack || error.message}`);
    showStartupFailure('Desktop server is missing', error.message);
    setTimeout(() => app.quit(), 100);
    return;
  }

  writeStartupLog(
    `[desktop] Starting Flask ${server.bundled ? 'binary' : 'development fallback'} ` +
    `from ${server.command}`,
  );
  if (dataDir) {
    writeStartupLog(`[desktop] User data dir: ${dataDir}`);
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
    writeStartupLog(`[flask] ${data.toString().trimEnd()}`);
  });

  flaskProcess.stderr.on('data', (data) => {
    writeStartupLog(`[flask] ${data.toString().trimEnd()}`);
  });

  flaskProcess.on('exit', (code, signal) => {
    if (!isQuitting) {
      writeStartupLog(`[desktop] Flask exited unexpectedly (code=${code}, signal=${signal})`);
      showStartupFailure(
        'Server crashed',
        `The Flask server stopped unexpectedly (exit code ${code}).\n` +
        'Please restart the application. If the problem persists, reinstall the app ' +
        'or contact support.',
      );
      setTimeout(() => app.quit(), 100);
    }
  });

  flaskProcess.on('error', (err) => {
    writeStartupLog(`[desktop] Failed to spawn Flask: ${err.stack || err.message}`);
    showStartupFailure(
      'Cannot start server',
      `Could not launch the bundled server.\n\nError: ${err.message}\n\n` +
      'Please reinstall the application or contact support.',
    );
    setTimeout(() => app.quit(), 100);
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
  mainWindow.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL) => {
    writeStartupLog(
      `[desktop] Renderer failed to load ${validatedURL}: ${errorCode} ${errorDescription}`,
    );
  });
}

// ─── IPC handlers ─────────────────────────────────────────────────────────────

ipcMain.handle('get-flask-url', () => FLASK_URL);

// ─── App lifecycle ────────────────────────────────────────────────────────────

app.whenReady().then(async () => {
  initStartupLog();
  writeStartupLog(`[desktop] Launching ${app.getVersion()} packaged=${app.isPackaged}`);
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
    writeStartupLog('[desktop] Flask is ready — loading UI');

    // Load the Flask app into the main window
    await mainWindow.loadURL(FLASK_URL);

    // Fade out loading, show main
    mainWindow.show();
    if (loadingWin) {
      loadingWin.close();
    }
  } catch (err) {
    writeStartupLog(`[desktop] Flask failed to start: ${err.stack || err.message}`);
    showStartupFailure(
      'Startup timeout',
      `The application server did not start in time.\n\nDetails: ${err.message}\n\n` +
      'Please reinstall the application or contact support.',
    );
    setTimeout(() => app.quit(), 100);
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
