import test from 'node:test';
import assert from 'node:assert/strict';
import {appearanceImportArgs,appearanceMutationTarget} from '../src/appearance-library.js';

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

test('upload filename is only a fallback, never an override for embedded metadata',()=>{
 const css='/* amplifier-appearance: {"name":"Portable title"} */\n#amp-one{color:red}';
 assert.deepEqual(appearanceImportArgs('downloaded.amplifier.css',css),{css,fallbackName:'downloaded'});
 assert.deepEqual(appearanceImportArgs('.css',css),{css,fallbackName:'Imported appearance'});
 assert.equal(appearanceImportArgs('x'.repeat(200)+'.CSS',css).fallbackName.length,100);
});