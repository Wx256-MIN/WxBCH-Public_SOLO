import asyncio
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
                    info, mining = {}, {}
                    if outer.rpc is not None:
                        try:
                            info = outer.rpc.get_blockchain_info()
                            mining = outer.rpc.get_mining_info()
                        except Exception as exc:
                            info = {"error": str(exc)}
                    workers, blocks, events = outer.db.snapshot()
                    job = outer.pool.job if outer.pool is not None else None
                    obj = {
                        "setup_required": not outer.cfg.configured,
                        "pool": outer.cfg.pool_id,
                        "setup_required": False,
                        "pool": outer.cfg.pool_id,
                        "height": job.height if job else None,
                        "job_id": job.job_id if job else None,
                        "network_target": f"{job.network_target:064x}" if job else None,
                        "job_created": job.created if job else None,
                        "tx_count": len(job.tx_hex) if job else 0,
                        "coinbase_value": job.coinbase_value if job else None,
                        "miners_connected": len(outer.pool.miners) if outer.pool is not None else 0,
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

                if path == "/api/action":
                    try:
                        body = self._json_body()
                        action = str(body.get("action", "")).strip()
                        if action == "refresh_job":
                            if outer.pool is None:
                                raise ValueError("Pool is not running")
                            if not getattr(outer, "loop", None):
                                raise ValueError("Pool event loop is not ready")
                            future = asyncio.run_coroutine_threadsafe(
                                outer.pool.refresh_job("dashboard"),
                                outer.loop
                            )
                            future.result(timeout=15)
                            self._send(200, json.dumps({"ok": True, "message": "New mining job created"}))
                            return
                        raise ValueError("Unknown dashboard action")
                    except Exception as exc:
                        self._send(400, json.dumps({"ok": False, "error": str(exc)}))
                    return

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
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>BCH Solo Pool — Dashboard</title>
<style>
:root{--bg:#090d12;--panel:#111821;--panel2:#151e28;--line:#263241;--text:#edf3f8;--muted:#8e9aaa;--accent:#38d39f;--accent2:#62a8ff;--danger:#ff6b78;--warn:#f5c451;--shadow:0 18px 50px rgba(0,0,0,.28)}
*{box-sizing:border-box}
body{margin:0;background:radial-gradient(circle at 20% -10%,#173049 0,transparent 34%),var(--bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:1180px;margin:auto;padding:24px 18px 70px}
button{border:0;cursor:pointer;font:inherit}
.top{display:flex;justify-content:space-between;gap:16px;align-items:center;margin-bottom:22px}
.brand{display:flex;gap:13px;align-items:center}.logo{width:46px;height:46px;border-radius:14px;display:grid;place-items:center;background:linear-gradient(135deg,#16b981,#1976d2);font-weight:900;font-size:21px;box-shadow:0 10px 30px rgba(56,211,159,.18)}
h1{font-size:28px;margin:0}.sub{color:var(--muted);font-size:13px;margin-top:3px}
.actions{display:flex;gap:8px;flex-wrap:wrap}.btn{padding:10px 13px;border-radius:10px;background:#1c2631;color:#eaf0f5;border:1px solid #2b3948}.btn.primary{background:var(--accent);color:#07130f;border-color:transparent;font-weight:800}.btn:hover{filter:brightness(1.08)}
.hero{background:linear-gradient(135deg,rgba(26,38,51,.96),rgba(14,22,31,.96));border:1px solid var(--line);border-radius:18px;padding:22px;box-shadow:var(--shadow);margin-bottom:14px}
.heroRow{display:flex;justify-content:space-between;align-items:flex-start;gap:18px}.eyebrow{font-size:11px;letter-spacing:.13em;text-transform:uppercase;color:var(--muted);font-weight:800}.endpoint{font-size:20px;font-weight:800;margin:7px 0 6px;word-break:break-all}.hint{color:var(--muted);font-size:13px}.status{display:inline-flex;align-items:center;gap:8px;padding:8px 11px;border-radius:999px;background:#10271f;color:#8ff0c6;font-size:12px;font-weight:800}.dot{width:8px;height:8px;border-radius:50%;background:var(--accent);box-shadow:0 0 14px var(--accent)}
.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:14px 0}.metric{background:rgba(17,24,33,.92);border:1px solid var(--line);border-radius:15px;padding:17px}.metric .label{font-size:12px;color:var(--muted);margin-bottom:8px}.metric .value{font-size:24px;font-weight:850;letter-spacing:-.02em}.metric .small{font-size:11px;color:var(--muted);margin-top:5px}
.grid2{display:grid;grid-template-columns:1.35fr .65fr;gap:14px}.card{background:rgba(17,24,33,.94);border:1px solid var(--line);border-radius:16px;padding:18px;margin-bottom:14px}.cardHead{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:14px}.card h2{font-size:17px;margin:0}.muted{color:var(--muted)}
.kv{display:grid;grid-template-columns:1fr 1fr;gap:10px}.kv div{background:#0d141c;border:1px solid #202b38;border-radius:11px;padding:12px}.kv span{display:block;color:var(--muted);font-size:11px;margin-bottom:4px}.kv b{font-size:14px;word-break:break-word}
.tableWrap{overflow:auto}table{width:100%;border-collapse:collapse;min-width:650px}th,td{padding:11px 8px;border-bottom:1px solid #202a35;text-align:left;font-size:12px}th{color:var(--muted);font-weight:700}td{color:#dce5ec}.badge{display:inline-block;padding:4px 8px;border-radius:999px;background:#182c24;color:#7fe9bc;font-size:10px;font-weight:800}.badge.bad{background:#351a20;color:#ff9aa3}
.event{display:flex;gap:10px;padding:10px 0;border-bottom:1px solid #202a35}.event:last-child{border-bottom:0}.eventIcon{width:30px;height:30px;border-radius:9px;background:#182532;display:grid;place-items:center}.eventText{flex:1;font-size:12px}.eventTime{color:var(--muted);font-size:10px;margin-top:3px}
.controls{display:flex;gap:8px;flex-wrap:wrap}.toggle{display:inline-flex;align-items:center;gap:7px;background:#101820;border:1px solid #263241;border-radius:10px;padding:9px 11px;color:#cbd6df;font-size:12px}.toggle input{accent-color:var(--accent)}
.setup{display:none}.setup.visible{display:block}
.notice{padding:12px;border-radius:11px;background:#111a24;border:1px solid #263241;color:var(--muted)}
.field{margin-bottom:12px}.field label{display:block;margin-bottom:6px;font-size:12px}.field input{width:100%;padding:11px;background:#0b1118;color:#fff;border:1px solid #344354;border-radius:9px;font-size:14px;outline:none}.field input:focus{border-color:var(--accent2);box-shadow:0 0 0 2px rgba(98,168,255,.15)}
.row{display:flex;gap:8px}.row input{flex:1}
.hidden{display:none!important}
@media(max-width:900px){.metrics{grid-template-columns:repeat(2,1fr)}.grid2{grid-template-columns:1fr}.heroRow{flex-direction:column}.actions{width:100%}}
@media(max-width:520px){main{padding:16px 12px 50px}.top{align-items:flex-start}.top .actions{display:none}h1{font-size:23px}.metrics{gap:8px}.metric{padding:13px}.metric .value{font-size:20px}.kv{grid-template-columns:1fr}.hero{padding:17px}.endpoint{font-size:16px}}
</style>
</head>
<body><main>
<header class="top">
  <div class="brand"><div class="logo">₿</div><div><h1>BCH Solo Pool</h1><div class="sub">Self-hosted Bitcoin Cash solo mining</div></div></div>
  <div class="actions"><button class="btn" onclick="refreshData()">Refresh</button><button class="btn primary" onclick="newJob()">New job</button></div>
</header>

<section id="setup" class="card setup">
  <div class="cardHead"><h2>BCHN node setup</h2><button class="btn" onclick="hideSetup()">Close</button></div>
  <p class="muted">BCHN RPC and ZMQ are connected automatically from the Umbrel Bitcoin Cash Node app. You only need to enter your solo BCH payout address.</p>
  <div class="field"><label>RPC URL</label><input id="rpc" type="text" autocomplete="off" autocapitalize="none" spellcheck="false"></div>
  <div class="field"><label>RPC username</label><input id="user" type="text" autocomplete="off" autocapitalize="none" spellcheck="false"></div>
  <div class="field"><label>RPC password <span class="muted">(leave blank to use Umbrel BCHN password)</span></label><input id="pass" type="password" autocomplete="new-password" placeholder="Automatic from Umbrel BCHN"></div>
  <div class="field"><label>ZMQ hashblock URL</label><input id="zmq" type="text" autocomplete="off" autocapitalize="none" spellcheck="false"></div>
  <div class="field"><label>BCH payout address</label><input id="payout" type="text" autocomplete="off" autocapitalize="none" spellcheck="false"></div>
  <div class="actions"><button class="btn primary" onclick="saveSetup()">Save & restart</button><button class="btn" onclick="clearSetupFields()">Clear</button></div>
  <p id="setupmsg" class="muted"></p>
</section>

<section id="dash">
  <section class="hero">
    <div class="heroRow">
      <div><div class="eyebrow">Stratum endpoint</div><div class="endpoint" id="stratum">stratum+tcp://-:3334</div><div class="hint">Worker username: your BCH payout address, optionally followed by <b>.worker1</b>. Password: <b>x</b>.</div></div>
      <div class="status"><span class="dot"></span><span id="statusText">Connecting</span></div>
    </div>
    <div class="actions" style="margin-top:17px"><button class="btn primary" onclick="copyStratum()">Copy Stratum</button><button class="btn" onclick="showSetup()">Node / payout settings</button></div>
  </section>

  <section class="metrics">
    <div class="metric"><div class="label">NODE</div><div class="value" id="node">—</div><div class="small" id="chain">Checking BCHN…</div></div>
    <div class="metric"><div class="label">BLOCK HEIGHT</div><div class="value" id="height">—</div><div class="small" id="jobAge">Job age —</div></div>
    <div class="metric"><div class="label">CONNECTED MINERS</div><div class="value" id="miners">0</div><div class="small">Live Stratum connections</div></div>
    <div class="metric"><div class="label">CURRENT JOB</div><div class="value" id="job">—</div><div class="small" id="txs">— transactions</div></div>
  </section>

  <div class="grid2">
    <div>
      <section class="card">
        <div class="cardHead"><h2>Mining overview</h2><span class="badge" id="syncBadge">LIVE</span></div>
        <div class="kv">
          <div><span>Network difficulty</span><b id="difficulty">—</b></div>
          <div><span>Network hashrate</span><b id="networkHashrate">—</b></div>
          <div><span>Pool hashrate</span><b id="poolHashrate">—</b></div>
          <div><span>Best share difficulty</span><b id="bestDiff">—</b></div>
          <div><span>Accepted shares</span><b id="accepted">0</b></div>
          <div><span>Rejected shares</span><b id="rejected">0 (0%)</b></div>
          <div><span>Block reward</span><b id="reward">—</b></div>
          <div><span>Target</span><b id="target">—</b></div>
        </div>
      </section>

      <section class="card">
        <div class="cardHead"><h2>Workers</h2><span class="muted" id="workerCount">0 workers</span></div>
        <div class="tableWrap"><table><thead><tr><th>Worker</th><th>Shares</th><th>Rejected</th><th>Best diff</th><th>Last seen</th></tr></thead><tbody id="workers"></tbody></table></div>
      </section>

      <section class="card">
        <div class="cardHead"><h2>Block submissions</h2><span class="muted">Latest 20</span></div>
        <div class="tableWrap"><table><thead><tr><th>Time</th><th>Height</th><th>Worker</th><th>Hash</th><th>Result</th></tr></thead><tbody id="blocks"></tbody></table></div>
      </section>
    </div>

    <div>
      <section class="card">
        <div class="cardHead"><h2>Controls</h2></div>
        <div class="controls">
          <button class="btn primary" onclick="newJob()">⚡ New job</button>
          <button class="btn" onclick="refreshData()">↻ Refresh</button>
          <label class="toggle"><input id="auto" type="checkbox" checked onchange="setAuto()"> Auto-refresh</label>
          <label class="toggle"><input id="compact" type="checkbox" onchange="document.body.classList.toggle('compact')"> Compact view</label>
        </div>
        <div class="notice" style="margin-top:12px">The pool creates a new mining job only when BCHN reports a new block through ZMQ or when you press New job.</div>
      </section>

      <section class="card">
        <div class="cardHead"><h2>Recent events</h2><span class="muted">Live</span></div>
        <div id="events"><div class="muted">No events yet.</div></div>
      </section>

      <section class="card">
        <div class="cardHead"><h2>Node details</h2></div>
        <div class="kv">
          <div><span>Chain</span><b id="nodeChain">—</b></div>
          <div><span>Verification progress</span><b id="progress">—</b></div>
          <div><span>Headers</span><b id="headers">—</b></div>
          <div><span>Connections</span><b id="connections">—</b></div>
          <div><span>Current job height</span><b id="jobHeight">—</b></div>
          <div><span>Pool status</span><b id="poolStatus">—</b></div>
        </div>
      </section>
    </div>
  </div>
</section>
</main>
<script>
const $=id=>document.getElementById(id);
let timer=null,lastData=null;
function esc(x){return String(x??'').replace(/[&<>\"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[m]))}
function fmtHash(n){if(n==null||!isFinite(Number(n)))return '—';n=Number(n);if(n>=1e18)return (n/1e18).toFixed(2)+' EH/s';if(n>=1e15)return (n/1e15).toFixed(2)+' PH/s';if(n>=1e12)return (n/1e12).toFixed(2)+' TH/s';if(n>=1e9)return (n/1e9).toFixed(2)+' GH/s';if(n>=1e6)return (n/1e6).toFixed(2)+' MH/s';return Math.round(n)+' H/s'}
function fmtNum(n){if(n==null||!isFinite(Number(n)))return '—';return Number(n).toLocaleString()}
function ago(t){if(!t)return '—';const s=Math.max(0,Date.now()/1000-Number(t));if(s<60)return Math.round(s)+'s ago';if(s<3600)return Math.floor(s/60)+'m ago';return Math.floor(s/3600)+'h ago'}
function fmtTime(t){return t?new Date(Number(t)*1000).toLocaleString():'—'}
async function api(url,opt){const r=await fetch(url,Object.assign({cache:'no-store'},opt||{}));return await r.json()}
async function setupState(){
  try{const c=await api('/api/config');if(!c.configured){await showSetup(true)}else{$('setup').classList.remove('visible')}$('dash').classList.remove('hidden');return true}catch(e){$('dash').classList.remove('hidden');return true}
}
async function showSetup(loadSaved=true){
  $('setup').classList.add('visible');$('dash').classList.add('hidden');
  if(!loadSaved)return;
  try{const c=await api('/api/config');$('rpc').value=c.rpc_url||'';$('user').value=c.rpc_user||'';$('zmq').value=c.zmq_url||'';$('payout').value=c.payout_address||'';$('pass').value='';}catch(e){}
}
function hideSetup(){$('setup').classList.remove('visible');$('dash').classList.remove('hidden')}
function clearSetupFields(){['rpc','user','pass','zmq','payout'].forEach(x=>$(x).value='');$('rpc').focus()}
async function saveSetup(){
  const body={BCH_RPC_URL:$('rpc').value.trim(),BCH_RPC_USER:$('user').value.trim(),BCH_RPC_PASSWORD:$('pass').value,BCH_ZMQ_URL:$('zmq').value.trim(),BCH_PAYOUT_ADDRESS:$('payout').value.trim()};
  $('setupmsg').textContent='Testing RPC and saving…';
  try{const x=await api('/api/setup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});$('setupmsg').textContent=x.ok?x.message:'Error: '+x.error}catch(e){$('setupmsg').textContent='Error: '+e}
}
async function refreshData(){
  try{
    await setupState();
    const x=await api('/api/status');lastData=x;
    $('statusText').textContent=x.node?.blocks!=null?(x.node.initialblockdownload?'Node syncing':'Pool online'):'Node offline';
    $('node').textContent=x.node?.blocks!=null?(x.node.initialblockdownload?'Syncing':'Online'):'Offline';
    $('chain').textContent=x.node?.chain||'BCHN';
    $('height').textContent=fmtNum(x.height);
    $('miners').textContent=fmtNum(x.miners_connected);
    $('job').textContent=x.job_id||'—';
    $('jobAge').textContent=x.job_created?'Job '+ago(x.job_created):'Job age unavailable';
    $('txs').textContent=fmtNum(x.tx_count)+' transactions';
    $('stratum').textContent='stratum+tcp://'+location.hostname+':3334';
    const m=x.mining||{}, workers=x.workers||[];
    $('difficulty').textContent=fmtNum(m.difficulty);
    $('networkHashrate').textContent=fmtHash(m.networkhashps);
    $('poolHashrate').textContent=fmtHash(workers.reduce((a,w)=>a+Number(w.hashrate||0),0));
    $('bestDiff').textContent=workers.length?Math.max(...workers.map(w=>Number(w.best_diff||0))).toFixed(6):'—';
    const accepted=workers.reduce((a,w)=>a+Number(w.shares||0),0),rejected=workers.reduce((a,w)=>a+Number(w.rejected||0),0),total=accepted+rejected;
    $('accepted').textContent=fmtNum(accepted);$('rejected').textContent=fmtNum(rejected)+' ('+(total?(rejected/total*100).toFixed(2):'0')+'%)';
    $('reward').textContent=x.coinbase_value?((Number(x.coinbase_value)/1e8).toFixed(8)+' BCH'):'—';
    $('target').textContent=x.network_target?'0x'+x.network_target.slice(0,18)+'…':'—';
    $('workerCount').textContent=workers.length+' worker'+(workers.length===1?'':'s');
    $('workers').innerHTML=workers.length?workers.map(w=>'<tr><td><b>'+esc(w.worker)+'</b></td><td>'+fmtNum(w.shares)+'</td><td>'+fmtNum(w.rejected)+'</td><td>'+Number(w.best_diff||0).toFixed(6)+'</td><td>'+ago(w.last_seen)+'</td></tr>').join(''):'<tr><td colspan="5" class="muted">No miners connected yet.</td></tr>';
    $('blocks').innerHTML=(x.blocks||[]).length?(x.blocks||[]).map(b=>'<tr><td>'+fmtTime(b.time)+'</td><td>'+esc(b.height)+'</td><td>'+esc(b.worker)+'</td><td><code>'+esc((b.hash||'').slice(0,18))+'…</code></td><td><span class="badge '+(String(b.result).toLowerCase()==='none'?'':'bad')+'">'+esc(b.result||'submitted')+'</span></td></tr>').join(''):'<tr><td colspan="5" class="muted">No block submissions yet.</td></tr>';
    $('events').innerHTML=(x.events||[]).slice(0,12).map(e=>'<div class="event"><div class="eventIcon">•</div><div class="eventText"><b>'+esc(e.kind)+'</b> '+esc(e.detail||'')+'<div class="eventTime">'+esc(e.worker||'pool')+' · '+fmtTime(e.time)+'</div></div></div>').join('')||'<div class="muted">No events yet.</div>';
    $('nodeChain').textContent=x.node?.chain||'—';$('progress').textContent=x.node?.verificationprogress!=null?(Number(x.node.verificationprogress)*100).toFixed(2)+'%':'—';$('headers').textContent=fmtNum(x.node?.headers);$('connections').textContent=fmtNum(x.node?.connections);$('jobHeight').textContent=fmtNum(x.height);$('poolStatus').textContent=x.node?.blocks!=null?'Running':'Node unavailable';
    $('syncBadge').textContent=x.node?.initialblockdownload?'SYNCING':'LIVE';
  }catch(e){$('statusText').textContent='Connection error';$('node').textContent='Offline'}
}
async function newJob(){
  try{const x=await api('/api/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'refresh_job'})});if(!x.ok)throw Error(x.error||'Failed');await refreshData()}catch(e){alert('Could not refresh job: '+e.message)}
}
async function copyStratum(){try{await navigator.clipboard.writeText('stratum+tcp://'+location.hostname+':3334')}catch(e){prompt('Copy Stratum endpoint:','stratum+tcp://'+location.hostname+':3334')}}
function setAuto(){if(timer)clearInterval(timer);timer=$('auto').checked?setInterval(refreshData,3000):null}
refreshData();setAuto();
</script>
</body></html>"""
