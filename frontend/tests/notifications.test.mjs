import test from 'node:test';
import assert from 'node:assert/strict';
import {notificationBody,desktopNotificationsEnabled} from '../src/notifications.js';
test('desktop notifications respect opt-out and never include response text without preview opt-in',()=>{
 const message={text:'Private work details'};
 assert.equal(desktopNotificationsEnabled({desktop:false}),false);
 assert.equal(desktopNotificationsEnabled({desktop:true}),true);
 assert.equal(notificationBody(message,{}),'Your Amplifier response is ready.');
 assert.equal(notificationBody(message,{preview:false}),'Your Amplifier response is ready.');
 assert.equal(notificationBody(message,{preview:true}),'Private work details');
});
