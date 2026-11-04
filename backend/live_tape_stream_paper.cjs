#!/usr/bin/env node
'use strict';
/*
 * PAPER-ONLY read-only Solana trade observer.
 *
 * Socket.IO per-pool data + independent Solana getSignatureStatuses confirmation.
 * No wallet, credentials, transaction signing, order execution or trade submission.
 * An incomplete 60-second window is UNKNOWN (fail closed for the GOLD strategy).
 */
const fs = require('node:fs');
const path = require('node:path');

const SOURCE = 'readonly-pool-stream+solana-confirmed-signature';
const WS_URL = process.env.NEO_STREAM_WS_URL || 'wss://sol.shrine.trade/socket.io/?EIO=4&transport=websocket';
const API = process.env.NEO_LOCAL_API || 'http://127.0.0.1:18804/state';
const OUT = process.env.NEO_STREAM_OUT || '/var/lib/neo-market/live_tape_stream_paper.json';
const RPC = process.env.NEO_STREAM_RPC || 'https://api.mainnet.solana.com';
const BACKUP_RPC = process.env.NEO_STREAM_RPC_FALLBACK || 'https://solana-rpc.publicnode.com';
const MAX_POOLS = Math.min(40, Math.max(1, Number(process.env.NEO_STREAM_MAX_POOLS) || 12));
const MAX_EVENTS = Math.min(1600, Math.max(100, Number(process.env.NEO_STREAM_MAX_EVENTS) || 800));
const WINDOW_MS = 60000;
const STICKY_MS = 210000;
const CONFIRM_MAX_AGE_MS = 12000;
const MAX_PENDING = 2000;
const EVENT_ID = (e) => [e.signature, e.pool, e.wallet, e.is_buy ? 'BUY' : 'SELL'].join(':');
const num = v => { const n = Number(v); return Number.isFinite(n) ? n : 0; };

function eligible(c) {
  const score = num(c.score), liq = num(c.liquidityUsd);
  const m5 = num((c.priceChange || {}).m5);
  const age = num(c.ageMinutes);
  const gold = score >= 85 && liq >= 15000 && -5 <= m5 && m5 <= 25;
  return Boolean(c.pairAddress && c.address && num(c.priceUsd)>0 &&
    ((age<=240 && score>=20 && liq>=1500 && -25<=m5 && m5<=35) || gold));
}
function isGold(c) {
  const m5=num(c.priceChange?.m5);
  return num(c.score)>=85 && num(c.liquidityUsd)>=15000 && -5<=m5 && m5<=25;
}
function chooseStableCandidates(candidates, existing, t, maxPools=MAX_POOLS) {
  const ranked=[...candidates.values()].sort((a,b)=>rank(b)-rank(a));
  const chosen=new Map();
  // Qualified GOLD setups displace warmers; other subscribed pools retain
  // their 60-second observation windows even when scanner order rotates.
  for(const c of ranked) {
    if(isGold(c)&&chosen.size<maxPools)chosen.set(String(c.pairAddress),c);
  }
  for(const [pool,p] of existing) {
    if(chosen.size>=maxPools)break;
    if(!chosen.has(pool)&&(candidates.has(pool) || t-p.lastEligible < STICKY_MS)) {
      chosen.set(pool,candidates.get(pool)||p.snapshot);
    }
  }
  for(const c of ranked) {
    if(chosen.size>=maxPools)break;
    const pool=String(c.pairAddress);
    if(!chosen.has(pool))chosen.set(pool,c);
  }
  return chosen;
}
function rank(c) {
  const buys = num(c.txns?.m5?.buys), sells = num(c.txns?.m5?.sells);
  const ratio = buys/Math.max(sells,1);
  const m5 = num(c.priceChange?.m5);
  const gold = num(c.score)>=85 && num(c.liquidityUsd)>=15000 && -5<=m5 && m5<=25;
  return (gold ? 1e9 : 0) + Math.min(500,ratio)*1e6 +
    num(c.volume?.m5)*2 + buys*100 + num(c.score)*10;
}
function convertTrade(x, meta, observedAt) {
  if (!x || (x.is_buy !== true && x.is_buy !== false) ||
      x.pool !== meta.pair || x.mint !== meta.mint ||
      typeof x.signature !== 'string' || x.signature.length < 64 ||
      typeof x.wallet !== 'string' || x.wallet.length < 32) return null;
  const amount = num(x.token_amount), quote = num(x.quote_amount);
  const chainTime = num(x.timestamp);
  const ts = chainTime > 1e12 ? Math.round(chainTime) : Math.round(chainTime * 1000);
  if (!(amount>0 && quote>0 && ts>0 && ts<=observedAt+5000 && ts>=observedAt-10000)) return null;
  const usd = amount * num(meta.price);
  if (!(usd>0 && Number.isFinite(usd))) return null;
  return {
    ts, event_time:ts, observed_at:observedAt,
    direction:x.is_buy?'BUY':'SELL', token_amount:amount,
    usd_amount:Number(usd.toFixed(6)), wallet:x.wallet,
    address:meta.mint, pairAddress:meta.pair, symbol:meta.symbol,
    signature:x.signature, event_id:EVENT_ID(x),
    price_estimate:num(meta.price), quality_flags:[],
    provider:SOURCE, note:x.is_buy?'BUY':'SELL',
    confirmed_swap:false, usd_valuation_estimated:true,
  };
}
function confirmResult(status) {
  if (!status || status.err !== null) return false;
  return status.confirmationStatus === 'confirmed' || status.confirmationStatus === 'finalized';
}

