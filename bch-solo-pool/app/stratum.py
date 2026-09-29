import asyncio
import json
import logging
import os
import struct
import time
import secrets

from .crypto import sha256d, merkle_root, ser_compact_size, encode_compact_target, difficulty_to_target, target_to_difficulty, hash_meets_target, u32le
from .address import address_to_script

log = logging.getLogger(__name__)

def push_data(data: bytes) -> bytes:
    n = len(data)
    if n < 76:
        return bytes([n]) + data
    if n <= 255:
        return b"\x4c" + bytes([n]) + data
    return b"\x4d" + struct.pack("<H", n) + data

def encode_height(height):
    # Bitcoin ScriptNum minimal positive integer encoding.
    n = height
    b = bytearray()
    while n:
        b.append(n & 0xff)
        n >>= 8
    if not b:
        b = bytearray(b"\x00")
    if b[-1] & 0x80:
        b.append(0)
    return bytes(b)

def tx_hash_from_hex(tx_hex):
    return sha256d(bytes.fromhex(tx_hex))

class Job:
    def __init__(self, template, payout_script, coinbase_message, ex1_size=4, ex2_size=4):
        self.template = template
        self.height = int(template["height"])
        self.job_id = secrets.token_hex(8)
        self.prevhash = template["previousblockhash"]
        self.version = int(template["version"])
        self.bits = template["bits"]
        self.ntime = int(template["curtime"])
        self.network_target = encode_compact_target(self.bits)
        self.created = time.time()
        self.tx_hex = [x["data"] for x in template.get("transactions", [])]
        self.tx_hashes = [bytes.fromhex(x["txid"]) for x in template.get("transactions", [])]
        self.merkle_branch = self._branches(self.tx_hashes)
        self.payout_script = payout_script
        self.coinbase_value = int(template["coinbasevalue"])
        self.ex1_size = ex1_size
        self.ex2_size = ex2_size
        msg = coinbase_message.encode("utf-8", "ignore")[:60]
        script = push_data(encode_height(self.height)) + msg
        # Keep scriptSig within the consensus 2..100 byte range.
        min_extra = 2 + ex1_size + ex2_size
        if len(script) + min_extra < 2:
            script += b"\x00" * (2 - len(script) - min_extra)
        self.script_sig_prefix = script
        self.script_sig_suffix = b""
        self.script_sig_len = len(script) + ex1_size + ex2_size

    def _branches(self, hashes):
        branches = []
        index = 0
        level = list(hashes)
        while len(level) > 1:
            if len(level) & 1:
                level.append(level[-1])
            sibling = index ^ 1
            branches.append(level[sibling])
            index //= 2
            level = [sha256d(level[i] + level[i+1]) for i in range(0, len(level), 2)]
        return branches

    def coinbase_parts(self, ex1, ex2):
        # Coinbase transaction with one P2PKH/P2SH payout output.
        version = struct.pack("<I", 1)
        vin = b"\x01" + b"\x00"*32 + b"\xff"*4
        scriptsig = self.script_sig_prefix + ex1 + ex2 + self.script_sig_suffix
        if not (2 <= len(scriptsig) <= 100):
            raise ValueError("coinbase scriptSig must be 2..100 bytes")
        vin += ser_compact_size(len(scriptsig)) + scriptsig + b"\xff"*4
        vout = b"\x01" + struct.pack("<Q", self.coinbase_value) + ser_compact_size(len(self.payout_script)) + self.payout_script
        return version + vin, vout + b"\x00"*4

    def coinbase(self, ex1, ex2):
        a, b = self.coinbase_parts(ex1, ex2)
        return a + b

    def merkle_for_coinbase(self, cb):
        h = sha256d(cb)
        for branch in self.merkle_branch:
            h = sha256d(h + branch)
        return h

    def header(self, coinbase, nonce, ntime=None, version=None):
        ntime = self.ntime if ntime is None else ntime
        version = self.version if version is None else version
        mr = self.merkle_for_coinbase(coinbase)
        # previousblockhash and merkle root are serialized internally little-endian.
        return (
            struct.pack("<I", version) +
            bytes.fromhex(self.prevhash)[::-1] +
            mr +
            struct.pack("<I", ntime) +
            bytes.fromhex(self.bits)[::-1] +
            struct.pack("<I", nonce)
        )

    def block_hex(self, coinbase, nonce, ntime=None, version=None):
        header = self.header(coinbase, nonce, ntime, version)
        txs = [coinbase] + [bytes.fromhex(x) for x in self.tx_hex]
        return (header + ser_compact_size(len(txs)) + b"".join(txs)).hex()

    def notify(self, clean=True):
        coinbase_prefix = (
            struct.pack("<I", 1) +
            b"\x01" + b"\x00"*32 + b"\xff"*4 +
            ser_compact_size(self.script_sig_len) +
            self.script_sig_prefix
        ).hex()
        coinbase_suffix = (
            b"\xff"*4 +
            b"\x01" + struct.pack("<Q", self.coinbase_value) +
            ser_compact_size(len(self.payout_script)) + self.payout_script +
            b"\x00"*4
        ).hex()
        return [
            self.job_id,
            bytes.fromhex(self.prevhash)[::-1].hex(),
            coinbase_prefix,
            coinbase_suffix,
            [x.hex() for x in self.merkle_branch],
            struct.pack("<I", self.version).hex(),
            self.bits,
            struct.pack("<I", self.ntime).hex(),
            clean
        ]

