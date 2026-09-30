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

def apply_version_rolling(base_version: int, version_mask: int, version_bits: int) -> int:
    """Apply BIP310 rolling bits without changing protected version bits."""
    mask = int(version_mask) & 0xffffffff
    base = int(base_version) & 0xffffffff
    bits = int(version_bits) & 0xffffffff
    return ((base & ~mask) | (bits & mask)) & 0xffffffff


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
        # BCHN getblocktemplate returns txid/hash in little-endian
        # hexadecimal. Merkle branches also use that internal byte order,
        # so do not reverse the txid here.
        self.tx_hashes = [bytes.fromhex(x["txid"]) for x in template.get("transactions", [])]
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
        self.minimum_difficulty = 0.0
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
        # Highest share difficulty reached during this TCP session.
        self.session_best_diff = 0.0
        self.share_samples = []
        self.share_time_ema = None
        self.authorized_at = 0.0
        self.send_lock = asyncio.Lock()
        self.closed = False
        self.handshake_timeout = 30.0
        self.activity_timeout = 900.0
        self.last_activity = time.monotonic()
        self.submit_window_start = time.monotonic()
        self.submit_window_count = 0
        self.max_submits_per_second = 20

    async def send(self, obj):
        if self.closed:
            raise ConnectionError("miner connection is closed")
        data = (json.dumps(obj, separators=(",", ":")) + "\n").encode()
        async with self.send_lock:
            if self.closed:
                raise ConnectionError("miner connection is closed")
            self.writer.write(data)
            await asyncio.wait_for(self.writer.drain(), timeout=10.0)

    async def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:
            pass

    async def run(self):
        self.pool.miners.add(self)
        try:
            handshake_deadline = time.monotonic() + self.handshake_timeout
            while True:
                if not (self.subscribed or self.authorized):
                    remaining = handshake_deadline - time.monotonic()
                    if remaining <= 0:
                        log.warning("miner handshake timeout: %s", self.worker)
                        break
                    line = await asyncio.wait_for(self.reader.readline(), remaining)
                else:
                    try:
                        line = await asyncio.wait_for(self.reader.readline(), self.activity_timeout)
                    except asyncio.TimeoutError:
                        log.warning("miner idle timeout: %s", self.worker)
                        break
                if not line:
                    break
                self.last_activity = time.monotonic()
                if len(line) > 65536:
                    log.warning("miner sent oversized Stratum message")
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
            if self.authorized and self.worker != "unknown":
                self.pool.update_worker_connection(self.worker)
            self.pool.db.event("disconnect", self.worker, "")
            await self.close()

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
            # BIP310 extension negotiation. We support version rolling,
            # minimum-difficulty, subscribe-extranonce and info.
            extensions = params[0] if params else []
            requested = params[1] if len(params) > 1 and isinstance(params[1], dict) else {}
            if not isinstance(extensions, list):
                extensions = []

            result = {}

            if "version-rolling" in extensions:
                mask = self.version_mask
                requested_mask = requested.get("version-rolling.mask")
                if isinstance(requested_mask, str):
                    try:
                        mask &= int(requested_mask, 16)
                    except (TypeError, ValueError):
                        mask = 0
                self.version_mask = mask & 0xffffffff
                self.version_rolling = bool(self.version_mask)
                result["version-rolling"] = self.version_rolling
                result["version-rolling.mask"] = f"{self.version_mask:08x}"
            else:
                result["version-rolling"] = False

            if "minimum-difficulty" in extensions:
                try:
                    self.minimum_difficulty = max(
                        0.0, float(requested.get("minimum-difficulty.value", 0))
                    )
                except (TypeError, ValueError):
                    self.minimum_difficulty = 0.0
                result["minimum-difficulty"] = True
                if self.minimum_difficulty:
                    self.difficulty = min(
                        self.pool.cfg.vardiff_max,
                        max(self.difficulty, self.minimum_difficulty)
                    )

            if "subscribe-extranonce" in extensions:
                # extranonce1 is stable for this connection, so no
                # mining.set_extranonce notification is required.
                result["subscribe-extranonce"] = True

            if "info" in extensions:
                result["info"] = True

            await self.send({"id": mid, "result": result, "error": None})

            if self.version_rolling:
                await self.send({
                    "id": None,
                    "method": "mining.set_version_mask",
                    "params": [f"{self.version_mask:08x}"]
                })
            if self.minimum_difficulty > 0 and self.shares == 0:
                await self.send({
                    "id": None,
                    "method": "mining.set_difficulty",
                    "params": [self.difficulty]
                })
            return

        if method == "mining.suggest_difficulty":
            try:
                suggested = float(params[0])
            except (IndexError, TypeError, ValueError):
                suggested = 0.0
            if suggested > 0 and self.shares == 0 and self.pool.cfg.vardiff_enabled:
                self.difficulty = max(
                    self.pool.cfg.vardiff_min,
                    self.minimum_difficulty,
                    min(self.pool.cfg.vardiff_max, suggested)
                )
                self.pool.db.touch_worker(self.worker, self.difficulty)
                await self.send({
                    "id": None,
                    "method": "mining.set_difficulty",
                    "params": [self.difficulty]
                })
            if mid is not None:
                await self.send({"id": mid, "result": True, "error": None})
            return

        if method == "mining.suggest_target":
            try:
                target_hex = str(params[0]).strip().lower()
                if target_hex.startswith("0x"):
                    target_hex = target_hex[2:]
                if not target_hex or len(target_hex) > 64:
                    raise ValueError
                target = int(target_hex, 16)
                if target <= 0 or target > (1 << 256) - 1:
                    raise ValueError
                suggested = target_to_difficulty(target)
            except (IndexError, TypeError, ValueError):
                if mid is not None:
                    await self.send({"id": mid, "result": False,
                                     "error": [20, "Invalid target", None]})
                return
            if suggested > 0 and self.shares == 0 and self.pool.cfg.vardiff_enabled:
                self.difficulty = max(
                    self.pool.cfg.vardiff_min,
                    self.minimum_difficulty,
                    min(self.pool.cfg.vardiff_max, suggested)
                )
                self.pool.db.touch_worker(self.worker, self.difficulty)
                await self.send({
                    "id": None,
                    "method": "mining.set_difficulty",
                    "params": [self.difficulty]
                })
            if mid is not None:
                await self.send({"id": mid, "result": True, "error": None})
            return

        if method == "mining.authorize":
            self.worker = str(params[0]) if params else "worker"
            self.authorized = True
            self.authorized_at = time.time()
            self.pool.db.touch_worker(self.worker, self.difficulty, connected=True)
            await self.send({"id": mid, "result": True, "error": None})
            return

        if method == "mining.extranonce.subscribe":
            await self.send({"id": mid, "result": True, "error": None})
            return

        if method == "mining.submit":
            result, error = await self.pool.submit_share(self, params)
            await self.send({"id": mid, "result": result, "error": error})
            return

        if method == "mining.ping":
            await self.send({"id": mid, "result": True, "error": None})
            return

        if method == "mining.get_transactions":
            job_id = str(params[0]) if params else ""
            requested_ids = params[1] if len(params) > 1 and isinstance(params[1], list) else []
            job = self.pool.jobs.get(job_id)
            if job is None:
                await self.send({"id": mid, "result": [], "error": [21, "Job not found", None]})
                return
            tx_map = {
                str(item.get("txid", "")).lower(): str(item.get("data", ""))
                for item in job.template.get("transactions", [])
            }
            result = [tx_map[x.lower()] for x in requested_ids if str(x).lower() in tx_map]
            await self.send({"id": mid, "result": result, "error": None})
            return

        if method == "client.get_version":
            await self.send({"id": mid, "result": "bch-solo-pool/1.7.1", "error": None})
            return

        if method == "mining.capabilities":
            await self.send({
                "id": mid,
                "result": {
                    "mining.configure": True,
                    "mining.extranonce.subscribe": True,
                    "mining.get_transactions": True,
                    "mining.ping": True,
                    "mining.suggest_difficulty": True,
                    "mining.suggest_target": True,
                    "version-rolling": True
                },
                "error": None
            })
            return

        if method == "mining.get_version":
            await self.send({"id": mid, "result": "bch-solo-pool/1.7.1", "error": None})
            return

        if mid is not None:
            await self.send({"id": mid, "result": None, "error": [20, "Method not found", None]})


    def live_hashrate(self):
        """Return a short-window effective hashrate for dashboard display."""
        now = time.time()
        cutoff = now - 300.0
        self.share_samples = [(t, d) for t, d in self.share_samples if t >= cutoff]
        if not self.share_samples:
            return 0.0

        # Show an immediate expected hashrate after the first accepted share.
        # With multiple shares, use the rolling observed share rate.
        if len(self.share_samples) < 2:
            if now - self.share_samples[-1][0] > 120:
                return 0.0
            return self.share_samples[-1][1] * (2 ** 32) / max(
                self.pool.cfg.vardiff_target_seconds, 1.0
            )

        elapsed = self.share_samples[-1][0] - self.share_samples[0][0]
        if elapsed < 1.0:
            return self.share_samples[-1][1] * (2 ** 32) / max(
                self.pool.cfg.vardiff_target_seconds, 1.0
            )
        work = sum(d * (2 ** 32) for _, d in self.share_samples[1:])
        rate = work / elapsed
        # A worker that has stopped producing shares is not considered live.
        if now - self.share_samples[-1][0] > 120:
            return 0.0
        return rate

