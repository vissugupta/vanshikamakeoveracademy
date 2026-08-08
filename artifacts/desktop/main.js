const { app, BrowserWindow, shell, ipcMain } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const http = require('http');

// ─── Configuration ────────────────────────────────────────────────────────────
const FLASK_PORT = 5050;          // internal port used by the spawned Flask server
const FLASK_URL  = `http://localhost:${FLASK_PORT}`;
const POLL_MS    = 500;           // how often to probe Flask while starting (ms)
const TIMEOUT_MS = 60_000;        // give up waiting after this long

// Resolve paths relative to the workspace root (two levels up from artifacts/desktop/)
const WORKSPACE_ROOT = path.resolve(__dirname, '..', '..');
const SALON_APP_DIR  = path.join(WORKSPACE_ROOT, 'salon-app');
const PYTHON_BIN     = process.env.PYTHON_BIN || 'python3';

// ─── State ────────────────────────────────────────────────────────────────────
let flaskProcess = null;
let mainWindow   = null;

// ─── Flask process management ─────────────────────────────────────────────────
function startFlask() {
  const env = {
    ...process.env,
    PORT: String(FLASK_PORT),
    // Disable Secure cookies when running as a local desktop app (no HTTPS proxy)
    FLASK_DESKTOP_MODE: '1',
  };

  flaskProcess = spawn(PYTHON_BIN, ['app.py'], {
    cwd: SALON_APP_DIR,
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  flaskProcess.stdout.on('data', (d) => process.stdout.write(`[flask] ${d}`));
  flaskProcess.stderr.on('data', (d) => process.stderr.write(`[flask] ${d}`));

  flaskProcess.on('exit', (code) => {
    if (code !== 0 && code !== null) {
      console.error(`[desktop] Flask exited with code ${code}`);
    }
  });
}

function waitForFlask() {
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + TIMEOUT_MS;

    function probe() {
      http.get(FLASK_URL, (res) => {
        res.resume();
        resolve();
      }).on('error', () => {
        if (Date.now() >= deadline) {
          reject(new Error(`Flask did not start within ${TIMEOUT_MS / 1000}s`));
        } else {
          setTimeout(probe, POLL_MS);
        }
      });
    }

    probe();
  });
}

// ─── Window management ────────────────────────────────────────────────────────
function createLoadingWindow() {
  const win = new BrowserWindow({
    width: 460,
    height: 320,
    frame: false,
    resizable: false,
    center: true,
    backgroundColor: '#0d0d0d',
    webPreferences: { contextIsolation: true },
  });
  win.loadFile(path.join(__dirname, 'loading.html'));
  return win;
}

function createMainWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 900,
    minHeight: 600,
    center: true,
    backgroundColor: '#0d0d0d',
    title: 'Salon ERP',
    webPreferences: {
      contextIsolation: true,
      // Allow the in-app page to open external links in the system browser
      nativeWindowOpen: true,
    },
  });

  // Open external links in the system browser, not in Electron
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (!url.startsWith(FLASK_URL)) {
      shell.openExternal(url);
      return { action: 'deny' };
    }
    return { action: 'allow' };
  });

  return win;
}

// ─── App lifecycle ────────────────────────────────────────────────────────────
app.whenReady().then(async () => {
  // Disable Secure-cookie requirement so sessions work without HTTPS
  app.on('certificate-error', (event, webContents, url, error, certificate, callback) => {
    callback(false);
  });

  const loadingWin = createLoadingWindow();

  try {
    startFlask();
    await waitForFlask();

    mainWindow = createMainWindow();
    mainWindow.loadURL(FLASK_URL);

    mainWindow.webContents.on('did-finish-load', () => {
      loadingWin.close();
      mainWindow.show();
      mainWindow.focus();
    });

    mainWindow.on('closed', () => { mainWindow = null; });
  } catch (err) {
    console.error('[desktop] Startup failed:', err.message);
    loadingWin.close();
    app.quit();
  }
});

app.on('window-all-closed', () => {
  if (flaskProcess) {
    flaskProcess.kill();
    flaskProcess = null;
  }
  app.quit();
});

app.on('activate', () => {
  if (!mainWindow) {
    createMainWindow();
  }
});
