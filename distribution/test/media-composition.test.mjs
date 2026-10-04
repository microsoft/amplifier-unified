import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';

test('composition forwards the explicit installed media mode and preserves legacy default',()=>{
  // Substitute only the owner factory, importing the real composition module.
  // No worker, listener, credential lookup or application state is created.
  const script = [
    "import {mock} from 'node:test';",
    "import assert from 'node:assert/strict';",
    "mock.module('@amplifier/unified-media-capability',{namedExports:{createMediaCapability:options=>options}});",
    "const {composeMedia}=await import("+JSON.stringify(new URL('../src/media.js',import.meta.url).href)+");",
    "const context={directory:'/fixture/state'};",
    "const installed=await composeMedia({python:'/fixture/runtime/bin/python',pythonMode:'installed'},context);",
    "assert.equal(installed.python,'/fixture/runtime/bin/python');",
    "assert.equal(installed.pythonMode,'installed');",
    "assert.equal(installed.directory,'/fixture/state/media');",
    "const legacy=await composeMedia({python:'/fixture/original/python'},context);",
    "assert.equal(legacy.pythonMode,undefined);",
    "assert.equal(legacy.python,'/fixture/original/python');",
  ].join('\n');
  const result=spawnSync(process.execPath,['--experimental-test-module-mocks','--input-type=module','-e',script],
    {encoding:'utf8',cwd:new URL('..',import.meta.url)});
  assert.equal(result.status,0,result.stderr);
});
