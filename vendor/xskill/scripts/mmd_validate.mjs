// Validate a Mermaid file using the real mermaid parser in headless Chromium,
// exactly as mermaid.live does. Exits non-zero on parse/render error.
import { chromium } from 'playwright-core';
import { readFileSync } from 'node:fs';

const file = process.argv[2];
const shotPath = process.argv[3]; // optional screenshot output
const code = readFileSync(file, 'utf8');

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1200 } });
const consoleErrors = [];
page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text()); });
page.on('pageerror', e => consoleErrors.push('pageerror: ' + e.message));

await page.setContent(`<!doctype html><html><body><div id="out"></div></body></html>`);

const result = await page.evaluate(async (graph) => {
  const mod = await import('https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs');
  const mermaid = mod.default;
  mermaid.initialize({ startOnLoad: false, securityLevel: 'loose' });
  try {
    await mermaid.parse(graph);            // throws on syntax error
    const { svg } = await mermaid.render('graphDiv', graph); // full render
    document.getElementById('out').innerHTML = svg;
    return { ok: true, version: mermaid.version ? mermaid.version() : 'n/a', svgLen: svg.length };
  } catch (e) {
    return { ok: false, error: (e && (e.str || e.message)) || String(e), hash: e && e.hash };
  }
}, code);

if (result.ok && shotPath) {
  const el = await page.$('#out svg');
  if (el) await el.screenshot({ path: shotPath });
}

await browser.close();

if (result.ok) {
  console.log(`OK  mermaid@${result.version}  svg ${result.svgLen} bytes`);
  if (consoleErrors.length) console.log('console errors:\n' + consoleErrors.join('\n'));
  process.exit(0);
} else {
  console.log('PARSE/RENDER ERROR:');
  console.log(result.error);
  if (result.hash) console.log('hash: ' + JSON.stringify(result.hash));
  process.exit(1);
}
