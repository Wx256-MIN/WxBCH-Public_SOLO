require('dotenv').config();

const net = require('net');
const http = require('http');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const Database = require('better-sqlite3');
const zmq = require('zeromq');
const bitcoin = require('bitcoinjs-lib');
const {
  cashAddressToLockingBytecode,
  base58AddressToLockingBytecode
} = require('@bitauth/libauth');

const STRATUM_PORT = Number(process.env.STRATUM_PORT || 3333);
const API_PORT = Number(process.env.API_PORT || 3334);
const NETWORK = process.env.NETWORK || 'mainnet';
const RPC_URL = process.env.BCHN_RPC_URL || 'http://127.0.0.1:8332';
const RPC_USER = process.env.BCHN_RPC_USER || '';
const RPC_PASSWORD = process.env.BCHN_RPC_PASSWORD || '';
const RPC_PORT = Number(process.env.BCHN_RPC_PORT || 8332);
const ZMQ_HOST = process.env.BCHN_ZMQ_HOST || '';
const POOL_IDENTIFIER = process.env.POOL_IDENTIFIER || 'WxBCH-Public-Pool';
const DB_PATH = process.env.DB_PATH || './data/pool.sqlite';
const INITIAL_DIFFICULTY = Number(process.env.INITIAL_DIFFICULTY || 8192);
const MIN_DIFFICULTY = Number(process.env.MIN_DIFFICULTY || 0.001);
const MAX_DIFFICULTY = Number(process.env.MAX_DIFFICULTY || 1e12);
const TARGET_SHARE_SECONDS = Number(process.env.TARGET_SHARE_SECONDS || 30);
const VERSION_ROLLING_MASK = Number.parseInt(process.env.VERSION_ROLLING_MASK || '1fffe000', 16) >>> 0;
const EXTRANONCE1_BYTES = 4;
const EXTRANONCE2_BYTES = 8;
const DIFF1_TARGET = 0x00000000ffffn * (1n << 208n);

fs.mkdirSync(path.dirname(DB_PATH), { recursive: true });
const db = new Database(DB_PATH);
db.pragma('journal_mode = WAL');
db.exec(`
CREATE TABLE IF NOT EXISTS shares (
  id INTEGER PRIMARY KEY,
  created_at INTEGER NOT NULL,
  address TEXT NOT NULL,
  worker TEXT NOT NULL,
  difficulty REAL NOT NULL,
  accepted INTEGER NOT NULL,
  reason TEXT
);
CREATE TABLE IF NOT EXISTS blocks (
  id INTEGER PRIMARY KEY,
  created_at INTEGER NOT NULL,
  height INTEGER NOT NULL,
  address TEXT NOT NULL,
  worker TEXT NOT NULL,
  block_hex TEXT NOT NULL,
  result TEXT
);
`);

const insertShare = db.prepare('INSERT INTO shares(created_at,address,worker,difficulty,accepted,reason) VALUES(?,?,?,?,?,?)');
const insertBlock = db.prepare('INSERT INTO blocks(created_at,height,address,worker,block_hex,result) VALUES(?,?,?,?,?,?)');

function rpcUrl() {
  const u = new URL(RPC_URL);
  if (RPC_PORT > 0) u.port = String(RPC_PORT);
  return u.toString();
}

let rpcId = 0;
async function rpc(method, params = []) {
  const body = JSON.stringify({ jsonrpc: '1.0', id: ++rpcId, method, params });
  const headers = { 'content-type': 'application/json', 'content-length': Buffer.byteLength(body) };
  if (RPC_USER || RPC_PASSWORD) {
    headers.authorization = 'Basic ' + Buffer.from(`${RPC_USER}:${RPC_PASSWORD}`).toString('base64');
  }
  const response = await fetch(rpcUrl(), { method: 'POST', headers, body });
  const json = await response.json();
  if (json.error) throw new Error(json.error.message || JSON.stringify(json.error));
  return json.result;
}

