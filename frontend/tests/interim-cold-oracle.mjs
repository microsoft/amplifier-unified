// Test-only browser oracle. Never derives expected bodies from the detail API.
import assert from 'node:assert/strict';

export const coldBody=index=>`COLD-BODY-${index}-START\n${`row-${index}:漢字é𝄞\n`.repeat(5000)}COLD-BODY-${index}-END`;
export const coldOrder=()=>[1,2].flatMap(index=>[
 {kind:'message',text:`Cold progress ${index}`},
 {kind:'tool',callId:`cold-call-${index}`,path:`cold-${index}.txt`},
 {kind:'llm',provider:`cold-provider-${index}`,model:`cold-model-${index}`},
]);
export function assertColdOrder(actual){
 assert.deepEqual(actual,coldOrder(),'each original tool/model pair belongs at its own progress gap');
}
export function assertColdBody(actual,index,surface){
 const expected=coldBody(index);
 assert.ok(expected.length>16384,'fixture must cross the full-field page boundary');
 assert.equal(actual===expected,true,
  `${surface}: complete call ${index} body mismatch (${actual?.length??'missing'} vs ${expected.length} characters)`);
}