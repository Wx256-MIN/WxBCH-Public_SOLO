import base64
import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

RPC_HOST = os.getenv("BCH_RPC_HOST", "bchn")
RPC_PORT = os.getenv("BCH_RPC_PORT", "8332")
RPC_ENV = "/run/wxbch/rpc.env"
SOCK_PARENT = os.getenv("CKPOOL_SOCKET_PARENT", "/run")
SOCK_NAME = os.getenv("CKPOOL_SOCKET_NAME", "ckpool")
SOCK_PROCESS = os.getenv("CKPOOL_SOCKET_PROCESS", "stratifier")

with open(RPC_ENV) as f:
    env = dict(line.strip().split("=", 1) for line in f if "=" in line)

def rpc(method, params=None):
    body = json.dumps({"jsonrpc":"1.0","id":"wxbch","method":method,"params":params or []}).encode()
    token = base64.b64encode(f'{env["BCH_RPC_USER"]}:{env["BCH_RPC_PASSWORD"]}'.encode()).decode()
    req = Request(f"http://{RPC_HOST}:{RPC_PORT}/", data=body, headers={
        "Content-Type":"application/json", "Authorization":f"Basic {token}"
    })
    with urlopen(req, timeout=8) as r:
        return json.loads(r.read())

def ck(command):
    try:
        p = subprocess.run(
            ["ckpmsg", "-s", SOCK_PARENT, "-n", SOCK_NAME, "-N", SOCK_PROCESS],
            input=command + "\n", text=True, capture_output=True, timeout=3
        )
        out = p.stdout
        marker = "Received response: "
        if marker in out:
            out = out.split(marker, 1)[1]
        out = out.replace("\n", "")
        return json.loads(out)
    except Exception as e:
        return {"error": str(e)}

class Handler(BaseHTTPRequestHandler):
    def send_json(self, obj, status=200):
        raw = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type","application/json")
        self.send_header("Cache-Control","no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/api/status":
            try:
                chain = rpc("getblockchaininfo")["result"]
                net = rpc("getnetworkinfo")["result"]
                stats = ck("stats")
                self.send_json({
                    "ok": True,
                    "node": {
                        "blocks": chain.get("blocks"),
                        "headers": chain.get("headers"),
                        "verificationprogress": chain.get("verificationprogress"),
                        "initialblockdownload": chain.get("initialblockdownload"),
                        "chain": chain.get("chain"),
                        "connections": net.get("connections"),
                        "version": net.get("subversion")
                    },
                    "pool": stats
                })
            except Exception as e:
                self.send_json({"ok":False,"error":str(e)}, 503)
            return

        if self.path == "/api/workers":
            self.send_json(ck("workers"))
            return

        if self.path == "/api/users":
            self.send_json(ck("users"))
            return

        if self.path == "/":
            body = open("index.html", "rb").read()
            self.send_response(200)
            self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Content-Length",str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_error(404)

    def log_message(self, *_):
        pass

ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
