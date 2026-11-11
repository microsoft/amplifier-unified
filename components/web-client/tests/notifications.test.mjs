import test from 'node:test';
import assert from 'node:assert/strict';
import {notificationBody,desktopNotificationsEnabled,notificationMessages,notificationEvents,takeNotifications,showBrowserNotification} from '../src/notifications.js';

test('compact off-page notifications preserve session targets and deduplicate active messages',()=>{
 const state={sessions:[{id:'active',messages:[{id:'same',role:'assistant',via:'text',text:'Full body'},{id:'chat-only',role:'assistant',via:'chat'}]}],notificationMessages:[{id:'same',sessionId:'active',role:'assistant',via:'text'},{id:'off-page',sessionId:'older-chat',role:'assistant',via:'text',text:'Finished'},{id:'wrong-role',sessionId:'other',role:'user',via:'text'}]};
 assert.deepEqual(notificationMessages(state),[{id:'same',sessionId:'active',role:'assistant',via:'text'},{id:'off-page',sessionId:'older-chat',role:'assistant',via:'text',text:'Finished'}]);
 assert.equal(notificationMessages({sessions:state.sessions})[0].text,'Full body','older full snapshots remain supported');
 assert.equal(notificationBody(notificationMessages(state)[1],{preview:false}),'Your Amplifier response is ready.');
});
test('desktop notifications respect opt-out and never include response text without preview opt-in',()=>{
 const message={text:'Private work details'};
 assert.equal(desktopNotificationsEnabled({desktop:false}),false);
 assert.equal(desktopNotificationsEnabled({desktop:true}),true);
 assert.equal(notificationBody(message,{}),'Your Amplifier response is ready.');
 assert.equal(notificationBody(message,{preview:false}),'Your Amplifier response is ready.');
 assert.equal(notificationBody(message,{preview:true}),'Private work details');
});

test('loading an old chat cannot create notifications outside the authoritative window',()=>{
 const sessions=[{id:'old-chat',messages:[{id:'old-reply',role:'assistant',via:'text',text:'An old response'}]}];
 assert.deepEqual(notificationMessages({sessions,notificationMessages:[]}),[]);
 assert.deepEqual(notificationMessages({sessions,notificationMessages:[{id:'new-reply',sessionId:'other-chat',role:'assistant',via:'text'}]}),[{id:'new-reply',sessionId:'other-chat',role:'assistant',via:'text'}]);
});
test('decision alerts require separate opt-in, permission and off-chat/hidden state',()=>{
 const state={selectedSessionId:'chat',notificationSettings:{desktop:true,needsInput:true},notificationMessages:[],attention:{items:[{id:'question:q',sessionId:'chat',fingerprint:'v1',detail:'private question'},{id:'approval:a',sessionId:'other',fingerprint:'v1',detail:'private approval'},{id:'session:x',sessionId:'other',fingerprint:'v1'}]}};
 const opts={connected:true,permission:'granted',hidden:false};
 assert.equal(takeNotifications(state,new Set(),opts).length,1);
 assert.equal(takeNotifications(state,new Set(),{...opts,hidden:true}).length,2);
 for(const patch of [{connected:false},{permission:'denied'},{permission:'default'}])assert.equal(takeNotifications(state,new Set(),{...opts,...patch}).length,0);
 for(const settings of [{desktop:false,needsInput:true},{desktop:true},{}])assert.equal(takeNotifications({...state,notificationSettings:settings},new Set(),opts).length,0);
 assert.equal(notificationBody(notificationEvents(state)[0],{}),'Amplifier needs your input.');
 assert.equal(notificationBody(notificationEvents(state)[0],{preview:true}),'Amplifier needs your input.');
});
test('initial seeding, repeated snapshots and enabling later never replay old notifications',()=>{
 const state={selectedSessionId:'chat',notificationSettings:{desktop:true,needsInput:false},notificationMessages:[],attention:{items:[{id:'question:q',sessionId:'other',fingerprint:'v1'}]}},opts={connected:true,permission:'granted',hidden:true},seen=new Set();
 assert.deepEqual(takeNotifications(state,seen,opts),[]);
 state.notificationSettings.needsInput=true;
 assert.deepEqual(takeNotifications(state,seen,opts),[]);
 state.attention.items[0].fingerprint='v2';
 assert.equal(takeNotifications(state,seen,opts).length,1);
 assert.deepEqual(takeNotifications(state,seen,opts),[]);
 const initial=new Set(notificationEvents(state).map(event=>event.id));
 assert.deepEqual(takeNotifications(state,initial,opts),[]);
 state.notificationMessages=[{id:'ready',sessionId:'other',role:'assistant',via:'text'}];
 assert.equal(takeNotifications(state,initial,opts).length,1);
});
test('mobile uses active service worker, desktop fallback opens chat only, failures never request permission',async()=>{
 const message={id:'event',sessionId:'chat',kind:'needs-input',text:'private'};
 const shown=[],clicked=[];
 await showBrowserNotification(message,{}, {navigator:{serviceWorker:{getRegistration:async()=>({active:true,showNotification:async(...args)=>shown.push(args)})}},icon:'local'});
 assert.equal(shown[0][1].body,'Amplifier needs your input.');
 assert.deepEqual(shown[0][1].data,{sessionId:'chat'});
 assert.equal(shown[0][1].actions,undefined);
 let notice;
 await showBrowserNotification(message,{}, {navigator:{},window:{Notification:class{constructor(...args){this.args=args;notice=this}}},onClick:()=>clicked.push('open')});
 notice.onclick();assert.deepEqual(clicked,['open']);
 await assert.rejects(showBrowserNotification(message,{}, {navigator:{},window:{Notification:class{constructor(){throw Error('mobile unsupported')}}}}),/mobile unsupported/);
});
