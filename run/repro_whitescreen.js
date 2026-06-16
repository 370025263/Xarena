// 复现 algo.xskill.wiki 白屏：用真实 Chromium 测「#root 为空(白屏)」的持续时长
// 对比：A 不限速(本数据中心)  B 限速模拟跨境慢链路
const { chromium } = require('/home/admin/node_modules/playwright-core');
const CHROME = '/home/admin/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome';
const URL = process.argv[2] || 'https://algo.xskill.wiki';

async function run(label, throttle) {
  const browser = await chromium.launch({
    executablePath: CHROME, headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--ignore-certificate-errors'],
  });
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
  const page = await ctx.newPage();
  const cdp = await ctx.newCDPSession(page);
  await cdp.send('Network.enable');
  if (throttle) {
    await cdp.send('Network.emulateNetworkConditions', {
      offline: false,
      downloadThroughput: throttle.bps / 8,   // bytes/s
      uploadThroughput: throttle.bps / 8,
      latency: throttle.latencyMs,
    });
  }

  const net = [];
  let wsOpened = false, wsUrl = '';
  page.on('response', async (r) => {
    const u = r.url();
    if (/\/static\/js\/index\..*\.js$/.test(u) || /\/static\/css\/index\..*\.css$/.test(u)) {
      const h = r.headers();
      net.push({ url: u.split('/').pop(), status: r.status(),
        enc: h['content-encoding'] || 'NONE', len: h['content-length'] || '?' });
    }
  });
  page.on('websocket', (ws) => { if (/_stcore\/stream/.test(ws.url())) { wsOpened = true; wsUrl = ws.url(); } });
  const errs = [];
  page.on('console', (m) => { if (m.type() === 'error') errs.push(m.text().slice(0, 120)); });

  const t0 = Date.now();
  await page.goto(URL, { waitUntil: 'commit', timeout: 120000 });
  const tNav = Date.now() - t0;

  // 白屏结束 = #root 第一次出现子节点(React/Streamlit 挂载并首绘)
  let tFirstPaint = null;
  try {
    await page.waitForFunction(() => {
      const r = document.getElementById('root');
      return r && r.childElementCount > 0 && r.innerText.trim().length > 0;
    }, { timeout: 110000, polling: 100 });
    tFirstPaint = Date.now() - t0;
  } catch (e) { tFirstPaint = 'TIMEOUT(>110s)'; }

  console.log(`\n===== ${label} =====`);
  if (throttle) console.log(`  限速: ${(throttle.bps/1e6).toFixed(2)} Mbps down, +${throttle.latencyMs}ms 延迟`);
  console.log(`  导航返回(HTML commit): ${tNav} ms`);
  console.log(`  静态资源:`);
  for (const n of net) console.log(`    ${n.url}  status=${n.status}  content-encoding=${n.enc}  declared-len=${n.len}`);
  console.log(`  WebSocket /_stcore/stream 建立: ${wsOpened ? 'YES' : 'NO'}`);
  console.log(`  >>> 白屏持续(到 #root 首次出内容): ${tFirstPaint} ${typeof tFirstPaint==='number'?'ms':''}`);
  console.log(`  控制台 error: ${errs.length ? errs.join(' | ') : '无'}`);
  await browser.close();
  return { label, tFirstPaint };
}

(async () => {
  await run('A. 不限速(本数据中心，相当于服务器自测)', null);
  await run('B. 限速 4Mbps +120ms(模拟较好跨境宽带)', { bps: 4e6, latencyMs: 120 });
  await run('C. 限速 1.5Mbps +200ms(模拟拥塞跨境/移动网)', { bps: 1.5e6, latencyMs: 200 });
})().catch(e => { console.error('FATAL', e); process.exit(1); });
