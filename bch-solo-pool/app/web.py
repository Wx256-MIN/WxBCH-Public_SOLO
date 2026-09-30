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
                        "vardiff_enabled": outer.cfg.vardiff_enabled,
                        "vardiff_target_seconds": outer.cfg.vardiff_target_seconds,
                        "start_difficulty": outer.cfg.start_difficulty,
                        "min_difficulty": outer.cfg.vardiff_min,
                        "max_difficulty": outer.cfg.vardiff_max,
                    }))
                    return

                if path == "/api/status":
                    info, mining, network = {}, {}, {}
                    if outer.rpc is not None:
                        try:
                            info = outer.rpc.get_blockchain_info()
                        except Exception as exc:
                            info = {"error": str(exc)}
                        try:
                            mining = outer.rpc.get_mining_info()
                        except Exception:
                            mining = {}
                        try:
                            network = outer.rpc.get_network_info()
                        except Exception:
                            network = {}
                    workers, blocks, events = outer.db.snapshot()
                    live = {}
                    if outer.pool is not None:
                        for miner in list(outer.pool.miners):
                            if not miner.authorized or miner.worker in ("", "unknown"):
                                continue
                            item = live.setdefault(miner.worker, {
                                "hashrate": 0.0,
                                "difficulty": miner.difficulty,
                                "last_share": 0.0,
                            })
                            item["hashrate"] += miner.live_hashrate()
                            item["difficulty"] = miner.difficulty
                            item["last_share"] = max(item["last_share"], miner.last_share)
                    workers = [w for w in workers if w.get("worker") in live]
                    for w in workers:
                        item = live[w["worker"]]
                        w["hashrate"] = item["hashrate"]
                        w["difficulty"] = item["difficulty"]
                        w["session_best_diff"] = max((m.session_best_diff for m in outer.pool.miners if m.authorized and m.worker == w["worker"]), default=0.0)
                        if item["last_share"]:
                            w["last_seen"] = item["last_share"]
                    authorized_miners = len(live)
                    job = outer.pool.job if outer.pool is not None else None
                    obj = {
                        "setup_required": not outer.cfg.configured,
                        "pool": outer.cfg.pool_id,
                        "height": job.height if job else None,
                        "job_id": job.job_id if job else None,
                        "network_target": f"{job.network_target:064x}" if job else None,
                        "start_difficulty": outer.cfg.start_difficulty,
                        "min_difficulty": outer.cfg.vardiff_min,
                        "max_difficulty": outer.cfg.vardiff_max,
                        "vardiff_enabled": outer.cfg.vardiff_enabled,
                        "vardiff_target_seconds": outer.cfg.vardiff_target_seconds,
                        "job_created": job.created if job else None,
                        "tx_count": len(job.tx_hex) if job else 0,
                        "coinbase_value": job.coinbase_value if job else None,
                        "miners_connected": authorized_miners,
                        "workers": workers,
                        "blocks": blocks,
                        "events": events,
                        "node": info,
                        "mining": mining,
                        "network": network,
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

                if path == "/api/vardiff-settings":
                    try:
                        body = self._json_body()
                        enabled = bool(body.get("enabled", True))
                        target = float(body.get("target_seconds"))
                        start = float(body.get("start_difficulty"))
                        minimum = float(body.get("min_difficulty"))
                        maximum = float(body.get("max_difficulty"))
                        enabled, target, start, minimum, maximum = outer.cfg.save_vardiff_settings(
                            enabled, target, start, minimum, maximum
                        )
                        changed = 0
                        if outer.pool is not None:
                            outer.pool.start_difficulty = start
                            outer.cfg.vardiff_enabled = enabled
                            outer.cfg.vardiff_target_seconds = target
                            outer.cfg.vardiff_min = minimum
                            outer.cfg.vardiff_max = maximum
                            # When Vardiff is disabled, put connected miners at
                            # the configured fixed start difficulty. When it is
                            # enabled, preserve their current difficulty unless
                            # it violates the new bounds.
                            for miner in list(outer.pool.miners):
                                if not enabled:
                                    new_difficulty = start
                                else:
                                    new_difficulty = max(minimum, min(maximum, miner.difficulty))
                                if abs(new_difficulty - miner.difficulty) > 1e-12:
                                    miner.difficulty = new_difficulty
                                    outer.pool.db.touch_worker(miner.worker, new_difficulty)
                                    try:
                                        awaitable = miner.send({
                                            "id": None,
                                            "method": "mining.set_difficulty",
                                            "params": [new_difficulty]
                                        })
                                        future = asyncio.run_coroutine_threadsafe(awaitable, outer.loop)
                                        future.result(timeout=5)
                                        changed += 1
                                    except Exception:
                                        pass
                        self._send(200, json.dumps({
                            "ok": True,
                            "message": "Vardiff settings saved",
                            "enabled": enabled,
                            "target_seconds": target,
                            "start_difficulty": start,
                            "min_difficulty": minimum,
                            "max_difficulty": maximum,
                            "miners_updated": changed,
                        }))
                    except Exception as exc:
                        self._send(400, json.dumps({"ok": False, "error": str(exc)}))
                    return

                if path == "/api/pool-settings":
                    try:
                        body = self._json_body()
                        start = float(body.get("start_difficulty"))
                        minimum = float(body.get("min_difficulty"))
                        start, minimum = outer.cfg.save_pool_settings(start, minimum)

                        # Minimum difficulty is a live floor. If an already
                        # connected miner is below it, raise that miner now.
                        changed = 0
                        if outer.pool is not None:
                            for miner in list(outer.pool.miners):
                                new_difficulty = max(minimum, start)
                                if abs(miner.difficulty - new_difficulty) > 1e-12:
                                    miner.difficulty = new_difficulty
                                    outer.pool.db.touch_worker(miner.worker, miner.difficulty)
                                    try:
                                        coroutine = miner.send({
                                            "id": None,
                                            "method": "mining.set_difficulty",
                                            "params": [miner.difficulty]
                                        })
                                        if getattr(outer, "loop", None):
                                            future = asyncio.run_coroutine_threadsafe(
                                                coroutine, outer.loop
                                            )
                                            future.result(timeout=5)
                                        changed += 1
                                    except Exception:
                                        pass
                        self._send(200, json.dumps({
                            "ok": True,
                            "message": "Difficulty settings saved",
                            "start_difficulty": start,
                            "min_difficulty": minimum,
                            "miners_updated": changed,
                        }))
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
<link rel="icon" type="image/svg+xml" href="https://cdn.jsdelivr.net/gh/Wx256-MIN/WxBCH-SOLO@ecbcf979ed51a03e6eb2b773df69d2db3df50623/bch-solo-pool/icon.jpg">
<link rel="shortcut icon" href="https://cdn.jsdelivr.net/gh/Wx256-MIN/WxBCH-SOLO@ecbcf979ed51a03e6eb2b773df69d2db3df50623/bch-solo-pool/icon.jpg">
<link rel="apple-touch-icon" href="https://cdn.jsdelivr.net/gh/Wx256-MIN/WxBCH-SOLO@ecbcf979ed51a03e6eb2b773df69d2db3df50623/bch-solo-pool/icon.jpg">
<title>BCH Solo Pool — Command Center</title>
<style>
:root{
 --bg:#f4f1e8;--surface:#fffdf7;--surface2:#f7f5ee;--ink:#18352b;--muted:#718178;
 --line:#dfe5dc;--green:#2f8f68;--green2:#4eaa7d;--mint:#dcefe4;--gold:#d5a84a;
 --danger:#c95d5d;--blue:#4f86a8;--shadow:0 12px 40px rgba(38,61,49,.09);--radius:24px;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;color:var(--ink);background:
 radial-gradient(700px 420px at -8% -5%,rgba(130,185,145,.28),transparent 65%),
 radial-gradient(650px 430px at 108% 10%,rgba(232,210,157,.22),transparent 62%),
 linear-gradient(180deg,#f7f5ee 0%,#f1efe7 100%);
 font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;min-height:100vh}
body.dark{--bg:#07100c;--surface:#0f1b15;--surface2:#13231b;--ink:#edf7f0;--muted:#91a79a;--line:#243a2d;--green:#52c98b;--green2:#72dda4;--mint:#173b2a;--gold:#dcb85d;--shadow:0 14px 45px rgba(0,0,0,.28)}\nbody.dark .hero{background:linear-gradient(135deg,#10251a 0%,#172218 58%,#10271c 100%);border-color:#294333}\nbody.dark .heroStat,body.dark .metric{background:rgba(15,29,22,.88);border-color:#294033}\nbody.dark .card{background:rgba(15,26,20,.94);border-color:#263c30}\nbody.dark .kvItem,body.dark .workerMeta div,body.dark .worker{background:#101f17;border-color:#263c30}\nbody.dark .tableWrap,body.dark table{background:#0e1b15;border-color:#263c30}body.dark th{background:#13231b;color:#8da497}body.dark td{color:#d3e4d8;border-color:#203429}\nbody.dark .btn{background:#14251c;color:#e5f1e9;border-color:#2b4335}body.dark .toggle{background:#122219;color:#c5d8ca;border-color:#294033}body.dark .notice{background:#13271c;border-color:#294033}body.dark .sectionTag{background:#173022;color:#9bc5aa;border-color:#294333}body.dark .ring:after{background:#0f1b15}body.dark .event{border-color:#263c30}body.dark .eventIcon{background:#173022;border-color:#294333}body.dark .field input{background:#0d1913;color:#edf7f0;border-color:#2b4335}\n/* Dark mode contrast/accessibility fixes */
body.dark{color:var(--ink);background:
 radial-gradient(700px 420px at -8% -5%,rgba(54,126,88,.16),transparent 65%),
 radial-gradient(650px 430px at 108% 10%,rgba(185,151,61,.10),transparent 62%),
 linear-gradient(180deg,#07100c 0%,#09140f 100%)}
body.dark .brand h1{color:#edf7f0}
body.dark .brand .sub{color:#91a79a}
body.dark .logo{border-color:rgba(255,255,255,.14);box-shadow:0 10px 28px rgba(0,0,0,.35)}
body.dark .themeToggle{background:#101c16;border-color:#2b4435}
body.dark .themeToggle button{color:#8fa497}
body.dark .themeToggle button.active{background:#254534;color:#f1faf4;box-shadow:0 2px 8px rgba(0,0,0,.28)}
body.dark .btn{background:#14231b;color:#e7f3eb;border-color:#2b4435;box-shadow:0 4px 14px rgba(0,0,0,.18)}
body.dark .btn:hover{box-shadow:0 8px 20px rgba(0,0,0,.28)}
body.dark .btn.primary{background:linear-gradient(135deg,#31855f,#4db77d);color:#fff}
body.dark .pill{background:#153424;border-color:#28533b;color:#9be0b6}
body.dark .hero{background:linear-gradient(135deg,#10251a 0%,#14271e 58%,#102319 100%);border-color:#294333}
body.dark .eyebrow{color:#86a596}
body.dark .endpoint{color:#e8f5ed}
body.dark .hint{color:#9aada2}
body.dark .heroStat{background:rgba(9,24,17,.72);border-color:#2b4435}
body.dark .heroStat span{color:#8ea398}
body.dark .heroStat b{color:#e7f3eb}
body.dark .metric{background:#0f1d16;border-color:#294033}
body.dark .metric .label{color:#91a79a}
body.dark .metric .value{color:#e6f5ec}
body.dark .metric .small{color:#91a79a}
body.dark .card{background:#0e1a14;border-color:#294033}
body.dark .card h2{color:#e2f0e7}
body.dark .cardHead .muted,body.dark .muted{color:#91a79a}
body.dark .sectionTag{background:#173224;color:#a7d6b8;border-color:#2c503c}
body.dark .kvItem{background:#101f17;border-color:#294033}
body.dark .kvItem span{color:#91a79a}
body.dark .kvItem b{color:#e0f0e7}
body.dark .progressText b{color:#e2f0e7}
body.dark .progressText p{color:#91a79a}
body.dark .ring{background:conic-gradient(var(--green) var(--progress,0%),#26392f 0)}
body.dark .ring:after{background:#0e1a14}
body.dark .toggle{background:#122219;color:#cfe0d5;border-color:#294033}
body.dark .notice{background:#13271c;border-color:#2d4b38;color:#a4b7ac}
body.dark .tableWrap{border-color:#294033}
body.dark table{background:#0c1712}
body.dark th{background:#13241b;color:#91a79a;border-color:#24392e}
body.dark td{color:#d3e4d9;border-color:#20352a}
body.dark tr:hover td{background:#12221a}
body.dark .badge{background:#173b29;color:#9fe0b7}
body.dark .badge.bad{background:#3a2020;color:#f0a4a4}
body.dark .worker{background:#101f17;border-color:#294033}
body.dark .workerName{color:#e3f1e8}
body.dark .workerHash{color:#69d99b}
body.dark .workerMeta div{background:#16271f;border-color:#263f32}
body.dark .workerMeta span{color:#91a79a}
body.dark .workerMeta b{color:#d4e5db}
body.dark .event{border-color:#263c30}
body.dark .eventIcon{background:#173224;border-color:#2c503c;color:#8ed5a8}
body.dark .eventText{color:#d7e7dd}
body.dark .eventTime{color:#91a79a}
body.dark .footer{color:#71877a}
body.dark .field label{color:#b3c7bb}
body.dark .field input{background:#0b1711;color:#edf7f0;border-color:#2b4435}
body.dark .field input::placeholder{color:#687c70}
body:before{content:"";position:fixed;inset:0;pointer-events:none;opacity:.28;background-image:radial-gradient(rgba(31,72,52,.08) .7px,transparent .7px);background-size:13px 13px}
button{font:inherit;cursor:pointer}
main{max-width:1320px;margin:auto;padding:22px clamp(12px,3vw,30px) 60px;position:relative}
.topbar{display:flex;justify-content:space-between;align-items:center;gap:18px;margin-bottom:18px}
.brand{display:flex;align-items:center;gap:13px}.logo{width:54px;height:54px;border-radius:18px;display:grid;place-items:center;background:linear-gradient(145deg,#377f60,#8bbf91);font-size:26px;font-weight:950;color:#fff;box-shadow:0 10px 28px rgba(47,143,104,.2);border:4px solid rgba(255,255,255,.72)}
.brand h1{font-size:25px;line-height:1.05;margin:0;letter-spacing:-.04em;color:#17382b}.sub{font-size:12px;color:var(--muted);margin-top:5px}
.topActions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.themeToggle{display:inline-flex;align-items:center;gap:5px;padding:4px;border-radius:13px;background:rgba(255,255,255,.68);border:1px solid var(--line)}.themeToggle button{border:0;background:transparent;color:var(--muted);padding:7px 9px;border-radius:9px;font-size:12px;font-weight:800}.themeToggle button.active{background:#fff;color:var(--ink);box-shadow:0 2px 8px rgba(30,60,43,.1)}body.dark .themeToggle{background:#122219}.themeToggle button.active{background:#fff}
.btn{border:1px solid var(--line);background:rgba(255,255,255,.72);color:var(--ink);padding:10px 14px;border-radius:14px;transition:.18s;box-shadow:0 4px 14px rgba(36,64,49,.05);font-weight:700}
.btn:hover{transform:translateY(-1px);box-shadow:0 8px 20px rgba(36,64,49,.1)}.btn.primary{background:linear-gradient(135deg,#348f68,#58aa7e);color:#fff;border-color:transparent}.btn.blue{background:#e3eef3;color:#315d74}
.pill{display:inline-flex;align-items:center;gap:7px;padding:8px 11px;border-radius:999px;background:var(--mint);border:1px solid #c9e3d3;color:#286b4f;font-size:10px;font-weight:900;text-transform:uppercase;letter-spacing:.07em}.dot{width:7px;height:7px;border-radius:50%;background:var(--green);box-shadow:0 0 0 4px rgba(47,143,104,.12)}
.hero{position:relative;overflow:hidden;border:1px solid #d7e2d9;border-radius:30px;padding:28px;background:
 linear-gradient(135deg,#edf7ef 0%,#fbf8ee 58%,#eaf2e9 100%);box-shadow:var(--shadow);margin-bottom:14px}
.hero:before{content:"";position:absolute;width:330px;height:330px;right:-90px;top:-170px;border-radius:50%;background:radial-gradient(circle,rgba(91,165,117,.2),transparent 68%);pointer-events:none}
.hero:after{content:"";position:absolute;width:180px;height:180px;right:120px;bottom:-130px;border-radius:50%;border:28px solid rgba(211,171,82,.08);pointer-events:none}
.heroMain{display:flex;justify-content:space-between;gap:24px;position:relative;z-index:1}.heroCopy{min-width:0}
.eyebrow{font-size:10px;letter-spacing:.15em;text-transform:uppercase;color:#678072;font-weight:900}
.endpoint{font-size:clamp(18px,3vw,30px);font-weight:900;letter-spacing:-.04em;margin:8px 0;color:#163a2b;word-break:break-all}.hint{color:#65766d;font-size:12px;line-height:1.6}
.heroStats{display:grid;grid-template-columns:repeat(3,minmax(110px,1fr));gap:8px;margin-top:20px;max-width:740px}.heroStat{background:rgba(255,255,255,.58);border:1px solid rgba(160,187,168,.42);border-radius:16px;padding:11px 13px}.heroStat span{display:block;color:#728177;font-size:9px;text-transform:uppercase;letter-spacing:.08em;font-weight:800}.heroStat b{display:block;margin-top:4px;font-size:14px}
.heroActions{display:flex;gap:8px;flex-wrap:wrap;margin-top:18px}
.metrics{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:14px}
.metric{position:relative;overflow:hidden;padding:17px;border-radius:19px;background:rgba(255,253,247,.82);border:1px solid var(--line);box-shadow:0 7px 24px rgba(48,70,55,.055)}
.metric:after{content:"";position:absolute;right:-25px;bottom:-28px;width:85px;height:85px;border-radius:50%;background:radial-gradient(circle,rgba(83,165,115,.12),transparent 68%)}
.metric .label{font-size:9px;color:#7b897f;letter-spacing:.09em;text-transform:uppercase;font-weight:900}.metric .value{font-size:23px;font-weight:900;margin-top:8px;letter-spacing:-.04em;color:#1c4736}.metric .small{font-size:10px;color:var(--muted);margin-top:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dashboardGrid{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(300px,.75fr);gap:14px}
.card{background:rgba(255,253,247,.88);border:1px solid var(--line);border-radius:var(--radius);padding:19px;box-shadow:var(--shadow);margin-bottom:14px}
.cardHead{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:14px}.card h2{font-size:16px;margin:0;letter-spacing:-.02em;color:#214535}.cardHead .muted{font-size:11px}
.sectionTag{font-size:9px;padding:6px 9px;border-radius:999px;background:#edf4ee;color:#547265;border:1px solid #d8e5db;font-weight:900;letter-spacing:.04em}
.split{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.kv{display:grid;grid-template-columns:1fr 1fr;gap:9px}.kvItem{background:#f7f6f0;border:1px solid #e2e6dd;border-radius:15px;padding:12px}.kvItem span{display:block;color:#77857c;font-size:9px;margin-bottom:5px;font-weight:700}.kvItem b{font-size:13px;word-break:break-word;color:#214636}
.progressBox{display:grid;grid-template-columns:94px 1fr;gap:17px;align-items:center}.ring{width:94px;height:94px;border-radius:50%;display:grid;place-items:center;background:conic-gradient(var(--green) var(--progress,0%),#e4e9e1 0);position:relative}.ring:after{content:"";position:absolute;inset:10px;border-radius:50%;background:#fffdf7}.ring b{position:relative;z-index:1;font-size:15px}.progressText b{font-size:15px;color:#254938}.progressText p{color:var(--muted);font-size:11px;line-height:1.5;margin:5px 0 0}
.controls{display:flex;gap:8px;flex-wrap:wrap}.toggle{display:inline-flex;align-items:center;gap:7px;background:#f5f5ee;border:1px solid var(--line);border-radius:13px;padding:9px 11px;color:#52645a;font-size:11px;font-weight:700}.toggle input{accent-color:var(--green)}
.notice{padding:13px;border-radius:15px;background:#f1f6f0;border:1px solid #dce8dd;color:#66776d;font-size:11px;line-height:1.55}
.tableWrap{overflow:auto;border:1px solid #e4e7df;border-radius:14px}table{width:100%;border-collapse:collapse;min-width:600px;background:#fffdf8}th,td{padding:11px 9px;border-bottom:1px solid #edf0e9;text-align:left;font-size:11px}th{color:#7a897f;font-weight:900;text-transform:uppercase;font-size:9px;letter-spacing:.08em;background:#f7f7f1}td{color:#365347}tr:last-child td{border-bottom:0}tr:hover td{background:#f8faf6}
.badge{display:inline-flex;padding:5px 8px;border-radius:999px;background:#e3f2e9;color:#357657;font-size:9px;font-weight:900}.badge.bad{background:#f8e5e3;color:#a64d4d}
.workerGrid{display:grid;grid-template-columns:repeat(2,1fr);gap:9px}.worker{padding:14px;border:1px solid #e0e7df;border-radius:16px;background:#fafaf5}.workerTop{display:flex;justify-content:space-between;gap:8px}.workerName{font-weight:850;font-size:12px;word-break:break-all}.workerHash{color:#32815e;font-size:11px;font-weight:900}.workerMeta{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:10px}.workerMeta div{background:#f1f3ed;border-radius:10px;padding:7px}.workerMeta span{display:block;color:#7b897f;font-size:8px}.workerMeta b{font-size:10px;color:#365548}
.event{display:flex;gap:10px;padding:10px 0;border-bottom:1px solid #e8ece5}.event:last-child{border-bottom:0}.eventIcon{width:30px;height:30px;border-radius:11px;background:#e8f2ea;border:1px solid #d5e4d8;display:grid;place-items:center;color:#438362;font-size:13px}.eventText{flex:1;font-size:11px;line-height:1.4}.eventTime{color:var(--muted);font-size:9px;margin-top:3px}
.setup{display:none}.setup.visible{display:block}.field{margin-bottom:12px}.field label{display:block;margin-bottom:6px;font-size:11px;font-weight:800;color:#466155}.field input{width:100%;padding:12px;background:#fbfbf7;color:#18382b;border:1px solid #d7e0d7;border-radius:12px;font-size:13px;outline:none}.field input:focus{border-color:#72aa8b;box-shadow:0 0 0 3px rgba(80,153,111,.1)}.row{display:flex;gap:8px}.row input{flex:1}
.compact .card{padding:13px}.compact .metric{padding:12px}.compact .metrics{gap:7px}.compact .workerMeta{margin-top:6px}.hidden{display:none!important}.muted{color:var(--muted)}
.empty{padding:18px 8px;color:var(--muted);font-size:11px;text-align:center}
.footer{color:#829087;text-align:center;font-size:10px;padding-top:5px}
/* Responsive layout */
main,.topbar,.hero,.metrics,.dashboardGrid,.card,.heroMain,.heroCopy,.topActions,.heroActions,.metric,.kvItem,.worker,.tableWrap{min-width:0}
body{overflow-x:hidden}
.heroCopy{flex:1 1 auto}
.heroActions{flex:0 0 auto}
.dashboardGrid>div{min-width:0}

@media (max-width:1199px){
  main{max-width:1180px;padding-left:20px;padding-right:20px}
  .metrics{grid-template-columns:repeat(3,minmax(0,1fr))}
  .dashboardGrid{grid-template-columns:minmax(0,1.25fr) minmax(280px,.85fr)}
  .heroMain{gap:18px}
}
@media (max-width:980px){
  main{padding:16px 16px 50px}
  .dashboardGrid{grid-template-columns:1fr}
  .heroMain{flex-direction:column}
  .heroActions[style]{margin-top:0!important}
  .heroActions{width:100%}
  .heroActions .btn{flex:0 1 auto}
}
@media (max-width:760px){
  main{padding:12px 12px 42px}
  .topbar{align-items:center;gap:10px}
  .brand{min-width:0;flex:1}
  .brand h1{font-size:20px}
  .brand .sub{font-size:10px}
  .logo{width:46px;height:46px;flex:0 0 46px}
  .topActions{max-width:none;flex:0 0 auto;justify-content:flex-end}
  .topActions>.btn:not(.primary){display:none}
  .themeToggle button{font-size:10px;padding:7px 8px}
  .topActions>.btn.primary{font-size:11px;padding:9px 10px}
  .hero{padding:20px;border-radius:22px}
  .endpoint{font-size:clamp(15px,3.4vw,22px)}
  .heroStats{grid-template-columns:repeat(2,minmax(0,1fr));max-width:none}
  .heroStat:nth-child(3){grid-column:1/-1}
  .metrics{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
  .metric:nth-child(5){grid-column:1/-1}
  .card{padding:16px;border-radius:20px}
  .kv{grid-template-columns:repeat(2,minmax(0,1fr))}
  .workerGrid{grid-template-columns:1fr}
  .workerTop{align-items:flex-start}
}
@media (max-width:560px){
  main{padding:8px 8px 32px}
  .topbar{align-items:flex-start;margin-bottom:10px}
  .brand{gap:8px}
  .logo{width:40px;height:40px;flex-basis:40px;border-radius:13px;font-size:20px;border-width:3px}
  .brand h1{font-size:17px;white-space:nowrap}
  .brand .sub{display:none}
  .topActions{gap:4px}
  .topActions .pill{font-size:7px;padding:6px 7px;gap:4px}
  .dot{width:6px;height:6px}
  .themeToggle{padding:2px;border-radius:10px}
  .themeToggle button{font-size:9px;padding:6px 6px}
  .topActions>.btn.primary{font-size:10px;padding:8px 8px;border-radius:11px}
  .hero{padding:14px;border-radius:17px}
  .eyebrow{font-size:8px}
  .endpoint{font-size:13px;line-height:1.4;margin:5px 0}
  .hint{font-size:9px;line-height:1.45}
  .heroStats{gap:5px;margin-top:11px}
  .heroStat{padding:8px 9px;border-radius:11px}
  .heroStat span{font-size:7px}
  .heroStat b{font-size:10px}
  .heroActions{gap:5px;margin-top:12px}
  .heroActions .btn{padding:8px 7px;font-size:10px;border-radius:11px}
  .metrics{gap:5px;margin-bottom:8px}
  .metric{padding:10px;border-radius:13px}
  .metric .label{font-size:7px}
  .metric .value{font-size:16px;margin-top:4px}
  .metric .small{font-size:8px}
  .card{padding:11px;border-radius:15px;margin-bottom:8px}
  .cardHead{margin-bottom:9px}
  .card h2{font-size:13px}
  .cardHead .muted{font-size:9px}
  .sectionTag{font-size:7px;padding:5px 6px}
  .kv{gap:5px}
  .kvItem{padding:8px;border-radius:11px}
  .kvItem span{font-size:7px;margin-bottom:4px}
  .kvItem b{font-size:10px}
  .progressBox{grid-template-columns:58px minmax(0,1fr);gap:10px}
  .ring{width:58px;height:58px}
  .ring:after{inset:7px}
  .ring b{font-size:11px}
  .progressText b{font-size:11px}
  .progressText p{font-size:8px}
  .controls{display:grid;grid-template-columns:1fr 1fr;gap:5px}
  .controls .btn,.controls .toggle{min-width:0;width:100%;justify-content:center;padding:8px 5px;font-size:9px}
  .notice{padding:9px;font-size:8px}
  .worker{padding:9px}
  .workerName{font-size:10px}
  .workerHash{font-size:9px}
  .workerMeta{gap:4px;margin-top:7px}
  .workerMeta div{padding:5px;border-radius:8px}
  .workerMeta span{font-size:7px}
  .workerMeta b{font-size:8px}
  .tableWrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
  table{min-width:540px}
  th,td{padding:8px 6px;font-size:8px}
  .event{gap:7px;padding:7px 0}
  .eventIcon{width:25px;height:25px;border-radius:8px;font-size:11px;flex:0 0 25px}
  .eventText{font-size:9px}
  .eventTime{font-size:7px}
  .footer{font-size:7px}
  .setup .split{grid-template-columns:1fr}
  .field input{font-size:11px;padding:10px}
}
@media (max-width:390px){
  .topActions .pill{display:none}
  .topActions>.btn.primary{font-size:9px;padding:7px}
  .themeToggle button{font-size:8px;padding:5px}
  .heroStats{grid-template-columns:1fr}
  .heroStat:nth-child(3){grid-column:auto}
  .metrics{grid-template-columns:1fr}
  .metric:nth-child(5){grid-column:auto}
  .kv{grid-template-columns:1fr}
  .controls{grid-template-columns:1fr}
}
@media (min-width:981px){
  .heroActions[style]{align-self:flex-start}
}
</style>
</head>
<body><main>
<header class="topbar">
  <div class="brand"><div class="logo">₿</div><div><h1>BCH Solo Pool</h1><div class="sub">Self-hosted Bitcoin Cash solo mining · Command Center</div></div></div>
  <div class="topActions"><span class="pill"><i class="dot"></i><span id="topStatus">Connecting</span></span><div class="themeToggle" aria-label="Theme"><button id="lightBtn" onclick="setTheme('light')">☀ Light</button><button id="darkBtn" onclick="setTheme('dark')">☾ Dark</button></div><button class="btn" onclick="refreshData()">↻ Refresh</button><button class="btn primary" onclick="newJob()">⚡ New job</button></div>
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

 <div class="cardHead" style="margin-top:18px"><h2>Difficulty (Vardiff)</h2><span class="muted">Automatic share difficulty</span></div>
 <div class="split">
  <div class="field"><label>Vardiff</label><label class="toggle"><input id="vardiffEnabled" type="checkbox"> Enable automatic difficulty</label></div>
  <div class="field"><label>Target share time (seconds)</label><input id="vardiffTarget" type="number" min="5" max="600" step="1" placeholder="30"></div>
 </div>
 <div class="split">
  <div class="field"><label>Start difficulty</label><input id="startDiff" type="number" min="0.000001" step="any" placeholder="1000"></div>
  <div class="field"><label>Minimum difficulty</label><input id="minDiff" type="number" min="0.000001" step="any" placeholder="0.001"></div>
 </div>
 <div class="field"><label>Maximum difficulty</label><input id="maxDiff" type="number" min="0.000001" step="any" placeholder="65536"></div>
 <p class="muted" style="font-size:10px">Vardiff adjusts each miner's share difficulty toward the target share time. Start is used for new miners; Minimum and Maximum are hard vardiff limits.</p>
 <div class="heroActions"><button class="btn primary" onclick="saveSetup()">Save & restart</button><button class="btn" onclick="saveVardiffSettings()">Apply Vardiff</button><button class="btn" onclick="clearSetupFields()">Clear</button></div>
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
  <div class="metric"><div class="label">BCHN peers</div><div class="value" id="peers">—</div><div class="small">Active node connections</div></div>
  <div class="metric"><div class="label">Current job</div><div class="value" id="job">—</div><div class="small" id="txs">— transactions</div></div>
 </section>

 <div class="dashboardGrid">
  <div>
   <section class="card">
    <div class="cardHead"><h2>Mining performance</h2><span class="sectionTag" id="syncBadge">LIVE</span></div>
    <div class="kv">
      <div class="kvItem"><span>Network difficulty</span><b id="difficulty">—</b></div>
      <div class="kvItem"><span>Vardiff</span><b id="vardiffStatus">—</b></div>
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
async function showSetup(loadSaved=true){
 $('setup').classList.add('visible');$('dash').classList.add('hidden');
 if(!loadSaved)return;
 try{
  const c=await api('/api/config');
  $('rpc').value=c.rpc_url||'';$('user').value=c.rpc_user||'';$('zmq').value=c.zmq_url||'';$('payout').value=c.payout_address||'';$('pass').value='';
  $('vardiffEnabled').checked=c.vardiff_enabled!==false;
  $('vardiffTarget').value=c.vardiff_target_seconds??30;
  $('startDiff').value=c.start_difficulty??1000;
  $('minDiff').value=c.min_difficulty??0.001;
  $('maxDiff').value=c.max_difficulty??65536;
 }catch(e){}
}
function hideSetup(){$('setup').classList.remove('visible');$('dash').classList.remove('hidden')}
function clearSetupFields(){['rpc','user','pass','zmq','payout'].forEach(x=>$(x).value='');$('rpc').focus()}
async function saveSetup(){
 const body={BCH_RPC_URL:$('rpc').value.trim(),BCH_RPC_USER:$('user').value.trim(),BCH_RPC_PASSWORD:$('pass').value,BCH_ZMQ_URL:$('zmq').value.trim(),BCH_PAYOUT_ADDRESS:$('payout').value.trim()};
 $('setupmsg').textContent='Testing RPC and saving…';
 try{
  const x=await api('/api/setup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(x.ok){await saveVardiffSettings(true);$('setupmsg').textContent=x.message}else $('setupmsg').textContent='Error: '+x.error
 }catch(e){$('setupmsg').textContent='Error: '+e}
}
async function saveVardiffSettings(silent=false){
 const enabled=$('vardiffEnabled').checked,target=Number($('vardiffTarget').value),start=Number($('startDiff').value),minimum=Number($('minDiff').value),maximum=Number($('maxDiff').value);
 if(!isFinite(target)||!isFinite(start)||!isFinite(minimum)||!isFinite(maximum)||target<5||target>600||start<=0||minimum<=0||maximum<=0){$('setupmsg').textContent='Error: enter valid Vardiff values';return false}
 try{
  const x=await api('/api/vardiff-settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled,target_seconds:target,start_difficulty:start,min_difficulty:minimum,max_difficulty:maximum})});
  if(!x.ok){$('setupmsg').textContent='Error: '+x.error;return false}
  if(!silent)$('setupmsg').textContent=x.message+(x.miners_updated?' · Updated '+x.miners_updated+' miner(s)':'');
  return true
 }catch(e){$('setupmsg').textContent='Error: '+e;return false}
}
async function savePoolSettings(silent=false){return saveVardiffSettings(silent)}
function displayWorkerName(name){
 const s=String(name||'').trim();
 if(!s)return 'Unknown';
 // Pool usernames are payoutAddress.workerName. Never expose the payout
 // address on the dashboard; show only the worker suffix.
 const dot=s.lastIndexOf('.');
 return dot>=0 && dot<s.length-1 ? s.slice(dot+1) : s;
}
function renderWorkers(workers){
 $('workerCount').textContent=workers.length+' worker'+(workers.length===1?'':'s');
 if(!workers.length){$('workers').innerHTML='<div class="empty">No miners connected yet.</div>';return}
 $('workers').innerHTML=workers.map(w=>'<div class="worker"><div class="workerTop"><div class="workerName">'+esc(displayWorkerName(w.worker))+'</div><div class="workerHash">'+fmtHash(w.hashrate||0)+'</div></div><div class="workerMeta"><div><span>Accepted</span><b>'+fmtNum(w.shares)+'</b></div><div><span>Rejected</span><b>'+fmtNum(w.rejected)+'</b></div><div><span>Share diff</span><b>'+fmtDifficulty(w.difficulty)+'</b></div><div><span>Best diff</span><b>'+fmtDifficulty(w.best_diff)+'</b></div><div><span>Session best</span><b>'+fmtDifficulty(w.session_best_diff)+'</b></div></div><div class="muted" style="font-size:9px;margin-top:8px">Last seen · '+ago(w.last_seen)+'</div></div>').join('')
}
async function refreshData(){
 try{
  await setupState();const x=await api('/api/status');lastData=x;const n=x.node||{},m=x.mining||{},net=x.network||{},workers=x.workers||[];
  const online=n.blocks!=null, syncing=!!n.initialblockdownload;
  const poolHash=workers.reduce((a,w)=>a+Number(w.hashrate||0),0);
  const accepted=workers.reduce((a,w)=>a+Number(w.shares||0),0),rejected=workers.reduce((a,w)=>a+Number(w.rejected||0),0),total=accepted+rejected;
  const progress=n.verificationprogress!=null?Math.max(0,Math.min(100,Number(n.verificationprogress)*100)):0;
  $('topStatus').textContent=online?(syncing?'Node syncing':'Pool online'):'Node offline';
  $('statusText') && ($('statusText').textContent=online?(syncing?'Node syncing':'Pool online'):'Node offline');
  $('node').textContent=online?(syncing?'Syncing':'Online'):'Offline';$('chain').textContent=n.chain||'BCHN';
  $('height').textContent=fmtNum(x.height);$('miners').textContent=fmtNum(x.miners_connected);$('peers').textContent=fmtNum(net.connections);$('job').textContent=x.job_id||'—';$('jobAge').textContent=x.job_created?'Job '+ago(x.job_created):'Job age unavailable';$('txs').textContent=fmtNum(x.tx_count)+' transactions';
  $('heroHash').textContent=fmtHash(poolHash);$('heroMiners').textContent=fmtNum(x.miners_connected)+' connected';$('heroJob').textContent=x.job_id||'—';
  $('stratum').textContent='stratum+tcp://'+location.hostname+':3334';
  $('difficulty').textContent=fmtDifficulty(m.difficulty);
    $('vardiffStatus').textContent=x.vardiff_enabled===false?'OFF':('ON · '+fmtNum(x.vardiff_target_seconds)+'s');
    $('networkHashrate').textContent=fmtHash(m.networkhashps);$('poolHashrate').textContent=fmtHash(poolHash);
  $('bestDiff').textContent=workers.length?Math.max(...workers.map(w=>Number(w.best_diff||0))).toFixed(6):'—';$('accepted').textContent=fmtNum(accepted);$('rejected').textContent=fmtNum(rejected)+' ('+(total?(rejected/total*100).toFixed(2):'0')+'%)';
  $('reward').textContent=x.coinbase_value?((Number(x.coinbase_value)/1e8).toFixed(8)+' BCH'):'—';$('jobTxs').textContent=fmtNum(x.tx_count);$('target').textContent=x.network_target?'0x'+x.network_target.slice(0,18)+'…':'—';
  renderWorkers(workers);
  $('blocks').innerHTML=(x.blocks||[]).length?(x.blocks||[]).map(b=>'<tr><td>'+fmtTime(b.time)+'</td><td>'+esc(b.height)+'</td><td>'+esc(b.worker)+'</td><td><code>'+esc((b.hash||'').slice(0,18))+'…</code></td><td><span class="badge '+(String(b.result).toLowerCase()==='none'?'':'bad')+'">'+esc(b.result||'submitted')+'</span></td></tr>').join(''):'<tr><td colspan="5" class="empty">No block submissions yet.</td></tr>';
  $('events').innerHTML=(x.events||[]).slice(0,12).map(e=>'<div class="event"><div class="eventIcon">'+(String(e.kind).toLowerCase().includes('block')?'◆':'•')+'</div><div class="eventText"><b>'+esc(e.kind)+'</b> '+esc(e.detail||'')+'<div class="eventTime">'+esc(e.worker||'pool')+' · '+fmtTime(e.time)+'</div></div></div>').join('')||'<div class="empty">No events yet.</div>';
  $('nodeChain').textContent=n.chain||'—';$('progress').textContent=n.verificationprogress!=null?(Number(n.verificationprogress)*100).toFixed(2)+'%':'—';$('headers').textContent=fmtNum(n.headers);$('connections').textContent=fmtNum(net.connections);$('poolStatus').textContent=online?'Running':'Node unavailable';
  $('syncBadge').textContent=syncing?'SYNCING':'LIVE';$('nodeState').textContent=syncing?'SYNCING':'BCHN';
  $('progressPct').textContent=progress.toFixed(1)+'%';$('ring').style.setProperty('--progress',progress+'%');$('syncTitle').textContent=online?(syncing?'BCHN is synchronizing':'BCHN is fully available'):'BCHN unavailable';
  $('syncDetail').textContent=online?('Height '+fmtNum(n.blocks)+' · '+fmtNum(n.headers)+' headers · '+fmtNum(net.connections)+' peer connections'): 'Waiting for live BCHN blockchain information.';
 }catch(e){$('topStatus').textContent='Connection error';$('node').textContent='Offline';$('syncTitle').textContent='Connection error'}
}
async function newJob(){try{const x=await api('/api/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'refresh_job'})});if(!x.ok)throw Error(x.error||'Failed');await refreshData()}catch(e){alert('Could not create new job: '+e.message)}}
async function copyStratum(){const s='stratum+tcp://'+location.hostname+':3334';try{await navigator.clipboard.writeText(s)}catch(e){prompt('Copy Stratum endpoint:',s)}}
function setTheme(theme){document.body.classList.toggle('dark',theme==='dark');try{localStorage.setItem('bch_pool_theme',theme)}catch(e){}updateThemeButtons()}\nfunction updateThemeButtons(){const dark=document.body.classList.contains('dark');$('lightBtn').classList.toggle('active',!dark);$('darkBtn').classList.toggle('active',dark)}\nfunction restoreTheme(){let theme='light';try{theme=localStorage.getItem('bch_pool_theme')||'light'}catch(e){}setTheme(theme)}\nfunction setAuto(){if(timer)clearInterval(timer);const enabled=$('auto').checked;try{localStorage.setItem('bch_pool_auto_refresh',enabled?'1':'0')}catch(e){}timer=enabled?setInterval(refreshData,30000):null}
function restoreAuto(){let enabled=true;try{const saved=localStorage.getItem('bch_pool_auto_refresh');if(saved!==null)enabled=saved==='1'}catch(e){}$('auto').checked=enabled;if(enabled)timer=setInterval(refreshData,30000)}
restoreTheme();refreshData();restoreAuto();
</script>
</body></html>"""
