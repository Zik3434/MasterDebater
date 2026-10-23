'use strict';
/* MasterDebater renderer: block list on the left, speech doc on the right. */

const env = { JSZip: window.JSZip, DOMParser: window.DOMParser, XMLSerializer: window.XMLSerializer };

const state = {
  blocksPath: null,
  doc: null,          // parsed document (DocxBlocks.load)
  level: 'auto',      // 'auto' or a heading level number as string
  blocks: [],         // current block list (including group labels)
  blockById: new Map(),
  selected: [],       // ordered block ids in the speech
  speechPath: null,   // where the speech .docx was last saved
  filter: '',
  htmlCache: new Map(),
  dirtySinceSave: false,
  lastSavedAt: null,
};

const $ = (id) => document.getElementById(id);
const els = {
  open: $('btn-open'), open2: $('btn-open-2'), fileName: $('file-name'), level: $('level-select'),
  copy: $('btn-copy'), save: $('btn-save'), saveOpen: $('btn-save-open'), reset: $('btn-reset'),
  search: $('search'), list: $('block-list'), sidebarEmpty: $('sidebar-empty'),
  speech: $('speech'), speechEmpty: $('speech-empty'), speechStats: $('speech-stats'),
  statusLeft: $('status-left'), statusRight: $('status-right'), toast: $('toast'),
};

// ---------- helpers ----------
let toastTimer = null;
function toast(msg, kind = '', ms = 2600) {
  els.toast.textContent = msg;
  els.toast.className = 'toast ' + kind;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => els.toast.classList.add('hidden'), ms);
}