class Miner:
    def __init__(self, reader, writer, pool):
        self.reader = reader
        self.writer = writer
        self.pool = pool
        self.worker = "unknown"
        self.ex1 = os.urandom(4)
        self.ex2_size = 4
        self.subscribed = False
        self.authorized = False
        self.difficulty = pool.start_difficulty
        self.last_share = 0.0
        self.shares = 0

    async def send(self, obj):
        self.writer.write((json.dumps(obj,separators=(",",":"))+"\n").encode())
        await self.writer.drain()

    async def run(self):
        self.pool.miners.add(self)
        try:
            while True:
                line = await self.reader.readline()
                if not line:
                    break
                if len(line) > 65536:
                    break
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                await self.handle(msg)
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            pass
        except Exception:
            log.exception("miner connection error")
        finally:
            self.pool.miners.discard(self)
            self.pool.db.event("disconnect", self.worker, "")

    async def handle(self, msg):
        method = msg.get("method")
        mid = msg.get("id")
        params = msg.get("params") or []
        if method == "mining.subscribe":
            self.subscribed = True
            await self.send({"id":mid,"result":[[["mining.set_difficulty", "1"],["mining.notify","1"]], self.ex1.hex(), self.ex2_size],"error":None})
            await self.send({"id":None,"method":"mining.set_difficulty","params":[self.difficulty]})
            if self.pool.job:
                await self.send({"id":None,"method":"mining.notify","params":self.pool.job.notify(True)})
            return
        if method in ("mining.authorize", "mining.configure"):
            if method == "mining.authorize":
                username = str(params[0]) if params else "worker"
                self.worker = username
                if "." in username:
                    self.worker = username
            self.authorized = True
            self.pool.db.touch_worker(self.worker, self.difficulty)
            await self.send({"id":mid,"result":True,"error":None})
            return
        if method == "mining.extranonce.subscribe":
            await self.send({"id":mid,"result":True,"error":None})
            return
        if method == "mining.submit":
            result, error = await self.pool.submit_share(self, params)
            await self.send({"id":mid,"result":result,"error":error})
            return
        if method == "mining.get_version":
            await self.send({"id":mid,"result":"bch-solo-pool/1.0","error":None})
            return
        if mid is not None:
            await self.send({"id":mid,"result":None,"error":[20,"Method not found",None]})

