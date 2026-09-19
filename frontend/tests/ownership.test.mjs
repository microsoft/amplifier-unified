import test from 'node:test';
import assert from 'node:assert/strict';
import {ownershipState} from '../src/ownership.js';
import {sessionStatus} from '../src/session-status.js';

test('yielded views stay read-only and need deliberate resumption',()=>{
  const session={status:'idle',ownership:{status:'yielded',source:'Amplifier CLI'}};
  assert.equal(ownershipState(session).blocked,true);
  assert.equal(ownershipState(session).canTakeover,true);
  assert.match(sessionStatus(session).label,/Amplifier CLI/);
  assert.equal(sessionStatus(session).busy,false);
});
test('pending or failed shutdown never offers an overlapping takeover',()=>{
  for(const status of ['yielding','yield-failed','taking-over']){
    assert.equal(ownershipState({ownership:{status}}).blocked,true);
    assert.equal(ownershipState({ownership:{status}}).canTakeover,false);
  }
});
test('ordinary idle parking stays available',()=>{
  assert.equal(ownershipState({status:'idle'}).blocked,false);
});
