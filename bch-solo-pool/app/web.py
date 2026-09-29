import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from urllib.parse import urlparse

from .config import Config
from .node import BCHRPC
from .address import address_to_script


class Web:
    def __init__(self, cfg, pool, rpc, db):
        self.cfg, self.pool, self.rpc, self.db = cfg, pool, rpc, db
        self.server = None

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

            def _json_body(self):
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 16384:
                    raise ValueError("invalid request body")
                return json.loads(self.rfile.read(length))

            def do_GET(self):
                path = urlparse(self.path).path

                if path == "/health":
                    self._send(200, json.dumps({
                        "ok": True,
                        "configured": bool(outer.cfg and outer.cfg.configured)
                    }))
                    return

                if path == "/api/config":
                    self._send(200, json.dumps({
                        "configured": outer.cfg.configured,
                        "rpc_url": outer.cfg.rpc_url,
                        "rpc_user": outer.cfg.rpc_user,
                        "zmq_url": outer.cfg.zmq_url,
                        "payout_address": outer.cfg.payout_address,
                    }))
                    return

                if path == "/api/status":
                    if outer.pool is None or outer.rpc is None:
                        self._send(200, json.dumps({
                            "setup_required": True,
                            "pool": outer.cfg.pool_id,
                            "miners_connected": 0,
                            "workers": [],
                            "blocks": [],
                            "events": [],
                        }))
                        return
                    try:
                        info = outer.rpc.get_blockchain_info()
                        mining = outer.rpc.get_mining_info()
                    except Exception as exc:
                        info, mining = {"error": str(exc)}, {}
                    workers, blocks, events = outer.db.snapshot()
                    job = outer.pool.job
                    obj = {
                        "setup_required": False,
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

                if path in ("/", "/index.html"):
                    self._send(200, outer.html(), "text/html; charset=utf-8")
                    return

                self._send(404, "not found", "text/plain")

            def do_POST(self):
                path = urlparse(self.path).path
                if path != "/api/setup":
                    self._send(404, "not found", "text/plain")
                    return

                try:
                    body = self._json_body()
                    required = ("BCH_RPC_URL", "BCH_RPC_USER", "BCH_PAYOUT_ADDRESS")
                    if any(not str(body.get(k, "")).strip() for k in required):
                        raise ValueError("RPC URL, RPC username and BCH payout address are required")

                    address_to_script(str(body["BCH_PAYOUT_ADDRESS"]).strip())
                    test_rpc = BCHRPC(
                        str(body["BCH_RPC_URL"]).strip(),
                        str(body["BCH_RPC_USER"]).strip(),
                        str(body.get("BCH_RPC_PASSWORD", "")),
                    )
                    info = test_rpc.get_blockchain_info()
                    if not isinstance(info, dict):
                        raise ValueError("RPC returned an unexpected response")

                    outer.cfg.save_setup(body)
                    self._send(200, json.dumps({
                        "ok": True,
                        "message": "Configuration saved. The container will restart and start mining."
                    }))

                    def restart():
                        import time
                        time.sleep(0.5)
                        os._exit(0)

                    threading.Thread(target=restart, daemon=True).start()
                except Exception as exc:
                    self._send(400, json.dumps({"ok": False, "error": str(exc)}))

            def log_message(self, *_):
                return

        self.server = ThreadingHTTPServer(
            (self.cfg.web_host, self.cfg.web_port), Handler
        )
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self.server

    def html(self):
        return """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BCH Solo Pool</title>
<style>
body{font-family:system-ui,-apple-system,sans-serif;margin:0;background:#0d1117;color:#e6edf3}
main{max-width:1100px;margin:0 auto;padding:24px}.card{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:18px;margin-bottom:16px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}.muted{color:#8b949e}
.field{margin-bottom:12px}.field label{display:block;margin-bottom:6px}
input{display:block;width:100%;box-sizing:border-box;padding:12px;margin:0;background:#0d1117;color:#fff;border:1px solid #484f58;border-radius:7px;font-size:16px;outline:none;pointer-events:auto;user-select:text;-webkit-user-select:text}
input:focus{border-color:#58a6ff;box-shadow:0 0 0 2px rgba(88,166,255,.18)}
.row{display:flex;gap:8px;align-items:center}.row input{flex:1}
button{padding:10px 16px;border:0;border-radius:7px;cursor:pointer}.secondary{background:#30363d;color:#fff}.primary{background:#f0f6fc;color:#111}
.ok{color:#7ee787}.bad{color:#ff7b72}
table{width:100%;border-collapse:collapse}td,th{padding:8px;border-bottom:1px solid #30363d;text-align:left}code{word-break:break-all}
.hidden{display:none}.notice{padding:12px;border-radius:8px;background:#21262d}
</style>
</head>
<body><main>
<h1>BCH Solo Pool</h1>

<section id="setup" class="card hidden">
<h2 id="setupTitle">BCHN node setup</h2>
<p class="muted">All fields below are editable. Enter your standalone BCHN RPC credentials, optional ZMQ hashblock endpoint, and the BCH address that should receive the solo block reward.</p>

<div class="field">
<label for="rpc">RPC URL</label>
<input id="rpc" type="text" autocomplete="off" autocapitalize="none" spellcheck="false" value="http://10.21.21.50:8332/">
</div>

<div class="field">
<label for="user">RPC username</label>
<input id="user" type="text" autocomplete="off" autocapitalize="none" spellcheck="false" value="bchn">
</div>

<div class="field">
<label for="pass">RPC password</label>
<div class="row">
<input id="pass" type="password" autocomplete="new-password" autocapitalize="none" spellcheck="false">
<button type="button" class="secondary" onclick="togglePassword()">Show</button>
</div>
</div>

<div class="field">
<label for="zmq">ZMQ hashblock URL (optional)</label>
<input id="zmq" type="text" autocomplete="off" autocapitalize="none" spellcheck="false" value="tcp://10.21.21.50:28332">
</div>

<div class="field">
<label for="payout">BCH payout address <b class="bad">*</b></label>
<input id="payout" type="text" autocomplete="off" autocapitalize="none" spellcheck="false" placeholder="bitcoincash:q...">
</div>

<div class="row">
<button type="button" class="primary" onclick="saveSetup()">Save and start</button>
<button type="button" class="secondary" onclick="clearSetupFields()">Clear fields</button>
</div>
<p id="setupmsg" class="muted"></p>
</section>

<section id="dash" class="hidden">
<div class="card"><button type="button" onclick="showSetup()">Reconfigure node / payout</button></div>
<div class="grid">
<div class="card">Node <b id="node">...</b></div>
<div class="card">Height <b id="height">...</b></div>
<div class="card">Miners <b id="miners">...</b></div>
<div class="card">Job <b id="job">...</b></div>
</div>
<div class="card"><b>Stratum:</b> <code id="stratum"></code><br><span class="muted">Use the payout address as the worker username, optionally followed by .worker1. Password can be x.</span></div>

<div class="card"><h2>Workers</h2>
<table><thead><tr><th>Worker</th><th>Shares</th><th>Rejected</th><th>Best diff</th><th>Last seen</th></tr></thead>
<tbody id="workers"></tbody></table></div>

<div class="card"><h2>Block submissions</h2>
<table><thead><tr><th>Time</th><th>Height</th><th>Worker</th><th>Hash</th><th>Result</th></tr></thead>
<tbody id="blocks"></tbody></table></div>
</section>
</main>
<script>
const $=id=>document.getElementById(id);
function esc(x){return String(x??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}

function togglePassword(){
  const p=$('pass');
  const b=p.parentElement.querySelector('button');
  if(p.type==='password'){p.type='text';b.textContent='Hide'}
  else{p.type='password';b.textContent='Show'}
}

function clearSetupFields(){
  $('rpc').value='';
  $('user').value='';
  $('pass').value='';
  $('zmq').value='';
  $('payout').value='';
  $('setupmsg').textContent='Fields cleared. Enter your BCHN connection details.';
  $('rpc').focus();
}

async function setupState(){
  let c=await (await fetch('/api/config',{cache:'no-store'})).json();
  if(!c.configured){
    if($('setup').classList.contains('hidden')) showSetup(false);
    return false;
  }
  $('setup').classList.add('hidden');$('dash').classList.remove('hidden');
  return true;
}

async function showSetup(loadSaved=true){
  $('setup').classList.remove('hidden');$('dash').classList.add('hidden');
  if(!loadSaved){
    $('rpc').focus();
    return;
  }
  try{
    let c=await (await fetch('/api/config',{cache:'no-store'})).json();
    $('rpc').value=c.rpc_url||'http://10.21.21.50:8332/';
    $('user').value=c.rpc_user||'';
    $('zmq').value=c.zmq_url||'tcp://10.21.21.50:28332';
    $('payout').value=c.payout_address||'';
    $('pass').value='';
    $('setupmsg').textContent='Edit any field and enter the RPC password before saving.';
    $('rpc').focus();
  }catch(e){
    $('setupmsg').textContent='Could not load saved settings. You can enter them manually.';
  }
}

async function saveSetup(){
  const rpc=$('rpc').value.trim();
  const user=$('user').value.trim();
  const payout=$('payout').value.trim();
  if(!rpc||!user||!payout){
    $('setupmsg').textContent='Error: RPC URL, RPC username and BCH payout address are required.';
    if(!rpc)$('rpc').focus();else if(!user)$('user').focus();else $('payout').focus();
    return;
  }

  $('setupmsg').textContent='Testing RPC and saving...';
  const body={
    BCH_RPC_URL:rpc,
    BCH_RPC_USER:user,
    BCH_RPC_PASSWORD:$('pass').value,
    BCH_ZMQ_URL:$('zmq').value.trim(),
    BCH_PAYOUT_ADDRESS:payout
  };
  try{
    const r=await fetch('/api/setup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const x=await r.json();
    $('setupmsg').textContent=x.ok?x.message:('Error: '+x.error);
  }catch(e){$('setupmsg').textContent='Error: '+e}
}

async function go(){
 try{
  if(!(await setupState())) return;
  let x=await (await fetch('/api/status',{cache:'no-store'})).json();
  $('height').textContent=x.height??'-';$('miners').textContent=x.miners_connected;
  $('job').textContent=x.job_id??'-';
  $('node').textContent=x.node?.blocks!=null?(x.node.initialblockdownload?'syncing':'synced'):'offline';
  $('stratum').textContent='stratum+tcp://'+location.hostname+':3334';
  $('workers').innerHTML=(x.workers||[]).map(w=>'<tr><td>'+esc(w.worker)+'</td><td>'+w.shares+'</td><td>'+w.rejected+'</td><td>'+Number(w.best_diff).toFixed(6)+'</td><td>'+new Date(w.last_seen*1000).toLocaleString()+'</td></tr>').join('');
  $('blocks').innerHTML=(x.blocks||[]).map(b=>'<tr><td>'+new Date(b.time*1000).toLocaleString()+'</td><td>'+b.height+'</td><td>'+esc(b.worker)+'</td><td><code>'+esc(b.hash)+'</code></td><td>'+esc(b.result)+'</td></tr>').join('');
 }catch(e){$('node').textContent='offline'}
}
go();setInterval(go,3000);
</script>
</body></html>"""
