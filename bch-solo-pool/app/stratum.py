import asyncio
import json
import logging
import os
import struct
import time
import secrets

from .crypto import (
    sha256d, ser_compact_size, encode_compact_target,
    difficulty_to_target, target_to_difficulty, hash_meets_target
)
from .address import address_to_script

log = logging.getLogger(__name__)


def push_data(data: bytes) -> bytes:
    n = len(data)
    if n < 76:
        return bytes([n]) + data
    if n <= 255:
        return b"\x4c" + bytes([n]) + data
    return b"\x4d" + struct.pack("<H", n) + data




def reverse_32bit_words(data: bytes) -> bytes:
    """Reverse the order of 32-bit words without changing each word."""
    if len(data) % 4:
        raise ValueError("32-bit word reversal requires a multiple of 4 bytes")
    return b"".join(
        data[i:i + 4] for i in range(len(data) - 4, -1, -4)
    )

def encode_height(height):
    n = int(height)
    b = bytearray()
    while n:
        b.append(n & 0xff)
        n >>= 8
    if not b:
        b.append(0)
    if b[-1] & 0x80:
        b.append(0)
    return bytes(b)


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
        # RPC txid is displayed big-endian. Stratum merkle branches use the
        # internal/little-endian transaction hash byte order.
        self.tx_hashes = [bytes.fromhex(x["txid"])[::-1] for x in template.get("transactions", [])]
        self.merkle_branch = self._branches(self.tx_hashes)
        self.payout_script = payout_script
        self.coinbase_value = int(template["coinbasevalue"])
        self.ex1_size = ex1_size
        self.ex2_size = ex2_size

        msg = coinbase_message.encode("utf-8", "ignore")[:60]
        script = push_data(encode_height(self.height)) + msg
        # Coinbase scriptSig must remain 2..100 bytes after extranonce fields.
        min_len = len(script) + ex1_size + ex2_size
        if min_len < 2:
            script += b"\x00" * (2 - min_len)
        if len(script) + ex1_size + ex2_size > 100:
            script = script[:100 - ex1_size - ex2_size]
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
            branches.append(level[index ^ 1])
            index //= 2
            level = [sha256d(level[i] + level[i + 1]) for i in range(0, len(level), 2)]
        return branches

    def coinbase(self, ex1, ex2):
        version = struct.pack("<I", 1)
        vin = b"\x01" + b"\x00" * 32 + b"\xff" * 4
        scriptsig = self.script_sig_prefix + ex1 + ex2 + self.script_sig_suffix
        if not 2 <= len(scriptsig) <= 100:
            raise ValueError("coinbase scriptSig must be 2..100 bytes")
        vin += ser_compact_size(len(scriptsig)) + scriptsig + b"\xff" * 4
        vout = (
            b"\x01" + struct.pack("<Q", self.coinbase_value) +
            ser_compact_size(len(self.payout_script)) + self.payout_script
        )
        return version + vin + vout + b"\x00" * 4

    def merkle_for_coinbase(self, cb):
        h = sha256d(cb)
        for branch in self.merkle_branch:
            h = sha256d(h + branch)
        return h

    def header(self, coinbase, nonce, ntime=None, version=None):
        ntime = self.ntime if ntime is None else int(ntime)
        version = self.version if version is None else int(version)
        return (
            struct.pack("<I", version) +
            bytes.fromhex(self.prevhash)[::-1] +
            self.merkle_for_coinbase(coinbase) +
            struct.pack("<I", ntime) +
            bytes.fromhex(self.bits)[::-1] +
            struct.pack("<I", int(nonce))
        )

    def block_hex(self, coinbase, nonce, ntime=None, version=None):
        header = self.header(coinbase, nonce, ntime, version)
        txs = [coinbase] + [bytes.fromhex(x) for x in self.tx_hex]
        return (header + ser_compact_size(len(txs)) + b"".join(txs)).hex()

    def notify(self, clean=True):
        coinbase_prefix = (
            struct.pack("<I", 1) +
            b"\x01" + b"\x00" * 32 + b"\xff" * 4 +
            ser_compact_size(self.script_sig_len) + self.script_sig_prefix
        ).hex()
        coinbase_suffix = (
            b"\xff" * 4 + b"\x01" + struct.pack("<Q", self.coinbase_value) +
            ser_compact_size(len(self.payout_script)) + self.payout_script + b"\x00" * 4
        ).hex()
        return [
            self.job_id,
            reverse_32bit_words(bytes.fromhex(self.prevhash)).hex(),
            coinbase_prefix,
            coinbase_suffix,
            [x.hex() for x in self.merkle_branch],
            # Stratum encodes version and ntime as normal 8-char
            # hexadecimal uint32 strings. Do not serialize these two fields
            # in little-endian byte order; only the binary block header does.
            f"{self.version & 0xffffffff:08x}",
            self.bits,
            f"{self.ntime & 0xffffffff:08x}",
            bool(clean),
        ]


