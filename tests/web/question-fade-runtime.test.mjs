import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import test from 'node:test';
import {questionFadeExecution} from './question-fade-runtime.mjs';
import {readyExecution} from './ready-runtime.mjs';
import {fileURLToPath} from 'node:url';

const root='/workspace/project';
const defaultContract='/opt/unified-prepared/execution-contract.json';
const explicitContract='/prepared/question-contract.json';
const canonicalContract='/prepared/contracts/question-contract.json';
const selectedPython='/selected/python';
const python='/runtime/python3';
const selectedArtifacts='/selected/artifacts';
const artifacts='/evidence/question-fade';
const backgroundArtifacts=root+'/output/browser-checks/question-transcript-fade';
const owner='0123456789abcdef0123456789abcdef';
const ioError=code=>Object.assign(new Error('Synthetic '+code),{code});
const contract=()=>({
 version:1,profileId:'browser-test',workspace:root,python,artifacts,
 source:{revision:'synthetic-source',kind:'snapshot'},unprojected:'not evidence',
});

// All filesystem operations are synchronous in-memory injections. These checks
// start no fixture/browser, touch no real artifact directory and use no packages.
function executionCase(value=contract()) {
 const state={
  env:{AMPLIFIER_TEST_PYTHON:selectedPython,AMPLIFIER_TEST_ARTIFACTS:selectedArtifacts},
  bytes:Buffer.from(typeof value==='string'?value:JSON.stringify(value)),
  paths:new Map([
   [root,root],[defaultContract,canonicalContract],[explicitContract,canonicalContract],
   [selectedPython,python],[python,python],[selectedArtifacts,artifacts],[artifacts,artifacts],
  ]),
  pathErrors:new Map(),readErrors:new Map(),calls:[],directoryError:null,
 };
 const realpath=path=>{
  state.calls.push({operation:'realpath',path});
  if(state.pathErrors.has(path))throw state.pathErrors.get(path);
  if(!state.paths.has(path))throw ioError('ENOENT');
  return state.paths.get(path);
 };
 const read=path=>{
  state.calls.push({operation:'read',path});
  if(state.readErrors.has(path))throw state.readErrors.get(path);
  assert.equal(path,canonicalContract);
  return state.bytes;
 };
 const ensureDirectory=(path,options)=>{
  state.calls.push({operation:'ensureDirectory',path,options});
  if(state.directoryError)throw state.directoryError;
  if(!state.paths.has(path))state.paths.set(path,path);
 };
 state.run=()=>questionFadeExecution({root,env:state.env,read,realpath,ensureDirectory});
 state.directories=()=>state.calls.filter(call=>call.operation==='ensureDirectory');
 return state;
}
function backgroundCase() {
 const state=executionCase();
 state.pathErrors.set(defaultContract,ioError('ENOENT'));
 state.env.AMPLIFIER_BROWSER_CHECK_OWNER=owner;
 state.env.AMPLIFIER_TEST_ARTIFACTS=backgroundArtifacts;
 return state;
}
function managedEvidence(state,value) {
 return {
  executionMode:'managed',
  executionContract:{version:value.version,profileId:value.profileId,source:value.source},
  contractSha256:createHash('sha256').update(state.bytes).digest('hex'),
 };
}

test('default managed contract keeps projected source and the exact contract byte hash',()=>{
 const value=contract(),state=executionCase(value);
 state.bytes=Buffer.from(JSON.stringify(value,null,2)+'\n');
 assert.deepEqual(state.run(),{python:selectedPython,artifactRoot:artifacts,evidence:managedEvidence(state,value)});
 assert.deepEqual(state.calls[0],{operation:'realpath',path:defaultContract});
});

test('explicit managed contract resolves aliases and does not consult the default',()=>{
 const value={...contract(),workspace:'/selected/workspace'};
 const state=executionCase(value);
 state.paths.set(value.workspace,root);
 state.env.AMPLIFIER_TEST_CONTRACT=explicitContract;
 state.env.AMPLIFIER_BROWSER_CHECK_OWNER='not-an-authority';
 state.pathErrors.set(defaultContract,ioError('ENOENT'));
 assert.deepEqual(state.run(),{python:selectedPython,artifactRoot:artifacts,evidence:managedEvidence(state,value)});
 assert.equal(state.calls.some(call=>call.path===defaultContract),false);
});