class Pool:
    def __init__(self, cfg, rpc, db):
        self.cfg = cfg
        self.rpc = rpc
        self.db = db
        self.miners = set()
        self.job = None
        # Retain a small ring of recent jobs so in-flight same-tip shares
        # survive template refreshes without accepting old-chain work.
        self.jobs = {}
        self.job_history_limit = 8
        self.job_lock = asyncio.Lock()
        self.start_difficulty = cfg.start_difficulty
        self.payout_script = address_to_script(cfg.payout_address)
        self.running = True
        self._seen_shares = set()
        self._seen_order = []
        self._seen_limit = 100000

    def update_worker_connection(self, worker):
        """Keep the DB connected flag correct when a worker has multiple sessions."""
        connected = any(
            miner.authorized and miner.worker == worker
            for miner in self.miners
        )
        self.db.set_worker_connected(worker, connected)

    async def refresh_job(self, reason="poll", only_if_new_block=False):
        try:
            async with self.job_lock:
                template = await asyncio.to_thread(self.rpc.get_template)
                same_tip = False
                if self.job is not None:
                    same_tip = (
                        int(template.get("height", -1)) == self.job.height
                        and str(template.get("previousblockhash", "")) == self.job.prevhash
                    )
                    if only_if_new_block and same_tip:
                        return False

                new_job = Job(template, self.payout_script, self.cfg.coinbase_message)
                self.job = new_job
                self.jobs[new_job.job_id] = new_job
                while len(self.jobs) > self.job_history_limit:
                    oldest = next(iter(self.jobs))
                    self.jobs.pop(oldest, None)

            clean = not same_tip
            log.info(
                "new job height=%s job=%s reason=%s txs=%s clean=%s",
                self.job.height, self.job.job_id, reason, len(self.job.tx_hex), clean
            )
            await self.broadcast_job(clean=clean)
            return True
        except Exception:
            log.exception("template refresh failed")
            return False

    async def broadcast_job(self, clean=False):
        if not self.job:
            return
        miners = list(self.miners)
        if not miners:
            return
        params = self.job.notify(clean)

        async def deliver(miner):
            try:
                self.remember_job_for_miner(miner)
                await asyncio.wait_for(
                    miner.send({"id": None, "method": "mining.notify", "params": params}),
                    timeout=5.0,
                )
                return True
            except Exception as exc:
                log.warning("dropping miner during job broadcast worker=%s: %s", miner.worker, exc)
                self.miners.discard(miner)
                if miner.authorized and miner.worker != "unknown":
                    self.update_worker_connection(miner.worker)
                await miner.close()
                return False

        await asyncio.gather(*(deliver(miner) for miner in miners), return_exceptions=True)

    def remember_job_for_miner(self, miner):
        """Pin the share difficulty to the job the miner was actually given."""
        if not self.job:
            return
        miner.job_difficulties[self.job.job_id] = miner.difficulty
        if len(miner.job_difficulties) > self.job_history_limit + 4:
            keep = set(self.jobs)
            for old in list(miner.job_difficulties):
                if old not in keep:
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
        job = self.jobs.get(job_id)
        if job is None:
            return False, [21, "Stale share", None]
        if job.prevhash != self.job.prevhash or job.height != self.job.height:
            return False, [21, "Stale share", None]

        now_mono = time.monotonic()
        if now_mono - miner.submit_window_start >= 1.0:
            miner.submit_window_start = now_mono
            miner.submit_window_count = 0
        miner.submit_window_count += 1
        if miner.submit_window_count > miner.max_submits_per_second:
            return False, [20, "Submitting too fast", None]

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
        mintime = int(job.template.get("mintime", 0))
        # Keep rolling time bounded relative to the advertised job while
        # allowing normal ASIC clock rolling and future timestamps.
        lower_ntime = max(mintime, int(job.ntime) - 600)
        if not lower_ntime <= ntime <= int(time.time()) + 7200:
            return False, [20, "Invalid ntime", None]

        key = (job.job_id, miner.worker, ex2_hex.lower(), ntime, nonce, version_bits)
        if not self._remember_share(key):
            return False, [22, "Duplicate share", None]

        cb = job.coinbase(miner.ex1, ex2)
        header_version = (
            apply_version_rolling(job.version, miner.version_mask, version_bits)
            if miner.version_rolling else job.version
        )
        header = job.header(cb, nonce, ntime, header_version)
        digest = sha256d(header)

        # A vardiff update must never retroactively change the target for a
        # job already in the miner. Validate against the difficulty pinned to
        # this job, not the miner's newer pending/current difficulty.
        share_difficulty = miner.job_difficulties.get(
            job.job_id, miner.difficulty
        )
        share_target = difficulty_to_target(share_difficulty)
        if not hash_meets_target(digest, share_target):
            self.db.share(miner.worker, False, difficulty=share_difficulty)
            return False, [23, "Low difficulty share", None]

        block = hash_meets_target(digest, job.network_target)
        best_diff = target_to_difficulty(int.from_bytes(digest, "little"))
        miner.session_best_diff = max(miner.session_best_diff, best_diff)
        now = time.time()
        previous = miner.last_share
        miner.last_share = now
        miner.shares += 1

        # Estimate effective hashrate from a rolling window of accepted
        # shares. Use the difficulty pinned to each submitted job; using the
        # miner's current difficulty badly distorts hashrate after vardiff
        # changes. A rolling window also recovers quickly after reconnects.
        miner.share_samples.append((now, max(share_difficulty, 0.000001)))
        if len(miner.share_samples) > 12:
            miner.share_samples.pop(0)
        if len(miner.share_samples) >= 2:
            first_time = miner.share_samples[0][0]
            elapsed = now - first_time
            if elapsed >= 1.0:
                work = sum(d * (2 ** 32) for _, d in miner.share_samples[1:])
                miner.hashrate = work / elapsed
        if not miner.first_share_time:
            miner.first_share_time = now
        miner.accepted_work = sum(d * (2 ** 32) for _, d in miner.share_samples)

        self.db.share(
            miner.worker,
            True,
            best_diff,
            hashrate=miner.hashrate,
            difficulty=share_difficulty
        )

        if self.cfg.vardiff_enabled and previous:
            interval = max(0.001, now - previous)
            if miner.share_time_ema is None:
                miner.share_time_ema = interval
            else:
                # Exponential moving average prevents one lucky/unlucky share
                # from causing a large difficulty jump.
                miner.share_time_ema = (0.70 * miner.share_time_ema) + (0.30 * interval)

            # Retarget in small, predictable steps after enough observations.
            if miner.shares >= 4 and miner.shares % 4 == 0 and miner.share_time_ema:
                target = max(self.cfg.vardiff_target_seconds, 1.0)
                factor = (target / miner.share_time_ema) ** 0.5
                factor = max(0.5, min(2.0, factor))
                nd = miner.difficulty * factor
                nd = min(
                    self.cfg.vardiff_max,
                    max(self.cfg.vardiff_min, miner.minimum_difficulty, nd)
                )
                if abs(nd - miner.difficulty) / max(miner.difficulty, 1e-12) >= 0.05:
                    miner.difficulty = nd
                    self.db.touch_worker(miner.worker, nd)
                    try:
                        await miner.send({"id": None, "method": "mining.set_difficulty", "params": [nd]})
                    except Exception:
                        pass

        if block:
            block_hex = job.block_hex(cb, nonce, ntime, header_version)
            result = None
            last_error = None
            # submitblock is safe to retry for the exact same candidate.
            for attempt in range(3):
                try:
                    result = await asyncio.to_thread(self.rpc.submit_block, block_hex)
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    log.exception("block submission attempt %s/3 failed height=%s worker=%s",
                                  attempt + 1, job.height, miner.worker)
                    if attempt < 2:
                        await asyncio.sleep(0.5 * (attempt + 1))

            h = digest[::-1].hex()
            outcome = str(result) if last_error is None else "RPC error: " + str(last_error)
            self.db.block(job.height, job.job_id, miner.worker, h, outcome)
            self.db.event("block", miner.worker, f"height={job.height} hash={h} result={outcome}")
            log.warning("BLOCK CANDIDATE: height=%s worker=%s hash=%s submit=%s",
                        job.height, miner.worker, h, outcome)
            if last_error is None and (result is None or str(result).lower() == "duplicate"):
                await asyncio.sleep(0.25)
                await self.refresh_job("block-submit", only_if_new_block=True)

        return True, None
