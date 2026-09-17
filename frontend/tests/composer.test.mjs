import test from 'node:test';
import assert from 'node:assert/strict';
import {resizeComposer} from '../src/composer.js';
test('composer starts compact, expands with content, and scrolls only at its bound',()=>{
 const element={style:{},scrollHeight:28};resizeComposer(element);assert.equal(element.style.height,'44px');assert.equal(element.style.overflowY,'hidden');
 element.scrollHeight=112;resizeComposer(element);assert.equal(element.style.height,'112px');
 element.scrollHeight=260;resizeComposer(element);assert.equal(element.style.height,'180px');assert.equal(element.style.overflowY,'auto');
 element.scrollHeight=24;resizeComposer(element);assert.equal(element.style.height,'44px');assert.equal(element.style.overflowY,'hidden');
});