test('a present default contract remains managed even with a valid runner tag',()=>{
 const state=executionCase();
 state.env.AMPLIFIER_BROWSER_CHECK_OWNER=owner;
 const result=state.run();
 assert.equal(result.evidence.executionMode,'managed');
 assert.equal(Object.hasOwn(result.evidence,'runnerOwnerTag'),false);
});

test('absent default contract and canonical owner classify background evidence only',()=>{
 const state=backgroundCase();
 const result=state.run();
 assert.deepEqual(result,{
  python:selectedPython,artifactRoot:backgroundArtifacts,
  evidence:{executionMode:'background',runnerOwnerTag:owner},
 });
 assert.equal(Object.hasOwn(result.evidence,'executionContract'),false);
 assert.equal(Object.hasOwn(result.evidence,'contractSha256'),false);
 assert.equal(state.calls.some(call=>call.operation==='read'),false);
});

test('only default ENOENT also permits fallback if the contract disappears before read',()=>{
 const state=backgroundCase();
 state.pathErrors.delete(defaultContract);
 state.readErrors.set(canonicalContract,ioError('ENOENT'));
 assert.equal(state.run().evidence.executionMode,'background');
});

for(const [label,tag] of [
 ['absent',undefined],['empty',''],['uppercase',owner.toUpperCase()],
 ['short',owner.slice(1)],['long',owner+'0'],
 ['hyphenated','01234567-89ab-cdef-0123-456789abcdef'],
 ['nonhex','g'+owner.slice(1)],['trailing newline',owner+'\n'],['leading space',' '+owner],
]){
 test('absent default contract rejects '+label+' owner',()=>{
  const state=backgroundCase();
  if(tag===undefined)delete state.env.AMPLIFIER_BROWSER_CHECK_OWNER;
  else state.env.AMPLIFIER_BROWSER_CHECK_OWNER=tag;
  assert.throws(state.run,/canonical browser-check runner owner tag/);
 });
}

for(const mode of ['managed','background']){
 for(const [label,key,value] of [
  ['missing Python','AMPLIFIER_TEST_PYTHON',undefined],
  ['empty Python','AMPLIFIER_TEST_PYTHON',''],
  ['relative Python','AMPLIFIER_TEST_PYTHON','python3'],
  ['missing artifacts','AMPLIFIER_TEST_ARTIFACTS',undefined],
  ['empty artifacts','AMPLIFIER_TEST_ARTIFACTS',''],
  ['relative artifacts','AMPLIFIER_TEST_ARTIFACTS','output/question-fade'],
 ]){
  test(mode+' rejects '+label,()=>{
   const state=mode==='managed'?executionCase():backgroundCase();
   if(value===undefined)delete state.env[key];
   else state.env[key]=value;
   assert.throws(state.run,key==='AMPLIFIER_TEST_PYTHON'
    ?/explicit absolute Python/:/explicit absolute artifact/);
  });
 }
 test(mode+' resolves the selected Python rather than trusting its path text',()=>{
  const state=mode==='managed'?executionCase():backgroundCase();
  const failure=ioError('ENOENT');
  state.pathErrors.set(selectedPython,failure);
  assert.throws(state.run,error=>error===failure);
 });
 test(mode+' preserves the selected virtualenv path after validating its realpath',()=>{
  const state=mode==='managed'?executionCase():backgroundCase();
  const result=state.run();
  assert.equal(result.python,selectedPython);
  assert.notEqual(result.python,python);
  assert.ok(state.calls.some(call=>call.operation==='realpath'&&call.path===selectedPython));
 });
}