function compactToTarget(bitsHex) {
  const n = Number.parseInt(bitsHex, 16) >>> 0;
  const exponent = n >>> 24;
  let mantissa = BigInt(n & 0x007fffff);
  if (n & 0x00800000) mantissa = -mantissa;
  if (exponent <= 3) return mantissa >> BigInt(8 * (3 - exponent));
  return mantissa << BigInt(8 * (exponent - 3));
}

function targetToDifficulty(target) {
  if (target <= 0n) return Infinity;
  return Number(DIFF1_TARGET) / Number(target);
}

function hashDifficulty(header) {
  const hash = crypto.createHash('sha256').update(
    crypto.createHash('sha256').update(header).digest()
  ).digest();
  const value = BigInt('0x' + Buffer.from(hash).reverse().toString('hex'));
  return {
    hash,
    difficulty: value === 0n ? Infinity : Number(DIFF1_TARGET) / Number(value)
  };
}

function encodeCompactSize(n) {
  if (n < 0xfd) return Buffer.from([n]);
  if (n <= 0xffff) {
    const b = Buffer.alloc(3); b[0] = 0xfd; b.writeUInt16LE(n, 1); return b;
  }
  if (n <= 0xffffffff) {
    const b = Buffer.alloc(5); b[0] = 0xfe; b.writeUInt32LE(n, 1); return b;
  }
  const b = Buffer.alloc(9); b[0] = 0xff; b.writeBigUInt64LE(BigInt(n), 1); return b;
}

function scriptNumber(n) {
  if (n === 0) return Buffer.alloc(0);
  const out = [];
  let v = n;
  while (v > 0) { out.push(v & 0xff); v = Math.floor(v / 256); }
  if (out[out.length - 1] & 0x80) out.push(0);
  return Buffer.from(out);
}

function cashPrefixForNetwork() {
  if (NETWORK === 'testnet') return 'bchtest';
  if (NETWORK === 'regtest') return 'bchreg';
  return 'bitcoincash';
}

function payoutScript(address) {
  const value = String(address || '').trim();
  if (!value) throw new Error('empty BCH payout address');

  if (value.includes(':') || /^(bitcoincash|bchtest|bchreg):/i.test(value)) {
    const result = cashAddressToLockingBytecode(value);
    if (typeof result === 'string') throw new Error(result);
    if (result.tokenSupport) throw new Error('token-aware payout addresses are not accepted');
    if (result.prefix !== cashPrefixForNetwork()) throw new Error('BCH address is for a different network');
    return Buffer.from(result.bytecode);
  }

  const result = base58AddressToLockingBytecode(value);
  if (typeof result === 'string') throw new Error(result);
  const decoded = bitcoin.address.fromBase58Check(value);
  const versions = NETWORK === 'mainnet' ? [0, 5] : [111, 196];
  if (!versions.includes(decoded.version)) throw new Error('legacy address is for a different network');
  return Buffer.from(result.bytecode);
}

function normalizeWorker(username) {
  const [address, ...rest] = String(username || '').split('.');
  return { address, worker: rest.join('.') || 'default' };
}

function merkleBranches(txids) {
  if (!txids.length) return [];
  let layer = txids.map(x => Buffer.from(x, 'hex'));
  let index = 0;
  const result = [];
  while (layer.length > 1) {
    const sibling = index ^ 1;
    result.push(Buffer.from(layer[sibling < layer.length ? sibling : index]));
    const next = [];
    for (let i = 0; i < layer.length; i += 2) {
      const right = layer[i + 1] || layer[i];
      next.push(hash256(Buffer.concat([layer[i], right])));
    }
    layer = next;
    index = Math.floor(index / 2);
  }
  return result.map(x => x.toString('hex'));
}

function hash256(data) {
  return crypto.createHash('sha256').update(
    crypto.createHash('sha256').update(data).digest()
  ).digest();
}

