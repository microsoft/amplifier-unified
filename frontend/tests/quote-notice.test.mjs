import test from 'node:test';
import assert from 'node:assert/strict';
import {quoteNotice,quoteNoticeAfterSend,visibleQuoteNotice} from '../src/quote-notice.js';
test('quote feedback is visible only in its originating chat',()=>{
 for(const text of ['Adding quote…','Quote added. Earlier quotes, commentary and attachments kept.','Choose a passage of at most 12,000 characters. Nothing was shortened.']){
  const notice=quoteNotice('a',text);
  assert.equal(visibleQuoteNotice(notice,'a'),text);
  assert.equal(visibleQuoteNotice(notice,'b'),'');
  assert.equal(visibleQuoteNotice(notice,undefined),'');
 }
});
test('a late refusal after navigation waits in its originating chat',()=>{
 const notice=quoteNotice('a','The conversation changed. The quote was not added.');
 assert.equal(visibleQuoteNotice(notice,'b'),'','the newly selected chat shows nothing');
 assert.equal(visibleQuoteNotice(notice,'a'),'The conversation changed. The quote was not added.','returning shows why the quote is missing');
});
test('sending ends the feedback of the chat it was sent from',()=>{
 const notice=quoteNotice('a','Quote added. Earlier quotes, commentary and attachments kept.');
 assert.equal(quoteNoticeAfterSend(notice,'a'),null);
 assert.equal(quoteNoticeAfterSend(notice,'b'),notice,'a send elsewhere leaves it in place');
 assert.equal(quoteNoticeAfterSend(null,'a'),null);
});
test('no notice, or one without an originating chat, shows nothing',()=>{
 assert.equal(visibleQuoteNotice(null,'a'),'');
 assert.equal(visibleQuoteNotice(quoteNotice(undefined,'Quote added.'),undefined),'');
});
