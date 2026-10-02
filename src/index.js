/* WxBCH Public Pool
 * Public-Pool-style, BCHN-focused solo mining server.
 * GPL-3.0-or-later. Clean-room implementation; see README for upstream inspiration.
 */
const net = require("node:net");
const http = require("node:http");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const Database = require("better-sqlite3");
const { Subscriber } = require("zeromq");
const { cashAddressToLockingBytecode, base58AddressToLockingBytecode } = require("@bitauth/libauth");

const env=(k,d)=>process.env[k] ?? d;
const cfg={
 rpcUrl:env("RPC_URL","http://127.0.0.1:8432"), rpcUser:env("RPC_USER",""), rpcPassword:env("RPC_PASSWORD",""),
 zmq:env("ZMQ_HASHBLOCK",""), stratumHost:env("STRATUM_HOST","0.0.0.0"), stratumPort:Number(env("STRATUM_PORT","3336")),
 apiHost:env("API_HOST","0.0.0.0"), apiPort:Number(env("API_PORT","3337")), dbPath:env("DB_PATH","./data/pool.sqlite"),
 tag:env("COINBASE_TAG","/WxBCH-Pool/"), extraHex:env("COINBASE_EXTRA_HEX",""),
 initialDiff:Number(env("INITIAL_DIFFICULTY","8192")), minDiff:Number(env("MIN_DIFFICULTY","1")),
 maxDiff:Number(env("MAX_DIFFICULTY","1000000000000")), targetShareSeconds:Number(env("TARGET_SHARE_SECONDS","30")),
 vardiffMinInterval:Number(env("VARDIFF_MIN_INTERVAL_SECONDS","60")), refreshSeconds:Number(env("JOB_REFRESH_SECONDS","10")),
 ex1Size:Number(env("EXTRANONCE1_SIZE","4")), ex2Size:Number(env("EXTRANONCE2_SIZE","4")),
 versionMask:parseInt(env("VERSION_ROLLING_MASK","1fffe000"),16), maxConnections:Number(env("MAX_STRATUM_CONNECTIONS","10000"))
};
const HEX=/^[0-9a-fA-F]+$/;
const D1=0x00000000ffff0000000000000000000000000000000000000000000000000000n;
const dsha=b=>crypto.createHash("sha256").update(crypto.createHash("sha256").update(b).digest()).digest();
const rev=b=>Buffer.from(b).reverse();
const u32le=n=>{const b=Buffer.alloc(4);b.writeUInt32LE(n>>>0);return b;};
const u64le=n=>{const b=Buffer.alloc(8);b.writeBigUInt64LE(BigInt(n));return b;};
function compactTarget(bits){const n=parseInt(bits,16),e=n>>>24,m=BigInt(n&0x007fffff);return e<=3?m>>BigInt(8*(3-e)):m<<BigInt(8*(e-3));}
function targetForDifficulty(d){let t=D1/BigInt(Math.max(1,Math.floor(d)));const max=(1n<<256n)-1n;return t<1n?1n:t>max?max:t;}
function hashInt(h){return BigInt("0x"+rev(h).toString("hex"));}
function varint(n){if(n<0xfd)return Buffer.from([n]);if(n<=0xffff){const b=Buffer.alloc(3);b[0]=0xfd;b.writeUInt16LE(n,1);return b;}if(n<=0xffffffff){const b=Buffer.alloc(5);b[0]=0xfe;b.writeUInt32LE(n,1);return b;}const b=Buffer.alloc(9);b[0]=0xff;b.writeBigUInt64LE(BigInt(n),1);return b;}
function pushData(data){const b=Buffer.from(data);if(b.length<76)return Buffer.concat([Buffer.from([b.length]),b]);if(b.length<=255)return Buffer.concat([Buffer.from([0x4c,b.length]),b]);throw new Error("push too large");}
function scriptNum(n){let x=BigInt(n),neg=x<0n;if(neg)x=-x;const a=[];while(x){a.push(Number(x&255n));x>>=8n;}if(!a.length)a.push(0);if(a[a.length-1]&0x80)a.push(neg?0x80:0);else if(neg)a[a.length-1]|=0x80;return Buffer.from(a);}
function addressScript(address){
 let r;
 try{r=/^(bitcoincash|bchtest|bchreg):/i.test(address)?cashAddressToLockingBytecode(address):base58AddressToLockingBytecode(address);}
 catch{throw new Error("Invalid BCH payout address");}
 if(typeof r==="string"||!r?.bytecode)throw new Error("Invalid BCH payout address");
 return Buffer.from(r.bytecode);
}
function splitUser(u){const p=String(u||"").split(".");return{address:p[0],worker:p.slice(1).join(".")||"default"};}
function jsonLine(s){try{return JSON.parse(s);}catch{return null;}}

