import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdirSync,readFileSync,realpathSync} from 'node:fs';
import {isAbsolute,join,relative,sep} from 'node:path';
import {fileURLToPath} from 'node:url';

const defaultContractPath='/opt/unified-prepared/execution-contract.json';
const within=(parent,path)=>{
 const rel=relative(parent,path);
 return rel===''||(!isAbsolute(rel)&&rel!=='..'&&!rel.startsWith('..'+sep));
};
const absolutePath=(value,message)=>{
 assert.ok(typeof value==='string'&&value.length>0&&isAbsolute(value),message);
 return value;
};

export function scopedSourceRequested(env) {
 if(!Object.hasOwn(env,'AMPLIFIER_TEST_EXECUTION_MODE'))return false;
 assert.equal(env.AMPLIFIER_TEST_EXECUTION_MODE,'scoped-source','Unknown explicit test execution mode.');
 assert.ok(!Object.hasOwn(env,'AMPLIFIER_TEST_CONTRACT'),
  'An explicit execution contract conflicts with scoped-source mode.');
 return true;
}

// A projection of the existing runner's declared provenance, not an execution
// permit, live-owner proof, Git attestation, or replacement managed contract.
export function scopedSourceExecution({
 root=fileURLToPath(new URL('../../',import.meta.url)),scenario,env=process.env,
 read=readFileSync,realpath=realpathSync,entrypoint=process.argv[1],
}={}) {
 assert.ok(scopedSourceRequested(env),'Explicit scoped-source mode is required.');
 const scripts={ready:'tests/web/ready-browser.mjs',
  'question-transcript-fade':'tests/web/question-transcript-fade-browser.mjs'};
 assert.ok(Object.hasOwn(scripts,scenario),'Unknown scoped-source scenario.');
 assert.equal(env.AMPLIFIER_BROWSER_CHECK_OWNER?.length,32,'Expected an exact runner cleanup tag.');
 assert.match(env.AMPLIFIER_BROWSER_CHECK_OWNER||'',/^[a-f0-9]{32}$/,
  'Scoped-source requires the canonical runner cleanup owner tag.');
 const actualRoot=realpath(absolutePath(root,'An absolute selected root is required.'));
 assert.equal(actualRoot,realpath(fileURLToPath(new URL('../../',import.meta.url))),
  'Use this helper’s actual source root.');
 assert.equal(actualRoot,realpath(absolutePath(env.AMPLIFIER_UNIFIED_ROOT,
  'AMPLIFIER_UNIFIED_ROOT must name the selected root.')),'Selected root mismatch.');
 assert.equal(realpath(absolutePath(entrypoint,'An absolute actual entrypoint is required.')),
  realpath(join(actualRoot,scripts[scenario])),'Run the expected scenario entrypoint.');
 const python=absolutePath(env.AMPLIFIER_TEST_PYTHON,'Use an explicit absolute Python executable.');
 realpath(python); // Validate existence, but retain the selected virtualenv path.
 const artifactRoot=realpath(absolutePath(env.AMPLIFIER_TEST_ARTIFACTS,
  'Use an explicit absolute artifact directory.'));
 assert.ok(!within(actualRoot,artifactRoot),'Scoped evidence must stay outside the repository.');
 const reportPath=realpath(absolutePath(env.AMPLIFIER_TEST_RUNNER_REPORT,
  'Use an explicit absolute canonical runner report.'));
 const bytes=read(reportPath),report=JSON.parse(bytes);
 assert.equal(report.schemaVersion,1);assert.equal(report.complete,false);
 assert.equal(report.passed,false);
 assert.ok(Array.isArray(report.scenarios),'Runner report must list scenarios.');
 const matches=report.scenarios.filter(row=>row.id===scenario);
 assert.equal(matches.length,1,'Expected one matching scenario.');
 const [row]=matches;
 assert.equal(row.status,'running','Expected a running scenario at report read time.');
 assert.equal(report.scenarios.filter(item=>item.status==='running').length,1,
  'Expected one running scenario.');
 assert.deepEqual(row.command,['node',scripts[scenario]],'Unexpected scenario command.');
 assert.ok(row.cwd===undefined||row.cwd==='.', 'Unexpected scenario working directory.');
 assert.equal(row.env?.AMPLIFIER_TEST_EXECUTION_MODE,'scoped-source');
 assert.equal(realpath(absolutePath(row.env?.AMPLIFIER_TEST_RUNNER_REPORT,
  'Scenario must name its runner report.')),reportPath);
 assert.equal(row.env?.AMPLIFIER_TEST_PYTHON,python,'Scenario Python selection mismatch.');
 assert.equal(realpath(absolutePath(row.env?.AMPLIFIER_TEST_ARTIFACTS,
  'Scenario must name its artifact directory.')),artifactRoot,'Scenario artifact mismatch.');
 const source=report.source;
 assert.ok(source&&typeof source==='object'&&!Array.isArray(source),'Missing runner source provenance.');
 assert.equal(realpath(absolutePath(source.root,'Runner provenance must name its source root.')),
  actualRoot,'Runner source root mismatch.');
 assert.equal(source.python,python,'Runner Python selection mismatch.');
 assert.equal(source.source?.length,40);assert.equal(source.tree?.length,40);
 assert.match(source.source||'',/^[a-f0-9]{40}$/,'Expected recorded source revision.');
 assert.match(source.tree||'',/^[a-f0-9]{40}$/,'Expected recorded source tree.');
 const componentBytes=read(join(actualRoot,'amplifier_web/static/client-component.json'));
 const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
 assert.equal(sha(componentBytes),source.componentSha256,'Current component receipt differs.');
 const component=JSON.parse(componentBytes);
 const buildBytes=read(join(actualRoot,'amplifier_web/static/build.json'));
 const build=JSON.parse(buildBytes);
 const selection=JSON.parse(read(join(actualRoot,'components/web-client.source.json')));
 assert.equal(component.kind,'unified-web-static-artifact');assert.equal(component.schema,1);
 assert.deepEqual(component.source,source.client,'Runner client provenance differs.');
 assert.deepEqual(component.source,selection.source,'Selected client differs.');
 assert.deepEqual(component.build,source.build,'Runner build provenance differs.');
 assert.deepEqual(build,source.build,'Current build identity differs.');
 assert.equal(source.client?.revision?.length,40);
 assert.match(source.client?.revision||'',/^[a-f0-9]{40}$/);
 assert.ok(typeof component.source.repository==='string'&&component.source.repository.length<=512);
 assert.ok(typeof build.id==='string'&&build.id.length>0&&build.id.length<=128);
 assert.ok(typeof build.version==='string'&&build.version.length>0&&build.version.length<=128);
 return {python,artifactRoot,evidence:{
  executionMode:'scoped-source',managedSnapshot:false,runnerOwnerTag:env.AMPLIFIER_BROWSER_CHECK_OWNER,
  scopedSource:{identityKind:'recorded-provenance',root:actualRoot,selectedPython:python,artifactRoot,
   scenario,source:source.source,tree:source.tree,client:component.source,build,
   componentSha256:source.componentSha256,buildSha256:sha(buildBytes),
   runnerReport:reportPath,runnerReportSha256:sha(bytes),runnerReportPhase:'running-at-read'},
 }};
}

