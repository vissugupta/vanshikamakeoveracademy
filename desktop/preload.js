'use strict';
// Preload script — runs in the renderer with Node integration disabled.
// Expose only a minimal, safe API to the renderer via contextBridge.

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('desktopBridge', {
  /** Ask the main process for the Flask base URL */
  getFlaskUrl: () => ipcRenderer.invoke('get-flask-url'),

  /** Platform identifier so the UI can adapt if needed */
  platform: process.platform,

  /** Trigger a quit-and-install cycle when an update has been downloaded */
  restartForUpdate: () => ipcRenderer.invoke('restart-for-update'),

  /**
   * Read the owner's update-installation preference from disk.
   * Returns { mode: 'on-quit'|'on-download', maintenanceHour: null|0-23 }
   */
  getUpdatePrefs: () => ipcRenderer.invoke('get-update-prefs'),

  /**
   * Persist the owner's update-installation preference.
   * @param {{ mode: string, maintenanceHour: number|null }} prefs
   * Returns the sanitised prefs object that was saved.
   */
  setUpdatePrefs: (prefs) => ipcRenderer.invoke('set-update-prefs', prefs),
});

// Make restartForUpdate available as a plain global so the injected banner
// button (which can't use contextBridge) can call it directly.
window.__restartForUpdate = () => ipcRenderer.invoke('restart-for-update');
