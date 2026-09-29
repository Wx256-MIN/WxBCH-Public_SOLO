import hashlib
import struct

def sha256d(b: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(b).digest()).digest()

def ser_compact_size(n: int) -> bytes:
    if n < 253:
        return bytes([n])
    if n <= 0xffff:
        return b"\xfd" + struct.pack("<H", n)
    if n <= 0xffffffff:
        return b"\xfe" + struct.pack("<I", n)
    return b"\xff" + struct.pack("<Q", n)

def encode_compact_target(bits_hex: str) -> int:
    bits = int(bits_hex, 16)
    exponent = bits >> 24
    mantissa = bits & 0x007fffff
    if bits & 0x00800000:
        mantissa = -mantissa
    if exponent <= 3:
        return mantissa >> (8 * (3 - exponent))
    return mantissa << (8 * (exponent - 3))

def target_to_difficulty(target: int) -> float:
    if target <= 0:
        return 0.0
    return DIFF1_TARGET / target

# Bitcoin/BCH SHA256 mining difficulty-1 target.
DIFF1_TARGET = 0x00000000FFFF0000000000000000000000000000000000000000000000000000

def difficulty_to_target(diff: float) -> int:
    if diff <= 0:
        return DIFF1_TARGET
    return max(1, int(DIFF1_TARGET / diff))

def hash_meets_target(digest: bytes, target: int) -> bool:
    return int.from_bytes(digest, "little") <= target

def merkle_root(tx_hashes_internal):
    if not tx_hashes_internal:
        return bytes(32)
    level = list(tx_hashes_internal)
    while len(level) > 1:
        if len(level) & 1:
            level.append(level[-1])
        level = [sha256d(level[i] + level[i+1]) for i in range(0, len(level), 2)]
    return level[0]

def u32le(n: int) -> bytes:
    return struct.pack("<I", n & 0xffffffff)

def compact_size_prefix_len(n: int) -> int:
    return len(ser_compact_size(n))

def bits_hex_from_int(bits: int) -> str:
    return f"{bits:08x}"