class Miner:
    def __init__(self, reader, writer, pool):
        self.reader = reader
        self.writer = writer
        self.pool = pool
        self.worker = "unknown"
        self.ex1 = os.urandom(4)
        self.ex2_size = 4
        # BIP310 version-rolling is used by Bitaxe/AxeOS and some Avalon
        # firmware. The miner submits the XOR delta from the job version.
        self.version_mask = 0x1fffe000
        self.version_rolling = False
        self.subscribed = False
        self.authorized = False
        self.difficulty = pool.start_difficulty
        # Difficulty is applied to future jobs. Keep the difficulty that was
        # active when each job was issued so vardiff changes cannot reject
        # valid shares from work the miner is still finishing.
        self.job_difficulties = {}
        self.last_share = 0.0
        self.shares = 0
        self.first_share_time = 0.0
        self.accepted_work = 0.0
        self.hashrate = 0.0

    async def send(self, obj):
        self.writer.write((json.dumps(obj, separators=(",", ":")) + "\n").encode())
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
                if not isinstance(msg, dict):
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
            await self.send({
                "id": mid,
                "result": [[["mining.set_difficulty", "1"], ["mining.notify", "1"]], self.ex1.hex(), self.ex2_size],
                "error": None
            })
            await self.send({"id": None, "method": "mining.set_difficulty", "params": [self.difficulty]})
            if self.pool.job:
                self.pool.remember_job_for_miner(self)
                await self.send({"id": None, "method": "mining.notify", "params": self.pool.job.notify(True)})
            return

        if method == "mining.configure":
            # BIP310: advertise version rolling. Bitaxe/AxeOS sends the
            # sixth mining.submit parameter when it rolls the header version.
            extensions = params[0] if params else []
            requested = {}
            if len(params) > 1 and isinstance(params[1], dict):
                requested = params[1]
            if isinstance(extensions, list) and "version-rolling" in extensions:
                mask = self.version_mask
                requested_mask = requested.get("version-rolling.mask")
                if isinstance(requested_mask, str):
                    try:
                        mask &= int(requested_mask, 16)
                    except ValueError:
                        pass
                self.version_mask = mask
                self.version_rolling = True
                result = {
                    "version-rolling": True,
                    "version-rolling.mask": f"{mask:08x}",
                }
                await self.send({"id": mid, "result": result, "error": None})
                await self.send({
                    "id": None,
                    "method": "mining.set_version_mask",
                    "params": [f"{mask:08x}"]
                })
            else:
                result = {"version-rolling": False}
                await self.send({"id": mid, "result": result, "error": None})
            return

        if method == "mining.suggest_difficulty":
            try:
                suggested = float(params[0])
            except (IndexError, TypeError, ValueError):
                suggested = 0.0
            if suggested > 0 and self.shares == 0:
                self.difficulty = max(
                    self.pool.cfg.vardiff_min,
                    min(self.pool.cfg.vardiff_max, suggested)
                )
                self.pool.db.touch_worker(self.worker, self.difficulty)
            await self.send({"id": mid, "result": True, "error": None})
            if suggested > 0 and self.shares == 0:
                await self.send({
                    "id": None,
                    "method": "mining.set_difficulty",
                    "params": [self.difficulty]
                })
            return

        if method == "mining.authorize":
            self.worker = str(params[0]) if params else "worker"
            self.authorized = True
            self.pool.db.touch_worker(self.worker, self.difficulty)
            await self.send({"id": mid, "result": True, "error": None})
            return

        if method == "mining.extranonce.subscribe":
            await self.send({"id": mid, "result": True, "error": None})
            return

        if method == "mining.submit":
            result, error = await self.pool.submit_share(self, params)
            await self.send({"id": mid, "result": result, "error": error})
            return

        if method == "mining.get_version":
            await self.send({"id": mid, "result": "bch-solo-pool/1.1", "error": None})
            return

        if mid is not None:
            await self.send({"id": mid, "result": None, "error": [20, "Method not found", None]})


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
        self._seen_shares = set()
        self._seen_order = []
        self._seen_limit = 100000

    async def refresh_job(self, reason="poll"):
        try:
            template = await asyncio.to_thread(self.rpc.get_template)
            async with self.job_lock:
                self.job = Job(template, self.payout_script, self.cfg.coinbase_message)
            log.info(
                "new job height=%s job=%s reason=%s txs=%s",
                self.job.height, self.job.job_id, reason, len(self.job.tx_hex)
            )
            await self.broadcast_job(clean=True)
        except Exception:
            log.exception("template refresh failed")

    async def broadcast_job(self, clean=False):
        if not self.job:
            return
        for miner in list(self.miners):
            try:
                self.remember_job_for_miner(miner)
                msg = {"id": None, "method": "mining.notify", "params": self.job.notify(clean)}
                await miner.send(msg)
            except Exception:
                self.miners.discard(miner)

    def remember_job_for_miner(self, miner):
        """Pin the share difficulty to the job the miner was actually given."""
        if not self.job:
            return
        miner.job_difficulties[self.job.job_id] = miner.difficulty
        # Keep only a small history in case a miner submits a just-finished job.
        if len(miner.job_difficulties) > 16:
            for old in list(miner.job_difficulties)[:-16]:
                miner.job_difficulties.pop(old, None)

    def _remember_share(self, key):
        if key in self._seen_shares:
            return False
        self._seen_shares.add(key)
        self._seen_order.append(key)
        if len(self._seen_order) > self._seen_limit:
            old = self._seen_order.pop(0)
            self._seen_shares.discard(old)
        return True

    async def submit_share(self, miner, params):
        if not self.job:
            return False, [21, "No current job", None]
        if not miner.authorized:
            return False, [24, "Unauthorized", None]
        if len(params) < 5:
            return False, [20, "Malformed submit", None]

        worker, job_id, ex2_hex, ntime_hex, nonce_hex = map(str, params[:5])
        version_bits_hex = str(params[5]) if len(params) >= 6 else None
        if worker != miner.worker:
            return False, [21, "Worker mismatch", None]
        if job_id != self.job.job_id:
            return False, [21, "Stale share", None]

        try:
            ex2 = bytes.fromhex(ex2_hex)
            ntime = int(ntime_hex, 16)
            nonce = int(nonce_hex, 16)
            version_bits = int(version_bits_hex, 16) if version_bits_hex is not None else 0
        except ValueError:
            return False, [20, "Invalid hex", None]

        if version_bits & ~miner.version_mask:
            return False, [20, "Invalid version rolling bits", None]
        if version_bits_hex is not None and not miner.version_rolling:
            return False, [20, "Version rolling not negotiated", None]

        if len(ex2) != miner.ex2_size or not 0 <= nonce <= 0xffffffff:
            return False, [20, "Invalid extranonce2/nonce", None]
        mintime = int(self.job.template.get("mintime", 0))
        # mining.submit ntime is a normal uint32 hex value, matching the
        # ntime field advertised by mining.notify. The block header later
        # serializes it as little-endian bytes.
        if not mintime <= ntime <= int(time.time()) + 7200:
            return False, [20, "Invalid ntime", None]

        key = (self.job.job_id, miner.worker, ex2_hex.lower(), ntime, nonce, version_bits)
        if not self._remember_share(key):
            return False, [22, "Duplicate share", None]

        cb = self.job.coinbase(miner.ex1, ex2)
        header_version = self.job.version ^ version_bits
        header = self.job.header(cb, nonce, ntime, header_version)
        digest = sha256d(header)

        # A vardiff update must never retroactively change the target for a
        # job already in the miner. Validate against the difficulty pinned to
        # this job, not the miner's newer pending/current difficulty.
        share_difficulty = miner.job_difficulties.get(
            self.job.job_id, miner.difficulty
        )
        share_target = difficulty_to_target(share_difficulty)
        if not hash_meets_target(digest, share_target):
            self.db.share(miner.worker, False, difficulty=share_difficulty)
            return False, [23, "Low difficulty share", None]

        block = hash_meets_target(digest, self.job.network_target)
        best_diff = target_to_difficulty(int.from_bytes(digest, "little"))
        now = time.time()
        previous = miner.last_share
        miner.last_share = now
        miner.shares += 1

        # Estimate effective hashrate from accepted share work. Each accepted
        # share represents approximately difficulty * 2^32 hashes. Use the
        # difficulty the miner is currently working at, which matches ASIC
        # firmware such as Bitaxe that applies mining.set_difficulty promptly.
        if not miner.first_share_time:
            miner.first_share_time = now
        miner.accepted_work += max(miner.difficulty, 0.000001) * (2 ** 32)
        elapsed = now - miner.first_share_time
        if elapsed >= 1.0:
            miner.hashrate = miner.accepted_work / elapsed

        self.db.share(
            miner.worker,
            True,
            best_diff,
            hashrate=miner.hashrate,
            difficulty=share_difficulty
        )

        if self.cfg.vardiff_enabled and previous:
            interval = now - previous
            nd = miner.difficulty

            # Vardiff must not react to a single lucky share by repeatedly
            # doubling into an absurd target. Require a few accepted shares
            # before increasing difficulty and limit each adjustment.
            if miner.shares >= 4:
                if interval < self.cfg.vardiff_target_seconds / 2:
                    nd *= 2
                elif interval > self.cfg.vardiff_target_seconds * 2:
                    nd /= 2

            nd = min(self.cfg.vardiff_max, max(self.cfg.vardiff_min, nd))
            if abs(nd - miner.difficulty) / max(miner.difficulty, 1e-12) >= 0.01:
                miner.difficulty = nd
                self.db.touch_worker(miner.worker, nd)
                try:
                    await miner.send({"id": None, "method": "mining.set_difficulty", "params": [nd]})
                except Exception:
                    pass

        if block:
            block_hex = self.job.block_hex(cb, nonce, ntime, header_version)
            result = await asyncio.to_thread(self.rpc.submit_block, block_hex)
            h = digest[::-1].hex()
            self.db.block(self.job.height, self.job.job_id, miner.worker, h, str(result))
            self.db.event("block", miner.worker, f"height={self.job.height} hash={h} result={result}")
            log.warning(
                "BLOCK CANDIDATE: height=%s worker=%s hash=%s submit=%s",
                self.job.height, miner.worker, h, result
            )
            await self.refresh_job("block-submit")

        return True, None
