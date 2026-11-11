import test from 'node:test';
import assert from 'node:assert/strict';
import {appearanceImportFileArgs,appearanceMutationTarget,downloadBlobContent} from '../src/appearance-library.js';

function uploadFile(name,content){
 const bytes=content instanceof Uint8Array?content:new TextEncoder().encode(content);
 return {name,size:bytes.byteLength,arrayBuffer:async()=>bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength)};
}

test('mutation targets retain the exact read fingerprint and exclude CSS drafts',()=>{
 const read={id:'saved:ocean.amplifier.css',source:'saved',fingerprint:'a'.repeat(64),name:'Ocean',css:'original'};
 assert.deepEqual(appearanceMutationTarget(read),{id:read.id,expectedFingerprint:read.fingerprint});
 const refreshed={...read,fingerprint:'b'.repeat(64)};
 assert.notEqual(appearanceMutationTarget(read).expectedFingerprint,appearanceMutationTarget(refreshed).expectedFingerprint);
});

test('included entries and incomplete reads cannot become mutation targets',()=>{
 for(const entry of [null,{id:'builtin:aurora',source:'built-in',fingerprint:'a'.repeat(64)},{id:'saved:ocean.css',source:'saved'},{id:'saved:ocean.css',source:'saved',fingerprint:'invalid'}]){
  assert.throws(()=>appearanceMutationTarget(entry),/Read a saved appearance/);
 }
});

test('import accepts version-2 ZIP packages by content magic, not filename',async()=>{
 const archive=Uint8Array.of(0x50,0x4b,0x03,0x04,0x00,0xff);
 const expectedBase64=btoa(String.fromCharCode(...archive));
 assert.deepEqual(await appearanceImportFileArgs(uploadFile('mystery.bundle',archive)),{packageBase64:expectedBase64});
 await assert.rejects(appearanceImportFileArgs(uploadFile('looks-like.zip','not a ZIP archive')),/version-2 ZIP/);
 await assert.rejects(appearanceImportFileArgs({name:'missing-reader.zip',size:4}),/Choose a version-2 ZIP appearance package/);
});

test('ZIP import enforces the four-megabyte limit before and after reading',async()=>{
 const archive=new Uint8Array(4_000_000);archive.set([0x50,0x4b,0x03,0x04]);
 const packaged=await appearanceImportFileArgs(uploadFile('at-limit.zip',archive));
 assert.equal(packaged.packageBase64.length,5_333_336);
 const oversized={name:'over-limit.zip',size:4_000_001,arrayBuffer(){throw Error('Oversized input must be rejected before reading.')}};
 await assert.rejects(appearanceImportFileArgs(oversized),/4 MB or smaller/);
 const inaccurate={name:'small.zip',size:4,arrayBuffer:async()=>new Uint8Array(4_000_001).buffer};
 await assert.rejects(appearanceImportFileArgs(inaccurate),/4 MB or smaller/);
});

test('binary ZIP downloads require canonical base64 and return the original bytes',()=>{
 const bytes=Uint8Array.of(0x50,0x4b,0x03,0x04),base64=btoa(String.fromCharCode(...bytes));
 assert.deepEqual(downloadBlobContent({base64},'application/zip'),bytes);
 assert.throws(()=>downloadBlobContent({base64:'UEsDBA==',extra:true},'application/zip'),/payload is invalid/);
 assert.throws(()=>downloadBlobContent({base64:'not base64!'},'application/zip'),/payload is invalid/);
});