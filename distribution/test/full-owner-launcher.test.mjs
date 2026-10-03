import test from 'node:test';
import assert from 'node:assert/strict';
import {OWNERS, inspectConfig, requireLaunchConfig} from '../src/validate-config.mjs';

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
