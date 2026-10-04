import test from 'node:test';
import assert from 'node:assert/strict';
import {chmod, copyFile, mkdir, mkdtemp, readFile, rm, symlink, writeFile} from 'node:fs/promises';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {OWNERS, inspectConfig, requireLaunchConfig, assertOwnerCensus} from '../src/validate-config.mjs';

const exec = promisify(execFile);

// Deliberately unresolved operator input: validation must never turn staging
// into permission to create owners, trust, listeners or service authority.
const staged = () => ({
  schema: 'unified-full-owner-composition-v1',
  expectedOwners: [...OWNERS],
  application: {
    ...Object.fromEntries(['workspaces', 'nativeAdmin', 'maintenance',
      'applicationUpdates', 'media', 'mcp', 'notifications', 'diagnostics',
      'operations', 'coordination', 'worktrees', 'publishing', 'recall',
      'feedback', 'portability', 'recovery', 'historyImport', 'historyCleanup',
      'managedFiles'].map(key => [key, {}])),
    recovery: {authorization: 'local-account', credentials: false},
    conversationPresentation: {},
    catalogProcess: {args: ['--scan-interval', '0', '--workspace-check-interval', '0']},
    gateway: {host: '127.0.0.1'},
    nativeAdmin: {engine: 'amplifier'},
    portability: {engines: ['amplifier']},
    manualIngress: {stateDirectory: '/fixture/ingress'},
  },
  release: {entrypoint: 'src/full-owner-launcher.mjs', prepared: 'UNRESOLVED_PREPARED'},
  authority: {
    installationId: 'fixture', ownerId: 'fixture-owner', dataScope: 'fixture-scope',
    sourceDirectory: '/fixture/source', claimDirectory: '/fixture/claim',
    supervisorDirectory: '/fixture/supervisor',
    supervisorDiscoveryFile: '/fixture/supervisor-discovery.json',
    supervisorTokenFile: '/fixture/supervisor-token',
    hostDiscoveryFile: '/fixture/host-discovery.json',
    hostTokenFile: '/fixture/host-token',
  },
});

test('structurally valid staging never confers launch authority', () => {
  const config = staged(), result = inspectConfig(config);
  assert.equal(result.valid, true);
  assert.equal(result.launchable, false);
  assert.deepEqual(result.unresolved, ['UNRESOLVED_PREPARED']);
  assert.throws(() => requireLaunchConfig(config), /not_qualified/);
  // Removing placeholders alone still cannot substitute for reviewed evidence.
  config.release.prepared = {identity: {digest: 'a'.repeat(64)}};
  assert.throws(() => requireLaunchConfig(config), /not_qualified/);
});

test('omitted or duplicate owners refuse the full service census', () => {
  for (const change of [c => delete c.application.operations,
    c => c.expectedOwners.push(c.expectedOwners[0])]) {
    const config = staged(); change(config);
    assert.equal(inspectConfig(config).valid, false);
  }
});

test('effective census is exact and does not broaden the immutable base configuration',()=>{
  const effective=[...OWNERS,'native-message-metadata'];
  assertOwnerCensus([...effective].reverse(),effective);
  for(const actual of [OWNERS,[...effective,'unreviewed'],[...effective,effective[0]],null])
    assert.throws(()=>assertOwnerCensus(actual,effective),/configured_owner_census_mismatch/);
  assert.throws(()=>assertOwnerCensus([...effective,effective[0]],[...effective,effective[0]]),/configured_owner_census_mismatch/);
  const config=staged();config.expectedOwners=effective;
  assert.ok(inspectConfig(config).issues.includes('owner-census'));
});

test('competing catalog writers refuse before launch', () => {
  for (const change of [c => c.application.catalogProcess.args.push('--scan-on-start'),
    c => { c.application.catalogProcess.maintenanceMs = 1000; },
    c => c.application.catalogProcess.args.push('--hint-directory', '/fixture/hints'),
    c => { c.application.catalogProcess.args[1] = '300'; }]) {
    const config = staged(); change(config);
    assert.equal(inspectConfig(config).valid, false);
  }
});

