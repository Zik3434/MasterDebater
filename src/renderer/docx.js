/* MasterDebater — Word (.docx) block engine.
 *
 * Works in the Electron renderer (browser DOMParser/XMLSerializer) and in
 * Node for tests (pass a DOMParser/XMLSerializer from @xmldom/xmldom).
 *
 * Responsibilities:
 *   - split a Word document into "blocks" by heading paragraphs
 *   - render block content to HTML with formatting (bold, underline,
 *     highlight, size, colour …) resolved through Word styles
 *   - rebuild a new .docx containing only the chosen blocks, copying the
 *     original paragraph XML so Word formatting is preserved verbatim
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.DocxBlocks = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main';

  const HIGHLIGHT = {
    yellow: '#ffff00', green: '#00ff00', cyan: '#00ffff', magenta: '#ff00ff',
    blue: '#0000ff', red: '#ff0000', darkBlue: '#000080', darkCyan: '#008080',
    darkGreen: '#008000', darkMagenta: '#800080', darkRed: '#800000',
    darkYellow: '#808000', darkGray: '#808080', lightGray: '#c0c0c0',
    black: '#000000', white: '#ffffff',
  };

  // ---------- tiny DOM helpers (browser + xmldom compatible) ----------
  const isEl = (n) => n && n.nodeType === 1;
  const isW = (n, name) => isEl(n) && n.namespaceURI === W && n.localName === name;
  const children = (n) => Array.from(n.childNodes || []).filter(isEl);
  const childW = (n, name) => children(n).find((c) => isW(c, name)) || null;
  const attrW = (n, name) => (n ? (n.getAttributeNS(W, name) || n.getAttribute('w:' + name) || null) : null);
  const val = (n) => attrW(n, 'val');
  const onOff = (n) => {
    if (!n) return undefined;
    const v = val(n);
    if (v === null || v === undefined || v === '') return true;
    return !['0', 'false', 'off'].includes(String(v).toLowerCase());
  };

  function esc(s) {
    return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  // ---------- run / paragraph property parsing ----------
  function parseRPr(rPr) {
    const p = {};
    if (!rPr) return p;
    for (const c of children(rPr)) {
      switch (c.localName) {
        case 'b': p.bold = onOff(c); break;
        case 'i': p.italic = onOff(c); break;
        case 'u': { const v = val(c); p.underline = !(v === null || v === 'none'); break; }
        case 'strike': p.strike = onOff(c); break;
        case 'dstrike': p.strike = onOff(c); break;
        case 'caps': p.caps = onOff(c); break;
        case 'smallCaps': p.smallCaps = onOff(c); break;
        case 'vanish': p.hidden = onOff(c); break;
        case 'highlight': { const v = val(c); p.highlight = v && v !== 'none' ? v : null; break; }
        case 'shd': { const f = attrW(c, 'fill'); p.shading = f && f !== 'auto' ? f : null; break; }
        case 'color': { const v = val(c); p.color = v && v !== 'auto' ? v : null; break; }
        case 'sz': { const v = parseInt(val(c), 10); if (!Number.isNaN(v)) p.size = v / 2; break; }
        case 'vertAlign': p.vertAlign = val(c); break;
        case 'rStyle': p.rStyle = val(c); break;
        default: break;
      }
    }
    return p;
  }

  function parsePPr(pPr) {
    const p = {};
    if (!pPr) return p;
    for (const c of children(pPr)) {
      switch (c.localName) {
        case 'pStyle': p.pStyle = val(c); break;
        case 'jc': p.align = val(c); break;
        case 'outlineLvl': { const v = parseInt(val(c), 10); if (!Number.isNaN(v)) p.outlineLvl = v; break; }
        case 'numPr': p.numbered = true; break;
        case 'ind': { const l = parseInt(attrW(c, 'left') || attrW(c, 'start') || '0', 10); if (l) p.indentLeft = l; break; }
        case 'rPr': p.rPr = parseRPr(c); break;
        default: break;
      }
    }
    return p;
  }

  function merge(...objs) {
    const out = {};
    for (const o of objs) {
      if (!o) continue;
      for (const k of Object.keys(o)) if (o[k] !== undefined) out[k] = o[k];
    }
    return out;
  }

  // ---------- styles.xml ----------
  function parseStyles(stylesDoc) {
    const styles = {};
    let defaults = {};
    let defaultParagraphStyle = null;
    let defaultCharacterStyle = null;
    if (stylesDoc) {
      const rootEl = stylesDoc.documentElement;
      const docDefaults = childW(rootEl, 'docDefaults');
      if (docDefaults) {
        const rpd = childW(docDefaults, 'rPrDefault');
        if (rpd) defaults = parseRPr(childW(rpd, 'rPr'));
      }
      for (const s of children(rootEl)) {
        if (!isW(s, 'style')) continue;
        const id = attrW(s, 'styleId');
        if (!id) continue;
        const nameEl = childW(s, 'name');
        const basedOn = childW(s, 'basedOn');
        const type = attrW(s, 'type') || 'paragraph';
        const style = {
          id,
          type,
          name: nameEl ? (val(nameEl) || '') : '',
          basedOn: basedOn ? val(basedOn) : null,
          rPr: parseRPr(childW(s, 'rPr')),
          pPr: parsePPr(childW(s, 'pPr')),
          isDefault: attrW(s, 'default') === '1' || attrW(s, 'default') === 'true',
        };
        styles[id] = style;
        if (style.isDefault && type === 'paragraph') defaultParagraphStyle = id;
        if (style.isDefault && type === 'character') defaultCharacterStyle = id;
      }
    }
    const memo = {};
    function resolve(id) {
      if (!id || !styles[id]) return { rPr: {}, pPr: {} };
      if (memo[id]) return memo[id];
      memo[id] = { rPr: {}, pPr: {} }; // cycle guard
      const s = styles[id];
      const base = resolve(s.basedOn);
      memo[id] = { rPr: merge(base.rPr, s.rPr), pPr: merge(base.pPr, s.pPr) };
      return memo[id];
    }
    // Heading level for a paragraph style id (1-based), or 0 when not a heading.
    function headingLevel(id) {
      if (!id || !styles[id]) return 0;
      const chain = [];
      let cur = id;
      while (cur && styles[cur] && !chain.includes(cur)) { chain.push(cur); cur = styles[cur].basedOn; }
      for (const sid of chain) {
        const s = styles[sid];
        const m = /^heading\s*(\d)$/i.exec(s.name || '') || /^Heading(\d)$/.exec(sid);
        if (m) return parseInt(m[1], 10);
      }
      const r = resolve(id);
      if (r.pPr.outlineLvl !== undefined && r.pPr.outlineLvl < 9) return r.pPr.outlineLvl + 1;
      return 0;
    }
    return { styles, defaults, resolve, headingLevel, defaultParagraphStyle, defaultCharacterStyle };
  }

  // ---------- body walking ----------
  function paragraphText(p) {
    let out = '';
    (function walk(n) {
      for (const c of children(n)) {
        if (isW(c, 'del')) continue;
        if (isW(c, 't')) out += c.textContent || '';
        else if (isW(c, 'tab')) out += ' ';
        else if (isW(c, 'br') || isW(c, 'cr')) out += ' ';
        else if (isW(c, 'rPr') || isW(c, 'pPr')) continue;
        else walk(c);
      }
    })(p);
    return out.replace(/\s+/g, ' ').trim();
  }

  function paragraphLevel(p, stylesInfo) {
    const pPr = parsePPr(childW(p, 'pPr'));
    if (pPr.outlineLvl !== undefined && pPr.outlineLvl < 9) return pPr.outlineLvl + 1;
    return stylesInfo.headingLevel(pPr.pStyle);
  }

  // Flatten body into an ordered list of block-level items.
  function bodyItems(body, stylesInfo) {
    const items = [];
    let sectPr = null;
    (function walk(container) {
      for (const c of children(container)) {
        if (isW(c, 'p')) {
          items.push({ kind: 'p', node: c, level: paragraphLevel(c, stylesInfo), text: paragraphText(c) });
        } else if (isW(c, 'tbl')) {
          items.push({ kind: 'tbl', node: c, level: 0, text: tableText(c) });
        } else if (isW(c, 'sdt')) {
          const content = childW(c, 'sdtContent');
          if (content) walk(content);
        } else if (isW(c, 'sectPr')) {
          sectPr = c;
        }
        // bookmarks, proofErr, etc. at body level are dropped
      }
    })(body);
    return { items, sectPr };
  }

  function tableText(tbl) {
    const parts = [];
    (function walk(n) {
      for (const c of children(n)) {
        if (isW(c, 'p')) parts.push(paragraphText(c));
        else walk(c);
      }
    })(tbl);
    return parts.filter(Boolean).join(' ');
  }

  // ---------- block splitting ----------
  function detectLevels(items) {
    const counts = {};
    for (const it of items) if (it.kind === 'p' && it.level > 0 && it.text) counts[it.level] = (counts[it.level] || 0) + 1;
    return counts; // { level: count }
  }

  function autoLevel(counts) {
    let best = 0;
    let bestCount = -1;
    for (const [lvl, cnt] of Object.entries(counts)) {
      const l = parseInt(lvl, 10);
      if (cnt > bestCount || (cnt === bestCount && l < best)) { best = l; bestCount = cnt; }
    }
    return best;
  }

  function splitBlocks(items, splitLevel) {
    const blocks = [];
    let current = null;
    const seen = {};
    const finish = () => {
      if (!current) return;
      const hasBody = current.items.slice(1).some((it) => it.text || it.kind === 'tbl');
      if (!hasBody && current.level < splitLevel) {
        // A shallower heading with nothing under it is a section label.
        blocks.push({ ...current, isGroup: true });
      } else {
        blocks.push(current);
      }
      current = null;
    };
    for (const it of items) {
      const isHeading = it.kind === 'p' && it.level > 0 && it.level <= splitLevel && it.text;
      if (isHeading) {
        finish();
        const base = it.text;
        seen[base] = (seen[base] || 0) + 1;
        current = {
          id: seen[base] > 1 ? `${base} #${seen[base]}` : base,
          title: base,
          level: it.level,
          items: [it],
          isGroup: false,
        };
      } else if (current) {
        current.items.push(it);
      }
      // content before the first heading is ignored
    }
    finish();
    // attach group labels and derived info
    let group = null;
    for (const b of blocks) {
      if (b.isGroup) { group = b.title; continue; }
      b.group = group;
      const bodyText = b.items.slice(1).map((i) => i.text).filter(Boolean).join(' ');
      b.preview = bodyText.slice(0, 160);
      b.wordCount = bodyText ? bodyText.split(/\s+/).length : 0;
    }
    return blocks;
  }

  // ---------- HTML rendering ----------
  function runStyle(props) {
    const css = [];
    if (props.bold) css.push('font-weight:bold');
    if (props.italic) css.push('font-style:italic');
    const deco = [];
    if (props.underline) deco.push('underline');
    if (props.strike) deco.push('line-through');
    if (deco.length) css.push('text-decoration:' + deco.join(' '));
    if (props.caps) css.push('text-transform:uppercase');
    else if (props.smallCaps) css.push('font-variant:small-caps');
    const bg = props.highlight ? (HIGHLIGHT[props.highlight] || props.highlight) : (props.shading ? '#' + props.shading : null);
    if (bg) css.push('background-color:' + bg);
    if (props.color && /^[0-9a-fA-F]{6}$/.test(props.color)) css.push('color:#' + props.color);
    if (props.size) css.push('font-size:' + props.size + 'pt');
    if (props.vertAlign === 'superscript') css.push('vertical-align:super;font-size:smaller');
    if (props.vertAlign === 'subscript') css.push('vertical-align:sub;font-size:smaller');
    return css.join(';');
  }

  function renderRuns(container, ctx, inherited, out) {
    for (const c of children(container)) {
      if (isW(c, 'r')) {
        const direct = parseRPr(childW(c, 'rPr'));
        const charStyle = direct.rStyle ? ctx.styles.resolve(direct.rStyle).rPr : {};
        const props = merge(inherited, charStyle, direct);
        if (props.hidden) continue;
        let text = '';
        for (const rc of children(c)) {
          if (isW(rc, 't')) text += esc(rc.textContent || '');
          else if (isW(rc, 'tab')) text += '&emsp;';
          else if (isW(rc, 'br') || isW(rc, 'cr')) text += '<br>';
          else if (isW(rc, 'sym')) text += '&#9679;';
          else if (isW(rc, 'footnoteReference') || isW(rc, 'endnoteReference')) text += '';
        }
        if (!text) continue;
        const style = runStyle(props);
        out.push(style ? `<span style="${style}">${text}</span>` : text);
      } else if (isW(c, 'del') || isW(c, 'rPr') || isW(c, 'pPr') || isW(c, 'commentRangeStart') || isW(c, 'commentRangeEnd') || isW(c, 'bookmarkStart') || isW(c, 'bookmarkEnd') || isW(c, 'proofErr')) {
        continue;
      } else {
        // hyperlink, ins, sdt, smartTag, fldSimple, customXml …
        const inner = isW(c, 'sdt') ? childW(c, 'sdtContent') : c;
        if (inner) renderRuns(inner, ctx, inherited, out);
      }
    }
  }

  function renderParagraph(p, ctx) {
    const pPr = parsePPr(childW(p, 'pPr'));
    const styleId = pPr.pStyle || ctx.styles.defaultParagraphStyle;
    const resolved = ctx.styles.resolve(styleId);
    const inherited = merge(ctx.styles.defaults, resolved.rPr);
    const level = paragraphLevel(p, ctx.styles);
    const out = [];
    renderRuns(p, ctx, inherited, out);
    const css = ['margin:0 0 4pt 0', 'white-space:pre-wrap'];
    const align = pPr.align || resolved.pPr.align;
    if (align === 'center') css.push('text-align:center');
    else if (align === 'right' || align === 'end') css.push('text-align:right');
    else if (align === 'both') css.push('text-align:justify');
    const indent = pPr.indentLeft || resolved.pPr.indentLeft;
    if (indent) css.push('margin-left:' + Math.round(indent / 20) + 'pt');
    const bullet = (pPr.numbered || resolved.pPr.numbered) ? '&bull; ' : '';
    const inner = out.length ? bullet + out.join('') : '<br>';
    if (level > 0 && level <= 6) {
      const sizes = { 1: 16, 2: 14, 3: 12.5, 4: 12, 5: 11.5, 6: 11 };
      css.push('font-weight:bold', 'font-size:' + sizes[level] + 'pt', 'margin-top:8pt');
      return `<h${level} style="${css.join(';')}">${inner}</h${level}>`;
    }
    return `<p style="${css.join(';')}">${inner}</p>`;
  }

  function renderTable(tbl, ctx) {
    const rows = [];
    for (const tr of children(tbl)) {
      if (!isW(tr, 'tr')) continue;
      const cells = [];
      for (const tc of children(tr)) {
        if (!isW(tc, 'tc')) continue;
        const inner = [];
        for (const c of children(tc)) {
          if (isW(c, 'p')) inner.push(renderParagraph(c, ctx));
          else if (isW(c, 'tbl')) inner.push(renderTable(c, ctx));
        }
        cells.push(`<td style="border:1px solid #999;padding:3pt;vertical-align:top">${inner.join('')}</td>`);
      }
      rows.push(`<tr>${cells.join('')}</tr>`);
    }
    return `<table style="border-collapse:collapse;margin:4pt 0">${rows.join('')}</table>`;
  }

  function renderItems(items, ctx) {
    return items.map((it) => (it.kind === 'tbl' ? renderTable(it.node, ctx) : renderParagraph(it.node, ctx))).join('');
  }

  // ---------- document loading ----------
  async function load(data, env) {
    const { JSZip, DOMParser } = env;
    const zip = await JSZip.loadAsync(data);
    const docFile = zip.file('word/document.xml');
    if (!docFile) throw new Error('This file is not a Word .docx document (no word/document.xml).');
    const docXml = await docFile.async('string');
    const stylesFile = zip.file('word/styles.xml');
    const stylesXml = stylesFile ? await stylesFile.async('string') : null;
    const parser = new DOMParser();
    const docDom = parser.parseFromString(docXml, 'application/xml');
    const stylesDom = stylesXml ? parser.parseFromString(stylesXml, 'application/xml') : null;
    const body = childW(docDom.documentElement, 'body');
    if (!body) throw new Error('Could not find the document body.');
    const styles = parseStyles(stylesDom);
    const { items, sectPr } = bodyItems(body, styles);
    const levelCounts = detectLevels(items);
    return { zip, docDom, body, sectPr, items, styles, levelCounts, autoLevel: autoLevel(levelCounts) };
  }

  function blocksFor(doc, splitLevel) {
    const level = splitLevel && splitLevel !== 'auto' ? parseInt(splitLevel, 10) : doc.autoLevel;
    if (!level) return { level: 0, blocks: [] };
    return { level, blocks: splitBlocks(doc.items, level) };
  }

  function renderBlock(doc, block) {
    return renderItems(block.items, { styles: doc.styles });
  }

  function renderBlockBody(doc, block) {
    return renderItems(block.items.slice(1), { styles: doc.styles });
  }

  // ---------- building the speech .docx ----------
  const STRIP = new Set(['bookmarkStart', 'bookmarkEnd', 'commentRangeStart', 'commentRangeEnd', 'commentReference', 'proofErr', 'permStart', 'permEnd', 'moveFromRangeStart', 'moveFromRangeEnd', 'moveToRangeStart', 'moveToRangeEnd']);

  function sanitize(node) {
    for (const c of children(node)) {
      if (c.namespaceURI === W && STRIP.has(c.localName)) {
        node.removeChild(c);
      } else if (isW(c, 'r') && children(c).some((rc) => isW(rc, 'commentReference'))) {
        node.removeChild(c);
      } else {
        sanitize(c);
      }
    }
    return node;
  }

  function stripFootnoteRefs(node) {
    // Footnote/endnote parts are retained in the package, so references stay valid.
    return node;
  }

  async function buildSpeechDocx(doc, blocks, env) {
    const { XMLSerializer } = env;
    const docDom = doc.docDom;
    const body = doc.body;
    const newBody = docDom.createElementNS(W, 'w:body');
    for (const b of blocks) {
      for (const it of b.items) {
        const clone = it.node.cloneNode(true);
        newBody.appendChild(sanitize(stripFootnoteRefs(clone)));
      }
    }
    if (doc.sectPr) newBody.appendChild(doc.sectPr.cloneNode(true));
    const rootEl = docDom.documentElement;
    rootEl.replaceChild(newBody, body);
    let xml;
    try {
      xml = new XMLSerializer().serializeToString(docDom);
    } finally {
      rootEl.replaceChild(body, newBody); // restore so the doc stays reusable
    }
    xml = xml.replace(/^\s*<\?xml[^>]*\?>\s*/i, '');
    xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n' + xml;
    const zip = doc.zip;
    const original = await zip.file('word/document.xml').async('string');
    zip.file('word/document.xml', xml);
    try {
      return await zip.generateAsync({ type: 'base64', compression: 'DEFLATE', mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' });
    } finally {
      zip.file('word/document.xml', original);
    }
  }

  function speechText(doc, blocks) {
    return blocks.map((b) => b.items.map((it) => it.text).join('\n')).join('\n\n');
  }

  return { load, blocksFor, renderBlock, renderBlockBody, buildSpeechDocx, speechText, paragraphText, _internal: { parseRPr, parsePPr, parseStyles, splitBlocks } };
});
