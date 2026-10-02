const assert = require('assert');
const { compactToTarget, targetToDifficulty } = require('./app');

const target = compactToTarget('1d00ffff');
assert(target > 0n);
assert(Math.abs(targetToDifficulty(target) - 1) < 1e-12);
console.log('BCH difficulty/compact target tests passed');