function baseName(p) { return p ? p.split(/[\\/]/).pop() : ''; }
function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
function base64ToBytes(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

function persist() {
  window.md.setSettings({ level: state.level, selected: state.selected, speechPath: state.speechPath });
}

// ---------- loading blocks ----------
async function openBlocksDialog() {
  const p = await window.md.chooseBlocksFile();
  if (!p) return;
  const changedFile = state.blocksPath && p !== state.blocksPath;
  if (changedFile) {
    state.selected = [];
    state.speechPath = null;
  }
  await loadBlocks(p, { announce: true });
}

async function loadBlocks(p, { announce = false, silent = false } = {}) {
  try {
    const b64 = await window.md.readBlocksFile(p);
    const doc = await DocxBlocks.load(base64ToBytes(b64), env);
    state.blocksPath = p;
    state.doc = doc;
    state.htmlCache.clear();
    rebuildBlocks({ prune: true });
    els.fileName.textContent = baseName(p);
    els.fileName.title = p;
    if (announce) toast(`Loaded ${state.blocks.filter((b) => !b.isGroup).length} blocks from ${baseName(p)}`, 'ok');
    else if (!silent) toast('Blocks reloaded from Word', 'ok', 1500);
    if (!Object.keys(doc.levelCounts).length) {
      window.md.error('No headings found in this document.', 'MasterDebater splits blocks by Word headings. In Word, click a block title, then choose Home → Styles → Heading 1 (or 2). Save, and the app will pick it up.');
    }
  } catch (err) {
    console.error(err);
    window.md.error('Could not read the blocks document.', String(err.message || err));
  }
}

function rebuildBlocks({ prune = false } = {}) {
  if (!state.doc) { state.blocks = []; state.blockById.clear(); render(); return; }
  const { level, blocks } = DocxBlocks.blocksFor(state.doc, state.level);
  state.blocks = blocks;
  state.blockById = new Map(blocks.filter((b) => !b.isGroup).map((b) => [b.id, b]));
  if (prune) {
    // The Word file changed: drop speech entries whose heading no longer exists.
    const before = state.selected.length;
    state.selected = state.selected.filter((id) => state.blockById.has(id));
    if (state.selected.length !== before) toast('Some blocks in your speech no longer exist and were removed', '', 4000);
  }
  renderLevelSelect(level);
  render();
  persist();
}

function renderLevelSelect(activeLevel) {
  const counts = state.doc ? state.doc.levelCounts : {};
  const levels = Object.keys(counts).map(Number).sort((a, b) => a - b);
  els.level.innerHTML = '';
  const auto = document.createElement('option');
  auto.value = 'auto';
  auto.textContent = state.doc && state.doc.autoLevel ? `Auto (Heading ${state.doc.autoLevel})` : 'Auto';
  els.level.appendChild(auto);
  for (const l of levels) {
    const o = document.createElement('option');
    o.value = String(l);
    o.textContent = `Heading ${l} (${counts[l]})`;
    els.level.appendChild(o);
  }
  els.level.value = state.level === 'auto' || !levels.includes(Number(state.level)) ? 'auto' : String(state.level);
  els.level.disabled = !state.doc;
}

// ---------- rendering ----------
function blockHtml(block) {
  if (!state.htmlCache.has(block.id)) state.htmlCache.set(block.id, DocxBlocks.renderBlock(state.doc, block));
  return state.htmlCache.get(block.id);
}

function highlightMatch(title, q) {
  if (!q) return esc(title);
  const idx = title.toLowerCase().indexOf(q.toLowerCase());
  if (idx < 0) return esc(title);
  return esc(title.slice(0, idx)) + '<mark>' + esc(title.slice(idx, idx + q.length)) + '</mark>' + esc(title.slice(idx + q.length));
}

function visibleBlocks() {
  const q = state.filter.trim().toLowerCase();
  return state.blocks.filter((b) => !b.isGroup && (!q || b.title.toLowerCase().includes(q) || (b.preview || '').toLowerCase().includes(q)));
}

function renderList() {
  const hasDoc = !!state.doc;
  els.sidebarEmpty.classList.toggle('hidden', hasDoc);
  els.list.classList.toggle('hidden', !hasDoc);
  if (!hasDoc) { els.list.innerHTML = ''; return; }
  const q = state.filter.trim();
  const frag = document.createDocumentFragment();
  let shown = 0;
  for (const b of state.blocks) {
    if (b.isGroup) {
      if (q) continue;
      const g = document.createElement('div');
      g.className = 'group-label';
      g.textContent = b.title;
      frag.appendChild(g);
      continue;
    }
    if (q && !(b.title.toLowerCase().includes(q.toLowerCase()) || (b.preview || '').toLowerCase().includes(q.toLowerCase()))) continue;
    shown++;
    const pos = state.selected.indexOf(b.id);
    const el = document.createElement('div');
    el.className = 'block' + (pos >= 0 ? ' selected' : '');
    el.dataset.id = b.id;
    el.tabIndex = 0;
    el.setAttribute('role', 'option');
    el.setAttribute('aria-selected', pos >= 0 ? 'true' : 'false');
    el.title = pos >= 0 ? 'Click to remove from speech' : 'Click to add to speech';
    el.innerHTML =
      `<div class="pos">${pos >= 0 ? pos + 1 : '+'}</div>` +
      `<div class="title">${highlightMatch(b.title, q)}</div>` +
      `<div class="preview">${esc(b.preview || '')}${b.preview ? '' : '<span class="meta">(empty block)</span>'}</div>`;
    frag.appendChild(el);
  }
  els.list.innerHTML = '';
  if (!shown) {
    const n = document.createElement('div');
    n.className = 'no-match';
    n.textContent = q ? `No block matches “${q}”` : 'No blocks found. Use Heading styles in Word for each block title.';
    els.list.appendChild(n);
  }
  els.list.appendChild(frag);
}

function renderSpeech() {
  const existing = new Map();
  for (const node of els.speech.querySelectorAll('.speech-block')) existing.set(node.dataset.id, node);
  // Remove blocks no longer selected, then insert / reorder the rest
  let anchor = els.speechEmpty;
  const shown = state.selected.filter((id) => state.blockById.has(id));
  for (const [id, node] of existing) if (!shown.includes(id)) node.remove();
  els.speechEmpty.classList.toggle('hidden', shown.length > 0);
  shown.forEach((id, i) => {
    const block = state.blockById.get(id);
    let node = existing.get(id);
    if (!node) {
      node = document.createElement('article');
      node.className = 'speech-block';
      node.dataset.id = id;
      node.innerHTML =
        `<div class="speech-block-bar"><span class="num"></span><span class="t">${esc(block.title)}</span>` +
        `<button class="btn tiny" data-act="up" title="Move up">▲</button>` +
        `<button class="btn tiny" data-act="down" title="Move down">▼</button>` +
        `<button class="btn tiny remove" data-act="remove" title="Remove from speech">✕</button></div>` +
        `<div class="speech-content">${blockHtml(block)}</div>`;
    }
    node.querySelector('.num').textContent = String(i + 1);
    node.querySelector('[data-act="up"]').disabled = i === 0;
    node.querySelector('[data-act="down"]').disabled = i === shown.length - 1;
    anchor.insertAdjacentElement('afterend', node);
    anchor = node;
  });
  const words = shown.reduce((n, id) => n + (state.blockById.get(id)?.wordCount || 0), 0);
  els.speechStats.textContent = shown.length
    ? `${shown.length} block${shown.length === 1 ? '' : 's'} · ${words.toLocaleString()} words`
    : 'Empty';
  const enabled = shown.length > 0;
  els.copy.disabled = !enabled;
  els.save.disabled = !enabled;
  els.saveOpen.disabled = !enabled;
  els.reset.disabled = !enabled;
}

function renderStatus() {
  const n = state.blocks.filter((b) => !b.isGroup).length;
  els.statusLeft.textContent = state.doc ? `${n} blocks · ${state.blocksPath}` : 'Open a Word document to get started';
  if (state.speechPath) {
    const when = state.lastSavedAt ? ` at ${state.lastSavedAt.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}` : '';
    els.statusRight.textContent = `${state.dirtySinceSave ? 'Unsaved changes · last saved' : 'Saved'}${when} → ${baseName(state.speechPath)}`;
  } else {
    els.statusRight.textContent = state.selected.length ? 'Not saved yet' : '';
  }
}

function render() { renderList(); renderSpeech(); renderStatus(); }

// ---------- speech editing ----------
function toggleBlock(id) {
  const idx = state.selected.indexOf(id);
  if (idx >= 0) state.selected.splice(idx, 1);
  else state.selected.push(id);
  state.dirtySinceSave = true;
  render();
  persist();
  if (idx < 0) {
    const node = els.speech.querySelector(`.speech-block[data-id="${CSS.escape(id)}"]`);
    if (node) node.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }
}

function moveBlock(id, delta) {
  const i = state.selected.indexOf(id);
  const j = i + delta;
  if (i < 0 || j < 0 || j >= state.selected.length) return;
  [state.selected[i], state.selected[j]] = [state.selected[j], state.selected[i]];
  state.dirtySinceSave = true;
  render();
  persist();
}

async function resetSpeech() {
  if (!state.selected.length) return;
  const ok = await window.md.confirm('Clear the speech doc?', `This removes all ${state.selected.length} blocks from the speech. Your blocks document is not touched.`);
  if (!ok) return;
  state.selected = [];
  state.dirtySinceSave = true;
  render();
  persist();
  toast('Speech cleared', '', 1500);
}

// ---------- export ----------
function selectedBlocks() { return state.selected.map((id) => state.blockById.get(id)).filter(Boolean); }

function nextCopyName(p) {
  const m = /^(.*?)(?: \((\d+)\))?\.docx$/i.exec(p);
  if (!m) return p.replace(/\.docx$/i, '') + ' (2).docx';
  return `${m[1]} (${(parseInt(m[2] || '1', 10) + 1)}).docx`;
}

async function saveSpeech({ chooseNew = false, open = false } = {}) {
  if (!state.selected.length) { toast('Add some blocks first', 'err'); return null; }
  let target = state.speechPath;
  if (chooseNew || !target) {
    target = await window.md.chooseSpeechPath(target ? baseName(target) : 'Speech.docx');
    if (!target) return null;
  }
  let b64;
  try {
    b64 = await DocxBlocks.buildSpeechDocx(state.doc, selectedBlocks(), env);
  } catch (err) {
    console.error(err);
    window.md.error('Could not build the speech document.', String(err.message || err));
    return null;
  }
  let res = await window.md.writeSpeech(target, b64);
  if (!res.ok && /Word has this file open/.test(res.error)) {
    const alt = nextCopyName(target);
    const ok = await window.md.confirm(`Word still has ${baseName(target)} open.`, `Save a new copy as ${baseName(alt)} instead?`);
    if (!ok) return null;
    target = alt;
    res = await window.md.writeSpeech(target, b64);
  }
  if (!res.ok) {
    window.md.error('Could not save the speech.', res.error);
    return null;
  }
  state.speechPath = target;
  state.dirtySinceSave = false;
  state.lastSavedAt = new Date();
  persist();
  renderStatus();
  if (open) {
    const err = await window.md.openPath(target);
    if (err) window.md.error('Saved, but could not open Word.', err);
    else toast(`Saved and opened ${baseName(target)}`, 'ok');
  } else {
    toast(`Saved ${baseName(target)}`, 'ok');
  }
  return target;
}

async function copySpeech() {
  if (!state.selected.length) { toast('Add some blocks first', 'err'); return; }
  const blocks = selectedBlocks();
  const html = '<html><body style="font-family:Calibri,Arial,sans-serif;font-size:11pt">' + blocks.map(blockHtml).join('') + '</body></html>';
  const text = DocxBlocks.speechText(state.doc, blocks);
  await window.md.copyHtml(html, text);
  toast('Speech copied. Paste into Word with Ctrl+V.', 'ok');
}

// ---------- events ----------
els.open.addEventListener('click', openBlocksDialog);
els.open2.addEventListener('click', openBlocksDialog);
els.reset.addEventListener('click', resetSpeech);
els.copy.addEventListener('click', copySpeech);
els.save.addEventListener('click', () => saveSpeech());
els.saveOpen.addEventListener('click', () => saveSpeech({ open: true }));
els.level.addEventListener('change', () => {
  state.level = els.level.value;
  state.htmlCache.clear();
  rebuildBlocks();
});
els.search.addEventListener('input', () => { state.filter = els.search.value; renderList(); });
els.search.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { els.search.value = ''; state.filter = ''; renderList(); }
  if (e.key === 'Enter') {
    const first = visibleBlocks()[0];
    if (first) toggleBlock(first.id);
  }
});