for(const code of ['ENOENT','EACCES','EPERM','ENOTDIR','EIO']){
 for(const stage of ['realpath','read']){
  test('explicit contract fails closed on '+stage+' '+code+' despite a valid owner',()=>{
   const state=backgroundCase();
   state.env.AMPLIFIER_TEST_CONTRACT=explicitContract;
   const failure=ioError(code);
   if(stage==='realpath')state.pathErrors.set(explicitContract,failure);
   else state.readErrors.set(canonicalContract,failure);
   assert.throws(state.run,error=>error===failure);
   assert.equal(state.calls.some(call=>call.path===defaultContract),false);
  });
 }
}

test('an explicitly selected default path is still explicit and cannot fall back',()=>{
 const state=backgroundCase();
 state.env.AMPLIFIER_TEST_CONTRACT=defaultContract;
 const failure=state.pathErrors.get(defaultContract);
 assert.throws(state.run,error=>error===failure);
});

for(const value of ['',undefined,null]){
 test('an explicit empty/invalid contract environment value fails closed: '+String(value),()=>{
  const state=backgroundCase();
  state.env.AMPLIFIER_TEST_CONTRACT=value;
  assert.throws(state.run,/explicit executionContract path must not be empty/);
  assert.deepEqual(state.calls,[]);
 });
}

for(const code of ['EACCES','EPERM','ENOTDIR','EIO']){
 for(const stage of ['realpath','read']){
  test('default '+stage+' '+code+' is not absence and cannot fall back',()=>{
   const state=backgroundCase();
   state.pathErrors.delete(defaultContract);
   const failure=ioError(code);
   if(stage==='realpath')state.pathErrors.set(defaultContract,failure);
   else state.readErrors.set(canonicalContract,failure);
   assert.throws(state.run,error=>error===failure);
  });
 }
}

for(const [label,value,expected] of [
 ['empty bytes','',SyntaxError],['malformed JSON','{',SyntaxError],
 ['null',null,/executionContract must be an object/],
 ['array',[],/executionContract must be an object/],
 ['scalar',42,/executionContract must be an object/],
 ['wrong profile',{...contract(),profileId:'linux-python'},/browser-test/],
 ['missing profile',{...contract(),profileId:undefined},/browser-test/],
 ['wrong workspace',{...contract(),workspace:'/workspace/other'},/admitted source/],
 ['missing workspace',{...contract(),workspace:undefined},/absolute workspace/],
 ['relative workspace',{...contract(),workspace:'project'},/absolute workspace/],
 ['wrong Python',{...contract(),python:'/runtime/other-python'},/Python executable supplied/],
 ['missing contract Python',{...contract(),python:undefined},/explicit absolute Python/],
 ['relative contract Python',{...contract(),python:'python3'},/explicit absolute Python/],
 ['wrong artifacts',{...contract(),artifacts:'/evidence/other'},/artifact directory supplied/],
 ['missing contract artifacts',{...contract(),artifacts:undefined},/absolute artifact directory/],
 ['relative contract artifacts',{...contract(),artifacts:'evidence'},/absolute artifact directory/],
]){
 for(const explicit of [false,true]){
  test((explicit?'explicit':'default')+' contract rejects '+label+' without fallback',()=>{
   const state=executionCase(value);
   state.env.AMPLIFIER_BROWSER_CHECK_OWNER=owner;
   if(explicit)state.env.AMPLIFIER_TEST_CONTRACT=explicitContract;
   state.paths.set('/workspace/other','/workspace/other');
   state.paths.set('/runtime/other-python','/runtime/other-python');
   state.paths.set('/evidence/other','/evidence/other');
   assert.throws(state.run,expected);
  });
 }
}

for(const canonicalArtifacts of [root,root+'/output/question-fade']){
 test('managed evidence rejects repository containment after resolving aliases: '+canonicalArtifacts,()=>{
  const state=executionCase();
  state.env.AMPLIFIER_TEST_CONTRACT=explicitContract;
  state.env.AMPLIFIER_BROWSER_CHECK_OWNER=owner;
  state.paths.set(selectedArtifacts,canonicalArtifacts);
  state.paths.set(artifacts,canonicalArtifacts);
  assert.throws(state.run,/outside the repository/);
 });
}

