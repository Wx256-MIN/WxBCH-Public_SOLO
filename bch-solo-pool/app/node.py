import base64
import json
import threading
import urllib.request

log = __import__("logging").getLogger(__name__)


class BCHRPC:
    def __init__(self, url, user, password, timeout=20):
        self.url = url
        self.timeout = timeout
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self.headers = {"Content-Type": "application/json", "Authorization": f"Basic {token}"}
        self._id = 0
        self.lock = threading.Lock()

    def call(self, method, params=None):
        with self.lock:
            self._id += 1
            rid = self._id
        body = json.dumps({
            "jsonrpc": "1.0", "id": rid, "method": method, "params": params or []
        }).encode()
        req = urllib.request.Request(self.url, data=body, headers=self.headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                obj = json.loads(response.read())
        except Exception as exc:
            raise RuntimeError(f"RPC {method} failed: {exc}") from exc
        if obj.get("error"):
            raise RuntimeError(f"RPC {method}: {obj['error']}")
        return obj["result"]

    def get_template(self):
        return self.call("getblocktemplate", [{
            "capabilities": ["coinbasetxn", "workid", "longpoll"]
        }])

    def submit_block(self, block_hex):
        return self.call("submitblock", [block_hex])

    def get_blockchain_info(self):
        return self.call("getblockchaininfo")

    def get_mining_info(self):
        return self.call("getmininginfo")
