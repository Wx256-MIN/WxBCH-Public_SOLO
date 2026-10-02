import assert from 'node:assert';

function compactToTarget(bitsHex) {
  const n = Number.parseInt(bitsHex, 16) >>> 0;
  const exponent = n >>> 24;
  let mantissa = BigInt(n & 0x007fffff);
  if (n & 0x00800000) mantissa = -mantissa;
  if (exponent <= 3) return mantissa >> BigInt(8 * (3 - exponent));
  return mantissa << BigInt(8 * (exponent - 3));
}

function targetToDifficulty(target) {
  const diff1 = 0x00000000ffffn * (1n << 208n);
  return Number(diff1) / Number(target);
}

const target = compactToTarget('1d00ffff');
assert(target > 0n);
assert(Math.abs(targetToDifficulty(target) - 1) < 1e-12);
console.log('WxBCH BCHN compact-target tests passed');