class Rpc{
 async call(method,params=[]){
  const auth=Buffer.from(cfg.rpcUser+":"+cfg.rpcPassword).toString("base64");
  const r=await fetch(cfg.rpcUrl,{method:"POST",headers:{"content-type":"application/json","authorization":"Basic "+auth},
    body:JSON.stringify({jsonrpc:"1.0",id:"wxbch",method,params})});
  if(!r.ok)throw new Error("RPC HTTP "+r.status);
  const j=await r.json();if(j.error)throw new Error(j.error.message||JSON.stringify(j.error));return j.result;
 }
}
const rpc=new Rpc();
fs.mkdirSync(path.dirname(cfg.dbPath),{recursive:true});
const db=new Database(cfg.dbPath);
db.pragma("journal_mode = WAL");db.pragma("busy_timeout = 5000");
db.exec('CREATE TABLE IF NOT EXISTS shares(id INTEGER PRIMARY KEY AUTOINCREMENT,ts INTEGER NOT NULL,address TEXT NOT NULL,worker TEXT NOT NULL,job_id TEXT NOT NULL,difficulty REAL NOT NULL,hash TEXT NOT NULL,accepted INTEGER NOT NULL,block INTEGER NOT NULL DEFAULT 0,reason TEXT);CREATE INDEX IF NOT EXISTS shares_addr_ts ON shares(address,ts);CREATE TABLE IF NOT EXISTS blocks(id INTEGER PRIMARY KEY AUTOINCREMENT,ts INTEGER NOT NULL,height INTEGER NOT NULL,hash TEXT NOT NULL,address TEXT NOT NULL,worker TEXT NOT NULL,status TEXT NOT NULL);');

const stats={started:Date.now(),templates:0,accepted:0,rejected:0,blocks:0,lastBlock:null};
const miners=new Map(),sessions=new Set();let jobSeq=0,current=null;

function minerState(address,worker){
 const key=address+"."+worker;let m=miners.get(key);
 if(!m)m={key,address,worker,shares:0,rejected:0,lastShare:0,lastVardiff:Date.now(),difficulty:cfg.initialDiff,hashrate:0,bestDiff:0};
 miners.set(key,m);return m;
}
function recordShare(m,job,hashHex,accepted,block,reason){
 db.prepare("INSERT INTO shares(ts,address,worker,job_id,difficulty,hash,accepted,block,reason) VALUES(?,?,?,?,?,?,?,?,?)").run(Date.now(),m.address,m.worker,job.id,m.difficulty,hashHex,accepted?1:0,block?1:0,reason||null);
 if(accepted){stats.accepted++;m.shares++;}else{stats.rejected++;m.rejected++;}
 if(accepted&&m.lastShare){const dt=(Date.now()-m.lastShare)/1000;if(dt>0)m.hashrate=m.difficulty*4294967296/dt;}
 if(accepted)m.lastShare=Date.now();if(accepted&&m.difficulty>m.bestDiff)m.bestDiff=m.difficulty;
}
function buildCoinbase(t,ex1,ex2,address){
 const payout=addressScript(address),height=pushData(scriptNum(t.height)),tag=Buffer.from(cfg.tag),extra=cfg.extraHex&&HEX.test(cfg.extraHex)?Buffer.from(cfg.extraHex,"hex"):Buffer.alloc(0);
 const script=Buffer.concat([height,Buffer.from(ex1,"hex"),Buffer.from(ex2,"hex"),tag,extra]);
 if(script.length<2||script.length>100)throw new Error("coinbase scriptSig must be 2..100 bytes");
 return Buffer.concat([u32le(1),Buffer.from([1]),Buffer.alloc(32),Buffer.from("ffffffff","hex"),varint(script.length),script,Buffer.from("ffffffff","hex"),Buffer.from([1]),u64le(t.coinbasevalue),pushData(payout),Buffer.from("00000000","hex")]);
}
function txMerkleBranch(t){return(t.transactions||[]).map(x=>Buffer.from(x.txid||x.hash||dsha(Buffer.from(x.data,"hex")).toString("hex"),"hex"));}
function merkleRoot(coinbase,branches){let root=dsha(coinbase);for(const b of branches)root=dsha(Buffer.concat([root,b]));return root;}
function wordReverseHex(hex){const b=Buffer.from(hex,"hex"),o=Buffer.alloc(b.length);for(let i=0;i<b.length;i+=4)Buffer.from(b.subarray(i,i+4)).reverse().copy(o,i);return o.toString("hex");}

