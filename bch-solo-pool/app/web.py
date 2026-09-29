import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

class Web:
    def __init__(self, cfg, pool, rpc, db):
        self.cfg, self.pool, self.rpc, self.db = cfg, pool, rpc, db

    def start(self):
        outer = self
        class Handler(BaseHTTPRequestHandler):
            def _send(self, code, body, ctype="application/json"):
                data = body.encode() if isinstance(body, str) else body
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/health":
                    self._send(200, '{"ok":true}')
                    return
                if self.path == "/api/status":
                    try:
                        info = outer.rpc.get_blockchain_info()
                        mining = outer.rpc.get_mining_info()
                    except Exception as e:
                        info, mining = {"error":str(e)}, {}
                    workers, blocks, events = outer.db.snapshot()
                    job = outer.pool.job
                    obj = {
                        "pool": outer.cfg.pool_id,
                        "height": job.height if job else None,
                        "job_id": job.job_id if job else None,
                        "network_target": f"{job.network_target:064x}" if job else None,
                        "miners_connected": len(outer.pool.miners),
                        "workers": workers,
                        "blocks": blocks,
                        "events": events,
                        "node": info,
                        "mining": mining,
                    }
                    self._send(200, json.dumps(obj))
                    return
                if self.path == "/" or self.path.startswith("/index.html"):
                    self._send(200, outer.html(), "text/html; charset=utf-8")
                    return
                self._send(404, "not found", "text/plain")

            def log_message(self, *_):
                return

        server = ThreadingHTTPServer((self.cfg.web_host, self.cfg.web_port), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    def html(self):
        return """<!doctype html>
<html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>BCH Solo Pool</title>
<style>
body{font-family:system-ui;margin:24px;background:#0d1117;color:#e6edf3} .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}
.card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px} table{width:100%;border-collapse:collapse;margin-top:18px}
td,th{padding:8px;border-bottom:1px solid #30363d;text-align:left} code{word-break:break-all}.muted{color:#8b949e}
</style></head><body><h1 id=title>BCH Solo Pool</h1>
<div class=grid><div class=card>Node <b id=node>...</b></div><div class=card>Height <b id=height>...</b></div>
<div class=card>Miners <b id=miners>...</b></div><div class=card>Job <b id=job>...</b></div></div>
<h2>Workers</h2><table><thead><tr><th>Worker</th><th>Shares</th><th>Rejected</th><th>Best diff</th><th>Last seen</th></tr></thead><tbody id=workers></tbody></table>
<h2>Block submissions</h2><table><thead><tr><th>Time</th><th>Height</th><th>Worker</th><th>Hash</th><th>Result</th></tr></thead><tbody id=blocks></tbody></table>
<script>
function esc(x){return String(x??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
async function go(){try{let x=await (await fetch('/api/status')).json();
title.textContent=x.pool; height.textContent=x.height??'-'; miners.textContent=x.miners_connected;
job.textContent=x.job_id??'-'; node.textContent=x.node?.blocks!=null?(x.node.initialblockdownload?'syncing':'synced'):'offline';
workers.innerHTML=x.workers.map(w=>`<tr><td>${esc(w.worker)}</td><td>${w.shares}</td><td>${w.rejected}</td><td>${Number(w.best_diff).toFixed(6)}</td><td>${new Date(w.last_seen*1000).toLocaleString()}</td></tr>`).join('');
blocks.innerHTML=x.blocks.map(b=>`<tr><td>${new Date(b.time*1000).toLocaleString()}</td><td>${b.height}</td><td>${esc(b.worker)}</td><td><code>${esc(b.hash)}</code></td><td>${esc(b.result)}</td></tr>`).join('');
}catch(e){node.textContent='offline'}} go(); setInterval(go,3000)
</script></body></html>"""