test('overlapping authority roots and oversized source socket refuse', () => {
  const config = staged();
  config.authority.sourceDirectory = '/' + 'a'.repeat(100);
  assert.ok(inspectConfig(config).issues.includes('source-socket-path-length'));
  config.authority.sourceDirectory = '/fixture/source';
  config.authority.claimDirectory = '/fixture/source/claim';
  assert.ok(inspectConfig(config).issues.includes('nonoverlapping-authority-paths'));
});

test('presentation cannot silently gain credential export authority', () => {
  const config = staged(); config.application.recovery.credentials = true;
  assert.ok(inspectConfig(config).issues.includes('presentation-account-policy'));
});

async function launcherFixture(t, {runtimeIdentity, source = true} = {}) {
  const directory = await mkdtemp(join(tmpdir(), 'au-launch-'));
  t.after(() => rm(directory, {recursive: true, force: true}));
  const src = join(directory, 'src'); await mkdir(src);
  for (const file of ['full-owner-launcher.mjs', 'validate-config.mjs', 'preview-access.mjs', 'release-runtime.mjs'])
    await copyFile(new URL('../src/' + file, import.meta.url), join(src, file));
  for (const [name, body] of [
    ['unified-distribution-update-owner', runtimeIdentity ? `import {writeFileSync} from 'node:fs';
export async function createRuntimeIdentity() {return {identity:${JSON.stringify(runtimeIdentity)}};}
export function serviceIdentity() {writeFileSync(process.env.FIXTURE_AUTHORITY_MARKER,'owner-boundary');throw Error('fixture_authority_boundary');}
` : `import {writeFile} from 'node:fs/promises';
export async function createRuntimeIdentity() {
 await writeFile(process.env.FIXTURE_AUTHORITY_MARKER, 'entered', {flag:'wx', mode:0o600});
 throw Error('fixture_authority_boundary');
}`],
    ['unified', `export function createDistribution() { throw Error('unexpected_distribution_creation'); }`],
  ]) {
    const pkg = join(directory, 'node_modules/@amplifier', name); await mkdir(pkg, {recursive: true});
    await writeFile(join(pkg, 'package.json'), JSON.stringify({type: 'module', exports: './index.js'}));
    await writeFile(join(pkg, 'index.js'), body);
  }
  const config = staged();
  for (const key of ['sourceDirectory', 'claimDirectory', 'supervisorDirectory', 'supervisorDiscoveryFile',
    'supervisorTokenFile', 'hostDiscoveryFile', 'hostTokenFile']) config.authority[key] = join(directory, key);
  config.application.manualIngress.stateDirectory = join(directory, 'ingress');
  config.review = {status: 'approved', combinedLinuxReceiptSha256: 'a'.repeat(64),
    catalogWriterConcurrency: 'qualified', nativeModeProjection: 'qualified', operationsPortabilityResolver: 'qualified'};
  config.release.prepared = {identity: {digest: 'a'.repeat(64)}};
  config.release.trustedKeysFile = join(directory, 'trust.json');
  config.access = {keyFile: join(directory, 'key.pem'), certFile: join(directory, 'cert.pem'), codeFile: join(directory, 'access-code')};
  await exec('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
    '-subj', '/CN=localhost', '-keyout', config.access.keyFile, '-out', config.access.certFile]);
  await chmod(config.access.keyFile, 0o600); await chmod(config.access.certFile, 0o644);
  await writeFile(config.release.trustedKeysFile, JSON.stringify({fixture: 'public-fixture-key'}), {mode: 0o600});
  await writeFile(config.authority.hostTokenFile, 'b'.repeat(64), {mode: 0o600});
  await writeFile(config.access.codeFile, 'fixture-access-code-'.repeat(4), {mode: 0o600});
  const configFile = join(directory, 'config.json'), marker = join(directory, 'authority-entered');
  const run = async (extraEnv={}) => {
    await rm(marker, {force: true}); await writeFile(configFile, JSON.stringify(config), {mode: 0o600});
    try {
      await exec(process.execPath, [join(src, 'full-owner-launcher.mjs'), configFile], {env: {
        ...(source ? {UNIFIED_MANUAL_SOURCE: '1'} : {}), FIXTURE_AUTHORITY_MARKER: marker,
        AMPLIFIER_DISTRIBUTION_INSTALLATION_ID: config.authority.installationId,
        AMPLIFIER_DISTRIBUTION_OWNER_ID: config.authority.ownerId,
        AMPLIFIER_DISTRIBUTION_DATA_SCOPE: config.authority.dataScope, ...extraEnv,
      }, timeout: 10000});
      assert.fail('fixture must stop before real authority');
    } catch (error) {
      return {stderr: error.stderr ?? '', entered: await readFile(marker, 'utf8').then(() => true, () => false)};
    }
  };
  return {config, run, directory};
}

