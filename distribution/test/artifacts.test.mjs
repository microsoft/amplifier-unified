import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

test('artifact qualifier preserves exact runtime exports and bounds the two protocol data patterns',()=>{
 const result=spawnSync('python3',['-I','-B',fileURLToPath(new URL('./verify_artifacts_test.py',import.meta.url))],{encoding:'utf8',timeout:30000});
 assert.equal(result.status,0,result.stdout+result.stderr);
});