function wordSwap(buf) {
  const out = Buffer.alloc(buf.length);
  for (let i = 0; i < buf.length; i += 4) {
    out[i] = buf[i + 3]; out[i + 1] = buf[i + 2]; out[i + 2] = buf[i + 1]; out[i + 3] = buf[i];
  }
  return out;
}

function buildCoinbase(template, address, extranonce1, extranonce2) {
  const tx = new bitcoin.Transaction();
  tx.version = 2;
  tx.addInput(Buffer.alloc(32), 0xffffffff, 0xffffffff);

  const script = [];
  const height = scriptNumber(template.height);
  script.push(Buffer.from([height.length]), height);

  for (const value of Object.values(template.coinbaseaux || {})) {
    if (/^[0-9a-fA-F]*$/.test(value || '')) script.push(Buffer.from(value, 'hex'));
  }

  script.push(Buffer.from(POOL_IDENTIFIER, 'utf8'));
  script.push(Buffer.from(extranonce1 + extranonce2, 'hex'));

  let scriptSig = Buffer.concat(script);
  if (scriptSig.length > 100) {
    const fixed = Buffer.concat([
      Buffer.from([height.length]), height,
      ...Object.values(template.coinbaseaux || {})
        .filter(v => /^[0-9a-fA-F]*$/.test(v || ''))
        .map(v => Buffer.from(v, 'hex')),
      Buffer.from(extranonce1 + extranonce2, 'hex')
    ]);
    scriptSig = fixed.length <= 100 ? fixed : Buffer.concat([Buffer.from([height.length]), height, Buffer.from(extranonce1 + extranonce2, 'hex')]);
  }
  if (scriptSig.length < 2) throw new Error('coinbase scriptSig too short');
  tx.ins[0].script = scriptSig;
  tx.addOutput(payoutScript(address), template.coinbasevalue);

  return Buffer.from(tx.toHex(), 'hex');
}

function buildHeader(template, coinbase, versionMask, nonce, timestamp) {
  const coinbaseHash = hash256(coinbase);
  let root = coinbaseHash;
  const branches = merkleBranches(template.transactions.map(t => t.txid));
  for (const branch of branches) root = hash256(Buffer.concat([root, Buffer.from(branch, 'hex')]));

  let version = template.version >>> 0;
  version = (version ^ (versionMask >>> 0)) >>> 0;

  const header = Buffer.alloc(80);
  header.writeUInt32LE(version, 0);
  Buffer.from(template.previousblockhash, 'hex').reverse().copy(header, 4);
  root.copy(header, 36);
  header.writeUInt32LE(timestamp >>> 0, 68);
  header.writeUInt32LE(Number.parseInt(template.bits, 16) >>> 0, 72);
  header.writeUInt32LE(nonce >>> 0, 76);
  return header;
}

function buildBlock(template, coinbase, header) {
  const txs = template.transactions.map(t => Buffer.from(t.data, 'hex'));
  return Buffer.concat([
    header,
    encodeCompactSize(1 + txs.length),
    coinbase,
    ...txs
  ]);
}

function randomHex(bytes) {
  return crypto.randomBytes(bytes).toString('hex');
}

class Job {
  constructor(template, cleanJobs) {
    this.template = template;
    this.id = randomHex(4);
    this.cleanJobs = cleanJobs;
    this.createdAt = Date.now();
    this.branches = merkleBranches(template.transactions.map(t => t.txid));
    this.networkDifficulty = targetToDifficulty(compactToTarget(template.bits));
  }

