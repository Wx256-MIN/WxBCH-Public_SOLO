import sys
import unittest

sys.path.insert(0, ".")

from app.crypto import sha256d, encode_compact_target, difficulty_to_target, target_to_difficulty, DIFF1_TARGET, merkle_root, hash_meets_target
from app.address import decode_cashaddr
from app.stratum import Job, apply_version_rolling


class CoreTests(unittest.TestCase):
    def test_sha256d(self):
        self.assertEqual(len(sha256d(b"abc")), 32)

    def test_diff1(self):
        self.assertEqual(difficulty_to_target(1), DIFF1_TARGET)

    def test_compact(self):
        self.assertEqual(encode_compact_target("1d00ffff"), DIFF1_TARGET)

    def test_target_endianness(self):
        # SHA-256d returns the raw digest in reverse byte order relative to
        # the human-readable block hash. PoW target comparison is little-endian.
        self.assertTrue(hash_meets_target(b"\x00" * 4 + bytes.fromhex("ffff") + b"\x00" * 26, DIFF1_TARGET))
        self.assertTrue(hash_meets_target(b"\x00" * 32, DIFF1_TARGET))
        self.assertFalse(hash_meets_target(b"\x00" * 26 + bytes.fromhex("ffff") + b"\xff" * 4, DIFF1_TARGET))

    def test_job_header_and_coinbase(self):
        t = {
            "height": 900000, "previousblockhash": "11" * 32,
            "version": 2, "bits": "1d00ffff", "curtime": 1700000000,
            "mintime": 1699990000, "coinbasevalue": 1000, "transactions": []
        }
        script = decode_cashaddr("bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a")
        j = Job(t, script, "test")
        cb = j.coinbase(b"\x01\x02\x03\x04", b"\x05\x06\x07\x08")
        self.assertGreaterEqual(len(cb), 60)
        self.assertEqual(len(j.header(cb, 0)), 80)
        self.assertEqual(len(bytes.fromhex(j.block_hex(cb, 0))), 80 + 1 + len(cb))


    def test_stratum_notify_preserves_clean_jobs_flag(self):
        t = {
            "height": 900000, "previousblockhash": "11" * 32,
            "version": 2, "bits": "1d00ffff", "curtime": 1700000000,
            "mintime": 1699990000, "coinbasevalue": 1000, "transactions": []
        }
        script = decode_cashaddr("bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a")
        j = Job(t, script, "test")
        self.assertTrue(j.notify(True)[8])
        self.assertFalse(j.notify(False)[8])

    def test_stratum_notify_uses_uint32_hex_for_version_and_ntime(self):
        t = {
            "height": 900000, "previousblockhash": "11" * 32,
            "version": 0x20000002, "bits": "1d00ffff", "curtime": 1700000000,
            "mintime": 1699990000, "coinbasevalue": 1000, "transactions": []
        }
        script = decode_cashaddr("bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a")
        j = Job(t, script, "test")
        params = j.notify()
        self.assertEqual(params[5], "20000002")
        self.assertEqual(params[7], "6553f100")

    def test_stratum_prevhash_uses_word_reversal(self):
        t = {
            "height": 900000,
            "previousblockhash": "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f",
            "version": 2,
            "bits": "1d00ffff",
            "curtime": 1700000000,
            "mintime": 1699990000,
            "coinbasevalue": 1000,
            "transactions": [],
        }
        script = decode_cashaddr("bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a")
        j = Job(t, script, "test")
        self.assertEqual(
            j.notify()[1],
            "1c1d1e1f18191a1b14151617101112130c0d0e0f08090a0b0405060700010203",
        )
        cb = j.coinbase(b"\x01\x02\x03\x04", b"\x05\x06\x07\x08")
        self.assertEqual(
            j.header(cb, 0)[4:36],
            bytes.fromhex(t["previousblockhash"])[::-1],
        )

    def test_merkle_one(self):
        h = sha256d(b"abc")
        self.assertEqual(merkle_root([h]), h)

    def test_bip310_version_rolling_preserves_protected_bits(self):
        base = 0x20000002
        mask = 0x1fffe000
        rolled = 0x15554000
        actual = apply_version_rolling(base, mask, rolled)
        self.assertEqual(actual & ~mask, base & ~mask)
        self.assertEqual(actual & mask, rolled & mask)

    def test_bip310_version_rolling_zero_mask(self):
        base = 0x20000002
        self.assertEqual(apply_version_rolling(base, 0, 0xffffffff), base)

    def test_stratum_suggest_target_conversion_matches_difficulty(self):
        target = DIFF1_TARGET // 1000
        self.assertAlmostEqual(
            target_to_difficulty(target),
            1000.0,
            delta=0.001,
        )


    def test_config_persistence_is_atomic_and_private(self):
        import os
        import tempfile
        from app.config import Config

        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("CONFIG_PATH")
            os.environ["CONFIG_PATH"] = os.path.join(tmp, "config.json")
            try:
                cfg = Config()
                cfg.save_vardiff_settings(True, 30, 1000, 1, 65536)
                self.assertTrue(os.path.exists(cfg.config_path))
                self.assertEqual(os.stat(cfg.config_path).st_mode & 0o777, 0o600)
                loaded = Config()
                self.assertEqual(loaded.start_difficulty, 1000.0)
                self.assertTrue(loaded.vardiff_enabled)
            finally:
                if old is None:
                    os.environ.pop("CONFIG_PATH", None)
                else:
                    os.environ["CONFIG_PATH"] = old

    def test_db_can_reopen_after_clean_close(self):
        import tempfile
        from app.db import DB

        with tempfile.TemporaryDirectory() as tmp:
            path = tmp + "/pool.sqlite3"
            db = DB(path)
            db.touch_worker("test", 1000, connected=True)
            db.share("test", True, best_diff=1200, difficulty=1000)
            db.close()
            reopened = DB(path)
            workers, _, _ = reopened.snapshot()
            self.assertEqual(workers[0]["worker"], "test")
            self.assertEqual(workers[0]["shares"], 1)
            reopened.close()

    def test_cashaddr(self):
        script = decode_cashaddr("bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a")
        self.assertTrue(script.startswith(b"\x76\xa9"))

    def test_rpc_txid_is_little_endian_for_internal_merkle_hash(self):
        txid = "00" * 31 + "01"
        t = {
            "height": 900000, "previousblockhash": "11" * 32,
            "version": 2, "bits": "1d00ffff", "curtime": 1700000000,
            "mintime": 1699990000, "coinbasevalue": 1000,
            "transactions": [{"txid": txid, "data": "01000000"}],
        }
        script = decode_cashaddr("bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a")
        j = Job(t, script, "test")
        self.assertEqual(j.tx_hashes[0], bytes.fromhex(txid))


if __name__ == "__main__":
    unittest.main()