test('every required secret is private before any runtime authority is entered', async t => {
  const {config, run} = await launcherFixture(t);
  for (const path of [config.release.trustedKeysFile, config.access.keyFile,
    config.authority.hostTokenFile, config.access.codeFile]) {
    await chmod(path, 0o644);
    const result = await run();
    assert.equal(result.entered, false);
    assert.match(result.stderr, /private_launch_file_required/);
    await chmod(path, 0o600);
  }
});

test('owned public certificate 0644 passes preflight but writable certificate does not', async t => {
  const {config, run} = await launcherFixture(t);
  const accepted = await run();
  assert.equal(accepted.entered, true);
  assert.match(accepted.stderr, /fixture_authority_boundary/);
  await chmod(config.access.certFile, 0o664);
  const refused = await run();
  assert.equal(refused.entered, false);
  assert.match(refused.stderr, /public_certificate_file_required/);
});

test('invalid TLS, linked inputs, oversized code and malformed credentials fail before authority', async t => {
  const {config, run} = await launcherFixture(t);
  for (const key of ['keyFile', 'certFile']) {
    const path = config.access[key], original = await readFile(path);
    await writeFile(path, 'invalid-fixture-material');
    assert.equal((await run()).entered, false);
    await writeFile(path, original);
    config.access[key] = path + '.link'; await symlink(path, config.access[key]);
    assert.equal((await run()).entered, false);
    config.access[key] = path;
  }
  for (const [path, value] of [[config.access.codeFile, 'x'.repeat(1025)],
    [config.access.codeFile, 'too-short'], [config.authority.hostTokenFile, 'invalid-token']]) {
    const original = await readFile(path); await writeFile(path, value);
    assert.equal((await run()).entered, false);
    await writeFile(path, original);
  }
});

test('actual successor launcher refuses missing or wrong binding before owner boundary', async t => {
  const runtimeIdentity = {id:'successor',version:'2.0.0',revision:'b'.repeat(40),digest:'b'.repeat(64)};
  const {run,directory} = await launcherFixture(t,{runtimeIdentity,source:false});
  let result=await run();
  assert.equal(result.entered,false);
  assert.match(result.stderr,/release_runtime_binding_required/);
  await writeFile(join(directory,'release-runtime.json'),JSON.stringify({
    schema:'unified-release-runtime-v1',release:{id:'wrong',version:'2.0.0',revision:'b'.repeat(40)},
    baseConfigurationSha256:'a'.repeat(64),webDirectory:'web',mcpRuntime:'runtime.json'
  }));
  result=await run();assert.equal(result.entered,false);
  assert.match(result.stderr,/release_runtime_binding_invalid/);
});
test('actual source launcher still refuses a different signed identity before owner boundary', async t => {
  const runtimeIdentity={id:'successor',version:'2.0.0',revision:'b'.repeat(40),digest:'b'.repeat(64)};
  const {run}=await launcherFixture(t,{runtimeIdentity,source:true});
  const result=await run();assert.equal(result.entered,false);
  assert.match(result.stderr,/prepared_release_identity_mismatch/);
});


test('fresh configuration has a distinct digest-free initial shape and cannot alias source authority',()=>{
 const config=staged();config.schema='unified-full-owner-fresh-composition-v1';
 config.authority.installationId='12345678-1234-4234-8234-123456789abc';
 delete config.release.prepared;config.release.initial={id:'fresh',version:'1.0.0',revision:'a'.repeat(40)};
 config.review={status:'approved',combinedLinuxReceiptSha256:'a'.repeat(64),catalogWriterConcurrency:'qualified',nativeModeProjection:'qualified',operationsPortabilityResolver:'qualified'};
 assert.equal(inspectConfig(config,{launch:true}).valid,true);
 for(const change of [c=>{c.release.initial.digest='a'.repeat(64);},c=>{c.release.prepared={};},c=>{c.sourceUnit='other';},
  c=>{c.bootstrapRecovery={};},c=>{c.bindings={};},c=>{c.authority.installationId='reused';},
  c=>{c.schema='unified-full-owner-composition-v1';},c=>{c.schema='unified-full-owner-fresh-composition-v2';}]){
  const invalid=structuredClone(config);change(invalid);assert.throws(()=>requireLaunchConfig(invalid));
 }
});