const pairs = new Map();
const pending = new Map();
const events = [];
const eventIds = new Set();
let ws = null, socketConnected = false, lastFrameAt=0, sequence=0, uptimeSince=0;
let lastRpcSuccess=0, lastRpcErr=null, lastConfirmationAt=0;
let rpcChecks=0, rpcConfirmed=0, rpcFailed=0, wsDisconnects=0, failedSub=0;
let activeProvider = RPC, verifying = false, reconnectScheduled=false;
let discoveryError=null, lastFeedAt=0;
const now = () => Date.now();

function clearPair(pool) {
  pairs.delete(pool);
  for(const [id,row] of pending) if(row.meta.pair===pool) pending.delete(id);
}
function markGap(meta, t) {
  const pair = pairs.get(meta.pair);
  if(pair) {pair.since=t;pair.gaps++;pair.lastGap=t;}
}
async function pullFeed() {
  try {
    const res=await fetch(API,{signal:AbortSignal.timeout(6000)});
    if(!res.ok) throw new Error('state HTTP '+res.status);
    const s=await res.json();
    lastFeedAt=now(); discoveryError=null;
    const all = new Map();
    for(const c of s.feed||[]) if(eligible(c)) {
      const p=String(c.pairAddress); if(!all.has(p) || rank(c)>rank(all.get(p))) all.set(p,c);
    }
    for(const pos of s.positions||[]) {
      const c={...(pos.coin_snapshot||{}),pairAddress:pos.pairAddress,address:pos.address};
      if(c.pairAddress && c.address && !all.has(c.pairAddress)) all.set(c.pairAddress,c);
    }
    const chosen=chooseStableCandidates(all,pairs,now(),MAX_POOLS);
    for(const [pool,p] of pairs) {
      if(!chosen.has(pool)) {
        if(socketConnected) emit('unsubscribe_trades',{pool});
        clearPair(pool);
      }
    }
    for(const [pool,c] of chosen) {
      const meta={pair:pool,mint:String(c.address),symbol:String(c.symbol||'?'),price:num(c.priceUsd),snapshot:c};
      const old=pairs.get(pool);
      if(old) {
        if(old.meta.mint!==meta.mint){if(socketConnected) emit('unsubscribe_trades',{pool});clearPair(pool);}
        else {
          old.meta=meta;old.snapshot=c;
          if(all.has(pool))old.lastEligible=now();
          if(socketConnected&&!old.acked && now()-old.attempt>15000)subscribe(pool);
          continue;
        }
      }
      const p={meta,snapshot:c,lastEligible:now(),since:0,acked:false,gaps:0,lastGap:0,attempt:0};
      pairs.set(pool,p);
      if(socketConnected) subscribe(pool);
    }
  } catch(e){ discoveryError=String(e?.message||e).slice(0,180);}
}
function emit(method, payload, cb) {
  if(!ws || ws.readyState!==WebSocket.OPEN || !socketConnected) return;
  let id=0;
  if(cb){id=++sequence;acks.set(id,cb);}
  ws.send('42'+(id?String(id):'')+JSON.stringify([method,payload]));
}
const acks = new Map();
function subscribe(pool){
  const p=pairs.get(pool);
  if(!p||!socketConnected)return;
  p.attempt=now();
  emit('subscribe_trades',{pool},result=>{
    if(!pairs.has(pool))return;
    const q=pairs.get(pool);
    if(result && result.ok && result.pool===pool) {q.acked=true;q.since=now();}
    else {q.acked=false;q.since=0;failedSub++;}
  });
}
function acceptRaw(raw) {
  const data=String(raw);
  lastFrameAt=now();
  if(data==='2'){ws?.send('3');return;}
  if(data[0]==='0') {
    if(ws?.readyState===WebSocket.OPEN) ws.send('40');
    return;
  }
  if(data.startsWith('40')){
    socketConnected=true;uptimeSince=now();
    for(const [pool,p] of pairs){p.acked=false;p.since=0;subscribe(pool);}
    return;
  }
  if(data.startsWith('43')){
    const m=data.match(/^43(\d+)(.*)$/);if(!m)return;
    const fn=acks.get(Number(m[1]));acks.delete(Number(m[1]));
    if(fn){try{const x=JSON.parse(m[2]);fn(Array.isArray(x)?x[0]:x);}catch(e){fn({error:'bad_ack'});}}
    return;
  }
  if(!data.startsWith('42'))return;
  let v; try{v=JSON.parse(data.slice(2));}catch(e){return;}
  if(!Array.isArray(v))return;
  if(v[0]==='subscription_error') {failedSub++;return;}
  if(v[0]!=='trade')return;
  const x=v[1];const p=pairs.get(String(x?.pool||''));
  if(!p||!p.acked)return;
  const ev=convertTrade(x,p.meta,now());
  if(!ev)return;
  if(eventIds.has(ev.event_id) || pending.has(ev.event_id))return;
  if(pending.size>=MAX_PENDING){markGap(p.meta,now());return;}
  pending.set(ev.event_id,{event:ev,meta:p.meta,firstSeen:now()});
}
function connect(){
  if(reconnectScheduled)return;
  socketConnected=false;acks.clear();
  try {
    ws=new WebSocket(WS_URL);
    ws.addEventListener('message',e=>acceptRaw(e.data));
    ws.addEventListener('error',()=>{lastRpcErr=lastRpcErr || 'stream connection error';});
    ws.addEventListener('close',()=>{socketConnected=false;wsDisconnects++; for(const p of pairs.values()){p.acked=false;p.since=0;p.gaps++;}
      scheduleReconnect();});
  }catch(e){scheduleReconnect();}
}
function scheduleReconnect(){
  if(reconnectScheduled)return;
  reconnectScheduled=true;
  setTimeout(()=>{reconnectScheduled=false;connect()},3000);
}
async function rpcStatuses(sig,host) {
 const req={jsonrpc:'2.0',id:1,method:'getSignatureStatuses',params:[sig,{searchTransactionHistory:false}]};
 const r=await fetch(host,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(req),signal:AbortSignal.timeout(7000)});
 if(!r.ok)throw new Error('RPC_HTTP_'+r.status);
 const j=await r.json();
 if(j.error||!Array.isArray(j.result?.value) || j.result.value.length!==sig.length)throw new Error('RPC_BAD_STATUS');
 return j.result.value;
}
async function confirmPending() {
 if(verifying)return;verifying=true;
 try{
   const t=now();
   // Old/missing signatures are never counted as observed/confirmed flow.
   for(const [id,row] of pending) if(t-row.firstSeen>CONFIRM_MAX_AGE_MS){
     pending.delete(id); markGap(row.meta,t);rpcFailed++;
   }
   if(!pending.size){
     // Lightweight health verification still checks the RPC, not every 2s.
     if(lastRpcSuccess>0 && t-lastRpcSuccess<12000)return;
     try{await rpcStatuses([],activeProvider);lastRpcSuccess=now();lastRpcErr=null;}catch(e){lastRpcErr=e.message;}
     return;
   }
   const selected=[...pending.entries()].slice(0,200);
   const signatures=[...new Set(selected.map(([,row])=>row.event.signature))];
   let statuses;
   try{statuses=await rpcStatuses(signatures,activeProvider);}
   catch(e) {
     lastRpcErr=String(e.message).slice(0,120);
     const fallback=activeProvider===RPC?BACKUP_RPC:RPC;
     try{statuses=await rpcStatuses(signatures,fallback);activeProvider=fallback;}
     catch(e2){lastRpcErr+= ' / '+e2.message;return;}
   }
   lastRpcErr=null;lastRpcSuccess=now();rpcChecks++;
   const lookup=new Map(signatures.map((sg,i)=>[sg,statuses[i]]));
   for(const [id,row] of selected) {
     const status=lookup.get(row.event.signature);
     if(status && status.err!==null){pending.delete(id);markGap(row.meta,now());rpcFailed++;continue;}
     if(!confirmResult(status))continue;
     if(!pairs.has(row.meta.pair)){pending.delete(id);continue;}
     pending.delete(id);
     if(eventIds.has(id))continue;
     const event={...row.event,confirmed_swap:true,available_at:now(),ingested_at:now(),confirmation_status:status.confirmationStatus};
     eventIds.add(id);events.unshift(event);rpcConfirmed++;
     if(events.length>MAX_EVENTS){const v=events.pop();eventIds.delete(v.event_id);}
     lastConfirmationAt=now();
   }
 }finally{verifying=false;}
}
function stateSnapshot() {
 const t=now();
 const connected=socketConnected && t-lastFrameAt<50000 && t-uptimeSince>0;
 const rpcHealthy=lastRpcSuccess>0 && t-lastRpcSuccess<=12000;
 const coverage={};let complete=0;
 for(const [pool,p] of pairs) {
   const q=[...pending.values()].filter(x=>x.meta.pair===pool);
   const oldest=q.length? Math.min(...q.map(x=>x.firstSeen)):null;
   const noGap=p.acked && p.since>0 && t-p.since>=WINDOW_MS;
   const pendingLag=oldest?t-oldest:0;
   const ok=connected && rpcHealthy && noGap && pendingLag<=3000;
   if(ok)complete++;
   coverage[pool]={
     address:p.meta.mint,pairAddress:pool,status:ok?'COMPLETE':'UNKNOWN',
     complete_since_ms:ok?p.since:null,last_poll_at:lastRpcSuccess,
     backlog:q.length,oldest_pending_at:oldest,pagination_pending:false,
     unclassified:0,max_lag_ms:pendingLag,
     reason:ok?null:!connected?'STREAM_DISCONNECTED':!p.acked?'STREAM_NOT_SUBSCRIBED':!rpcHealthy?'RPC_CONFIRMATION_UNAVAILABLE':!noGap?'FLOW_WARMING':'FLOW_CONFIRMATION_BACKLOG',
   };
 }
 const recent=events.filter(x=>t-x.ts<=300000 && t-x.ts>=0);
 return {
   status:!connected?'degraded':complete?'online':'warming',
   tracked_pairs:pairs.size,source:SOURCE,schema_version:'paper-stream-1',
   updated_at:t,poll_seconds:2,window_ms:WINDOW_MS,
   events:recent,pair_coverage:coverage,coverage:complete/Math.max(1,pairs.size),
   warming_pairs:pairs.size-complete,events_total:events.length,
   backlog:pending.size,current_backlog:pending.size,
   stale_pending:0,lag_ms:Math.max(0,...[...pending.values()].map(x=>t-x.firstSeen)),
   stream_connected:connected,stream_uptime_seconds:connected?Math.floor((t-uptimeSince)/1000):0,
   last_stream_frame_age_ms:Math.max(0,t-lastFrameAt),
   last_rpc_success_age_ms:lastRpcSuccess?t-lastRpcSuccess:null,
   last_confirmed_event_age_ms:lastConfirmationAt?t-lastConfirmationAt:null,
   rpc_confirmed_total:rpcConfirmed,rpc_failed_total:rpcFailed,rpc_checks:rpcChecks,
   rpc_endpoint:activeProvider,ws_disconnects:wsDisconnects,subscription_failures:failedSub,
   discovery_updated_at:lastFeedAt,
   error:!connected?'stream_disconnected':discoveryError||lastRpcErr||null,
 };
}
function writeState(){
 const data=stateSnapshot();
 const temp=OUT+'.'+process.pid+'.tmp';
 try{
   fs.mkdirSync(path.dirname(OUT),{recursive:true});
   fs.writeFileSync(temp,JSON.stringify(data));
   fs.renameSync(temp,OUT);
 }catch(e){console.error('FILE_ERROR',String(e.message).slice(0,160));}
}
function main(){
 if(typeof WebSocket!=='function')throw new Error('Requires Node 22 native WebSocket');
 console.log('PAPER streaming observation starting',SOURCE,'max pools',MAX_POOLS);
 void pullFeed();
 connect();
 setInterval(()=>void pullFeed(),10000);
 setInterval(()=>void confirmPending(),2000);
 setInterval(writeState,3000);
 setInterval(()=>{
   const t=now();
   if(ws && ws.readyState===WebSocket.OPEN && t-lastFrameAt>55000){
     ws.close(); // reconnect and reset completeness
   }
   for(const [id,row] of pending) if(t-row.firstSeen>CONFIRM_MAX_AGE_MS){
     pending.delete(id);markGap(row.meta,t);rpcFailed++;
   }
 },5000);
 process.on('SIGTERM',()=>{writeState();ws?.close();process.exit(0);});
}
module.exports={eligible,rank,isGold,chooseStableCandidates,convertTrade,confirmResult,EVENT_ID};
if(require.main===module)main();
