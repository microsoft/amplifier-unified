import test from 'node:test';
import assert from 'node:assert/strict';
import {feedbackDiagnostics,trackAction,setFeedbackEventStream} from '../src/feedback-diagnostics.js';

test('diagnostics recognize Edge ahead of its Chrome token and expose only selected facts',()=>{
 const env={navigator:{userAgent:'Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/140.0.1 Safari/537.36 Edg/140.0.2 PRIVATE',onLine:true,serviceWorker:{controller:{secret:'PRIVATE'}}},innerWidth:1400,innerHeight:900,devicePixelRatio:2,isSecureContext:true,matchMedia:query=>({matches:query==='(prefers-color-scheme: dark)'}),performance:{now:()=>2000},document:{visibilityState:'visible'},location:{href:'PRIVATE'}};
 const facts=feedbackDiagnostics({view:{scheme:'system'},messages:['PRIVATE'],settings:{secret:'PRIVATE'}},env);
 assert.equal(facts.browser,'Edge');assert.equal(facts.browserVersion,'140.0.2');assert.equal(facts.deviceOS,'Windows');assert.equal(facts.resolvedAppearance,'dark');assert.equal(facts.serviceWorkerControlled,true);assert.equal(facts.pageAgeSeconds,2);assert.equal(JSON.stringify(facts).includes('PRIVATE'),false);
 const settle=trackAction();assert.equal(feedbackDiagnostics({},env).pendingActions,1);settle();assert.equal(feedbackDiagnostics({},env).pendingActions,0);
});


test('event stream reports its own lifecycle, independently of navigator connectivity',()=>{
 const environment={navigator:{onLine:true}};
 for(const value of ['connecting','open','reconnecting','closed']){
  setFeedbackEventStream(value);
  assert.equal(feedbackDiagnostics({},environment).eventStream,value);
  assert.equal(feedbackDiagnostics({},environment).online,true);
 }
 setFeedbackEventStream('PRIVATE arbitrary state');
 assert.equal(feedbackDiagnostics({},environment).eventStream,'unknown');
});
