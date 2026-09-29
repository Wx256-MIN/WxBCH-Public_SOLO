import sys, unittest
sys.path.insert(0, ".")
from app.crypto import sha256d, encode_compact_target, difficulty_to_target, DIFF1_TARGET, merkle_root
from app.address import decode_cashaddr, decode_legacy
from app.stratum import Job

class CoreTests(unittest.TestCase):
    def test_sha256d(self):
        self.assertEqual(len(sha256d(b"abc")), 32)

    def test_diff1(self):
        self.assertEqual(difficulty_to_target(1), DIFF1_TARGET)

    def test_compact(self):
        self.assertEqual(encode_compact_target("1d00ffff"), DIFF1_TARGET)


    def test_job_header_and_coinbase(self):
        t = {
            "height": 900000, "previousblockhash": "11"*32,
            "version": 2, "bits": "1d00ffff", "curtime": 1700000000,
            "mintime": 1699990000, "coinbasevalue": 1000, "transactions": []
        }
        script = decode_cashaddr("bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a")
        j = Job(t, script, "test")
        cb = j.coinbase(b"\x01\x02\x03\x04", b"\x05\x06\x07\x08")
        self.assertGreaterEqual(len(cb), 60)
        self.assertEqual(len(j.header(cb, 0)), 80)
        self.assertEqual(len(bytes.fromhex(j.block_hex(cb, 0))), 80 + 1 + len(cb))

    def test_merkle_one(self):
        h = sha256d(b"abc")
        self.assertEqual(merkle_root([h]), h)

    def test_cashaddr(self):
        # well-known BCH mainnet example
        script = decode_cashaddr("bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a")
        self.assertTrue(script.startswith(b"\x76\xa9"))

if __name__ == "__main__":
    unittest.main()
