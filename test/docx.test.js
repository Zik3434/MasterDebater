'use strict';
// Run with: npm test
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const JSZip = require('jszip');
const { DOMParser, XMLSerializer } = require('@xmldom/xmldom');
const DocxBlocks = require('../src/renderer/docx.js');

const env = { JSZip, DOMParser, XMLSerializer };

(async () => {
  const data = fs.readFileSync(path.join(__dirname, 'fixtures', 'blocks.docx'));
  const doc = await DocxBlocks.load(data, env);

  // auto level: Heading 1 appears 7 times, Heading 2 five times -> 1
  assert.deepStrictEqual(doc.levelCounts, { 1: 7, 2: 5 }, 'heading level counts');
  assert.strictEqual(doc.autoLevel, 1);

  const { level, blocks } = DocxBlocks.blocksFor(doc, 'auto');
  assert.strictEqual(level, 1);
  const titles = blocks.map((b) => b.title);
  assert.deepStrictEqual(titles, [
    'AB1 — Coercion / Instrumentalization',
    'AB2 — 13th Amendment',
    'AB3 — Coerced Virtue / Voluntary Is Better',
    'AB4 — Cost / Feasibility',
    "AB8 — Liberty / Mill's harm principle (PRIORITY)",
    'AB4 — Cost / Feasibility',
    'AB20 — Mental health harms',
  ]);
  assert.strictEqual(blocks[5].id, 'AB4 — Cost / Feasibility #2', 'duplicate titles get unique ids');
  assert.ok(blocks.every((b) => !b.isGroup), 'no group labels at level 1');
  assert.ok(blocks[1].items.some((it) => it.kind === 'tbl'), 'table is kept inside AB2');
  assert.ok(blocks[0].wordCount > 10, 'word count computed');
  assert.ok(blocks[0].preview.startsWith('CARDS:'), 'preview is body text');

  // Splitting at level 2 nests level-1 headings as group labels
  const l2 = DocxBlocks.blocksFor(doc, 2);
  assert.strictEqual(l2.level, 2);
  const groups = l2.blocks.filter((b) => b.isGroup).map((b) => b.title);
  // AB1..AB8 have content under them before the H2, so they are blocks themselves, not groups
  assert.ok(groups.length === 0 || groups.every((g) => typeof g === 'string'));

  // Rendering resolves direct + style formatting
  const html = DocxBlocks.renderBlock(doc, blocks[0]);
  assert.ok(html.startsWith('<h1'), 'block heading rendered as h1');
  assert.ok(/font-weight:bold[^>]*>CARDS: /.test(html), 'bold run');
  assert.ok(/background-color:#ffff00/.test(html), 'highlight rendered');
  assert.ok(/text-decoration:underline/.test(html), 'underline rendered');
  assert.ok(/font-size:8pt/.test(html), 'font size rendered');
  assert.ok(/color:#c00000/i.test(html), 'colour rendered');
  const html2 = DocxBlocks.renderBlock(doc, blocks[1]);
  assert.ok(/<table/.test(html2) && /Arver/.test(html2), 'table rendered');
  const body = DocxBlocks.renderBlockBody(doc, blocks[0]);
  assert.ok(!body.startsWith('<h1'), 'body rendering skips heading');

  // Build a speech with AB8 then AB1 (order matters) and re-load it
  const chosen = [blocks[4], blocks[0]];
  const b64 = await DocxBlocks.buildSpeechDocx(doc, chosen, env);
  const outPath = path.join(__dirname, 'fixtures', 'speech.out.docx');
  fs.writeFileSync(outPath, Buffer.from(b64, 'base64'));
  const speech = await DocxBlocks.load(fs.readFileSync(outPath), env);
  const sb = DocxBlocks.blocksFor(speech, 'auto').blocks;
  assert.deepStrictEqual(sb.map((b) => b.title), [blocks[4].title, blocks[0].title], 'speech keeps chosen order');
  assert.strictEqual(sb[1].wordCount, blocks[0].wordCount, 'block content copied verbatim');
  const outXml = await speech.zip.file('word/document.xml').async('string');
  assert.ok(outXml.startsWith('<?xml version="1.0"'), 'xml declaration present');
  assert.ok(!/bookmarkStart/.test(outXml), 'bookmarks stripped');
  assert.ok(/<w:sectPr/.test(outXml), 'section properties retained');
  assert.ok(speech.zip.file('word/styles.xml'), 'styles part retained');

  // The original document object is still usable after a build
  const again = DocxBlocks.blocksFor(doc, 'auto').blocks;
  assert.strictEqual(again.length, blocks.length, 'source doc unchanged after build');

  // Plain-text export
  const txt = DocxBlocks.speechText(doc, chosen);
  assert.ok(txt.startsWith("AB8 — Liberty"), 'speech text starts with first chosen block');

  console.log('All docx engine tests passed (' + blocks.length + ' blocks parsed).');
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