  notify(session) {
    const en1 = session.extranonce1;
    const zeroEn2 = '00'.repeat(EXTRANONCE2_BYTES);
    const coinbase = buildCoinbase(this.template, session.address, en1, zeroEn2);
    const scriptLength = coinbase[4 + 1 + 32 + 4];
    const scriptStart = 4 + 1 + 32 + 4 + 1;
    const scriptEnd = scriptStart + scriptLength;
    const split = scriptLength - (EXTRANONCE1_BYTES + EXTRANONCE2_BYTES);
    session.write({
      id: null,
      method: 'mining.notify',
      params: [
        this.id,
        wordSwap(Buffer.from(this.template.previousblockhash, 'hex')).toString('hex'),
        coinbase.subarray(0, scriptStart + split).toString('hex'),
        coinbase.subarray(scriptEnd).toString('hex'),
        this.branches,
        (this.template.version >>> 0).toString(16).padStart(8, '0'),
        this.template.bits.padStart(8, '0'),
        (this.template.curtime >>> 0).toString(16).padStart(8, '0'),
        this.cleanJobs
      ]
    });
  }
}

class Session {
  constructor(socket) {
    this.socket = socket;
    this.buffer = '';
    this.address = '';
    this.worker = 'default';
    this.userAgent = 'unknown';
    this.extranonce1 = randomHex(EXTRANONCE1_BYTES);
    this.extranonce2Size = EXTRANONCE2_BYTES;
    this.difficulty = INITIAL_DIFFICULTY;
    this.versionMask = VERSION_ROLLING_MASK;
    this.configuredVersionRolling = false;
    this.lastShare = Date.now();
    this.shares = 0;
    this.bestDifficulty = 0;
    this.jobs = new Map();
    this.socket.setNoDelay(true);
    socket.on('data', d => this.onData(d.toString()));
    socket.on('close', () => sessions.delete(this));
    socket.on('error', () => sessions.delete(this));
  }

  write(obj) {
    if (!this.socket.destroyed) this.socket.write(JSON.stringify(obj) + '\n');
  }

  onData(data) {
    this.buffer += data;
    const lines = this.buffer.split('\n');
    this.buffer = lines.pop();
    for (const line of lines) {
      if (!line.trim()) continue;
      try { this.handle(JSON.parse(line)); }
      catch (e) { this.write({id:null,result:null,error:[20,e.message]}); }
    }
  }

  handle(msg) {
    switch (msg.method) {
      case 'mining.subscribe':
        this.userAgent = String(msg.params?.[0] || 'unknown');
        this.write({id:msg.id,error:null,result:[[['mining.notify',this.extranonce1]],this.extranonce1,this.extranonce2Size]});
        if (currentJob) currentJob.notify(this);
        break;
      case 'mining.configure':
        this.configuredVersionRolling = Boolean(msg.params?.[1]?.['version-rolling'] || msg.params?.[0]?.includes?.('version-rolling'));
        this.write({id:msg.id,error:null,result:{'version-rolling':this.configuredVersionRolling,'version-rolling.mask':this.versionMask.toString(16)}});
        break;
      case 'mining.authorize': {
        const p = normalizeWorker(msg.params?.[0]);
        this.address = p.address;
        this.worker = p.worker;
        try { payoutScript(this.address); }
        catch (e) { this.write({id:msg.id,result:false,error:[24,'Invalid BCH payout address']}); return; }
        this.write({id:msg.id,result:true,error:null});
        if (currentJob) currentJob.notify(this);
        break;
      }
      case 'mining.suggest_difficulty':
        this.difficulty = clamp(Number(msg.params?.[0]) || INITIAL_DIFFICULTY, MIN_DIFFICULTY, MAX_DIFFICULTY);
        this.write({id:msg.id,result:true,error:null});
        break;
      case 'mining.ping':
        this.write({id:msg.id,result:'pong',error:null});
        break;
      case 'client.get_version':
        this.write({id:msg.id,result:'WxBCH-Stratum/1.0.0',error:null});
        break;
      case 'mining.submit':
        this.submit(msg);
        break;
    }
  }