test('a sibling evidence directory is outside the repository, not a string-prefix match',()=>{
 const state=executionCase();
 const sibling=root+'-evidence';
 state.paths.set(selectedArtifacts,sibling);
 state.paths.set(artifacts,sibling);
 assert.equal(state.run().artifactRoot,sibling);
});

test('managed mode requires an already-resolvable artifact directory',()=>{
 const state=executionCase();
 const failure=ioError('ENOENT');
 state.pathErrors.set(selectedArtifacts,failure);
 assert.throws(state.run,error=>error===failure);
});

// Directory mutation checks are separate from contract and classification checks.
test('valid managed execution never creates artifact directories',()=>{
 const state=executionCase();
 state.run();
 assert.deepEqual(state.directories(),[]);
});

test('invalid contracts never create artifact directories even with a canonical owner',()=>{
 for(const value of ['{',{...contract(),profileId:'linux-python'},null]){
  const state=executionCase(value);
  state.env.AMPLIFIER_TEST_CONTRACT=explicitContract;
  state.env.AMPLIFIER_BROWSER_CHECK_OWNER=owner;
  state.env.AMPLIFIER_TEST_ARTIFACTS=backgroundArtifacts;
  assert.throws(state.run);
  assert.deepEqual(state.directories(),[]);
 }
});

test('failed contract reads never create artifact directories',()=>{
 for(const explicit of [false,true]){
  const state=backgroundCase();
  state.pathErrors.set(defaultContract,ioError('EACCES'));
  if(explicit){
   state.env.AMPLIFIER_TEST_CONTRACT=explicitContract;
   state.pathErrors.set(explicitContract,ioError('ENOENT'));
  }
  assert.throws(state.run);
  assert.deepEqual(state.directories(),[]);
 }
});

test('background owner and explicit path preflight failures never create artifacts',()=>{
 for(const [key,value] of [
  ['AMPLIFIER_BROWSER_CHECK_OWNER','bad-owner'],['AMPLIFIER_TEST_PYTHON','python3'],
  ['AMPLIFIER_TEST_ARTIFACTS','output/question-fade'],
 ]){
  const state=backgroundCase();
  state.env[key]=value;
  assert.throws(state.run);
  assert.deepEqual(state.directories(),[]);
 }
 const state=backgroundCase();
 state.pathErrors.set(selectedPython,ioError('ENOENT'));
 assert.throws(state.run);
 assert.deepEqual(state.directories(),[]);
});

test('background artifacts are created recursively before their realpath is resolved',()=>{
 const state=backgroundCase();
 assert.equal(state.paths.has(backgroundArtifacts),false);
 assert.equal(state.run().artifactRoot,backgroundArtifacts);
 assert.deepEqual(state.directories(),[
  {operation:'ensureDirectory',path:backgroundArtifacts,options:{recursive:true}},
 ]);
 const createIndex=state.calls.findIndex(call=>call.operation==='ensureDirectory');
 const resolveIndex=state.calls.findIndex(call=>
  call.operation==='realpath'&&call.path===backgroundArtifacts);
 assert.ok(createIndex<resolveIndex);
});

test('background artifact aliases return their canonical directory after ensureDirectory',()=>{
 const state=backgroundCase();
 state.env.AMPLIFIER_TEST_ARTIFACTS=selectedArtifacts;
 assert.equal(state.run().artifactRoot,artifacts);
 assert.deepEqual(state.directories(),[
  {operation:'ensureDirectory',path:selectedArtifacts,options:{recursive:true}},
 ]);
});

test('background directory creation failure propagates without resolving a nonexistent directory',()=>{
 const state=backgroundCase();
 const failure=ioError('EACCES');
 state.directoryError=failure;
 assert.throws(state.run,error=>error===failure);
 assert.equal(state.calls.some(call=>
  call.operation==='realpath'&&call.path===backgroundArtifacts),false);
});

