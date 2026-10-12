import test from 'node:test';
import assert from 'node:assert/strict';
import {chatDeepLink} from '../src/chat-deep-link.js';
test('only a bounded explicit chat target is accepted',()=>{
 assert.equal(chatDeepLink('?chat=session-123'),'session-123');
 assert.equal(chatDeepLink('?chat=%2Fodd%20id'),'/odd id');
 assert.equal(chatDeepLink('?action=conversation.send&text=hello'),null);
 assert.equal(chatDeepLink('?chat='),null);
 assert.equal(chatDeepLink('?chat=%00'),null);
 assert.equal(chatDeepLink('?chat='+'a'.repeat(201)),null);
});