test('actual fresh launcher requires a consumed genuine claim and fixed installed receipt path before owners',async t=>{
 const api=await import('@amplifier/unified-distribution-update-owner');
 const initial={id:'fresh',version:'1.0.0',revision:'a'.repeat(40),digest:'b'.repeat(64)};
 const f=await launcherFixture(t,{runtimeIdentity:initial,source:false});
 const c=f.config,id='12345678-1234-4234-8234-123456789abc';
 const paths=await api.createPristineInstallation({directory:join(f.directory,'i'),dataScope:c.authority.dataScope,initial,plannedInstallationId:id});
 c.schema='unified-full-owner-fresh-composition-v1';delete c.release.prepared;
 c.release.initial={id:initial.id,version:initial.version,revision:initial.revision};c.authority.installationId=id;
 Object.assign(c.authority,{sourceDirectory:join(paths.directory,'source'),claimDirectory:join(paths.directory,'claim'),
  supervisorDirectory:paths.dataDirectory,supervisorDiscoveryFile:paths.supervisorDiscoveryFile,
  supervisorTokenFile:paths.supervisorTokenFile,hostDiscoveryFile:paths.hostDiscoveryFile,hostTokenFile:paths.hostTokenFile});
 c.application.stateDirectory=paths.applicationStateDirectory;c.application.manualIngress.stateDirectory=join(paths.directory,'ingress');c.receiptDirectory=join(paths.directory,'receipts');
 await writeFile(paths.hostTokenFile,'c'.repeat(64),{mode:0o600});
 const ownerModule=import.meta.resolve('@amplifier/unified-distribution-update-owner');
 await writeFile(join(f.directory,'node_modules/@amplifier/unified-distribution-update-owner/index.js'),`
 import {writeFileSync} from 'node:fs';
 export {inspectPristineInstallation} from ${JSON.stringify(ownerModule)};
 export async function createRuntimeIdentity(){return {identity:${JSON.stringify(initial)},dataScope:${JSON.stringify(c.authority.dataScope)},instanceId:'fresh-child'};}
 export function serviceIdentity(){writeFileSync(process.env.FIXTURE_AUTHORITY_MARKER,'entered');throw Error('fixture_authority_boundary');}
 `);
 let result=await f.run();assert.equal(result.entered,false);assert.match(result.stderr,/ENOENT/);
 const ports=api.createProductionSupervisorPorts({...paths,provisioningAuthorityFile:paths.authorityFile,resolveSources:async()=>[]});t.after(()=>ports.close());
 await ports.initialProvisioning.claim({commandId:'initial-fixture',instanceId:'fresh-child',dataScope:c.authority.dataScope,previousInstanceId:null,target:{identity:initial,handle:'release:'+initial.digest},signal:new AbortController().signal});
 const path=join(paths.directory,'initial-provisioning.claim'),claim=await readFile(path);
 for(const change of [v=>{v.targetDigest='d'.repeat(64);},v=>{v.installationId='other';},v=>{v.dataScope='other';},v=>{v.kind='imported';}]){
  const value=JSON.parse(claim);change(value);await writeFile(path,JSON.stringify(value));
  result=await f.run();assert.equal(result.entered,false);assert.match(result.stderr,/initial_claim_invalid/);
 }
 await writeFile(path,claim);
 result=await f.run({AMPLIFIER_DISTRIBUTION_RELEASE_RECEIPT:join(f.directory,'outside-receipt.json')});
 assert.equal(result.entered,false);assert.match(result.stderr,/fresh_installation_binding_mismatch/);
 result=await f.run({UNIFIED_MANUAL_SOURCE:'1'});assert.equal(result.entered,false);assert.match(result.stderr,/fresh_signed_supervision_required/);
 assert.deepEqual(await readFile(path),claim);
});