class Pool:
    def __init__(self, cfg, rpc, db):
        self.cfg = cfg
        self.rpc = rpc
        self.db = db
        self.miners = set()
        self.job = None
        self.job_lock = asyncio.Lock()
        self.start_difficulty = cfg.start_difficulty
        self.payout_script = address_to_script(cfg.payout_address)
        self.running = True

    async def refresh_job(self, reason="poll"):
        try:
            template = await asyncio.to_thread(self.rpc.get_template)
            async with self.job_lock:
                old = self.job
                self.job = Job(template, self.payout_script, self.cfg.coinbase_message)
            log.info("new job height=%s job=%s reason=%s", self.job.height, self.job.job_id, reason)
            await self.broadcast_job(clean=True)
        except Exception:
            log.exception("template refresh failed")

    async def broadcast_job(self, clean=False):
        if not self.job:
            return
        msg = {"id":None,"method":"mining.notify","params":self.job.notify(clean)}
        dead = []
        for m in list(self.miners):
            try:
                await m.send(msg)
            except Exception:
                dead.append(m)
        for m in dead:
            self.miners.discard(m)

    async def submit_share(self, miner, params):
        if not self.job:
            return False, [21, "No current job", None]
        if len(params) < 5:
            return False, [20, "Malformed submit", None]
        worker, job_id, ex2_hex, ntime_hex, nonce_hex = map(str, params[:5])
        if worker != miner.worker:
            return False, [21, "Worker mismatch", None]
        if job_id != self.job.job_id:
            return False, [21, "Stale share", None]
        try:
            ex2 = bytes.fromhex(ex2_hex)
            ntime = int(ntime_hex, 16)
            nonce = int(nonce_hex, 16)
        except ValueError:
            return False, [20, "Invalid hex", None]
        if len(ex2) != miner.ex2_size or not (0 <= nonce <= 0xffffffff):
            return False, [20, "Invalid extranonce2/nonce", None]
        if not (self.job.template.get("mintime", 0) <= ntime <= int(time.time()) + 7200):
            return False, [20, "Invalid ntime", None]

        cb = self.job.coinbase(miner.ex1, ex2)
        header = self.job.header(cb, nonce, ntime)
        digest = sha256d(header)
        share_target = difficulty_to_target(miner.difficulty)
        if not hash_meets_target(digest, share_target):
            self.db.share(miner.worker, False)
            return False, [23, "Low difficulty share", None]

        block = hash_meets_target(digest, self.job.network_target)
        best_diff = target_to_difficulty(int.from_bytes(digest, "little"))
        miner.last_share = time.time()
        miner.shares += 1
        self.db.share(miner.worker, True, best_diff)

        # Simple per-worker vardiff: adjust only after an accepted share and
        # never outside the configured bounds. This is intentionally gentle
        # to avoid thrashing small ASICs.
        if self.cfg.vardiff_enabled and miner.last_share:
            # miner.last_share is set immediately above, so use the previous
            # timestamp captured by the connection when available.
            prev = getattr(miner, "_prev_share_time", 0.0)
            if prev:
                interval = miner.last_share - prev
                nd = miner.difficulty
                if interval < self.cfg.vardiff_target_seconds / 2:
                    nd *= 2
                elif interval > self.cfg.vardiff_target_seconds * 2:
                    nd /= 2
                nd = min(self.cfg.vardiff_max, max(self.cfg.vardiff_min, nd))
                if abs(nd - miner.difficulty) / max(miner.difficulty, 1e-12) >= 0.01:
                    miner.difficulty = nd
                    self.db.touch_worker(miner.worker, nd)
                    try:
                        await miner.send({"id":None,"method":"mining.set_difficulty","params":[nd]})
                    except Exception:
                        pass
            miner._prev_share_time = miner.last_share

        if block:
            block_hex = self.job.block_hex(cb, nonce, ntime)
            result = await asyncio.to_thread(self.rpc.submit_block, block_hex)
            h = sha256d(header)[::-1].hex()
            self.db.block(self.job.height, self.job.job_id, miner.worker, h, str(result))
            self.db.event("block", miner.worker, f"height={self.job.height} hash={h} result={result}")
            log.warning("BLOCK CANDIDATE: height=%s worker=%s hash=%s submit=%s", self.job.height, miner.worker, h, result)
            await self.refresh_job("block-submit")
        return True, None