els.list.addEventListener('click', (e) => {
  const el = e.target.closest('.block');
  if (el) toggleBlock(el.dataset.id);
});
els.list.addEventListener('keydown', (e) => {
  const el = e.target.closest('.block');
  if (!el) return;
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleBlock(el.dataset.id); }
  if (e.key === 'ArrowDown') { e.preventDefault(); (el.nextElementSibling?.classList.contains('block') ? el.nextElementSibling : el.nextElementSibling?.nextElementSibling)?.focus(); }
  if (e.key === 'ArrowUp') { e.preventDefault(); (el.previousElementSibling?.classList.contains('block') ? el.previousElementSibling : el.previousElementSibling?.previousElementSibling)?.focus(); }
});

els.speech.addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-act]');
  if (!btn) return;
  const id = btn.closest('.speech-block').dataset.id;
  if (btn.dataset.act === 'remove') toggleBlock(id);
  if (btn.dataset.act === 'up') moveBlock(id, -1);
  if (btn.dataset.act === 'down') moveBlock(id, 1);
});

window.md.onMenu((action) => {
  switch (action) {
    case 'open-blocks': openBlocksDialog(); break;
    case 'reload-blocks': if (state.blocksPath) loadBlocks(state.blocksPath); break;
    case 'save-speech': saveSpeech(); break;
    case 'save-speech-as': saveSpeech({ chooseNew: true }); break;
    case 'save-open': saveSpeech({ open: true }); break;
    case 'copy-speech': copySpeech(); break;
    case 'reset': resetSpeech(); break;
    case 'find': els.search.focus(); els.search.select(); break;
    default: break;
  }
});

let reloadTimer = null;
window.md.onBlocksFileChanged((p) => {
  // Word writes several times during a save; wait for it to settle.
  clearTimeout(reloadTimer);
  reloadTimer = setTimeout(() => loadBlocks(p), 800);
});

// ---------- startup ----------
(async () => {
  const s = await window.md.getSettings();
  if (s.level) state.level = s.level;
  if (Array.isArray(s.selected)) state.selected = s.selected;
  if (s.speechPath) state.speechPath = s.speechPath;
  renderLevelSelect(0);
  render();
  if (s.blocksPath) await loadBlocks(s.blocksPath, { silent: true });
})();
