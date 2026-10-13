import test from 'node:test';
import assert from 'node:assert/strict';
import {chatDeepLink,createChatLinkReceiver} from '../src/chat-deep-link.js';
test('only a bounded explicit chat target is accepted',()=>{
 assert.equal(chatDeepLink('?chat=session-123'),'session-123');
 assert.equal(chatDeepLink('?chat=%2Fodd%20id'),'/odd id');
 assert.equal(chatDeepLink('?action=conversation.send&text=hello'),null);
 assert.equal(chatDeepLink('?chat='),null);
 assert.equal(chatDeepLink('?chat=%00'),null);
 assert.equal(chatDeepLink('?chat='+'a'.repeat(201)),null);
});

test('queued and repeated PWA launches select exact chats without navigating the document',async()=>{
 const location={href:'https://host/?chat=first',origin:'https://host',pathname:'/',search:'?chat=first'};
 const selected=[],errors=[],replaced=[];let launch;
 const receiver=createChatLinkReceiver({location,history:{replaceState:(_a,_b,url)=>replaced.push(url.href)},
  launchQueue:{setConsumer(fn){launch=fn}},reportError:error=>errors.push(error)});
 launch({targetURL:location.href}); // Cold launch is also present in location.
 launch({targetURL:'https://host/?chat=second'}); // Arrives before boot.
 assert.deepEqual(selected,[]);
 const unbind=receiver.bind(async id=>{selected.push(id);return {accepted:true}});
 await new Promise(resolve=>setImmediate(resolve));
 assert.deepEqual(selected,['first','second']);
 launch({targetURL:'https://evil/?chat=foreign'});launch({targetURL:'https://host/other?chat=wrong-path'});
 launch({targetURL:'https://host/?chat=third'});
 await new Promise(resolve=>setImmediate(resolve));
 assert.deepEqual(selected,['first','second','third']);assert.equal(replaced.length,1);assert.deepEqual(errors,[]);
 unbind();launch({targetURL:'https://host/?chat=fourth'});
 assert.equal(selected.length,3);
 receiver.bind(async id=>{selected.push(id);return {accepted:true}});
 await new Promise(resolve=>setImmediate(resolve));assert.equal(selected.at(-1),'fourth');
});
test('rejected navigation retains URL and reports errors, subsequent launches still work',async()=>{
 const errors=[];let launch;
 const receiver=createChatLinkReceiver({location:new URL('https://host/?chat=missing'),
  history:{replaceState(){assert.fail('Rejected navigation cleared URL')}},
  launchQueue:{setConsumer(fn){launch=fn}},reportError:error=>errors.push(error.message)});
 const selected=[];
 receiver.bind(async id=>{selected.push(id);throw Error('Unavailable')});
 await new Promise(resolve=>setImmediate(resolve));launch({targetURL:'https://host/?chat=next'});
 await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(selected,['missing','next']);assert.equal(errors.length,2);
});
