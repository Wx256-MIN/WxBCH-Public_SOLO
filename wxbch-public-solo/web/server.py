import base64,json,os,subprocess,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.request import Request,urlopen
RPC_HOST=os.getenv("BCH_RPC_HOST","bchn"); RPC_PORT=os.getenv("BCH_RPC_PORT","8332"); RPC_ENV="/run/wxbch/rpc.env"
SOCK_PARENT=os.getenv("CKPOOL_SOCKET_PARENT","/run"); SOCK_NAME=os.getenv("CKPOOL_SOCKET_NAME","ckpool"); SOCK_PROCESS=os.getenv("CKPOOL_SOCKET_PROCESS","stratifier")
SETTINGS="/run/wxbch/node-settings.conf"
for _ in range(60):
    if os.path.exists(RPC_ENV): break
    time.sleep(1)
with open(RPC_ENV) as f: env=dict(line.strip().split("=",1) for line in f if "=" in line)
def rpc(method,params=None):
    body=json.dumps({"jsonrpc":"1.0","id":"wxbch","method":method,"params":params or []}).encode()
    token=base64.b64encode(f'{env["BCH_RPC_USER"]}:{env["BCH_RPC_PASSWORD"]}'.encode()).decode()
    req=Request(f"http://{RPC_HOST}:{RPC_PORT}/",data=body,headers={"Content-Type":"application/json","Authorization":f"Basic {token}"})
    with urlopen(req,timeout=8) as r: return json.loads(r.read())
def ck(command):
    try:
        p=subprocess.run(["ckpmsg","-s",SOCK_PARENT,"-n",SOCK_NAME,"-N",SOCK_PROCESS],input=command+"\n",text=True,capture_output=True,timeout=3)
        out=p.stdout; marker="Received response: "; out=out.split(marker,1)[1] if marker in out else out
        return json.loads("".join(out.splitlines()))
    except Exception as e: return {"error":str(e)}
def read_prune():
    try:
        with open(SETTINGS) as f:
            for line in f:
                if line.startswith("PRUNE_MB="): return int(line.split("=",1)[1].strip())
    except Exception: pass
    return 0
def write_prune(value):
    value=int(value)
    if value not in (0,5500,10240,25600,51200): raise ValueError("Unsupported pruning target")
    tmp=SETTINGS+".tmp"
    with open(tmp,"w") as f: f.write(f"PRUNE_MB={value}\n")
    os.chmod(tmp,0o600); os.replace(tmp,SETTINGS)
class Handler(BaseHTTPRequestHandler):
    def send_json(self,obj,status=200):
        raw=json.dumps(obj).encode(); self.send_response(status); self.send_header("Content-Type","application/json"); self.send_header("Cache-Control","no-store"); self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_GET(self):
        if self.path=="/api/status":
            try:
                chain=rpc("getblockchaininfo")["result"]; net=rpc("getnetworkinfo")["result"]; mem=rpc("getmempoolinfo")["result"]
                blocks=chain.get("blocks",0); headers=chain.get("headers",0)
                self.send_json({"ok":True,"node":{
                    "blocks":blocks,"headers":headers,"blocks_remaining":max(0,headers-blocks),
                    "verificationprogress":chain.get("verificationprogress"),
                    "initialblockdownload":chain.get("initialblockdownload"),
                    "chain":chain.get("chain"),"chainwork":chain.get("chainwork"),
                    "bestblockhash":chain.get("bestblockhash"),"size_on_disk":chain.get("size_on_disk"),
                    "pruned":chain.get("pruned",False),"pruneheight":chain.get("pruneheight"),
                    "automatic_pruning":chain.get("automatic_pruning",False),
                    "prune_target_mib":round(chain.get("prune_target_size",0)/1048576) if chain.get("prune_target_size") else 0,
                    "warnings":chain.get("warnings",[]),"mediantime":chain.get("mediantime"),
                    "connections":net.get("connections"),"version":net.get("subversion"),
                    "networkactive":net.get("networkactive"),"protocolversion":net.get("protocolversion"),
                    "mempool_size":mem.get("size"),"mempool_bytes":mem.get("bytes")
                },"pool":ck("stats"),"settings":{"prune_mb":read_prune()}})
            except Exception as e: self.send_json({"ok":False,"error":str(e)},503)
            return
        if self.path=="/api/workers": self.send_json(ck("workers")); return
        if self.path=="/api/users": self.send_json(ck("users")); return
        if self.path=="/": 
            body=open("index.html","rb").read(); self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8"); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body); return
        self.send_error(404)
    def do_POST(self):
        if self.path!="/api/pruning": self.send_error(404); return
        try:
            n=int(self.headers.get("Content-Length","0")); data=json.loads(self.rfile.read(n) or b"{}"); write_prune(data.get("prune_mb",0))
            self.send_json({"ok":True,"prune_mb":read_prune(),"restart_required":True})
        except Exception as e: self.send_json({"ok":False,"error":str(e)},400)
    def log_message(self,*_): pass
ThreadingHTTPServer(("0.0.0.0",8080),Handler).serve_forever()
