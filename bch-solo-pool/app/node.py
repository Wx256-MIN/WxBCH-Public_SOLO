import base64
import json
import threading
import urllib.parse
import urllib.request
import time

log = __import__("logging").getLogger(__name__)


def normalize_rpc_url(url):
    """Accept normal HTTP URLs and convenient host:port input."""
    value = str(url or "").strip()
    if not value:
        raise ValueError("RPC URL is required")

    parsed = urllib.parse.urlparse(value)
    if not parsed.scheme:
        value = "http://" + value
        parsed = urllib.parse.urlparse(value)

    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            "RPC URL must start with http:// or https:// "
            "(for example http://bitcoind:8332/)"
        )

    if not parsed.netloc:
        raise ValueError("RPC URL must include a host, for example http://bitcoind:8332/")

    if not parsed.path:
        value += "/"

    return value


class BCHRPC:
    def __init__(self, url, user, password, timeout=20):
        self.url = normalize_rpc_url(url)
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
        last_exc = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    obj = json.loads(response.read())
                if obj.get("error"):
                    # JSON-RPC application errors are deterministic; retrying
                    # them only adds latency and can hide real configuration
                    # problems.
                    raise RuntimeError(f"RPC {method}: {obj['error']}")
                return obj["result"]
            except RuntimeError:
                raise
            except Exception as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(0.25 * (attempt + 1))
        raise RuntimeError(f"RPC {method} failed after 3 attempts: {last_exc}") from last_exc

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

    def get_network_info(self):
        return self.call("getnetworkinfo")
