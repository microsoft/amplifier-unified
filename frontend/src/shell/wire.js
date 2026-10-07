// Restore the public component contract while retaining shared object identity.
export function expandShell(next){
 if(!next.snapshotRefs)return next;
 const snapshots={...next.snapshots};
 for(const [id,refs] of Object.entries(next.snapshotRefs)){
  snapshots[id]={...snapshots[id]};
  for(const [key,index] of Object.entries(refs))snapshots[id][key]=next.sharedSnapshotValues[index];
 }
 const {sharedSnapshotValues,snapshotRefs,...rest}=next;
 return {...rest,snapshots};
}
// Preserve equal branches without allocating two large serialized strings.
export function retainEqual(previous,next){
 if(previous===next)return previous;
 if(!previous||!next||typeof previous!=='object'||typeof next!=='object'||Array.isArray(previous)!==Array.isArray(next))return next;
 const keys=Object.keys(next);let equal=keys.length===Object.keys(previous).length;
 const result=Array.isArray(next)?[]:{};
 for(const key of keys){result[key]=retainEqual(previous[key],next[key]);if(!Object.hasOwn(previous,key)||result[key]!==previous[key])equal=false}
 return equal?previous:result;
}