export function questionFadeExecution({
 root,env=process.env,read=readFileSync,realpath=realpathSync,ensureDirectory=mkdirSync,
 entrypoint=process.argv[1],
}={}) {
 if(scopedSourceRequested(env))return scopedSourceExecution({
  root,scenario:'question-transcript-fade',env,read,realpath,entrypoint});
 absolutePath(root,'An absolute admitted repository root is required.');
 const explicitContract=Object.hasOwn(env,'AMPLIFIER_TEST_CONTRACT');
 const contractPath=explicitContract?env.AMPLIFIER_TEST_CONTRACT:defaultContractPath;
 assert.ok(typeof contractPath==='string'&&contractPath.length>0,
  'An explicit executionContract path must not be empty.');
 let contractBytes,background=false;
 try{
  contractBytes=read(realpath(contractPath));
 }catch(error){
  if(explicitContract||error.code!=='ENOENT')throw error;
  // The canonical runner uses uuid.uuid4().hex for cleanup ownership. Its tag
  // classifies this invocation; it grants no managed execution authority.
  assert.ok(typeof env.AMPLIFIER_BROWSER_CHECK_OWNER==='string'
   &&/^[0-9a-f]{32}$/.test(env.AMPLIFIER_BROWSER_CHECK_OWNER),
  'An absent default contract requires the canonical browser-check runner owner tag.');
  background=true;
 }
 const python=absolutePath(env.AMPLIFIER_TEST_PYTHON,
  'Use an explicit absolute Python executable.');
 const artifactEnv=absolutePath(env.AMPLIFIER_TEST_ARTIFACTS,
  'Use an explicit absolute artifact directory.');
 // Validate identity without replacing the selected path: launching the resolved
 // base interpreter instead of a virtualenv's symlink would lose its environment.
 const resolvedPython=realpath(python);
 if(background){
  ensureDirectory(artifactEnv,{recursive:true});
  return {
   python,artifactRoot:realpath(artifactEnv),
   evidence:{executionMode:'background',runnerOwnerTag:env.AMPLIFIER_BROWSER_CHECK_OWNER},
  };
 }
 const contract=JSON.parse(contractBytes);
 assert.ok(contract&&typeof contract==='object'&&!Array.isArray(contract),
  'The executionContract must be an object.');
 assert.equal(contract.profileId,'browser-test');
 assert.equal(realpath(absolutePath(contract.workspace,
  'Use the contract’s absolute workspace.')),root,'Run the admitted source, not another checkout.');
 assert.equal(resolvedPython,realpath(absolutePath(contract.python,
  'Use the contract’s explicit absolute Python executable.')),
 'Use the Python executable supplied by the executionContract.');
 const artifactRoot=realpath(artifactEnv);
 assert.equal(artifactRoot,realpath(absolutePath(contract.artifacts,
  'Use the contract’s absolute artifact directory.')),
 'Use the artifact directory supplied by the executionContract.');
 assert.ok(!within(root,artifactRoot),'Retained evidence must stay outside the repository.');
 return {
  python,artifactRoot,
  evidence:{
   executionMode:'managed',
   executionContract:{version:contract.version,profileId:contract.profileId,source:contract.source},
   contractSha256:createHash('sha256').update(contractBytes).digest('hex'),
  },
 };
}