  submit(msg) {
    if (!currentJob || !this.address) {
      this.write({id:msg.id,result:false,error:[21,'Not initialized']});
      return;
    }
    const [workerName, jobId, extraNonce2, ntimeHex, nonceHex, versionHex] = msg.params || [];
    const job = currentJob.id === jobId ? currentJob : this.jobs.get(jobId);
    if (!job) {
      this.write({id:msg.id,result:false,error:[21,'Job not found']});
      return;
    }
    if (!/^[0-9a-fA-F]{16}$/.test(extraNonce2 || '') || !/^[0-9a-fA-F]{8}$/.test(ntimeHex || '') || !/^[0-9a-fA-F]{8}$/.test(nonceHex || '')) {
      this.write({id:msg.id,result:false,error:[20,'Malformed submission']});
      return;
    }

    let versionMask = 0;
    if (versionHex && /^[0-9a-fA-F]{8}$/.test(versionHex)) versionMask = Number.parseInt(versionHex,16) & this.versionMask;
    const timestamp = Number.parseInt(ntimeHex,16) >>> 0;
    const nonce = Number.parseInt(nonceHex,16) >>> 0;

    const coinbase = buildCoinbase(job.template, this.address, this.extranonce1, extraNonce2.toLowerCase());
    const header = buildHeader(job.template, coinbase, versionMask, nonce, timestamp);
    const { difficulty } = hashDifficulty(header);

    if (difficulty < this.difficulty) {
      insertShare.run(Date.now(),this.address,this.worker,difficulty,0,'low-difficulty');
      this.write({id:msg.id,result:false,error:[23,'Low difficulty share']});
      return;
    }

    this.shares++;
    this.lastShare=Date.now();
    this.bestDifficulty=Math.max(this.bestDifficulty,difficulty);
    insertShare.run(Date.now(),this.address,this.worker,difficulty,1,null);
    this.write({id:msg.id,result:true,error:null});

    if (difficulty >= job.networkDifficulty) {
      const block = buildBlock(job.template, coinbase, header);
      submitFoundBlock(this, job, block.toString('hex'));
    }
  }
}

const sessions = new Set();
let currentJob = null;
let lastTemplateSignature = '';

function clamp(n,min,max){ return Math.max(min,Math.min(max,n)); }

async function refreshJob(force=false) {
  try {
    const info = await rpc('getmininginfo');
    const template = await rpc('getblocktemplate', [{
      mode:'template',
      capabilities:['coinbasevalue','proposal','workid'],
      checkvalidity:true
    }]);
    const signature = [
      template.previousblockhash,template.version,template.bits,template.height,
      template.coinbasevalue,template.curtime,
      ...template.transactions.map(t=>t.txid)
    ].join('|');
    if (!force && signature === lastTemplateSignature) return;
    const clean = !currentJob || currentJob.template.height !== template.height;
    lastTemplateSignature = signature;
    currentJob = new Job(template,clean);
    for (const s of sessions) {
      if (s.address) {
        s.jobs.set(currentJob.id,currentJob);
        if (s.jobs.size > 8) s.jobs.delete(s.jobs.keys().next().value);
        currentJob.notify(s);
      }
    }
    console.log(`BCHN template height=${template.height} txs=${template.transactions.length} diff=${currentJob.networkDifficulty}`);
  } catch (e) {
    console.error('Template refresh failed:',e.message);
  }
}

async function submitFoundBlock(session,job,hex) {
  try {
    console.log(`BLOCK FOUND height=${job.template.height} address=${session.address}`);
    const result = await rpc('submitblock',[hex]);
    insertBlock.run(Date.now(),job.template.height,session.address,session.worker,hex,result==null?'SUCCESS!':String(result));
    console.log('BCHN submitblock result:',result);
  } catch (e) {
    insertBlock.run(Date.now(),job.template.height,session.address,session.worker,hex,String(e.message));
    console.error('submitblock failed:',e.message);
  }
}

