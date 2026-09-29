import hashlib

ALPHABET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
CHARSET = {c: i for i, c in enumerate(ALPHABET)}
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
B58MAP = {c: i for i, c in enumerate(B58)}


def _poly(values):
    generators = [0x98f2bc8e61, 0x79b76d99e2, 0xf33e5fb3c4, 0xae2eabe2a8, 0x1e4f43e470]
    c = 1
    for v in values:
        top = c >> 35
        c = ((c & 0x07ffffffff) << 5) ^ v
        for i in range(5):
            if (top >> i) & 1:
                c ^= generators[i]
    return c


def _prefix_expand(prefix):
    return [ord(x) & 0x1f for x in prefix] + [0]


def _convertbits(data, frombits, tobits, pad=True):
    acc = bits = 0
    ret = []
    maxv = (1 << tobits) - 1
    max_acc = (1 << (frombits + tobits - 1)) - 1
    for value in data:
        if value < 0 or (value >> frombits):
            raise ValueError("invalid bit value")
        acc = ((acc << frombits) | value) & max_acc
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            ret.append((acc >> bits) & maxv)
    if pad:
        if bits:
            ret.append((acc << (tobits - bits)) & maxv)
    elif bits >= frombits or ((acc << (tobits - bits)) & maxv):
        raise ValueError("invalid padding")
    return bytes(ret)


def decode_cashaddr(address):
    address = address.strip()
    if ":" in address:
        prefix, payload = address.lower().split(":", 1)
    else:
        prefix, payload = "bitcoincash", address.lower()
    if prefix not in ("bitcoincash", "bchtest", "bchreg"):
        raise ValueError("unsupported CashAddr prefix")
    if any(c not in CHARSET for c in payload):
        raise ValueError("invalid CashAddr character")
    vals = [CHARSET[c] for c in payload]
    if len(vals) < 8 or _poly(_prefix_expand(prefix) + vals) != 1:
        raise ValueError("invalid CashAddr checksum")
    data = vals[:-8]
    raw = _convertbits(data, 5, 8, False)
    if not raw:
        raise ValueError("empty CashAddr")
    version = raw[0]
    if version & 0xE0:
        raise ValueError("unsupported CashAddr version")
    addr_type = version >> 3
    size_code = version & 7
    sizes = {0: 20, 1: 24, 2: 28, 3: 32, 4: 40, 5: 48, 6: 56, 7: 64}
    if addr_type not in (0, 1):
        raise ValueError("unsupported CashAddr type")
    h = raw[1:]
    if len(h) != sizes[size_code]:
        raise ValueError("invalid CashAddr hash size")
    if addr_type == 0:
        return b"\x76\xa9\x14" + h + b"\x88\xac"
    return b"\xa9" + bytes([len(h)]) + h + b"\x87"


def _b58decode(s):
    n = 0
    for c in s:
        if c not in B58MAP:
            raise ValueError("invalid Base58")
        n = n * 58 + B58MAP[c]
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\x00" * (len(s) - len(s.lstrip("1"))) + raw


def decode_legacy(address):
    raw = _b58decode(address.strip())
    if len(raw) != 25:
        raise ValueError("invalid legacy address length")
    payload, checksum = raw[:-4], raw[-4:]
    if hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4] != checksum:
        raise ValueError("invalid legacy checksum")
    version, h = payload[0], payload[1:]
    if version == 0x00:
        return b"\x76\xa9\x14" + h + b"\x88\xac"
    if version == 0x05:
        return b"\xa9\x14" + h + b"\x87"
    raise ValueError("unsupported legacy BCH address")


def address_to_script(address):
    a = address.strip()
    if a.lower().startswith(("bitcoincash:", "bchtest:", "bchreg:")) or ":" not in a:
        try:
            return decode_cashaddr(a)
        except ValueError:
            return decode_legacy(a)
    return decode_legacy(a)
