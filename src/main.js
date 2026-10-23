'use strict';
// MasterDebater — Electron main process.
// Owns the window, file dialogs, disk I/O, settings persistence, and the
// blocks-file watcher. All document parsing happens in the renderer.

const { app, BrowserWindow, ipcMain, dialog, shell, Menu, clipboard } = require('electron');
const fs = require('fs');
const path = require('path');

let win = null;
let watchedPath = null;

// Lets automated tests point the app at a throwaway settings folder.
if (process.env.MASTERDEBATER_USER_DATA) app.setPath('userData', process.env.MASTERDEBATER_USER_DATA);

// ---------- settings ----------
const settingsFile = () => path.join(app.getPath('userData'), 'settings.json');

function loadSettings() {
  try {
    return JSON.parse(fs.readFileSync(settingsFile(), 'utf8'));
  } catch {
    return {};
  }
}

function saveSettings(patch) {
  const merged = { ...loadSettings(), ...patch };
  try {
    fs.mkdirSync(path.dirname(settingsFile()), { recursive: true });
    fs.writeFileSync(settingsFile(), JSON.stringify(merged, null, 2));
  } catch (err) {
    console.error('Could not save settings:', err);
  }
  return merged;
}

// ---------- blocks file watching ----------
function watchBlocksFile(filePath) {
  if (watchedPath) {
    fs.unwatchFile(watchedPath);
    watchedPath = null;
  }
  if (!filePath) return;
  watchedPath = filePath;
  // Polling is the only approach that is reliable across Word's
  // save-to-temp-then-rename behaviour and network/OneDrive folders.
  fs.watchFile(filePath, { interval: 1500 }, (curr, prev) => {
    if (curr.mtimeMs !== prev.mtimeMs && curr.size > 0 && win && !win.isDestroyed()) {
      win.webContents.send('blocks-file-changed', filePath);
    }
  });
}

function readFileBase64(filePath) {
  // Word keeps the .docx locked while saving; retry briefly on EBUSY.
  let lastErr;
  for (let i = 0; i < 5; i++) {
    try {
      return fs.readFileSync(filePath).toString('base64');
    } catch (err) {
      lastErr = err;
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 200);
    }
  }
  throw lastErr;
}

// ---------- IPC ----------
ipcMain.handle('settings:get', () => loadSettings());
ipcMain.handle('settings:set', (_e, patch) => saveSettings(patch));

ipcMain.handle('blocks:choose', async () => {
  const res = await dialog.showOpenDialog(win, {
    title: 'Open your blocks document',
    filters: [{ name: 'Word document', extensions: ['docx'] }],
    properties: ['openFile'],
  });
  if (res.canceled || !res.filePaths.length) return null;
  return res.filePaths[0];
});

ipcMain.handle('blocks:read', (_e, filePath) => {
  const data = readFileBase64(filePath);
  watchBlocksFile(filePath);
  saveSettings({ blocksPath: filePath });
  return data;
});

ipcMain.handle('speech:chooseSavePath', async (_e, suggestedName) => {
  const settings = loadSettings();
  const defaultDir = settings.lastSpeechDir || (settings.blocksPath ? path.dirname(settings.blocksPath) : app.getPath('documents'));
  const res = await dialog.showSaveDialog(win, {
    title: 'Save speech document',
    defaultPath: path.join(defaultDir, suggestedName || 'Speech.docx'),
    filters: [{ name: 'Word document', extensions: ['docx'] }],
  });
  if (res.canceled || !res.filePath) return null;
  saveSettings({ lastSpeechDir: path.dirname(res.filePath) });
  return res.filePath;
});

ipcMain.handle('speech:write', (_e, filePath, base64) => {
  try {
    fs.writeFileSync(filePath, Buffer.from(base64, 'base64'));
    return { ok: true };
  } catch (err) {
    const locked = err.code === 'EBUSY' || err.code === 'EPERM' || err.code === 'EACCES';
    return {
      ok: false,
      error: locked
        ? 'Word has this file open. Close it in Word, then save again.'
        : (err.message || String(err)),
    };
  }
});

ipcMain.handle('shell:open', (_e, filePath) => shell.openPath(filePath));
ipcMain.handle('shell:showInFolder', (_e, filePath) => shell.showItemInFolder(filePath));

ipcMain.handle('clipboard:writeHtml', (_e, html, text) => {
  clipboard.write({ html, text });
  return true;
});

ipcMain.handle('dialog:confirm', async (_e, message, detail) => {
  const res = await dialog.showMessageBox(win, {
    type: 'question',
    buttons: ['Cancel', 'Yes'],
    defaultId: 1,
    cancelId: 0,
    message,
    detail,
    noLink: true,
  });
  return res.response === 1;
});

ipcMain.handle('dialog:error', async (_e, message, detail) => {
  await dialog.showMessageBox(win, { type: 'error', message, detail, buttons: ['OK'] });
});

// ---------- window ----------
function sendMenu(action) {
  if (win && !win.isDestroyed()) win.webContents.send('menu', action);
}

function buildMenu() {
  const isMac = process.platform === 'darwin';
  const template = [
    ...(isMac ? [{ role: 'appMenu' }] : []),
    {
      label: 'File',
      submenu: [
        { label: 'Open Blocks Document…', accelerator: 'CmdOrCtrl+O', click: () => sendMenu('open-blocks') },
        { label: 'Reload Blocks', accelerator: 'F5', click: () => sendMenu('reload-blocks') },
        { type: 'separator' },
        { label: 'Save Speech', accelerator: 'CmdOrCtrl+S', click: () => sendMenu('save-speech') },
        { label: 'Save Speech As…', accelerator: 'CmdOrCtrl+Shift+S', click: () => sendMenu('save-speech-as') },
        { label: 'Save and Open in Word', accelerator: 'CmdOrCtrl+Enter', click: () => sendMenu('save-open') },
        { label: 'Copy Speech to Clipboard', accelerator: 'CmdOrCtrl+Shift+C', click: () => sendMenu('copy-speech') },
        { type: 'separator' },
        { label: 'Reset Speech', accelerator: 'CmdOrCtrl+Shift+R', click: () => sendMenu('reset') },
        { type: 'separator' },
        isMac ? { role: 'close' } : { role: 'quit' },
      ],
    },
    {
      label: 'Edit',
      submenu: [
        { label: 'Find Block', accelerator: 'CmdOrCtrl+F', click: () => sendMenu('find') },
        { type: 'separator' },
        { role: 'copy' }, { role: 'paste' }, { role: 'selectAll' },
      ],
    },
    {
      label: 'View',
      submenu: [
        { role: 'zoomIn' }, { role: 'zoomOut' }, { role: 'resetZoom' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
        { role: 'toggleDevTools' },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function createWindow() {
  const settings = loadSettings();
  const bounds = settings.windowBounds || {};
  win = new BrowserWindow({
    width: bounds.width || 1320,
    height: bounds.height || 860,
    x: bounds.x,
    y: bounds.y,
    minWidth: 820,
    minHeight: 520,
    title: 'MasterDebater',
    backgroundColor: '#1b1b1f',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });
  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  win.once('ready-to-show', () => win.show());
  let boundsTimer = null;
  const persistBounds = () => {
    clearTimeout(boundsTimer);
    boundsTimer = setTimeout(() => {
      if (win && !win.isDestroyed() && !win.isMinimized() && !win.isMaximized()) saveSettings({ windowBounds: win.getBounds() });
    }, 400);
  };
  win.on('resize', persistBounds);
  win.on('move', persistBounds);
  win.on('closed', () => { win = null; });
}

app.whenReady().then(() => {
  buildMenu();
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
