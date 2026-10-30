import test from 'node:test';
import assert from 'node:assert/strict';
import {download} from '../src/api.js';

function replace(target,key,value){
 const descriptor=Object.getOwnPropertyDescriptor(target,key);
 Object.defineProperty(target,key,{configurable:true,writable:true,value});
 return ()=>descriptor?Object.defineProperty(target,key,descriptor):delete target[key];
}

function captureDownload(filename,content,type){
 const anchor={href:'',download:'',clicked:false,click(){this.clicked=true}};
 let blob;
 const restoreDocument=replace(globalThis,'document',{createElement(name){assert.equal(name,'a');return anchor}});
 const restoreCreate=replace(URL,'createObjectURL',value=>{blob=value;return 'blob:appearance-package-test'});
 const restoreRevoke=replace(URL,'revokeObjectURL',()=>{});
 const restoreTimer=replace(globalThis,'setTimeout',()=>0);
 try{download(filename,content,type)}finally{restoreTimer();restoreRevoke();restoreCreate();restoreDocument()}
 return {anchor,blob};
}

test('ZIP download preserves exact bytes, MIME type, and server filename',async()=>{
 const bytes=Uint8Array.of(0x50,0x4b,0x03,0x04,0x00,0xff,0x41);
 const base64=btoa(String.fromCharCode(...bytes));
 const {anchor,blob}=captureDownload('lightline.zip',{base64},'application/zip');
 assert.equal(anchor.download,'lightline.zip');
 assert.equal(anchor.clicked,true);
 assert.equal(blob.type,'application/zip');
 assert.deepEqual(new Uint8Array(await blob.arrayBuffer()),bytes);
 assert.notEqual(await blob.text(),JSON.stringify({base64},null,2));
});

test('malformed binary payloads fail instead of downloading JSON',()=>{
 assert.throws(()=>download('broken.zip',{base64:'not base64'},'application/zip'),/binary download payload is invalid/);
 assert.throws(()=>download('broken.zip',{base64:42},'application/zip'),/binary download payload is invalid/);
 assert.throws(()=>download('broken.zip','plain text','application/zip'),/binary download payload is invalid/);
});

test('text and ordinary JSON downloads retain their original bytes',async()=>{
 const text='Résumé\r\n';
 const textDownload=captureDownload('notes.txt',text,'text/plain');
 assert.equal(await textDownload.blob.text(),text);
 const record={message:'café',count:2};
 const jsonDownload=captureDownload('report.json',record,'application/json');
 assert.equal(await jsonDownload.blob.text(),JSON.stringify(record,null,2));
 const base64Record={base64:'not a binary protocol at this MIME type'};
 const ordinaryDownload=captureDownload('record.json',base64Record,'application/json');
 assert.equal(await ordinaryDownload.blob.text(),JSON.stringify(base64Record,null,2));
});