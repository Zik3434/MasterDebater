'use strict';
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('md', {
  getSettings: () => ipcRenderer.invoke('settings:get'),
  setSettings: (patch) => ipcRenderer.invoke('settings:set', patch),
  chooseBlocksFile: () => ipcRenderer.invoke('blocks:choose'),
  readBlocksFile: (p) => ipcRenderer.invoke('blocks:read', p),
  chooseSpeechPath: (name) => ipcRenderer.invoke('speech:chooseSavePath', name),
  writeSpeech: (p, b64) => ipcRenderer.invoke('speech:write', p, b64),
  openPath: (p) => ipcRenderer.invoke('shell:open', p),
  showInFolder: (p) => ipcRenderer.invoke('shell:showInFolder', p),
  copyHtml: (html, text) => ipcRenderer.invoke('clipboard:writeHtml', html, text),
  confirm: (msg, detail) => ipcRenderer.invoke('dialog:confirm', msg, detail),
  error: (msg, detail) => ipcRenderer.invoke('dialog:error', msg, detail),
  onBlocksFileChanged: (cb) => ipcRenderer.on('blocks-file-changed', (_e, p) => cb(p)),
  onMenu: (cb) => ipcRenderer.on('menu', (_e, action) => cb(action)),
});
