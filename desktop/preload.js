'use strict';
// Preload script — runs in the renderer with Node integration disabled.
// Expose only a minimal, safe API to the renderer via contextBridge.

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('desktopBridge', {
  /** Ask the main process for the Flask base URL */
  getFlaskUrl: () => ipcRenderer.invoke('get-flask-url'),

  /** Platform identifier so the UI can adapt if needed */
  platform: process.platform,
});
