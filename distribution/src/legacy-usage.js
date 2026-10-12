import {createHash} from 'node:crypto';
import {closeSync,constants,fstatSync,lstatSync,openSync,readFileSync,realpathSync} from 'node:fs';
import {dirname,join,resolve} from 'node:path';

const MAX=64*1024*1024,MAX_STATE=256*1024*1024;
const live=new Set(['running','working','starting','queued','retrying','pending']);
const hash=value=>createHash('sha256').update(value).digest('hex');
const parse=text=>{if(typeof text!=='string'||Buffer.byteLength(text)>MAX)throw Error('Saved accounting exceeds migration capacity; originals were preserved');return JSON.parse(text);};

/** Scalar-only projection over one explicitly selected legacy snapshot. */
export function legacyUsageReader(db,{database,engineId}){
 const directory=dirname(realpathSync(database));
 const has=name=>!!db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?").get(name);
 const records=has('state_records'),state=has('state'),resources=has('state_resources');
 const cold=reference=>{
  const id=reference?.$resource;if(!resources||typeof id!=='string'||!/^[a-f0-9]{64}$/.test(id))throw Error('Saved accounting source is unavailable');
  const row=db.prepare('SELECT value FROM state_resources WHERE id=? AND length(CAST(value AS BLOB))<=?').get(id,MAX);
  let raw=row?.value,value=parse(raw);
  if(value&&Object.keys(value).length===1&&Object.hasOwn(value,'$blob')){
   if(value.$blob!==id)throw Error('Saved accounting resource identity differs');
   const file=join(directory,'artifacts',id+'.json');
   if(realpathSync(file)!==resolve(file)||!lstatSync(file).isFile())throw Error('Saved accounting resource is redirected');
   const fd=openSync(file,constants.O_RDONLY|(constants.O_NOFOLLOW??0));
   try{const before=fstatSync(fd);if(!before.isFile()||before.size>MAX)throw Error('Saved accounting resource exceeds capacity');raw=readFileSync(fd,'utf8');const after=fstatSync(fd);if(before.size!==after.size||before.mtimeMs!==after.mtimeMs||hash(raw)!==id)throw Error('Saved accounting resource changed');}finally{closeSync(fd);}
   value=parse(raw);
  }else if(hash(raw)!==id)throw Error('Saved accounting resource digest differs');
  return value;
 };
 return async({engineId:engine,nativeSessionId,workingDirectory})=>{
  if(engine!==engineId)return [];
  if(typeof nativeSessionId!=='string'||!nativeSessionId||nativeSessionId.length>200)throw Error('Exact native accounting identity required');
  db.exec('BEGIN');
  try{
   const found=new Map(),overridden=new Set();
   if(records){
    // Inspect identities in SQL; do not hydrate unrelated messages or sessions.
    const invalid=db.prepare("SELECT 1 FROM state_records WHERE kind='session' AND NOT json_valid(value) LIMIT 1").get();if(invalid)throw Error('Saved session index is malformed; accounting cannot be resolved');
    for(const row of db.prepare("SELECT id,CASE WHEN length(CAST(value AS BLOB))<=? THEN value END AS value FROM state_records WHERE kind='session' AND (id=? OR json_extract(value,'$.runtimeSessionId')=?)").iterate(MAX,nativeSessionId,nativeSessionId)){
     const value=parse(row.value);overridden.add(String(row.id));if(value!==null){if(value.id!==row.id)throw Error('Saved session accounting identity differs');found.set(value.id,value);}
    }
   }
   if(state){
    const base=db.prepare('SELECT 1 FROM state WHERE id=1 AND json_valid(value) AND length(CAST(value AS BLOB))<=?').get(MAX_STATE);
    if(!base)throw Error('Saved session checkpoint is unavailable or exceeds capacity');
    // json_each extracts only candidates; the entire checkpoint stays in SQLite.
    for(const row of db.prepare("SELECT j.value FROM state,json_each(state.value,'$.sessions') j WHERE state.id=1 AND (json_extract(j.value,'$.id')=? OR json_extract(j.value,'$.runtimeSessionId')=?)").iterate(nativeSessionId,nativeSessionId)){
     const value=parse(row.value);if(overridden.has(value.id))continue;
     if(records&&db.prepare("SELECT 1 FROM state_records WHERE kind='session' AND id=?").get(value.id))continue;
     if(found.has(value.id))throw Error('Duplicate saved session accounting identity');found.set(value.id,value);
    }
   }
   const matches=[...found.values()].filter(row=>(row.runtimeSessionId||row.id)===nativeSessionId);
   if(matches.length>1)throw Error('Native accounting identity is ambiguous');
   if(!matches.length)return [];
   const session=matches[0];if(session.workspace!==workingDirectory)throw Error('Saved accounting workspace differs from the authorized native mapping');
   const tree=Object.hasOwn(session,'execution')?session.execution:session._coldFields?.execution?cold(session._coldFields.execution):{};
   if(!tree||typeof tree!=='object'||Array.isArray(tree)||!Array.isArray(tree.nodes??[])||!Array.isArray(tree.retiredUsageNodes??[]))throw Error('Saved accounting projection is malformed');
   const nodes=[...(tree.nodes??[]),...(tree.retiredUsageNodes??[])];if(nodes.length>100000)throw Error('Saved accounting exceeds migration capacity');
   const bound=new Set([session.id]);for(const row of nodes)if(row?.kind==='worker'&&row.rootSessionId===session.id&&typeof row.sessionId==='string')bound.add(row.sessionId);
   for(const worker of session.workers??[])if(typeof worker?.sessionId==='string')bound.add(worker.sessionId);
   const calls=[];
   for(const row of nodes){
    if(row?.kind!=='llm'||row.rootSessionId!==session.id||!bound.has(row.sessionId))continue;
    const result={kind:'llm',rootSessionId:nativeSessionId,sessionId:row.sessionId===session.id?nativeSessionId:row.sessionId,id:row.id,revision:row.revision??0,phase:live.has(row.phase)?'outcome_unknown':row.phase};
    for(const key of ['producerId','provider','model','lifecycle','startedAt','endedAt','admittedAt','budgetRevision'])if(row[key]!==undefined)result[key]=row[key];
    if(row.usage){result.usage={};for(const key of ['inputTokens','outputTokens','totalTokens','reasoningTokens','cacheReadTokens','cacheWriteTokens','costUsd','costType'])if(row.usage[key]!==undefined)result.usage[key]=row.usage[key];}
    calls.push(result);
   }
   return calls;
  }finally{db.exec('ROLLBACK');}
 };
}