test('background directory realpath failure after creation still fails the invocation',()=>{
 const state=backgroundCase();
 const failure=ioError('EACCES');
 state.pathErrors.set(backgroundArtifacts,failure);
 assert.throws(state.run,error=>error===failure);
 assert.equal(state.directories().length,1);
});

// Scoped-source is explicitly selected evidence consistency. A mismatched old
// managed contract remains present, unread and unchanged; it never supplies this
// invocation's identity or authority. These injected tests run no real process.
function scopedCase(scenario) {
 const script=scenario==='ready'?'ready-browser.mjs':'question-transcript-fade-browser.mjs';
 const entrypoint=root+'/tests/web/'+script,reportPath='/evidence/runner/results.json';
 const build={version:'0.3.3',id:'current-build'};
 const client={repository:'https://github.com/microsoft/amplifier-client-web',revision:'b'.repeat(40)};
 const component={schema:1,kind:'unified-web-static-artifact',source:client,build};
 const componentBytes=Buffer.from(JSON.stringify(component));
 const state={calls:[],entrypoint,reportPath,component,componentBytes,
  env:{AMPLIFIER_TEST_EXECUTION_MODE:'scoped-source',AMPLIFIER_TEST_RUNNER_REPORT:reportPath,
   AMPLIFIER_BROWSER_CHECK_OWNER:owner,AMPLIFIER_UNIFIED_ROOT:root,
   AMPLIFIER_TEST_PYTHON:selectedPython,AMPLIFIER_TEST_ARTIFACTS:selectedArtifacts},
  paths:new Map([[fileURLToPath(new URL('../../',import.meta.url)),root],
   [root,root],[entrypoint,entrypoint],[selectedPython,python],[python,python],
   [selectedArtifacts,artifacts],[artifacts,artifacts],[reportPath,reportPath],
   ['/other/root','/other/root'],['/other/python','/other/python'],['/other/artifacts','/other/artifacts']]),
 };
 state.row={id:scenario,status:'running',command:['node','tests/web/'+script],env:{...state.env}};
 state.report={schemaVersion:1,complete:false,passed:false,source:{
  root,python:selectedPython,source:'a'.repeat(40),tree:'c'.repeat(40),client,build,
  componentSha256:createHash('sha256').update(componentBytes).digest('hex'),
 },scenarios:[state.row]};
 state.files=new Map([
  [root+'/amplifier_web/static/client-component.json',componentBytes],
  [root+'/amplifier_web/static/build.json',Buffer.from(JSON.stringify(build))],
  [root+'/components/web-client.source.json',Buffer.from(JSON.stringify({source:client}))],
 ]);
 const read=path=>{
  state.calls.push(['read',path]);
  assert.notEqual(path,defaultContract,'Scoped mode must not consult the default contract');
  if(state.readError)throw state.readError;
  if(path===reportPath)return state.reportBytes??Buffer.from(JSON.stringify(state.report));
  if(!state.files.has(path))throw ioError('ENOENT');
  return state.files.get(path);
 };
 const realpath=path=>{
  state.calls.push(['realpath',path]);
  if(!state.paths.has(path))throw ioError('ENOENT');
  return state.paths.get(path);
 };
 state.run=async()=>{
  if(scenario==='ready')return readyExecution({env:state.env,scopedRead:read,realpath,
   entrypoint:state.entrypoint,read:()=>assert.fail('No managed read')});
  const result=state.result=questionFadeExecution({root,env:state.env,read,realpath,entrypoint:state.entrypoint,
   ensureDirectory:()=>assert.fail('Scoped helper creates nothing')});
  return result.evidence;
 };
 return state;
}

