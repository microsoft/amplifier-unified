import {createHmac, randomBytes, timingSafeEqual, randomUUID} from 'node:crypto';
import {lstat, realpath, mkdir, open, rename} from 'node:fs/promises';
import {constants} from 'node:fs';
import {isAbsolute, join, dirname} from 'node:path';

export async function privateAuthorityDirectory(path:string,create=false) {
  if(!isAbsolute(path))throw Error('manual_authority_path_invalid');
  if(create){
    await mkdir(path,{mode:0o700}); // EEXIST is a permanent launch/claim guard.
    // Persist the exclusive directory entry before any launch/claim effects.
    // Syncing files inside it alone does not make the parent entry durable.
    await syncDirectory(dirname(path));
  }
  const s=await lstat(path);
  if(!s.isDirectory()||s.isSymbolicLink()||await realpath(path)!==path||s.uid!==process.getuid?.()||(s.mode&0o077))
    throw Error('manual_authority_path_invalid');
}
async function syncDirectory(path:string){const f=await open(path,constants.O_RDONLY);try{await f.sync();}finally{await f.close();}}
export async function privateBytes(path:string){
  // A FIFO must reach the regular-file guard without waiting for a writer.
  const f=await open(path,constants.O_RDONLY|constants.O_NOFOLLOW|constants.O_NONBLOCK);
  try{const s=await f.stat();if(!s.isFile()||s.uid!==process.getuid?.()||(s.mode&0o077)||s.size>65536)throw Error('manual_authority_file_invalid');
    const b=Buffer.alloc(s.size+1);let n=0;while(n<b.length){const r=await f.read(b,n,b.length-n,null);if(!r.bytesRead)break;n+=r.bytesRead;}
    const a=await f.stat();if(n!==s.size||a.size!==s.size||a.mtimeMs!==s.mtimeMs||a.ctimeMs!==s.ctimeMs)throw Error('manual_authority_file_changed');
    return b.subarray(0,n);
  }finally{await f.close();}
}
export function seal(value:unknown,key:Buffer){const text=JSON.stringify(value);if(Buffer.byteLength(text)>60000)throw Error('manual_authority_limit');return {text,mac:createHmac('sha256',key).update(text).digest('hex')};}
export function unseal(value:any,key:Buffer):any {
  if(!value||typeof value.text!=='string'||typeof value.mac!=='string'||! /^[a-f0-9]{64}$/.test(value.mac)||Buffer.byteLength(value.text)>60000)
    throw Error('manual_authority_authentication_failed');
  const actual=createHmac('sha256',key).update(value.text).digest();
  if(!timingSafeEqual(actual,Buffer.from(value.mac,'hex')))throw Error('manual_authority_authentication_failed');
  return JSON.parse(value.text);
}
export async function writeAuthority(path:string,key:Buffer,value:unknown){
  const temporary=join(path,randomUUID()+'.tmp'),f=await open(temporary,'wx',0o600);
  try{await f.writeFile(JSON.stringify(seal(value,key)));await f.sync();}finally{await f.close();}
  await rename(temporary,join(path,'authority.json'));await syncDirectory(path);
}
export async function createAuthority(path:string,value:unknown){
  await privateAuthorityDirectory(path,true);const key=randomBytes(32),f=await open(join(path,'key'),'wx',0o600);
  try{await f.writeFile(key);await f.sync();}finally{await f.close();}
  await writeAuthority(path,key,value);return key;
}
export async function authorityKey(path:string){await privateAuthorityDirectory(path);const key=await privateBytes(join(path,'key'));if(key.length!==32)throw Error('manual_authority_key_invalid');return key;}
export async function readAuthority(path:string,key:Buffer){await privateAuthorityDirectory(path);return unseal(JSON.parse((await privateBytes(join(path,'authority.json'))).toString()),key);}
