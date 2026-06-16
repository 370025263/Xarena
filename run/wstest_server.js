// 极简 WebSocket 测试站：GET / 返回测试页，/ws 是 echo。用于经 Cloudflare 验证公司网能否过 wss。
const http = require('http');
const { WebSocketServer } = require('/home/admin/node_modules/ws');

const PORT = 8123;
const PAGE = `<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WS over Cloudflare 测试</title>
<style>body{font-family:system-ui;max-width:680px;margin:40px auto;padding:0 16px}
#s{font-size:22px;font-weight:700;padding:16px;border-radius:10px;margin:16px 0}
.ok{background:#dcfce7;color:#166534}.bad{background:#fee2e2;color:#991b1b}.wait{background:#fef9c3;color:#854d0e}
pre{background:#f3f4f6;padding:12px;border-radius:8px;white-space:pre-wrap}</style></head>
<body><h2>WebSocket 经 Cloudflare 连通性测试</h2>
<div id="s" class="wait">⏳ 正在尝试建立 WebSocket…</div>
<pre id="log"></pre>
<script>
const s=document.getElementById('s'),L=document.getElementById('log');
const log=(m)=>{L.textContent+=m+"\\n"};
const url=(location.protocol==='https:'?'wss://':'ws://')+location.host+'/ws';
log('页面来源: '+location.href);
log('尝试连接: '+url);
const t0=Date.now();
try{
  const ws=new WebSocket(url);
  const timer=setTimeout(()=>{if(ws.readyState!==1){s.className='bad';s.textContent='❌ 超时：WebSocket 10 秒内未连上（很可能被掐）';try{ws.close()}catch(e){}}},10000);
  ws.onopen=()=>{clearTimeout(timer);s.className='ok';s.textContent='✅ WebSocket 已连上 Cloudflare！耗时 '+(Date.now()-t0)+'ms';log('onopen → 发送 ping');ws.send('ping '+Date.now())};
  ws.onmessage=(e)=>{log('收到回显: '+e.data+'  ←← 双向通了，wss 完全 OK')};
  ws.onerror=()=>{clearTimeout(timer);s.className='bad';s.textContent='❌ WebSocket 失败：你的网络/代理把 wss 掐了';log('onerror')};
  ws.onclose=(e)=>{log('onclose code='+e.code+' reason='+(e.reason||'(无)'))};
}catch(e){s.className='bad';s.textContent='❌ 异常: '+e;}
</script></body></html>`;

const server = http.createServer((req, res) => {
  if (req.url === '/' || req.url.startsWith('/?')) { res.writeHead(200, {'content-type':'text/html; charset=utf-8'}); res.end(PAGE); }
  else if (req.url === '/healthz') { res.writeHead(200); res.end('ok'); }
  else { res.writeHead(404); res.end('nf'); }
});
const wss = new WebSocketServer({ server, path: '/ws' });
wss.on('connection', (ws) => { ws.on('message', (m) => ws.send('echo: ' + m.toString())); ws.send('hello-from-server'); });
server.listen(PORT, '127.0.0.1', () => console.log('wstest on 127.0.0.1:'+PORT));