async function refreshTemplate(clean=true){
 const t=await rpc.call("getblocktemplate",[{capabilities:["coinbasetxn","workid","coinbase/append"]}]);
 const j={id:(++jobSeq).toString(16),t,branches:txMerkleBranch(t),created:Date.now(),clean};
 current=j;stats.templates++;return j;
}
function jobForSession(job,s){
 const {address}=splitUser(s.username),coinbase=buildCoinbase(job.t,s.ex1,"00".repeat(cfg.ex2Size),address);
 const scriptStart=4+1+32+4,scriptLenBytes=varint(coinbase[scriptStart]).length;
 const prefixLen=scriptStart+scriptLenBytes+pushData(scriptNum(job.t.height)).length+cfg.ex1Size;
 return{...job,coinb1:coinbase.subarray(0,prefixLen).toString("hex"),coinb2:coinbase.subarray(prefixLen+cfg.ex2Size).toString("hex"),
   branches:job.branches.map(b=>b.toString("hex")),prevhash:wordReverseHex(job.t.previousblockhash),version:(job.t.version>>>0).toString(16).padStart(8,"0"),bits:job.t.bits,ntime:(job.t.curtime>>>0).toString(16).padStart(8,"0")};
}
function send(s,o){if(!s.socket.destroyed)s.socket.write(JSON.stringify(o)+"\n");}
function notify(s,job,clean){const j=jobForSession(job,s);send(s,{id:null,method:"mining.set_difficulty",params:[s.miner.difficulty]});send(s,{id:null,method:"mining.notify",params:[j.id,j.prevhash,j.coinb1,j.coinb2,j.branches,j.version,j.bits,j.ntime,clean]});s.currentJob=j;}

function adjustDifficulty(m){
 if(!m.lastShare||Date.now()-m.lastVardiff<cfg.vardiffMinInterval*1000)return;
 const dt=(Date.now()-m.lastShare)/1000,ratio=dt/cfg.targetShareSeconds;
 if(ratio>1.8||ratio<0.55){m.difficulty=Math.min(cfg.maxDiff,Math.max(cfg.minDiff,m.difficulty/ratio));m.lastVardiff=Date.now();}
}
async function submitBlock(s,j,coinbase,root,version,ntimeHex,nonceHex,hashHex){
 const txs=[coinbase,...(j.t.transactions||[]).map(x=>Buffer.from(x.data,"hex"))];
 const block=Buffer.concat([u32le(version),rev(Buffer.from(j.t.previousblockhash,"hex")),root,Buffer.from(ntimeHex,"hex").reverse(),Buffer.from(j.t.bits,"hex").reverse(),Buffer.from(nonceHex,"hex").reverse(),varint(txs.length),...txs]).toString("hex");
 try{
  const result=await rpc.call("submitblock",[block]),status=result==null?"accepted":String(result);
  db.prepare("INSERT INTO blocks(ts,height,hash,address,worker,status) VALUES(?,?,?,?,?,?)").run(Date.now(),j.t.height,hashHex,s.miner.address,s.miner.worker,status);
  stats.blocks++;stats.lastBlock={height:j.t.height,hash:hashHex,status,address:s.miner.address};
  console.log("[BLOCK]",stats.lastBlock);if(result==null){const n=await refreshTemplate(true);broadcast(n,true);}
 }catch(e){console.error("[BLOCK SUBMIT]",e.message);}
}
async function submitShare(s,p){
 if(!s.authorized)throw[24,"unauthorized worker",null];
 if(!Array.isArray(p)||p.length<5)throw[20,"bad parameters",null];
 const[worker,jobId,ex2Hex,ntimeHex,nonceHex,versionHex]=p;
 if(!HEX.test(ex2Hex)||ex2Hex.length!==cfg.ex2Size*2||!HEX.test(ntimeHex)||ntimeHex.length!==8||!HEX.test(nonceHex)||nonceHex.length!==8)throw[20,"malformed share",null];
 const j=s.currentJob&&s.currentJob.id===String(jobId)?s.currentJob:null;if(!j)throw[21,"stale share",null];
 const{address}=splitUser(worker||s.username);if(address.toLowerCase()!==s.miner.address.toLowerCase())throw[24,"worker address mismatch",null];
 const version=parseInt(versionHex||j.version,16),base=parseInt(j.version,16);
 if(((version^base)&~cfg.versionMask)!==0)throw[20,"version rolling outside allowed mask",null];
 const coinbase=Buffer.from(j.coinb1+ex2Hex+j.coinb2,"hex"),root=merkleRoot(coinbase,j.branches.map(x=>Buffer.from(x,"hex")));
 const header=Buffer.concat([u32le(version),rev(Buffer.from(j.t.previousblockhash,"hex")),root,Buffer.from(ntimeHex,"hex").reverse(),Buffer.from(j.bits,"hex").reverse(),Buffer.from(nonceHex,"hex").reverse()]);
 const hash=dsha(header),hashHex=rev(hash).toString("hex"),value=hashInt(hash),shareTarget=targetForDifficulty(s.miner.difficulty);
 if(value>shareTarget){recordShare(s.miner,j,hashHex,false,false,"low-difficulty");throw[23,"low difficulty share",null];}
 const isBlock=value<=compactTarget(j.bits);recordShare(s.miner,j,hashHex,true,isBlock,null);adjustDifficulty(s.miner);
 if(isBlock)await submitBlock(s,j,coinbase,root,version,ntimeHex,nonceHex,hashHex);
 return true;
}
function broadcast(j,clean){for(const s of sessions)if(s.authorized)try{notify(s,j,clean);}catch(e){}}