function apiJson(res,status,data) {
  const body=JSON.stringify(data);
  res.writeHead(status,{'content-type':'application/json','access-control-allow-origin':'*'});
  res.end(body);
}

function apiServer(req,res) {
  const u=new URL(req.url,'http://localhost');
  if (u.pathname==='/') {
    res.writeHead(200,{'content-type':'text/html; charset=utf-8'});
    res.end(`<!doctype html><html><head><meta charset="utf-8"><title>WxBCH Public Pool</title></head><body><h1>WxBCH Public Pool</h1><p>BCHN Stratum V1 solo pool</p><pre id="out">loading...</pre><script>fetch('/api/pool').then(r=>r.json()).then(x=>out.textContent=JSON.stringify(x,null,2))</script></body></html>`);
    return;
  }
  if (u.pathname==='/api/pool') {
    const miners=[...sessions].filter(s=>s.address);
    const shares=db.prepare('SELECT COUNT(*) c FROM shares WHERE accepted=1').get().c;
    const blocks=db.prepare('SELECT COUNT(*) c FROM blocks').get().c;
    apiJson(res,200,{network:'BCH',node:'BCHN',height:currentJob?.template.height??null,networkDifficulty:currentJob?.networkDifficulty??null,miners:miners.length,shares,blocks,stratumPort:STRATUM_PORT,apiPort:API_PORT});
    return;
  }
  if (u.pathname==='/api/workers') {
    apiJson(res,200,[...sessions].filter(s=>s.address).map(s=>({address:s.address,worker:s.worker,userAgent:s.userAgent,difficulty:s.difficulty,shares:s.shares,bestDifficulty:s.bestDifficulty,lastShare:s.lastShare})));
    return;
  }
  if (u.pathname==='/api/blocks') {
    apiJson(res,200,db.prepare('SELECT created_at,height,address,worker,result FROM blocks ORDER BY id DESC LIMIT 50').all());
    return;
  }
  if (u.pathname==='/api/network') {
    rpc('getmininginfo').then(x=>apiJson(res,200,x)).catch(e=>apiJson(res,502,{error:e.message}));
    return;
  }
  apiJson(res,404,{error:'not found'});
}

const api=http.createServer(apiServer);
api.listen(API_PORT,'0.0.0.0',()=>console.log(`API listening on ${API_PORT}`));

const stratum=net.createServer(socket=>{
  const s=new Session(socket);
  sessions.add(s);
  socket.on('close',()=>sessions.delete(s));
});
stratum.maxConnections=Number(process.env.STRATUM_MAX_CONNECTIONS_PER_LISTENER||10000);
stratum.listen(STRATUM_PORT,'0.0.0.0',()=>console.log(`Stratum listening on ${STRATUM_PORT}`));

if (ZMQ_HOST) {
  const sub=new zmq.Subscriber();
  sub.connect(ZMQ_HOST);
  sub.subscribe('hashblock');
  (async()=>{for await (const [] of sub){ await refreshJob(true); }})().catch(console.error);
}

refreshJob(true);
setInterval(()=>refreshJob(false),5000);

setInterval(()=>{
  for(const s of sessions){
    if(!s.address||!s.shares) continue;
    const elapsed=Math.max(1,(Date.now()-s.lastShare)/1000);
    const estimated=Math.max(MIN_DIFFICULTY,Math.min(MAX_DIFFICULTY,(s.shares* s.difficulty * 4294967296)/elapsed));
    const ratio=estimated/Math.max(1,s.difficulty*4294967296/TARGET_SHARE_SECONDS);
    if(ratio>1.8) s.difficulty=clamp(s.difficulty*2,MIN_DIFFICULTY,MAX_DIFFICULTY);
    else if(ratio<0.55) s.difficulty=clamp(s.difficulty/2,MIN_DIFFICULTY,MAX_DIFFICULTY);
    s.shares=0;
  }
},60000);

module.exports={compactToTarget,targetToDifficulty};