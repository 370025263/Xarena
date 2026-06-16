// 证明机制：JS 正常加载，但屏蔽 WebSocket，看是否"加载完仍白屏"
const { chromium } = require('/home/admin/node_modules/playwright-core');
const CHROME = '/home/admin/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome';
const URL = process.argv[2] || 'https://algo.xskill.wiki';

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME, headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--ignore-certificate-errors'] });
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
  const page = await ctx.newPage();
  const cdp = await ctx.newCDPSession(page);
  await cdp.send('Network.enable');
  // 关键：只屏蔽 Streamlit 的 WebSocket，其它(JS/CSS/HTML)全放行
  await cdp.send('Network.setBlockedURLs', { urls: ['*_stcore/stream*'] });

  let jsDone = null;
  page.on('response', (r) => { if (/static\/js\/index\..*\.js$/.test(r.url())) jsDone = true; });

  await page.goto(URL, { waitUntil: 'commit', timeout: 60000 });
  await page.waitForTimeout(12000); // 等足够久，确认不是慢而是根本出不来

  const dom = await page.evaluate(() => {
    const r = document.getElementById('root');
    return { rootChildren: r ? r.childElementCount : -1,
             rootTextLen: r ? r.innerText.trim().length : -1,
             firstText: (r ? r.innerText.trim() : '').slice(0,120).replace(/\n+/g,' | ') };
  });
  await page.screenshot({ path: '/home/admin/leaderboard/run/wsblocked.png' }).catch(()=>{});
  console.log('JS 是否加载完:', !!jsDone);
  console.log('屏蔽 WS 后, 12s 时 DOM:', JSON.stringify(dom));
  console.log('截图 -> run/wsblocked.png');
  await browser.close();
})().catch(e => { console.error('FATAL', e); process.exit(1); });
