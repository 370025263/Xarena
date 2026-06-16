// 复现「JS 加载完，仍白屏」：盯 JS 加载完之后的阶段——WebSocket 帧、DOM 实际内容、截图
const { chromium } = require('/home/admin/node_modules/playwright-core');
const CHROME = '/home/admin/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome';
const URL = process.argv[2] || 'https://algo.xskill.wiki';

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME, headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--ignore-certificate-errors'] });
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
  const page = await ctx.newPage();

  const t0 = Date.now();
  let jsDone = null;
  page.on('response', (r) => { if (/static\/js\/index\..*\.js$/.test(r.url())) jsDone = Date.now()-t0; });

  // WebSocket 全生命周期
  const ws = { opened:0, closed:0, recv:0, sent:0, url:'', closeInfo:'' };
  page.on('websocket', (w) => {
    if (!/_stcore\/stream/.test(w.url())) return;
    ws.opened++; ws.url = w.url();
    w.on('framereceived', () => ws.recv++);
    w.on('framesent', () => ws.sent++);
    w.on('close', () => { ws.closed++; ws.closeInfo = 'closed@'+(Date.now()-t0)+'ms'; });
    w.on('socketerror', (e) => { ws.closeInfo = 'socketerror:'+e; });
  });

  const logs = [];
  page.on('console', (m) => logs.push(`[${m.type()}] ${m.text().slice(0,160)}`));
  page.on('pageerror', (e) => logs.push(`[pageerror] ${String(e).slice(0,200)}`));

  await page.goto(URL, { waitUntil: 'commit', timeout: 60000 });

  // 给足时间：JS 加载 + WS 连接 + 脚本首跑
  const snaps = [];
  for (const at of [2000, 5000, 9000, 15000]) {
    while (Date.now()-t0 < at) await page.waitForTimeout(150);
    const dom = await page.evaluate(() => {
      const r = document.getElementById('root');
      const overlay = document.querySelector('[data-testid="stStatusWidget"], .stConnectionStatus, [data-testid="stAppViewContainer"]');
      const txt = (r ? r.innerText : '').trim();
      // 找 Streamlit 的"连接中/请稍候/出错"提示
      const body = document.body ? document.body.innerText.trim() : '';
      return { rootChildren: r ? r.childElementCount : -1, rootTextLen: txt.length,
               firstText: txt.slice(0,140).replace(/\n+/g,' | '),
               hasAppContainer: !!document.querySelector('[data-testid="stAppViewContainer"]'),
               bodyLen: body.length };
    });
    snaps.push({ t: Math.round((Date.now()-t0)/1000)+'s', ...dom });
  }

  await page.screenshot({ path: '/home/admin/leaderboard/run/postload.png', fullPage: false }).catch(()=>{});

  console.log('JS 加载完成 @', jsDone, 'ms');
  console.log('WebSocket:', JSON.stringify(ws));
  console.log('DOM 随时间快照:');
  for (const s of snaps) console.log('  ', JSON.stringify(s));
  console.log('console/page 日志(' + logs.length + '):');
  for (const l of logs.slice(0,25)) console.log('   ', l);
  console.log('截图 -> run/postload.png');
  await browser.close();
})().catch(e => { console.error('FATAL', e); process.exit(1); });