function handle(s,m){
 if(!m?.method)return;const id=m.id;
 try{
  if(m.method==="mining.subscribe"){s.subscribed=true;s.ex1=crypto.randomBytes(cfg.ex1Size).toString("hex");send(s,{id,result:[[["mining.notify","1"]],s.ex1,cfg.ex2Size],error:null});return;}
  if(m.method==="mining.configure"){const req=m.params?.[0]||[],r={};if(req.includes("version-rolling")){r["version-rolling"]=true;r["version-rolling.mask"]=cfg.versionMask.toString(16).padStart(8,"0");}send(s,{id,result:r,error:null});return;}
  if(m.method==="mining.authorize"){const username=String(m.params?.[0]||"");const{address,worker}=splitUser(username);addressScript(address);s.username=username;s.miner=minerState(address,worker);s.authorized=true;send(s,{id,result:true,error:null});if(current)notify(s,current,current.clean);return;}
  if(m.method==="mining.suggest_difficulty"){const d=Number(m.params?.[0]);if(Number.isFinite(d))s.miner.difficulty=Math.min(cfg.maxDiff,Math.max(cfg.minDiff,d));send(s,{id,result:true,error:null});if(current&&s.authorized)notify(s,current,false);return;}
  if(m.method==="mining.extranonce.subscribe"){send(s,{id,result:true,error:null});return;}
  if(m.method==="mining.submit"){submitShare(s,m.params).then(()=>send(s,{id,result:true,error:null})).catch(e=>{const a=Array.isArray(e)?e:[20,e.message,null];send(s,{id,result:null,error:a});});return;}
  send(s,{id,result:null,error:[20,"method not supported",null]});
 }catch(e){send(s,{id,result:null,error:[20,e.message,null]});}
}

const stratum=net.createServer(socket=>{
 if(sessions.size>=cfg.maxConnections)return socket.destroy();
 socket.setNoDelay(true);const s={socket,subscribed:false,authorized:false,buffer:"",ex1:"",miner:null,currentJob:null,remote:socket.remoteAddress};sessions.add(s);
 socket.on("data",c=>{s.buffer+=c.toString();let i;while((i=s.buffer.indexOf("\n"))>=0){const line=s.buffer.slice(0,i).trim();s.buffer=s.buffer.slice(i+1);const m=jsonLine(line);if(m)handle(s,m);}});
 socket.on("close",()=>sessions.delete(s));socket.on("error",()=>sessions.delete(s));
});
stratum.listen(cfg.stratumPort,cfg.stratumHost,()=>console.log("Stratum listening on",cfg.stratumPort));

