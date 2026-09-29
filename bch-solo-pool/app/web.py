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
<meta name="theme-color" content="#07111a">
<title>BCH Solo Pool — Command Center</title>
<style>
:root{
 --bg:#060b11;--bg2:#0a1119;--panel:rgba(15,24,34,.88);--panel2:rgba(19,31,43,.82);
 --line:rgba(148,163,184,.14);--line2:rgba(148,163,184,.22);--text:#f5f8fb;--muted:#8e9bab;
 --green:#35d6a0;--green2:#19b985;--blue:#5aa7ff;--cyan:#45d9ff;--gold:#f4c95d;--red:#ff6474;
 --shadow:0 22px 70px rgba(0,0,0,.32);--radius:20px;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;color:var(--text);background:
 radial-gradient(900px 520px at 8% -8%,rgba(37,119,180,.28),transparent 60%),
 radial-gradient(700px 460px at 95% 0%,rgba(22,184,133,.16),transparent 58%),
 linear-gradient(180deg,#07101a 0%,#060b11 55%,#05090e 100%);
 font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;min-height:100vh}
body:before{content:"";position:fixed;inset:0;pointer-events:none;background-image:linear-gradient(rgba(255,255,255,.018) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.018) 1px,transparent 1px);background-size:36px 36px;mask-image:linear-gradient(to bottom,black,transparent 75%)}
button{font:inherit;cursor:pointer}
main{max-width:1260px;margin:auto;padding:22px 18px 70px;position:relative}
.topbar{display:flex;justify-content:space-between;align-items:center;gap:18px;margin-bottom:18px}
.brand{display:flex;align-items:center;gap:13px}.logo{width:52px;height:52px;border-radius:17px;display:grid;place-items:center;background:linear-gradient(145deg,#1ed0a1,#177bd0);font-size:25px;font-weight:950;box-shadow:0 12px 35px rgba(42,211,161,.22);border:1px solid rgba(255,255,255,.12)}
.brand h1{font-size:24px;line-height:1.05;margin:0;letter-spacing:-.04em}.sub{font-size:12px;color:var(--muted);margin-top:5px}
.topActions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.btn{border:1px solid var(--line2);background:rgba(18,29,40,.85);color:#eaf1f7;padding:10px 13px;border-radius:12px;transition:.18s;box-shadow:0 6px 18px rgba(0,0,0,.12)}
.btn:hover{transform:translateY(-1px);filter:brightness(1.1)}.btn.primary{background:linear-gradient(135deg,#3ee0a9,#20b98c);color:#03130d;border-color:transparent;font-weight:850}.btn.blue{background:linear-gradient(135deg,#5aa7ff,#357fe0);color:#06101c;border-color:transparent;font-weight:800}
.pill{display:inline-flex;align-items:center;gap:7px;padding:8px 11px;border-radius:999px;background:rgba(20,45,38,.72);border:1px solid rgba(53,214,160,.2);color:#8ff2cc;font-size:11px;font-weight:850;text-transform:uppercase;letter-spacing:.06em}.dot{width:7px;height:7px;border-radius:50%;background:var(--green);box-shadow:0 0 14px var(--green)}
.hero{position:relative;overflow:hidden;border:1px solid var(--line);border-radius:24px;padding:25px;background:
 linear-gradient(135deg,rgba(18,35,50,.96),rgba(8,17,26,.95) 58%,rgba(11,30,29,.94));box-shadow:var(--shadow);margin-bottom:14px}
.hero:after{content:"";position:absolute;width:330px;height:330px;right:-100px;top:-150px;border-radius:50%;background:radial-gradient(circle,rgba(53,214,160,.22),transparent 68%);pointer-events:none}
.heroMain{display:flex;justify-content:space-between;gap:20px;position:relative;z-index:1}.heroCopy{min-width:0}
.eyebrow{font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:#7f91a3;font-weight:900}
.endpoint{font-size:clamp(19px,3vw,29px);font-weight:900;letter-spacing:-.035em;margin:7px 0;word-break:break-all}.hint{color:var(--muted);font-size:12px;line-height:1.55}
.heroStats{display:grid;grid-template-columns:repeat(3,minmax(100px,1fr));gap:8px;margin-top:20px;max-width:720px}.heroStat{background:rgba(4,11,17,.34);border:1px solid var(--line);border-radius:14px;padding:10px 12px}.heroStat span{display:block;color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.08em}.heroStat b{display:block;margin-top:4px;font-size:14px}
.heroActions{display:flex;gap:8px;flex-wrap:wrap;margin-top:18px}
.dashboardGrid{display:grid;grid-template-columns:1.45fr .75fr;gap:14px}
.card{background:linear-gradient(180deg,rgba(16,25,35,.9),rgba(10,17,24,.9));border:1px solid var(--line);border-radius:var(--radius);padding:18px;box-shadow:0 12px 40px rgba(0,0,0,.13);margin-bottom:14px;backdrop-filter:blur(12px)}
.cardHead{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:14px}.card h2{font-size:16px;margin:0;letter-spacing:-.015em}.cardHead .muted{font-size:11px}
.sectionTag{font-size:10px;padding:5px 8px;border-radius:8px;background:#101d29;color:#89a0b4;border:1px solid var(--line)}
.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:14px}
.metric{position:relative;overflow:hidden;padding:16px;border-radius:17px;background:linear-gradient(145deg,rgba(17,28,39,.96),rgba(10,17,24,.96));border:1px solid var(--line)}
.metric:after{content:"";position:absolute;right:-22px;bottom:-26px;width:80px;height:80px;border-radius:50%;background:radial-gradient(circle,rgba(69,217,255,.11),transparent 68%)}
.metric .label{font-size:10px;color:#7f91a3;letter-spacing:.08em;text-transform:uppercase;font-weight:800}.metric .value{font-size:23px;font-weight:900;margin-top:8px;letter-spacing:-.035em}.metric .small{font-size:10px;color:var(--muted);margin-top:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.split{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.kv{display:grid;grid-template-columns:1fr 1fr;gap:9px}.kvItem{background:rgba(5,11,17,.55);border:1px solid var(--line);border-radius:13px;padding:12px}.kvItem span{display:block;color:var(--muted);font-size:10px;margin-bottom:5px}.kvItem b{font-size:13px;word-break:break-word}
.progressBox{display:grid;grid-template-columns:92px 1fr;gap:16px;align-items:center}.ring{width:92px;height:92px;border-radius:50%;display:grid;place-items:center;background:conic-gradient(var(--green) var(--progress,0%),#182530 0);position:relative}.ring:after{content:"";position:absolute;inset:9px;border-radius:50%;background:#0b141d}.ring b{position:relative;z-index:1;font-size:15px}.progressText b{font-size:15px}.progressText p{color:var(--muted);font-size:11px;line-height:1.5;margin:5px 0 0}
.controls{display:flex;gap:8px;flex-wrap:wrap}.toggle{display:inline-flex;align-items:center;gap:7px;background:#0b141d;border:1px solid var(--line);border-radius:11px;padding:9px 11px;color:#cbd6df;font-size:11px}.toggle input{accent-color:var(--green)}
.notice{padding:12px 13px;border-radius:13px;background:rgba(16,29,41,.7);border:1px solid var(--line);color:var(--muted);font-size:11px;line-height:1.5}
.tableWrap{overflow:auto}table{width:100%;border-collapse:collapse;min-width:620px}th,td{padding:11px 8px;border-bottom:1px solid rgba(148,163,184,.1);text-align:left;font-size:11px}th{color:#728395;font-weight:800;text-transform:uppercase;font-size:9px;letter-spacing:.08em}td{color:#dce5ec}tr:hover td{background:rgba(255,255,255,.018)}
.badge{display:inline-flex;padding:4px 8px;border-radius:999px;background:rgba(28,73,57,.55);color:#81e9bd;font-size:9px;font-weight:850}.badge.bad{background:rgba(101,31,40,.45);color:#ff9aa4}
.workerGrid{display:grid;grid-template-columns:repeat(2,1fr);gap:9px}.worker{padding:13px;border:1px solid var(--line);border-radius:14px;background:rgba(5,12,18,.5)}.workerTop{display:flex;justify-content:space-between;gap:8px}.workerName{font-weight:850;font-size:12px;word-break:break-all}.workerHash{color:#71e7bb;font-size:11px;font-weight:800}.workerMeta{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:10px}.workerMeta div{background:#0a131c;border-radius:9px;padding:7px}.workerMeta span{display:block;color:var(--muted);font-size:8px}.workerMeta b{font-size:10px}
.event{display:flex;gap:10px;padding:10px 0;border-bottom:1px solid rgba(148,163,184,.09)}.event:last-child{border-bottom:0}.eventIcon{width:30px;height:30px;border-radius:10px;background:#12212d;border:1px solid var(--line);display:grid;place-items:center;color:var(--cyan);font-size:13px}.eventText{flex:1;font-size:11px;line-height:1.4}.eventTime{color:var(--muted);font-size:9px;margin-top:3px}
.setup{display:none}.setup.visible{display:block}.field{margin-bottom:12px}.field label{display:block;margin-bottom:6px;font-size:11px;font-weight:700}.field input{width:100%;padding:11px;background:#081019;color:#fff;border:1px solid #2a3948;border-radius:10px;font-size:13px;outline:none}.field input:focus{border-color:var(--blue);box-shadow:0 0 0 3px rgba(90,167,255,.12)}.row{display:flex;gap:8px}.row input{flex:1}
.compact .card{padding:13px}.compact .metric{padding:11px}.compact .metrics{gap:7px}.compact .workerMeta{margin-top:6px}.hidden{display:none!important}.muted{color:var(--muted)}
.empty{padding:18px 8px;color:var(--muted);font-size:11px;text-align:center}
.footer{color:#5f7182;text-align:center;font-size:10px;padding-top:5px}
@media(max-width:980px){.metrics{grid-template-columns:repeat(2,1fr)}.dashboardGrid{grid-template-columns:1fr}.heroMain{flex-direction:column}.heroStats{max-width:none}}
@media(max-width:600px){main{padding:14px 11px 48px}.topbar{align-items:flex-start}.topActions .btn:not(.primary){display:none}.brand h1{font-size:21px}.logo{width:46px;height:46px}.hero{padding:18px;border-radius:19px}.heroStats{grid-template-columns:1fr 1fr 1fr}.heroStat{padding:8px}.metrics{gap:7px}.metric{padding:12px}.metric .value{font-size:19px}.split,.kv{grid-template-columns:1fr}.workerGrid{grid-template-columns:1fr}.card{padding:14px;border-radius:17px}.endpoint{font-size:17px}.progressBox{grid-template-columns:78px 1fr}.ring{width:78px;height:78px}}
</style>
</head>
<body><main>
<header class="topbar">
  <div class="brand"><div class="logo">₿</div><div><h1>BCH Solo Pool</h1><div class="sub">Self-hosted Bitcoin Cash solo mining · Command Center</div></div></div>
  <div class="topActions"><span class="pill"><i class="dot"></i><span id="topStatus">Connecting</span></span><button class="btn" onclick="refreshData()">↻ Refresh</button><button class="btn primary" onclick="newJob()">⚡ New job</button></div>
</header>

<section id="setup" class="card setup">
 <div class="cardHead"><h2>BCHN node & payout setup</h2><button class="btn" onclick="hideSetup()">Close</button></div>
 <p class="muted">BCHN RPC and ZMQ are connected automatically from the Umbrel Bitcoin Cash Node app. Enter your payout address to activate solo mining.</p>
 <div class="split">
  <div class="field"><label>RPC URL</label><input id="rpc" autocomplete="off" autocapitalize="none" spellcheck="false"></div>
  <div class="field"><label>RPC username</label><input id="user" autocomplete="off" autocapitalize="none" spellcheck="false"></div>
 </div>
 <div class="split">
  <div class="field"><label>RPC password <span class="muted">(leave blank for Umbrel BCHN password)</span></label><input id="pass" type="password" autocomplete="new-password" placeholder="Automatic from Umbrel BCHN"></div>
  <div class="field"><label>ZMQ hashblock URL</label><input id="zmq" autocomplete="off" autocapitalize="none" spellcheck="false"></div>
 </div>
 <div class="field"><label>BCH payout address</label><input id="payout" autocomplete="off" autocapitalize="none" spellcheck="false"></div>
 <div class="heroActions"><button class="btn primary" onclick="saveSetup()">Save & restart</button><button class="btn" onclick="clearSetupFields()">Clear</button></div>
 <p id="setupmsg" class="muted"></p>
</section>

<section id="dash">
 <section class="hero">
  <div class="heroMain">
   <div class="heroCopy">
    <div class="eyebrow">Stratum mining endpoint</div>
    <div class="endpoint" id="stratum">stratum+tcp://-:3334</div>
    <div class="hint">Username: <b>your BCH payout address</b>, optionally followed by <b>.worker1</b>. Password: <b>x</b>. Share difficulty is managed by the pool.</div>
    <div class="heroStats">
      <div class="heroStat"><span>Pool hashrate</span><b id="heroHash">0 H/s</b></div>
      <div class="heroStat"><span>Miners</span><b id="heroMiners">0 connected</b></div>
      <div class="heroStat"><span>Current job</span><b id="heroJob">—</b></div>
    </div>
   </div>
   <div class="heroActions" style="margin-top:0"><button class="btn primary" onclick="copyStratum()">Copy Stratum</button><button class="btn" onclick="showSetup()">⚙ Settings</button></div>
  </div>
 </section>

 <section class="metrics">
  <div class="metric"><div class="label">Node</div><div class="value" id="node">—</div><div class="small" id="chain">Checking BCHN…</div></div>
  <div class="metric"><div class="label">Block height</div><div class="value" id="height">—</div><div class="small" id="jobAge">Job age —</div></div>
  <div class="metric"><div class="label">Connected miners</div><div class="value" id="miners">0</div><div class="small">Live Stratum sessions</div></div>
  <div class="metric"><div class="label">Current job</div><div class="value" id="job">—</div><div class="small" id="txs">— transactions</div></div>
 </section>

 <div class="dashboardGrid">
  <div>
   <section class="card">
    <div class="cardHead"><h2>Mining performance</h2><span class="sectionTag" id="syncBadge">LIVE</span></div>
    <div class="kv">
      <div class="kvItem"><span>Network difficulty</span><b id="difficulty">—</b></div>
      <div class="kvItem"><span>Network hashrate</span><b id="networkHashrate">—</b></div>
      <div class="kvItem"><span>Pool hashrate</span><b id="poolHashrate">—</b></div>
      <div class="kvItem"><span>Best share difficulty</span><b id="bestDiff">—</b></div>
      <div class="kvItem"><span>Accepted shares</span><b id="accepted">0</b></div>
      <div class="kvItem"><span>Rejected shares</span><b id="rejected">0 (0%)</b></div>
      <div class="kvItem"><span>Block reward</span><b id="reward">—</b></div>
      <div class="kvItem"><span>Job transactions</span><b id="jobTxs">—</b></div>
    </div>
   </section>

   <section class="card">
    <div class="cardHead"><h2>Connected workers</h2><span class="muted" id="workerCount">0 workers</span></div>
    <div id="workers" class="workerGrid"><div class="empty">No miners connected yet.</div></div>
   </section>

   <section class="card">
    <div class="cardHead"><h2>Block submissions</h2><span class="muted">Latest 20</span></div>
    <div class="tableWrap"><table><thead><tr><th>Time</th><th>Height</th><th>Worker</th><th>Hash</th><th>Result</th></tr></thead><tbody id="blocks"></tbody></table></div>
   </section>
  </div>

  <div>
   <section class="card">
    <div class="cardHead"><h2>Node synchronization</h2><span class="sectionTag" id="nodeState">BCHN</span></div>
    <div class="progressBox">
      <div class="ring" id="ring"><b id="progressPct">0%</b></div>
      <div class="progressText"><b id="syncTitle">Checking node…</b><p id="syncDetail">Waiting for live BCHN blockchain information.</p></div>
    </div>
   </section>

   <section class="card">
    <div class="cardHead"><h2>Network & pool</h2><span class="sectionTag">LIVE DATA</span></div>
    <div class="kv">
      <div class="kvItem"><span>Chain</span><b id="nodeChain">—</b></div>
      <div class="kvItem"><span>Headers</span><b id="headers">—</b></div>
      <div class="kvItem"><span>Connections</span><b id="connections">—</b></div>
      <div class="kvItem"><span>Target</span><b id="target">—</b></div>
      <div class="kvItem"><span>Verification</span><b id="progress">—</b></div>
      <div class="kvItem"><span>Pool status</span><b id="poolStatus">—</b></div>
    </div>
   </section>

   <section class="card">
    <div class="cardHead"><h2>Controls</h2><span class="sectionTag">QUICK ACTIONS</span></div>
    <div class="controls">
      <button class="btn primary" onclick="newJob()">⚡ New job</button>
      <button class="btn" onclick="refreshData()">↻ Refresh</button>
      <label class="toggle"><input id="auto" type="checkbox" onchange="setAuto()"> Auto-refresh</label>
      <label class="toggle"><input id="compact" type="checkbox" onchange="document.body.classList.toggle('compact')"> Compact</label>
    </div>
    <div class="notice" style="margin-top:12px">New jobs are generated when BCHN reports a new block through ZMQ or when you press <b>New job</b>. Dashboard auto-refresh is browser-only and does not restart mining.</div>
   </section>

   <section class="card">
    <div class="cardHead"><h2>Recent activity</h2><span class="muted">Latest events</span></div>
    <div id="events"><div class="empty">No events yet.</div></div>
   </section>
  </div>
 </div>
 <div class="footer">BCH Solo Pool · BCHN powered · Stratum :3334</div>
</section>
</main>
<script>
const $=id=>document.getElementById(id);
let timer=null,lastData=null;
function esc(x){return String(x??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
function fmtHash(n){if(n==null||!isFinite(Number(n)))return '—';n=Number(n);if(n>=1e18)return (n/1e18).toFixed(2)+' EH/s';if(n>=1e15)return (n/1e15).toFixed(2)+' PH/s';if(n>=1e12)return (n/1e12).toFixed(2)+' TH/s';if(n>=1e9)return (n/1e9).toFixed(2)+' GH/s';if(n>=1e6)return (n/1e6).toFixed(2)+' MH/s';if(n>=1e3)return (n/1e3).toFixed(2)+' KH/s';return Math.round(n)+' H/s'}
function fmtNum(n){if(n==null||!isFinite(Number(n)))return '—';return Number(n).toLocaleString()}\nfunction fmtDifficulty(n){if(n==null||!isFinite(Number(n)))return '—';n=Number(n);if(n>=1e15)return (n/1e15).toFixed(2)+'P';if(n>=1e12)return (n/1e12).toFixed(2)+'T';if(n>=1e9)return (n/1e9).toFixed(2)+'G';if(n>=1e6)return (n/1e6).toFixed(2)+'M';if(n>=1e3)return (n/1e3).toFixed(2)+'K';return n.toFixed(2)}
function ago(t){if(!t)return '—';const s=Math.max(0,Date.now()/1000-Number(t));if(s<60)return Math.round(s)+'s ago';if(s<3600)return Math.floor(s/60)+'m ago';if(s<86400)return Math.floor(s/3600)+'h ago';return Math.floor(s/86400)+'d ago'}
function fmtTime(t){return t?new Date(Number(t)*1000).toLocaleString():'—'}
async function api(url,opt){const r=await fetch(url,Object.assign({cache:'no-store'},opt||{}));return await r.json()}
async function setupState(){
 try{const c=await api('/api/config');if(!c.configured){await showSetup(true)}else{$('setup').classList.remove('visible');$('dash').classList.remove('hidden')}return true}catch(e){$('dash').classList.remove('hidden');return true}
}
async function showSetup(loadSaved=true){$('setup').classList.add('visible');$('dash').classList.add('hidden');if(!loadSaved)return;try{const c=await api('/api/config');$('rpc').value=c.rpc_url||'';$('user').value=c.rpc_user||'';$('zmq').value=c.zmq_url||'';$('payout').value=c.payout_address||'';$('pass').value='';}catch(e){}}
function hideSetup(){$('setup').classList.remove('visible');$('dash').classList.remove('hidden')}
function clearSetupFields(){['rpc','user','pass','zmq','payout'].forEach(x=>$(x).value='');$('rpc').focus()}
async function saveSetup(){
 const body={BCH_RPC_URL:$('rpc').value.trim(),BCH_RPC_USER:$('user').value.trim(),BCH_RPC_PASSWORD:$('pass').value,BCH_ZMQ_URL:$('zmq').value.trim(),BCH_PAYOUT_ADDRESS:$('payout').value.trim()};
 $('setupmsg').textContent='Testing RPC and saving…';
 try{const x=await api('/api/setup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});$('setupmsg').textContent=x.ok?x.message:'Error: '+x.error}catch(e){$('setupmsg').textContent='Error: '+e}
}
function renderWorkers(workers){
 $('workerCount').textContent=workers.length+' worker'+(workers.length===1?'':'s');
 if(!workers.length){$('workers').innerHTML='<div class="empty">No miners connected yet.</div>';return}
 $('workers').innerHTML=workers.map(w=>'<div class="worker"><div class="workerTop"><div class="workerName">'+esc(w.worker)+'</div><div class="workerHash">'+fmtHash(w.hashrate||0)+'</div></div><div class="workerMeta"><div><span>Accepted</span><b>'+fmtNum(w.shares)+'</b></div><div><span>Rejected</span><b>'+fmtNum(w.rejected)+'</b></div><div><span>Best diff</span><b>'+Number(w.best_diff||0).toFixed(4)+'</b></div></div><div class="muted" style="font-size:9px;margin-top:8px">Last seen · '+ago(w.last_seen)+'</div></div>').join('')
}
async function refreshData(){
 try{
  await setupState();const x=await api('/api/status');lastData=x;const n=x.node||{},m=x.mining||{},workers=x.workers||[];
  const online=n.blocks!=null, syncing=!!n.initialblockdownload;
  const poolHash=workers.reduce((a,w)=>a+Number(w.hashrate||0),0);
  const accepted=workers.reduce((a,w)=>a+Number(w.shares||0),0),rejected=workers.reduce((a,w)=>a+Number(w.rejected||0),0),total=accepted+rejected;
  const progress=n.verificationprogress!=null?Math.max(0,Math.min(100,Number(n.verificationprogress)*100)):0;
  $('topStatus').textContent=online?(syncing?'Node syncing':'Pool online'):'Node offline';
  $('statusText') && ($('statusText').textContent=online?(syncing?'Node syncing':'Pool online'):'Node offline');
  $('node').textContent=online?(syncing?'Syncing':'Online'):'Offline';$('chain').textContent=n.chain||'BCHN';
  $('height').textContent=fmtNum(x.height);$('miners').textContent=fmtNum(x.miners_connected);$('job').textContent=x.job_id||'—';$('jobAge').textContent=x.job_created?'Job '+ago(x.job_created):'Job age unavailable';$('txs').textContent=fmtNum(x.tx_count)+' transactions';
  $('heroHash').textContent=fmtHash(poolHash);$('heroMiners').textContent=fmtNum(x.miners_connected)+' connected';$('heroJob').textContent=x.job_id||'—';
  $('stratum').textContent='stratum+tcp://'+location.hostname+':3334';
  $('difficulty').textContent=fmtDifficulty(m.difficulty);$('networkHashrate').textContent=fmtHash(m.networkhashps);$('poolHashrate').textContent=fmtHash(poolHash);
  $('bestDiff').textContent=workers.length?Math.max(...workers.map(w=>Number(w.best_diff||0))).toFixed(6):'—';$('accepted').textContent=fmtNum(accepted);$('rejected').textContent=fmtNum(rejected)+' ('+(total?(rejected/total*100).toFixed(2):'0')+'%)';
  $('reward').textContent=x.coinbase_value?((Number(x.coinbase_value)/1e8).toFixed(8)+' BCH'):'—';$('jobTxs').textContent=fmtNum(x.tx_count);$('target').textContent=x.network_target?'0x'+x.network_target.slice(0,18)+'…':'—';
  renderWorkers(workers);
  $('blocks').innerHTML=(x.blocks||[]).length?(x.blocks||[]).map(b=>'<tr><td>'+fmtTime(b.time)+'</td><td>'+esc(b.height)+'</td><td>'+esc(b.worker)+'</td><td><code>'+esc((b.hash||'').slice(0,18))+'…</code></td><td><span class="badge '+(String(b.result).toLowerCase()==='none'?'':'bad')+'">'+esc(b.result||'submitted')+'</span></td></tr>').join(''):'<tr><td colspan="5" class="empty">No block submissions yet.</td></tr>';
  $('events').innerHTML=(x.events||[]).slice(0,12).map(e=>'<div class="event"><div class="eventIcon">'+(String(e.kind).toLowerCase().includes('block')?'◆':'•')+'</div><div class="eventText"><b>'+esc(e.kind)+'</b> '+esc(e.detail||'')+'<div class="eventTime">'+esc(e.worker||'pool')+' · '+fmtTime(e.time)+'</div></div></div>').join('')||'<div class="empty">No events yet.</div>';
  $('nodeChain').textContent=n.chain||'—';$('progress').textContent=n.verificationprogress!=null?(Number(n.verificationprogress)*100).toFixed(2)+'%':'—';$('headers').textContent=fmtNum(n.headers);$('connections').textContent=fmtNum(n.connections);$('poolStatus').textContent=online?'Running':'Node unavailable';
  $('syncBadge').textContent=syncing?'SYNCING':'LIVE';$('nodeState').textContent=syncing?'SYNCING':'BCHN';
  $('progressPct').textContent=progress.toFixed(1)+'%';$('ring').style.setProperty('--progress',progress+'%');$('syncTitle').textContent=online?(syncing?'BCHN is synchronizing':'BCHN is fully available'):'BCHN unavailable';
  $('syncDetail').textContent=online?('Height '+fmtNum(n.blocks)+' · '+fmtNum(n.headers)+' headers · '+fmtNum(n.connections)+' peer connections'): 'Waiting for live BCHN blockchain information.';
 }catch(e){$('topStatus').textContent='Connection error';$('node').textContent='Offline';$('syncTitle').textContent='Connection error'}
}
async function newJob(){try{const x=await api('/api/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'refresh_job'})});if(!x.ok)throw Error(x.error||'Failed');await refreshData()}catch(e){alert('Could not create new job: '+e.message)}}
async function copyStratum(){const s='stratum+tcp://'+location.hostname+':3334';try{await navigator.clipboard.writeText(s)}catch(e){prompt('Copy Stratum endpoint:',s)}}
function setAuto(){if(timer)clearInterval(timer);const enabled=$('auto').checked;try{localStorage.setItem('bch_pool_auto_refresh',enabled?'1':'0')}catch(e){}timer=enabled?setInterval(refreshData,30000):null}
function restoreAuto(){let enabled=true;try{const saved=localStorage.getItem('bch_pool_auto_refresh');if(saved!==null)enabled=saved==='1'}catch(e){}$('auto').checked=enabled;if(enabled)timer=setInterval(refreshData,30000)}
refreshData();restoreAuto();
</script>
</body></html>"""
