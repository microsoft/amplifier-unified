// Private comparison cursors, never shared host read-state or conversation text.
const KEY='amplifier.related-work-seen.v1';
export function cursorStore(storage){
 let rows={};const refresh=()=>{try{const value=JSON.parse(storage?.getItem(KEY)||'null');if(value&&typeof value==='object'&&!Array.isArray(value))rows=value}catch{}};
 refresh();
 return {
  read(id){refresh();return typeof rows[id]?.cursor==='string'?rows[id].cursor:undefined},
  mark(id,expected,cursor){
   if(this.read(id)!==expected||typeof cursor!=='string')return false;
   rows[id]={cursor,at:Date.now()};rows=Object.fromEntries(Object.entries(rows).sort((a,b)=>(b[1]?.at||0)-(a[1]?.at||0)).slice(0,32));
   try{storage?.setItem(KEY,JSON.stringify(rows));return true}catch{return false}
  },
 };
}

export function portableClientTiming(actions,requests){
 const pick=(rows,fields)=>rows.slice(-30).map(row=>Object.fromEntries(fields.filter(key=>Number.isFinite(row[key])&&row[key]>=0).map(key=>[key,row[key]])));
 return {actionQueue:pick(actions,['at','queueMs','runMs']),request:pick(requests,['at','elapsedMs','serverMs','status']),
  rendering:{status:'not_collected'},
  limitations:['Browser actions include network and response handling; these are not render or provider timings.',
   'Client samples belong to this browser page and may include other chats. They contain no routes, command names, identifiers, drafts or request bodies.',
   'Client and host timestamps may differ; overlapping durations must not be summed.']};
}