function out(res,status,obj){const b=Buffer.from(JSON.stringify(obj));res.writeHead(status,{"content-type":"application/json","content-length":b.length,"access-control-allow-origin":"*"});res.end(b);}
function minerApi(m){return{address:m.address,worker:m.worker,difficulty:m.difficulty,hashrate:m.hashrate,bestDifficulty:m.bestDiff,accepted:m.shares,rejected:m.rejected,lastShare:m.lastShare||null};}
const api=http.createServer((req,res)=>{
 const u=new URL(req.url,"http://localhost");
 if(u.pathname==="/health")return out(res,200,{ok:true,node:!!current,height:current?.t?.height||null});
 if(u.pathname==="/api/pool")return out(res,200,{network:"Bitcoin Cash",node:"BCHN-compatible",stratumPort:cfg.stratumPort,apiPort:cfg.apiPort,connections:sessions.size,miners:[...miners.values()].map(minerApi),templates:stats.templates,acceptedShares:stats.accepted,rejectedShares:stats.rejected,blocksFound:stats.blocks,lastBlock:stats.lastBlock});
 if(u.pathname==="/api/miners")return out(res,200,[...miners.values()].map(minerApi));
 if(u.pathname==="/api/template/current"&&current)return out(res,200,{height:current.t.height,previousblockhash:current.t.previousblockhash,bits:current.t.bits,target:current.t.target||compactTarget(current.t.bits).toString(16).padStart(64,"0"),coinbasevalue:current.t.coinbasevalue,transactions:(current.t.transactions||[]).length,updatedAt:current.created});
 if(u.pathname==="/"){res.writeHead(200,{"content-type":"text/html; charset=utf-8"});return res.end('<!doctype html><html><head><meta charset="utf-8"><title>WxBCH Public Pool</title><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{font-family:system-ui;max-width:1100px;margin:40px auto;padding:0 16px;background:#111;color:#eee}table{width:100%;border-collapse:collapse}td,th{padding:8px;border-bottom:1px solid #333;text-align:left}code{color:#9f9}</style></head><body><h1>WxBCH Public Pool</h1><p>Bitcoin Cash solo mining • BCHN • Stratum V1</p><div id="a">Loading…</div><script>async function x(){let p=await fetch("/api/pool").then(r=>r.json());document.querySelector("#a").innerHTML="<p>Connections: "+p.connections+" • Miners: "+p.miners.length+" • Accepted: "+p.acceptedShares+" • Rejected: "+p.rejectedShares+" • Blocks: "+p.blocksFound+"</p><table><tr><th>Address</th><th>Worker</th><th>Difficulty</th><th>Hashrate</th><th>Accepted</th><th>Rejected</th></tr>"+p.miners.map(m=>"<tr><td><code>"+m.address+"</code></td><td>"+m.worker+"</td><td>"+m.difficulty.toFixed(4)+"</td><td>"+(m.hashrate/1e12).toFixed(3)+" TH/s</td><td>"+m.accepted+"</td><td>"+m.rejected+"</td></tr>").join("")+"</table>"}x();setInterval(x,5000)</script></body></html>');}
 out(res,404,{error:"not found"});
});
api.listen(cfg.apiPort,cfg.apiHost,()=>console.log("API listening on",cfg.apiPort));

async function watchZmq(){
 if(!cfg.zmq)return;
 try{const sub=new Subscriber();sub.connect(cfg.zmq);sub.subscribe("hashblock");for await(const [topic] of sub){if(topic.toString()==="hashblock"){try{const j=await refreshTemplate(true);broadcast(j,true);}catch(e){console.error("template refresh",e.message);}}}}
 catch(e){console.error("ZMQ",e.message);}
}
async function pollLoop(){
 while(true){
  try{const old=current?.t?.previousblockhash,j=await refreshTemplate(true);if(!old||j.t.previousblockhash!==old)broadcast(j,true);}
  catch(e){console.error("GBT",e.message);}
  await new Promise(r=>setTimeout(r,cfg.refreshSeconds*1000));
 }
}
(async()=>{
 for(let i=0;i<30&&!current;i++){try{current=await refreshTemplate(true);break;}catch(e){console.error("Waiting for BCHN:",e.message);await new Promise(r=>setTimeout(r,5000));}}
 watchZmq().catch(()=>{});pollLoop().catch(console.error);
})();