for(const scenario of ['ready','question-transcript-fade']){
 test(scenario+' scoped source projects consistent provenance, not a managed snapshot',async()=>{
  const state=scopedCase(scenario);
  state.report.source.unprojected='must not be copied';
  const bytes=Buffer.from(JSON.stringify(state.report,null,2)+'\n');
  state.reportBytes=bytes;
  const result=await state.run();
  if(scenario==='question-transcript-fade'){
   assert.equal(state.result.python,selectedPython);
   assert.equal(state.result.artifactRoot,artifacts);
  }
  assert.equal(result.executionMode,'scoped-source');
  assert.equal(result.managedSnapshot,false);
  assert.equal(result.runnerOwnerTag,owner);
  assert.equal(Object.hasOwn(result,'executionContract'),false);
  assert.equal(Object.hasOwn(result,'contractSha256'),false);
  assert.deepEqual(result.scopedSource,{
   identityKind:'recorded-provenance',root,selectedPython,artifactRoot:artifacts,scenario,
   source:'a'.repeat(40),tree:'c'.repeat(40),client:state.component.source,build:state.component.build,
   componentSha256:state.report.source.componentSha256,
   buildSha256:createHash('sha256').update(state.files.get(root+'/amplifier_web/static/build.json')).digest('hex'),
   runnerReport:state.reportPath,runnerReportSha256:createHash('sha256').update(bytes).digest('hex'),
   runnerReportPhase:'running-at-read',
  });
  state.report.complete=true;state.row.status='passed';
  assert.notEqual(result.scopedSource.runnerReportSha256,
   createHash('sha256').update(JSON.stringify(state.report)).digest('hex'));
  assert.equal(state.calls.some(([,path])=>path===defaultContract),false);
 });
 for(const change of [
  'unknown-mode','empty-mode','contract','empty-contract','owner','owner-newline',
  'root-env','root-report','entrypoint','python-report','python-row','same-base-alias',
  'artifacts-row','artifacts-inside-root','missing-python','missing-artifacts',
  'relative-report','missing-report','malformed-report','read-denied','schema',
  'complete','report-passed','missing-row','duplicate-row','other-running','stale-row',
  'wrong-command','wrong-cwd','row-mode','row-report','missing-source','bad-source','bad-tree',
  'component-bytes','client','selected-client','build','build-file',
 ]){
  test(scenario+' scoped source rejects '+change+' without managed fallback',async()=>{
   const state=scopedCase(scenario),s=state.report.source;
   if(change==='unknown-mode')state.env.AMPLIFIER_TEST_EXECUTION_MODE='managed';
   else if(change==='empty-mode')state.env.AMPLIFIER_TEST_EXECUTION_MODE='';
   else if(change==='contract')state.env.AMPLIFIER_TEST_CONTRACT=explicitContract;
   else if(change==='empty-contract')state.env.AMPLIFIER_TEST_CONTRACT='';
   else if(change==='owner')state.env.AMPLIFIER_BROWSER_CHECK_OWNER='bad';
   else if(change==='owner-newline')state.env.AMPLIFIER_BROWSER_CHECK_OWNER=owner+'\n';
   else if(change==='root-env')state.env.AMPLIFIER_UNIFIED_ROOT='/other/root';
   else if(change==='root-report')s.root='/other/root';
   else if(change==='entrypoint')state.entrypoint='/other/root';
   else if(change==='python-report')s.python='/other/python';
   else if(change==='python-row')state.row.env.AMPLIFIER_TEST_PYTHON='/other/python';
   else if(change==='same-base-alias')s.python=python; // Same resolved binary, different venv selection.
   else if(change==='artifacts-row')state.row.env.AMPLIFIER_TEST_ARTIFACTS='/other/artifacts';
   else if(change==='artifacts-inside-root')state.paths.set(selectedArtifacts,root);
   else if(change==='missing-python')state.paths.delete(selectedPython);
   else if(change==='missing-artifacts')state.paths.delete(selectedArtifacts);
   else if(change==='relative-report')state.env.AMPLIFIER_TEST_RUNNER_REPORT='results.json';
   else if(change==='missing-report')state.paths.delete(state.reportPath);
   else if(change==='malformed-report')state.reportBytes=Buffer.from('{');
   else if(change==='read-denied')state.readError=ioError('EACCES');
   else if(change==='schema')state.report.schemaVersion=2;
   else if(change==='complete')state.report.complete=true;
   else if(change==='report-passed')state.report.passed=true;
   else if(change==='missing-row')state.report.scenarios=[];
   else if(change==='duplicate-row')state.report.scenarios.push({...state.row});
   else if(change==='other-running')state.report.scenarios.push({id:'other',status:'running'});
   else if(change==='stale-row')state.row.status='passed';
   else if(change==='wrong-command')state.row.command.push('--unselected');
   else if(change==='wrong-cwd')state.row.cwd='/other/root';
   else if(change==='row-mode')delete state.row.env.AMPLIFIER_TEST_EXECUTION_MODE;
   else if(change==='row-report')state.row.env.AMPLIFIER_TEST_RUNNER_REPORT='/other/root';
   else if(change==='missing-source')delete state.report.source;
   else if(change==='bad-source')s.source='main';
   else if(change==='bad-tree')s.tree='c'.repeat(40)+'\n';
   else if(change==='component-bytes')state.files.set(root+'/amplifier_web/static/client-component.json',Buffer.from('{}'));
   else if(change==='client')s.client={...s.client,revision:'d'.repeat(40)};
   else if(change==='selected-client')state.files.set(root+'/components/web-client.source.json',
    Buffer.from(JSON.stringify({source:{...s.client,revision:'d'.repeat(40)}})));
   else if(change==='build')s.build={...s.build,id:'different'};
   else if(change==='build-file')state.files.set(root+'/amplifier_web/static/build.json',
    Buffer.from(JSON.stringify({...s.build,id:'different'})));
   const expected={
    'unknown-mode':/Unknown explicit test execution mode/,
    'empty-mode':/Unknown explicit test execution mode/,
    contract:/conflicts with scoped-source/, 'empty-contract':/conflicts with scoped-source/,
    owner:/exact runner cleanup tag/, 'owner-newline':/exact runner cleanup tag/,
    'root-env':/Selected root mismatch/, 'root-report':/Runner source root mismatch/,
    entrypoint:/expected scenario entrypoint/, 'python-report':/Runner Python selection mismatch/,
    'python-row':/Scenario Python selection mismatch/, 'same-base-alias':/Runner Python selection mismatch/,
    'artifacts-row':/Scenario artifact mismatch/, 'artifacts-inside-root':/outside the repository/,
    'missing-python':{code:'ENOENT'}, 'missing-artifacts':{code:'ENOENT'},
    'relative-report':/explicit absolute canonical runner report/, 'missing-report':{code:'ENOENT'},
    'malformed-report':SyntaxError, 'read-denied':{code:'EACCES'},
    schema:{code:'ERR_ASSERTION',actual:2,expected:1},
    complete:{code:'ERR_ASSERTION',actual:true,expected:false},
    'report-passed':{code:'ERR_ASSERTION',actual:true,expected:false},
    'missing-row':/one matching scenario/, 'duplicate-row':/one matching scenario/,
    'other-running':/one running scenario/, 'stale-row':/running scenario at report read time/,
    'wrong-command':/Unexpected scenario command/, 'wrong-cwd':/Unexpected scenario working directory/,
    'row-mode':{code:'ERR_ASSERTION',actual:undefined,expected:'scoped-source'},
    'row-report':{code:'ERR_ASSERTION',actual:'/other/root',expected:state.reportPath},
    'missing-source':/Missing runner source provenance/,
    'bad-source':{code:'ERR_ASSERTION',actual:4,expected:40},
    'bad-tree':{code:'ERR_ASSERTION',actual:41,expected:40},
    'component-bytes':/Current component receipt differs/, client:/Runner client provenance differs/,
    'selected-client':/Selected client differs/, build:/Runner build provenance differs/,
    'build-file':/Current build identity differs/,
   }[change];
   assert.ok(expected,'Every negative must name its intended refusal.');
   await assert.rejects(state.run,expected);
   if(['unknown-mode','empty-mode','contract','empty-contract'].includes(change))
    assert.deepEqual(state.calls,[]);
   assert.equal(state.calls.some(([,path])=>path===defaultContract),false);
  });
 }
}