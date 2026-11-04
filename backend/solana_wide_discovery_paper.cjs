#!/usr/bin/env node
'use strict';
/**
 * Read-only, non-executable Solana token discovery. Never signs, routes,
 * submits or purchases anything. Unverified mints are discovery leads ONLY.
 */
const fs=require('node:fs');
const path=require('node:path');
const URL=process.env.NEO_WIDE_DISCOVERY_WS||'wss://stream.pumpapi.io/';
const OUT=process.env.NEO_WIDE_DISCOVERY_PATH||'/var/lib/neo-market/wide_discovery.json';
const CAP=Math.max(1000,Math.min(20000,Number(process.env.NEO_WIDE_DISCOVERY_MEMORY_CAP)||12000));
const EMIT=Math.max(500,Math.min(8000,Number(process.env.NEO_WIDE_DISCOVERY_OUTPUT_CAP)||5000));
const TTL=Math.max(60000,Math.min(1800000,Number(process.env.NEO_WIDE_DISCOVERY_TTL_MS)||900000));
const BASE58=/^[1-9A-HJ-NP-Za-km-z]{32,44}$/;
const VALID_ACTIONS=new Set(['create','createPool','migrate','buy','sell']);
const data=new Map();
let ws=null,connected=false,startedAt=Date.now(),lastMessageAt=0,lastError=null,disconnects=0,received=0,accepted=0,totalUnique=0,writtenAt=0,parseErrors=0,reconnectTimer=null;
function observe(e,at=Date.now()) {
  if(!e || !VALID_ACTIONS.has(e.action))return false;
  const mint=String(e.mint||'');
  if(!BASE58.test(mint))return false;
  // Source events are leads, not verified tokens or executable prices.
  const prev=data.get(mint);
  const row=prev||{address:mint,first_seen:at,last_seen:at,events:0,buys:0,sells:0,created:0};
  if(!prev){totalUnique++;}
  row.last_seen=at;
  row.events=Math.min(50000,row.events+1);
  if(e.action==='buy')row.buys++;
  if(e.action==='sell')row.sells++;
  if(e.action==='create'||e.action==='createPool')row.created++;
  data.set(mint,row);
  accepted++;
  return true;
}
function rank(row,at){
  const age=Math.max(0,(at-row.last_seen)/1000);
  return Math.min(100,row.buys+row.sells)*1000+Math.min(8,row.created)*300+Math.min(100,row.events)-age*2;
}
function prune(at=Date.now()){
  for(const [mint,row] of data)if(at-row.last_seen>TTL)data.delete(mint);
  if(data.size>CAP){
    const sorted=[...data.values()].sort((a,b)=>rank(b,at)-rank(a,at));
    const keep=new Set(sorted.slice(0,CAP).map(x=>x.address));
    for(const mint of data.keys())if(!keep.has(mint))data.delete(mint);
  }
}
function snapshot(at=Date.now()){
  prune(at);
  const sorted=[...data.values()].sort((a,b)=>rank(b,at)-rank(a,at)).slice(0,EMIT);
  return {
    schema_version:1,mode:'READ_ONLY_PAPER_DISCOVERY',
    source:'pumpapi-trade-stream-unverified',
    updated_at:at,started_at:startedAt,connected:connected && at-lastMessageAt<30000,
    last_message_at:lastMessageAt,disconnected_count:disconnects,
    messages_received:received,mint_events_accepted:accepted,
    unique_mints_since_start:totalUnique,unique_mints_in_window:data.size,
    listed_mints:sorted.length,ttl_ms:TTL,
    error:lastError,parse_errors:parseErrors,
    // No wallets, private keys, amounts or unsigned transactions persisted.
    tokens:sorted.map(x=>x),
  };
}
function write(){
  const payload=snapshot(); const temp=OUT+'.'+process.pid+'.tmp';
  try{
    fs.mkdirSync(path.dirname(OUT),{recursive:true});
    fs.writeFileSync(temp,JSON.stringify(payload));
    fs.renameSync(temp,OUT);writtenAt=Date.now();
  }catch(e){console.error('WIDE_DISCOVERY_WRITE_ERROR',String(e.message).slice(0,160));}
}
function connect(){
  try{
    ws=new WebSocket(URL);
    ws.binaryType='arraybuffer';
    ws.addEventListener('open',()=>{connected=true;lastError=null;console.log('WIDE_DISCOVERY_CONNECTED',new Date().toISOString());});
    ws.addEventListener('message',evt=>{
      received++;
      let obj;
      try{
        const raw=typeof evt.data==='string'?evt.data:Buffer.from(evt.data).toString('utf8');
        obj=JSON.parse(raw);
      }catch(e){parseErrors++;return;}
      lastMessageAt=Date.now();
      observe(obj,lastMessageAt);
    });
    ws.addEventListener('close',()=>{connected=false;disconnects++;lastError='stream_disconnected';scheduleReconnect();});
    ws.addEventListener('error',()=>{lastError='stream_transport_error';});
  }catch(e){connected=false;lastError=String(e.message||e).slice(0,120);scheduleReconnect();}
}
function scheduleReconnect(){
  if(reconnectTimer)return;
  reconnectTimer=setTimeout(()=>{reconnectTimer=null;connect();},4000);
}
function main(){
  if(typeof WebSocket!=='function')throw new Error('Node 22 required');
  console.log('Starting read-only token discovery');
  connect();setInterval(write,10000);
  setInterval(()=>{
    if(ws?.readyState===WebSocket.OPEN && Date.now()-lastMessageAt>45000)ws.close();
  },10000);
  process.on('SIGTERM',()=>{write();ws?.close();process.exit(0);});
}
module.exports={observe,rank,prune,snapshot,BASE58,VALID_ACTIONS};
if(require.main===module)main();
