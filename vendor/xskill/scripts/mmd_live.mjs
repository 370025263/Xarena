// Build a mermaid.live URL for a .mmd file, then load mermaid.live and
// screenshot its own preview to prove the live site renders it.
import { chromium } from 'playwright-core';
import { readFileSync } from 'node:fs';
import { deflateSync } from 'node:zlib';

const file = process.argv[2];
const shot = process.argv[3];
const code = readFileSync(file, 'utf8');

const state = {
  code,
  mermaid: JSON.stringify({ theme: 'default' }, null, 2),
  autoSync: true,
  rough: false,
  updateDiagram: true,
  panZoom: true,
};
// mermaid.live serializeState: pako.deflate(JSON) -> base64url, prefixed "pako:"
const payload = deflateSync(Buffer.from(JSON.stringify(state)), { level: 9 })
  .toString('base64url');
const url = `https://mermaid.live/edit#pako:${payload}`;
console.log('URL_START');
console.log(url);
console.log('URL_END');

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1700, height: 1300 } });
const errs = [];
page.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
await page.goto(url, { waitUntil: 'networkidle', timeout: 60000 });
// mermaid.live renders the graph into #graph-div inside the preview pane
await page.waitForSelector('#graph-div svg, .mermaid svg, svg#graph-div', { timeout: 30000 });
// is there an error overlay?
const errBox = await page.$('text=Syntax error');
console.log('live_syntax_error_visible:', !!errBox);
if (shot) {
  const svg = await page.$('#graph-div, .mermaid svg, svg#graph-div');
  if (svg) await svg.screenshot({ path: shot });
  else await page.screenshot({ path: shot, fullPage: false });
}
await browser.close();
if (errs.length) console.log('console errors:\n' + errs.join('\n'));
