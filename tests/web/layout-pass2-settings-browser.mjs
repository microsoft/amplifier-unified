// Installed host Settings census, not pixel acceptance or a production-service test.
// Run only in the parent's prepared isolated environment. No dependency setup here.
// Required: AMPLIFIER_TEST_PYTHON, AMPLIFIER_TEST_PACKAGE_ROOT,
// AMPLIFIER_TEST_INSTALLED_PACKAGE, AMPLIFIER_TEST_WHEEL_IDENTITY (JSON file),
// AMPLIFIER_TEST_ARTIFACTS. WHEEL_IDENTITY is an accepted identity-file alias.
// Focused recovery: LAYOUT_PASS2_ROUTES=updates,appearance
//                  LAYOUT_PASS2_WIDTHS=390
// Final five read-only gaps only: LAYOUT_PASS2_FINAL_GAPS=1 (defaults to the four
// affected routes at 1440/390; original route/state denominator stays unvisited).
// Optimization draft scenes default to explicitly synthetic policy/no-operation
// reads. LAYOUT_PASS2_OPTIMIZATION_READS=host retains the actual host-disabled branch.
// Omitted routes/states remain explicitly unvisited, never implicitly passing.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {appendFile, mkdir, readFile, realpath, rename, writeFile} from 'node:fs/promises';
import {isAbsolute, join, resolve, sep} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium, expect} from '@playwright/test';
import {startFixture} from './fixture-readiness.mjs';
import {finishFixture} from './fixture-lifecycle.mjs';
import {fenceLayoutServiceWorkers} from './layout-pass2-browser-context.mjs';
import {openSettingsPage} from '../../.build/web-client/tests/browser-settings.mjs';
import {settingsSections} from '../../.build/web-client/src/settings-navigation.js';

const root = fileURLToPath(new URL('../../', import.meta.url));
const outputRoot = process.env.AMPLIFIER_TEST_ARTIFACTS;
assert.ok(outputRoot && isAbsolute(outputRoot), 'Use an explicit absolute evidence destination.');
// Recovery never overwrites a preceding run's evidence.
await mkdir(outputRoot, {recursive: true});
const output = join(outputRoot, 'layout-pass2-' + new Date().toISOString().replace(/[:.]/g, '-') + '-' + process.pid);
await mkdir(output);
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const csv = text => text ? text.split(',').map(value => value.trim()).filter(Boolean) : null;
const routeFilter = csv(process.env.LAYOUT_PASS2_ROUTES);
const widthFilter = csv(process.env.LAYOUT_PASS2_WIDTHS)?.map(Number);
const finalGapMode = process.env.LAYOUT_PASS2_FINAL_GAPS === '1';
const finalGapRoutes = new Set(['updates', 'diagnostics', 'desktop', 'testing']);
const critical = new Set(['updates', 'appearance', 'voice', 'notifications', 'providers',
  'routing', 'smart-tools', 'tool-connections', 'testing', 'workspace-starters',
  'diagnostics', 'permissions', 'external-agents', 'storage']);
const nested = new Set(['advanced', 'privacy', 'workspace-starters', 'providers',
  'routing', 'smart-tools', 'tool-connections', 'diagnostics']);
const pages = settingsSections.flatMap(section => section.pages
  .filter(([id]) => id !== 'overview')
  .map(([id, title]) => ({id, title, section: section.id})));
assert.equal(new Set(pages.map(row => row.id)).size, pages.length);
const ledger = {
  schema: 1, kind: 'layout-pass2-settings-interaction-inventory',
  startedAt: new Date().toISOString(), completedAt: null, pixelInspected: false,
  scope: 'One owned fixture; installed host/assets; synthetic accounts and state. Not release/native/provider acceptance.',
  selection: {routeFilter, widthFilter, finalGapMode, pages, overviewAliasExcluded: true,
    declaredPagesIncludingAliases: settingsSections.reduce((sum, section) => sum + section.pages.length, 0)},
  evidenceDirectory: output,
  syntheticCampaign: {
    appearance: 'Fixture-owned strict format-2 ZIP, selected/read only; Delete/Cancel never confirms.',
    voice: 'Public configured connection read and fallback preferences; no key, sample or call.',
    runners: 'Static inventory/machine/environment-limit protocol reads; no SSH, register, enable or save.',
    mcp: 'Startup-only package/server/schema data and synthetic schema ACK; no transport or tool invocation.',
    storage: 'Scene-scoped backup inventory/review response; Keep only, never host preview/deletion.',
    reset: 'Startup runtime-selection preview; confirmation text is cleared, Apply never requested.',
    finalGaps: finalGapMode ? 'Five synthetic read-only gap scenes; not a rerun or green rewrite of baseline evidence.' : 'Not selected.',
  },
  sources: {}, identity: {}, jobs: [], states: [], requests: [], errors: [], cleanup: null,
};
const reportPath = join(output, 'layout-pass2-settings.json');
async function save() {
  const temporary = reportPath + '.tmp';
  await writeFile(temporary, JSON.stringify(ledger, null, 2) + '\n');
  await rename(temporary, reportPath);
}
const errorText = error => String(error?.stack || error).slice(0, 6000);
const slug = text => String(text).replace(/[^a-z0-9_.-]+/gi, '-').slice(0, 150);

// The existing settings_ui_server still owns homes, app construction, synthetic
// provider probes, readiness and graceful shutdown. This small startup seed only
// supplies presentation data that opening a fresh empty host cannot produce.
// It does NOT call update/install/connect/repair/cleanup/provider/voice actions.
const seedProgram = String.raw`
import asyncio, atexit, hashlib, json, os, sys, tempfile, zipfile
from pathlib import Path
bootstrap = tempfile.TemporaryDirectory(prefix='layout-pass2-import-home-')
atexit.register(bootstrap.cleanup)
for name in ('HOME', 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME',
             'AMPLIFIER_HOME', 'AMPLIFIER_WEB_HOME', 'AMPLIFIER_SESSION_STATE_HOME',
             'AMPLIFIER_UNIFIED_IMPORT_HOME'):
    target = Path(bootstrap.name) / name.lower()
    target.mkdir()
    os.environ[name] = str(target)
sys.path.insert(0, sys.argv[1])
import settings_ui_server as fixture
from aiohttp import web
original_main = fixture.main
original_perform = fixture.SetupManager.perform
assert fixture.SetupManager.probe is fixture.probe
async def synthetic_provider_read(self, action, args):
    if action == 'providers.credentials':
        # Never inspect launch keys, key files, GitHub CLI or account state.
        return {'credentialCheck':{'module':args['module'],'available':False,
            'envVar':args.get('envVar') or 'LAYOUT_PASS2_SYNTHETIC_KEY',
            'defaultEnvVar':'LAYOUT_PASS2_SYNTHETIC_KEY','alternatives':[],
            'githubCliAvailable':False,'requestedEnvVar':args.get('envVar',''),
            'checkedAt':1700000000,'synthetic':True}}
    if action == 'providers.models':
        # The maintained fixture's probe returns literal models; bypass the
        # installed cached-probe preparation so no provider adapter can execute.
        result = await fixture.probe(self, action, args, args.get('workspace'))
        result.update(modelsProvenance='simulated', modelsSupported=True)
        return result
    return await original_perform(self, action, args)
fixture.SetupManager.perform = synthetic_provider_read
async def seeded_main(home):
    for name in ('HOME', 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME'):
        target = home / ('private-' + name.lower())
        target.mkdir()
        os.environ[name] = str(target)
    os.environ['AMPLIFIER_SESSION_STATE_HOME'] = str(home/'sessions')
    # Fixture-owned starting bytes, not theme.save/import or private user files.
    library = home/'appearance-library-v1'
    library.mkdir()
    saved_zip = library/'layout-pass2-synthetic.zip'
    with zipfile.ZipFile(saved_zip, 'w') as archive:
        archive.writestr('appearance.json', json.dumps({
            'type':'appearance','formatVersion':2,'base':'reference-v1',
            'name':'Layout pass two synthetic saved look',
            'description':'Synthetic disposable library seed; never a user appearance.',
            'stylesheet':'delta.css','assets':[],'licenses':[]}))
        archive.writestr('delta.css', '#amp-one { --a-accent: #5140ef; }')
    app = await original_main(home)
    service = app['service']
    updates = service.state['updates']
    updates.update(phase='checked', error=None, initialPreparation=False,
        currentStatus={'status':'current'}, diagnostics={
          'attemptId':'layout-pass2-current',
          'events':[{'id':'layout-pass2-event','at':1700000000,
            'attemptId':'layout-pass2-current','phase':'restart-ack','status':'succeeded',
            'expectedVersion':'0.21.0','observedVersion':'0.21.0','durationMs':125}],
          'activeFailures':{'layout-pass2-history':{'id':'layout-pass2-history',
            'occurrences':2,'latest':{'phase':'ecosystem-runtime-preflight',
            'status':'failed','at':1699999999,'package':'synthetic-fixture-module',
            'reason':'protected-runtime-source'}}}})
    if os.environ.get('LAYOUT_PASS2_FINAL_GAPS') == '1':
        # Public presentation data only. This is NOT an installed/completed
        # operation, a historical conversation import, or an updater request.
        updates['diagnostics']['completedBatches'] = [{
            'attemptId':'layout-pass2-synthetic-completed','kind':'ecosystem',
            'tier':'included','components':['synthetic-layout-component'],
            'completedAt':1700000000}]
        updates.setdefault('items', []).append({
            'id':'layout-pass2-synthetic-history',
            'label':'synthetic/layout-pass2-older-settings','kind':'history',
            'status':'historical',
            'detail':'Synthetic older conversation settings, presentation only; no conversation changed.'})
    # Pure public configuration seed: no API key is created/saved or inspected.
    # All preview/configure/call requests remain fenced before the host.
    from amplifier_web.voice import VoiceService
    from amplifier_web import voice_settings
    service.voice_service = VoiceService(service)
    voice_settings.configuration = lambda manager: {
        'available':True,'source':'environment','environmentAvailable':True,
        'privateKeyAvailable':False,'reason':None,'synthetic':True}
    # Settings streaming owns preferences; these change only disposable state.
    service.state['settings'].update(fallbackVoice='gpt-live-1', fallbackVoiceName='marin')
    # No real MCP executable/transport is created. The preloaded schema is data.
    tool_schema = {'type':'object','required':['query'],'properties':{
        'query':{'type':'string','title':'Synthetic query'},
        'limit':{'type':'integer','default':5},
        'format':{'type':'string','enum':['brief','full']}}}
    service.state['smartTools']['installations'].append({
        'id':'layout-pass2-package','name':'Layout pass two synthetic MCP package',
        'repository':'https://example.invalid/layout-pass2-not-fetched',
        'version':'0.0.0-synthetic','status':'installed','binDir':'/synthetic/not-installed',
        'guidance':'Synthetic setup instructions; no package or account was installed.',
        'manifest':{'name':'Layout pass two synthetic MCP package','requires':[
            {'name':'Synthetic dependency','optional':True,'purpose':'Presentation only',
             'install':'No installation required for this synthetic scene.','fixture':True}]}})
    service.state['smartTools']['servers'].append({
        'id':'layout-pass2-mcp','name':'Layout pass two synthetic MCP',
        'installationId':'layout-pass2-package','transport':'stdio','command':'/synthetic/not-executed',
        'args':[],'env':{},'cwd':'','status':'ready','connectionState':'Synthetic ready; no transport',
        'catalogState':'ready','catalogRevision':'layout-pass2-schema',
        'loadedSchemaRevision':'layout-pass2-schema',
        'tools':[{'name':'layout_pass2_lookup','title':'Synthetic lookup',
            'description':'Preloaded synthetic schema; Run tool is never invoked.',
            'inputSchema':tool_schema}],
        'loadedSchemas':{'layout_pass2_lookup':{'inputSchema':tool_schema}},
        'account':{'status':'unknown','detail':'Synthetic identity, not a verified account.'},
        'authorization':{'requestedScopes':[],'grantedScopes':[],'consent':'Not requested'}})
    # Reset preview data is a presentation seed, not an operation/recovery point.
    service.state.setdefault('maintenance', {})['resetPreview'] = {
        'parts':['runtime'],'paths':['/synthetic/no-files/layout-pass2-runtime']}
    service._publish()
    async def identity(request):
        import amplifier_web
        package = Path(amplifier_web.__file__).resolve().parent
        return web.json_response({'synthetic':True,'fixture':'settings_ui_server',
            'packageRoot':str(package),'folder':str(home/'workspace'),
            'installedPackageVerified':str(package)==os.environ['AMPLIFIER_TEST_PACKAGE_ROOT'],
            'syntheticProviderReads':{
                'providers.models':fixture.SetupManager.probe is fixture.probe and
                    fixture.SetupManager.perform is synthetic_provider_read,
                'providers.credentials':fixture.SetupManager.perform is synthetic_provider_read},
            'ordinarySeeds':{
                'appearance':{'id':'saved:layout-pass2-synthetic.zip',
                    'sha256':hashlib.sha256(saved_zip.read_bytes()).hexdigest()},
                'voice':'synthetic configured public read; no key/sample/call',
                'mcp':{'id':'layout-pass2-mcp','tool':'layout_pass2_lookup',
                    'schemaRevision':'layout-pass2-schema','installedOrConnected':False},
                'reset':'runtime confirmation preview only; no operation'},
            'finalGapSeeds':{
                'enabled':os.environ.get('LAYOUT_PASS2_FINAL_GAPS') == '1',
                'completedBatch':'layout-pass2-synthetic-completed',
                'historySource':'layout-pass2-synthetic-history'},
            'serverSha256':hashlib.sha256((package/'server.py').read_bytes()).hexdigest()})
    app.router.add_get('/fixture/layout-pass2-identity', identity)
    return app
fixture.main = seeded_main
asyncio.run(fixture.serve())
`;

// Deny by default BEFORE the request reaches the host. A fixture's fake install
// method is not a reason to admit install. Only UI drafts, browser acknowledgments,
// named reads, and reversible appearance preferences are forwarded.
const readActions = new Set([
  'providers.list', 'providers.decisionModels', 'providers.schema', 'routing.list',
  'routing.show', 'bundles.list', 'modules.list', 'modules.diagnostics', 'sources.list',
  'workspace.starters.list', 'workspace.list', 'externalAgents.list', 'externalAgents.get',
  'modelCapacity.read', 'access.get', 'permissions.get', 'notifications.get',
  'voice.configuration', 'desktop.readiness', 'diagnostics.records', 'testing.capacity.read',
  'testing.machine.read', 'testing.execution.read', 'testing.runners.read',
  'testing.runners.machine.read', 'testing.runners.capacity.read',
  'storage.inspect', 'storage.cleanup.read', 'storage.cleanup.preview',
  'storage.cleanup.evidence', 'theme.list', 'theme.read', 'appearance.preferences.read',
  'shell.inspect', 'smartTools.list', 'smartTools.catalog',
  'maintenance.installationRepairStatus',
]);
const appearanceActions = new Set([
  'appearance.preferences.select', 'appearance.preferences.update',
  'appearance.preferences.retry', 'appearance.client.report',
]);
const transportActions = new Set(['view.update', 'shell.report', 'settings.connection.start']);
const readPaths = [
  /^\/api\/(?:state(?:\/detail)?|events|actions|shell|sessions|workspaces)$/,
  /^\/api\/(?:conversation\/(?:detail|navigation)|ui-use\/v1\/state)$/,
  /^\/api\/(?:shell\/|history\/|appearance\/|themes\/)/,
];
const housekeepingPosts = new Set(['/api/clients/attach', '/api/view', '/api/shell/resources']);
const supportReport = {
  synthetic: true, source: 'layout-pass2 route fulfillment, NOT a host diagnostics result',
  schema: 1, runtime: {status: 'synthetic-read-only'},
  updates: {phase: 'checked', versions: ['0.21.0'], errors: [], timings: {elapsedMs: 125}},
};
const optimizationReadMode = process.env.LAYOUT_PASS2_OPTIMIZATION_READS || 'synthetic';
assert.ok(['synthetic', 'host'].includes(optimizationReadMode), 'Choose synthetic or host optimization reads.');
const optimizationReadSource = 'layout-pass2 synthetic policy/no-operation read; NOT installed host optimization support';
const optimizationPolicy = {revision: 1, requestKeepHours: null, responseKeepHours: null,
  synthetic: true, source: optimizationReadSource};
const runnerSeed = {
  id: 'layout-pass2-runner', name: 'Layout pass two synthetic runner',
  connection: 'synthetic-never-ssh', enabled: false, profiles: ['linux-python'],
  lastCheck: null,
};
const runnerDefaults = {maxRunning: 2, maxInstances: 4, minFreeBytes: 10 * 1024 ** 3};
const machineLimits = {maxReservedCpu: 4, maxReservedMemoryMiB: 8192,
  minAvailableMemoryMiB: 2048, minFreeBytes: 10 * 1024 ** 3, maxLoadPercent: 90, maxPreparing: 1};
async function installFence(context, job, identity) {
  const fence = {holdDiagnostics: false, pending: [], syntheticDiagnostics: 0, storageReview: false};
  const note = (request, disposition, action, reason) => {
    const url = new URL(request.url());
    const row = {at: new Date().toISOString(), job: job.id,
      state: job.currentState || 'startup', method: request.method(),
      path: url.pathname, action, disposition, reason};
    // Never retain action payloads, credentials, URL queries or request bodies.
    ledger.requests.push(row);
    return row;
  };
  const reject = async (route, action, reason) => {
    note(route.request(), 'blocked-before-host', action, reason);
    await route.fulfill({status: 403, contentType: 'application/json',
      body: JSON.stringify({accepted: false, status: 403, code: 'layout_audit_fence',
        error: 'Layout audit fenced this operation before the host: ' + reason})});
  };
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (!['http:', 'https:'].includes(url.protocol)) return route.continue();
    if (url.origin !== new URL(identity.base).origin)
      return reject(route, null, 'External origin; no external browser requests are admitted.');
    const method = request.method(), path = url.pathname;
    if (['GET', 'HEAD'].includes(method)) {
      // Static installed resources only; arbitrary same-origin setup/capture/URLs
      // must not become a second execution path around the action fence.
      const staticRead = path === '/' || path === '/favicon.ico' ||
        /^\/(?:assets|branding|licenses|fonts)\//.test(path) ||
        /^\/[A-Za-z0-9_.-]+\.(?:js|css|png|svg|ico|webmanifest)$/.test(path);
      if (staticRead || readPaths.some(pattern => pattern.test(path))) return route.continue();
      return reject(route, null, 'GET endpoint not in the Settings read/static allowlist.');
    }
    if (method === 'POST' && housekeepingPosts.has(path)) return route.continue();
    if (method !== 'POST' || path !== '/api/actions')
      return reject(route, null, 'Non-action mutation endpoint (including direct UI-use input).');
    let body;
    try { body = request.postDataJSON(); } catch { return reject(route, null, 'Unreadable action.'); }
    // Do not admit arbitrary shell.command nesting; Settings uses direct actions.
    const {action, args = {}} = body || {};
    const syntheticRead = async (result, reason) => {
      note(request, 'synthetic-route-fulfillment', action, reason);
      return route.fulfill({status: 200, contentType: 'application/json',
        body: JSON.stringify({accepted: true, result: {...result, synthetic: true, source: reason}})});
    };
    if (finalGapMode && ['desktop.readiness', 'testing.capacity.read'].includes(action)) {
      // Public clientId is envelope bookkeeping, not a native/conversation
      // target. Any other nonempty targeting argument fails before the host.
      if (!identity.finalGapSeeds?.enabled || job.route !==
        (action === 'desktop.readiness' ? 'desktop' : 'testing') ||
        Object.keys(args).some(key => key !== 'clientId'))
        return reject(route, action, 'Final-gap read requires its exact global route and absent session/native/runner target.');
      if (action === 'desktop.readiness')
        return syntheticRead({
          sessionId: null, observedAt: 1700000000,
          host: {host: {label: 'Synthetic layout host'}, platform: 'synthetic-linux',
            python: {version: '3.13', path: '/synthetic/not-executed/python'},
            computerUsePackage: {status: 'not-installed', version: null}},
          nativeObservation: {available: false, code: 'synthetic-layout-only',
            reason: 'Static seed, not native observation.', nextStep: 'No capture/install in this scene.'},
          featureSetup: {supported: false, reason: 'Synthetic presentation only.'}, nextActions: [],
        }, 'Final-gap static null-session Desktop report; no host/native status, permission or capture request.');
      return syntheticRead({
        revision: 'layout-pass2-synthetic-overrides',
        scope: 'Synthetic configured runner presentation; no contact.',
        runner: {backend: 'docker', description: 'Synthetic layout runner; not contacted.'},
        runnerId: runnerSeed.id, defaults: {}, runnerOverrides: {},
        effective: {maxRunning: 2, maxInstances: 6, minFreeBytes: 1024 ** 3},
        sources: {maxRunning: 'backend-default', maxInstances: 'backend-default', minFreeBytes: 'backend-default'},
        limitsScope: 'Synthetic environment limits only.',
      }, 'Final-gap static top-level capacity read with runnerId; no runner registration/contact/save.');
    }
    if (action === 'testing.runners.read')
      return syntheticRead({revision: 'layout-pass2-runners', runners: [runnerSeed], enrollments: [],
        scope: 'Synthetic read-only runner inventory; no SSH, controller or environment.'},
      'Maintained testing-settings-browser mounted protocol shape; static runner inventory only.');
    if (['testing.runners.machine.read', 'testing.runners.capacity.read'].includes(action)) {
      if (args.id !== runnerSeed.id) return reject(route, action, 'Only the explicit synthetic runner identity is seeded.');
      return syntheticRead(action.endsWith('capacity.read') ? {
        revision: 'layout-pass2-capacity', limits: {}, defaults: runnerDefaults, effective: runnerDefaults,
        scope: 'Synthetic runner environment limits.', behavior: 'Presentation only; no runner contacted.',
      } : {
        supported: true, revision: 'layout-pass2-machine', scope: 'Synthetic runner machine budgets.',
        limits: {}, effective: machineLimits, reserved: {cpus: 0, memoryMiB: 0, count: 0, preparing: 0},
        observed: {availableMemoryMiB: 16384, freeBytes: 100 * 1024 ** 3},
      }, 'Static synthetic runner read; no remote observation.');
    }
    if (action === 'smartTools.schemas') {
      if (args.id !== identity.ordinarySeeds?.mcp?.id ||
        args.catalogRevision !== identity.ordinarySeeds.mcp.schemaRevision ||
        JSON.stringify(args.names) !== JSON.stringify([identity.ordinarySeeds.mcp.tool]))
        return reject(route, action, 'Only the startup-seeded MCP/schema identity has a synthetic response.');
      return syntheticRead({schemasPreloaded: true, serverId: args.id, names: args.names},
        'Startup-preloaded synthetic schema acknowledgment; no MCP connection or schema request sent.');
    }
    if (fence.storageReview && action === 'storage.inspect') {
      const base = {label: 'Synthetic backup review storage', bytes: 1024, scanning: false,
        measuring: false, measuredAt: 1700000000, incomplete: false,
        description: 'Synthetic inventory; no filesystem measurement or deletion.'};
      return syntheticRead({...base, rows: args.category === 'backups' ? [{
        id: 'layout-pass2-backup', label: 'Synthetic older backup', bytes: 1024, directory: false,
        backupId: 'layout-pass2-backup', protected: false,
      }] : [{id: 'backups', label: 'Backups', bytes: 1024, directory: true,
        description: 'Synthetic cancellable review scene.'}]},
      'Read-only synthetic backup inventory, scoped to this scene.');
    }
    if (fence.storageReview && action === 'storage.backupDeletePreview') {
      if (args.id !== 'layout-pass2-backup') return reject(route, action, 'Unknown synthetic backup identity.');
      return syntheticRead({token: 'synthetic-not-a-host-deletion-token',
        description: 'Synthetic removal review. Keep backup cancels; deletion is never invoked.',
        inventory: {fileBytes: 1024, fileCount: 1}, path: '/synthetic/not-a-real-backup'},
      'Synthetic review response only; no host deletion preview or confirmation is issued.');
    }
    if (['providers.models', 'providers.credentials'].includes(action)) {
      if (identity.syntheticProviderReads?.[action] !== true)
        return reject(route, action, 'Owned fixture did not verify the exact synthetic provider-read method.');
      note(request, 'allowed-verified-synthetic-provider-read', action,
        action === 'providers.models' ? 'settings_ui_server.probe literal models, no adapter execution.' :
          'Startup wrapper returns unavailable synthetic credentials; no environment/key/CLI discovery.');
      return route.continue();
    }
    if (['maintenance.optimize.policy.read', 'maintenance.optimize.read'].includes(action)) {
      if (optimizationReadMode === 'host') {
        note(request, 'allowed-explicit-host-read', action,
          'Actual unsupported/disabled response retained; controls are never forced enabled.');
        return route.continue();
      }
      if (args.commandId || args.operationId || args.after != null)
        return reject(route, action, 'Synthetic no-operation seed cannot answer a real command, operation or result page.');
      note(request, 'synthetic-route-fulfillment', action, optimizationReadSource);
      return route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify({
        accepted: true, result: action === 'maintenance.optimize.policy.read' ? optimizationPolicy :
          {operation: null, synthetic: true, source: optimizationReadSource},
      })});
    }
    if (action === 'updates.diagnostics') {
      const receipt = note(request, 'synthetic-route-fulfillment', action, supportReport.source);
      fence.syntheticDiagnostics++;
      if (fence.holdDiagnostics) {
        await new Promise(done => {
          const timer = setTimeout(() => {receipt.deadlineReleased = true; done();}, 12000);
          fence.pending.push(() => {clearTimeout(timer); done();});
        });
      }
      return route.fulfill({status: 200, contentType: 'application/json',
        body: JSON.stringify({accepted: true, result: supportReport})});
    }
    if (action === 'attention.read') {
      note(request, 'synthetic-ack', action, 'Do not mutate fixture review history just by inspecting.');
      return route.fulfill({status: 200, contentType: 'application/json',
        body: JSON.stringify({accepted: true, result: {synthetic: true}})});
    }
    if (action === 'locations.list') {
      // Missing-path Browse is narrowed to our actual disposable fixture folder.
      // Never enumerate the host's home/default root. No locations.create admitted.
      const folder = args.path ? resolve(args.path) : identity.folder;
      if (!folder || !(folder === identity.folder || folder.startsWith(identity.folder + sep)))
        return reject(route, action, 'Location listing outside disposable fixture workspace.');
      note(request, 'fixture-confined-read', action, 'Missing path narrowed to synthetic workspace.');
      return route.continue({postData: JSON.stringify({...body, args: {...args, path: folder}})});
    }
    if (readActions.has(action) || appearanceActions.has(action) || transportActions.has(action)) {
      note(request, readActions.has(action) ? 'allowed-named-read' :
        appearanceActions.has(action) ? 'allowed-reversible-fixture-appearance' : 'allowed-ui-bookkeeping',
      action, 'Explicit allowlist; all homes are disposable.');
      return route.continue();
    }
    return reject(route, action, 'Provider/tool/voice execution, credentials, grants, saves, deletion, recovery and lifecycle effects are not admitted.');
  });
  return fence;
}

// Raw geometry only. No inference that screenshots were visually reviewed. Text
// ranges expose actual glyph boxes rather than treating the full button as text.
function observeDOM() {
  const rect = node => {
    const r = node.getBoundingClientRect();
    return {x: r.x, y: r.y, width: r.width, height: r.height,
      top: r.top, right: r.right, bottom: r.bottom, left: r.left};
  };
  const shown = node => {
    const css = getComputedStyle(node), r = node.getBoundingClientRect();
    return !node.closest('[hidden]') && css.display !== 'none' &&
      css.visibility !== 'hidden' && r.width > 0 && r.height > 0;
  };
  const clipping = node => {
    const bounds = rect(node), effective = {left: Math.max(0, bounds.left), top: Math.max(0, bounds.top),
      right: Math.min(innerWidth, bounds.right), bottom: Math.min(innerHeight, bounds.bottom)};
    const ancestors = [], scrollOwners = [];
    for (let parent = node.parentElement; parent; parent = parent.parentElement) {
      const css = getComputedStyle(parent), r = rect(parent);
      const entry = {tag: parent.tagName, id: parent.id || null, className: parent.className,
        bounds: r, overflowX: css.overflowX, overflowY: css.overflowY,
        clientWidth: parent.clientWidth, clientHeight: parent.clientHeight,
        scrollWidth: parent.scrollWidth, scrollHeight: parent.scrollHeight,
        scrollTop: parent.scrollTop, scrollLeft: parent.scrollLeft};
      if (['auto', 'scroll'].includes(css.overflowY) && parent.scrollHeight > parent.clientHeight ||
        ['auto', 'scroll'].includes(css.overflowX) && parent.scrollWidth > parent.clientWidth)
        scrollOwners.push(entry);
      if (['auto', 'scroll', 'hidden', 'clip'].includes(css.overflowX)) {
        effective.left = Math.max(effective.left, r.left + parent.clientLeft);
        effective.right = Math.min(effective.right, r.left + parent.clientLeft + parent.clientWidth);
        ancestors.push({...entry, clippingAxis: 'x'});
      }
      if (['auto', 'scroll', 'hidden', 'clip'].includes(css.overflowY)) {
        effective.top = Math.max(effective.top, r.top + parent.clientTop);
        effective.bottom = Math.min(effective.bottom, r.top + parent.clientTop + parent.clientHeight);
        ancestors.push({...entry, clippingAxis: 'y'});
      }
    }
    return {effective, clippedBy: ancestors, scrollOwners,
      fullyVisible: effective.left <= bounds.left + 1 && effective.right >= bounds.right - 1 &&
        effective.top <= bounds.top + 1 && effective.bottom >= bounds.bottom - 1,
      visibleWidth: Math.max(0, effective.right - effective.left),
      visibleHeight: Math.max(0, effective.bottom - effective.top)};
  };
  const scopes = [...document.querySelectorAll(
    '.a-settings-content,.a-settings-mobile-actions,.a-settings-draft-confirm')].filter(shown);
  const controls = [...new Set(scopes.flatMap(scope => [...scope.querySelectorAll(
    'button,input,select,textarea,summary,a[href]')]))].filter(shown).map(node => {
    const css = getComputedStyle(node), bounds = rect(node), glyphRects = [];
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const text = walker.currentNode;
      if (!text.textContent.trim() || text.parentElement.closest('svg,option')) continue;
      const range = document.createRange(); range.selectNodeContents(text);
      for (const r of range.getClientRects()) if (r.width && r.height)
        glyphRects.push({x: r.x, y: r.y, width: r.width, height: r.height,
          left: r.left, right: r.right, top: r.top, bottom: r.bottom});
    }
    const icons = [...node.querySelectorAll('svg')].filter(shown).map(rect);
    const firstText = glyphRects[0], firstIcon = icons[0];
    const gap = firstText && firstIcon ? {
      horizontal: Math.max(firstText.left - firstIcon.right, firstIcon.left - firstText.right),
      vertical: Math.max(firstText.top - firstIcon.bottom, firstIcon.top - firstText.bottom),
    } : null;
    const labels = [...(node.labels || [])].map(label => label.textContent.trim());
    return {
      tag: node.tagName, id: node.id || null, type: node.getAttribute('type'),
      name: node.getAttribute('aria-label') || labels.join(' ') ||
        node.textContent.trim().replace(/\s+/g, ' ').slice(0, 220),
      action: node.getAttribute('data-action'), disabled: node.matches(':disabled'),
      ownDisabled: node.disabled === true, disabledByFieldset: !!node.closest('fieldset[disabled]'),
      ariaDisabled: node.getAttribute('aria-disabled'), expanded: node.getAttribute('aria-expanded'),
      checked: ['checkbox', 'radio'].includes(node.type) ? node.checked : null,
      // Never record password/input/textarea values, including private drafts.
      options: node.tagName === 'SELECT' ? [...node.options].map(option =>
        ({label: option.label, disabled: option.disabled, selected: option.selected})) : undefined,
      bounds, inViewport: bounds.bottom > 0 && bounds.top < innerHeight &&
        bounds.right > 0 && bounds.left < innerWidth,
      padding: {top: css.paddingTop, right: css.paddingRight, bottom: css.paddingBottom, left: css.paddingLeft},
      font: {size: css.fontSize, lineHeight: css.lineHeight},
      paint: {background:css.backgroundColor,borderColor:css.borderColor,
        borderWidth:css.borderWidth,borderStyle:css.borderStyle,boxShadow:css.boxShadow},
      glyphRects, icons, iconLabelGap: gap,
      surface: node.closest('.a-settings-draft-confirm') ? 'portaled-unsaved-confirmation' : 'settings',
      clipping: clipping(node),
      focus: {active: document.activeElement === node, outlineStyle: css.outlineStyle,
        outlineWidth: css.outlineWidth, outlineOffset: css.outlineOffset, outlineColor: css.outlineColor},
      overflow: {scrollWidth: node.scrollWidth, clientWidth: node.clientWidth},
    };
  });
  const panels = [...document.querySelectorAll(
    '.a-dialog,.a-settings-experience,.a-settings-content,.a-settings-mobile-actions,.a-settings-draft-overlay,.a-settings-draft-confirm')].filter(shown)
    .map(node => ({className: node.className, bounds: rect(node),
      scrollTop: node.scrollTop, scrollHeight: node.scrollHeight,
      scrollWidth: node.scrollWidth, clientWidth: node.clientWidth, clientHeight: node.clientHeight,
      outsideAppDialog: !!node.matches('.a-settings-draft-confirm') && !node.closest('.a-dialog'),
      clipping: clipping(node)}));
  const root = document.querySelector('.a-settings-experience');
  return {route: root ? {...root.dataset} : null, controls, panels,
    document: {width: innerWidth, height: innerHeight, scrollWidth: document.documentElement.scrollWidth},
    gallery: [...document.querySelectorAll('.a-settings-page-content:not([hidden]) [data-appearance-card]')]
      .map(node => ({id: node.dataset.appearanceCard,
        name: node.querySelector('input')?.getAttribute('aria-label'),
        selected: node.querySelector('input')?.checked,
        thumbnail: node.querySelector('[data-thumbnail-state]')?.dataset.thumbnailState})),
    alerts: scopes.flatMap(scope => [...scope.querySelectorAll('[role=alert],[role=status]')])
      .filter(shown).map(node => node.textContent.trim().slice(0, 500)),
  };
}
class NotPresent extends Error {}
const visible = locator => locator.filter({visible: true});

function declareState(job, name) {
  const id = job.id + ':' + name;
  let row = ledger.states.find(item => item.id === id);
  if (!row) {
    row = {id, job: job.id, name, status: 'unvisited', reason: 'Not yet attempted',
      startedAt: null, endedAt: null, observations: []};
    ledger.states.push(row);
  }
  return row;
}
async function declareJourney(job) {
  // Execute recipe registration, NEVER the work callbacks. Thus the same
  // maintained recipes supply both the denominator and the actual journey.
  const declare = async name => {declareState(job, name); return false;};
  declareState(job, 'route.entry');
  await recipe({job, step: declare,
    disclose: name => declare(job.route + '.disclosure.' + slug(name)),
    click: (name, label = name) => declare(job.route + '.' + label),
    picker: name => declare(job.route + '.picker.' + slug(name)),
    unsaved: async () => {
      await declare(job.route + '.draft.cancel-and-review');
      if (job.width === 390) await declare(job.route + '.draft.short-height-focus');
    }});
  await finalGapRecipe({job, step: declare});
  declareState(job, 'route.bottom');
}

async function openRoute(page, destination, activate) {
  if (destination === 'workspace-starters') {
    // This real Manage starters button currently has no data-settings-destination
    // attribute. The instrumented generic helper correctly refuses its fallback.
    await openSettingsPage(page, 'workspaces', {activate});
    await activate('Manage starters through visible Workspaces control',
      page.locator('.a-settings-page-content:not([hidden])')
        .getByRole('button', {name: 'Manage starters →', exact: true}));
    await expect(page.locator('.a-settings-experience')).toHaveAttribute('data-settings-page', destination);
    return;
  }
  await openSettingsPage(page, destination, {activate});
}

// A recipe lists safe controls by meaning. It never clicks every discovered
// button, every summary, or a data-action merely because that attribute looks safe.
function makeJourney(page, job, fence) {
  const content = () => page.locator('.a-settings-content');
  const active = () => page.locator('.a-settings-page-content:not([hidden])');
  const footer = () => page.locator('.a-settings-mobile-actions');
  const button = name => content().getByRole('button', {name, exact: true})
    .or(footer().getByRole('button', {name, exact: true}));
  const field = name => content().getByLabel(name, {exact: true});
  const summary = name => {
    if (name === 'Advanced') return active().locator('.a-appearance-more > summary');
    // JSX puts strong title and small description in adjacent elements without
    // intervening whitespace. Match the exact title node, not combined text.
    const title = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    if (['Bundles & modules', 'Performance & runtime', 'Troubleshooting & recovery'].includes(name))
      return active().locator('.a-advanced-group > summary').filter({
        has: page.locator('strong').filter({hasText: new RegExp('^' + title + '$')}),
      });
    return content().locator('summary').filter({hasText: new RegExp('^' + title + '(?:\\s|$)')});
  };
  const requireControl = async locator => {
    const matching = visible(locator);
    try {await matching.first().waitFor({state: 'visible', timeout: 2000});}
    catch {throw new NotPresent('Matching control did not become displayed within the bounded precondition wait.');}
    const count = await matching.count();
    if (!count) throw new NotPresent('No currently displayed matching control in active Settings content/footer.');
    assert.equal(count, 1, 'Ambiguous visible control; the recipe must disambiguate, never choose a hidden editor.');
    return matching;
  };
  async function activate(label, locator, options = {}) {
    const control = await requireControl(locator);
    await control.scrollIntoViewIfNeeded();
    await expect(control).toBeEnabled({timeout: options.timeout || 5000});
    await control.focus();
    const geometry = await page.evaluate(observeDOM);
    const gesture = {state: job.currentState, label,
      focusMethod: 'locator.focus; keyboard-equivalence not inferred',
      focusedControls: geometry.controls.filter(row => row.focus.active)};
    job.gestures.push(gesture);
    if (/^Open .* Settings$|^Open App options$|^Open Settings from App options$/.test(label)) {
      const filename = slug(job.id + '-navigation-' + job.gestures.length) + '.png';
      await page.screenshot({path: join(output, filename), animations: 'disabled'});
      gesture.navigationScreenshot = filename;
      gesture.routeBefore = geometry.route;
    }
    await control.click(options);
  }
  async function snapshot(row, suffix = '') {
    await page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))));
    const name = slug(job.id + '.' + row.name + suffix) + '.png';
    row.observations ||= [];
    const observed = await page.evaluate(observeDOM);
    await page.screenshot({path: join(output, name), animations: 'disabled'});
    row.observations.push({screenshot: name, pixelInspected: false, ...observed});
  }
  async function step(name, work) {
    const row = declareState(job, name);
    job.currentState = name; row.startedAt = new Date().toISOString();
    await save();
    const errorsBefore = ledger.errors.length;
    try {
      row.detail = await work(row);
      await snapshot(row);
      row.status = 'visited'; delete row.reason;
    } catch (error) {
      row.status = error instanceof NotPresent ? 'unvisited' : 'failed';
      row.reason = errorText(error);
      try { await snapshot(row, '-incomplete'); } catch (captureError) { row.captureError = errorText(captureError); }
    } finally {
      row.errorsDuringState = ledger.errors.slice(errorsBefore);
      row.endedAt = new Date().toISOString();
      await appendFile(join(output, 'layout-pass2-states.jsonl'), JSON.stringify(row) + '\n');
      await save();
    }
    return row.status === 'visited';
  }
  const click = (name, label = name) => step(job.route + '.' + label, () => activate(label, button(name)));
  async function expandDisclosure(name, locator) {
    const control = await requireControl(locator);
    // Pin the observed DOM identity before a toggle makes more summaries
    // visible. Never reevaluate a broad locator after expansion or choose first.
    const handle = await control.elementHandle();
    assert.ok(handle, 'Observed disclosure still has a DOM identity.');
    try {
      const alreadyOpen = await handle.evaluate(node => node.parentElement.open);
      if (!alreadyOpen) await activate('Expand ' + name, control);
      await expect.poll(() => handle.evaluate(node =>
        node.isConnected && node.parentElement.tagName === 'DETAILS' && node.parentElement.open)).toBe(true);
      return {disclosure: name, alreadyOpen};
    } finally {await handle.dispose();}
  }
  const disclose = name => step(job.route + '.disclosure.' + slug(name),
    () => expandDisclosure(name, summary(name)));
  async function bottom(row) {
    await content().evaluate(node => {node.scrollTop = node.scrollHeight;});
    await snapshot(row, '-bottom');
    return {atBottom: await content().evaluate(node => node.scrollHeight - node.clientHeight - node.scrollTop <= 2)};
  }
  async function fill(name, value) {
    const control = await requireControl(field(name));
    await control.scrollIntoViewIfNeeded(); await control.fill(value);
    return control;
  }
  async function selectDraft(name, value) {
    const control = await requireControl(field(name));
    await control.scrollIntoViewIfNeeded(); await control.selectOption(value);
  }
  async function picker(name, inputLocator = field(name)) {
    await step(job.route + '.picker.' + slug(name), async row => {
      const input = await requireControl(typeof inputLocator === 'function' ? inputLocator() : inputLocator);
      const container = input.locator('xpath=ancestor::*[contains(concat(" ",normalize-space(@class)," ")," a-path-field ")][1]');
      await activate('Browse ' + name, container.getByRole('button', {name: 'Browse', exact: true}));
      const panel = container.locator('.a-location-picker');
      await expect(panel).toBeVisible();
      await snapshot(row, '-open');
      await activate('Show new folder draft', panel.getByRole('button', {name: 'New folder', exact: true}));
      await panel.getByLabel('New folder name', {exact: true}).fill('unsaved-layout-specimen');
      await snapshot(row, '-new-folder-draft');
      await activate('Cancel folder draft', panel.getByRole('button', {name: 'Cancel', exact: true}));
      await activate('Close location picker', panel.getByRole('button', {name: 'Close location picker', exact: true}));
      await expect(panel).not.toBeVisible();
      return {fixtureConfined: true, folderCreated: false};
    });
  }
  async function unsaved(name, value) {
    for (const short of job.width === 390 ? [false, true] : [false]) {
      await step(job.route + (short ? '.draft.short-height-focus' : '.draft.cancel-and-review'), async row => {
        const originalViewport = page.viewportSize(), violations = [];
        const dialog = page.getByRole('alertdialog', {name: 'Unsaved settings changes', exact: true});
        row.focusCycle = []; row.dismissals = []; row.violations = violations;
        const focusState = () => dialog.evaluate(node => {
          const buttons = [...node.querySelectorAll('button')], current = document.activeElement;
          return {inside: node.contains(current), index: buttons.indexOf(current),
            name: current?.getAttribute('aria-label') || current?.textContent.trim().slice(0, 160),
            count: buttons.length};
        });
        try {
          if (short) await page.setViewportSize({width: 390, height: 420});
          row.viewport = page.viewportSize();
          const input = await fill(name, value);
          // Capture identity only; the synthetic value is checked via the control,
          // never added to the general raw-control inventory.
          row.originalInput = await input.evaluate(node => ({id: node.id, type: node.type,
            label: [...(node.labels || [])].map(item => item.textContent.trim())}));
          const close = page.locator('.a-settings-mobile-head:visible').getByRole('button', {name: 'Close settings', exact: true})
            .or(page.locator('.a-dialog-head:visible').getByRole('button', {name: 'Close panel', exact: true}));
          await activate('Attempt close with private draft', close);
          await expect(dialog).toBeVisible();
          // IMPORTANT: snapshot before scrolling/focusing a dialog button; doing
          // that first could hide the original clipping in this portaled surface.
          await snapshot(row, '-confirmation-initial');
          const observed = await page.evaluate(observeDOM);
          row.initialDialog = observed.panels.find(item => item.className === 'a-settings-draft-confirm');
          row.initialLastButton = observed.controls.find(item => item.surface === 'portaled-unsaved-confirmation' &&
            item.name === 'Keep settings open');
          if (short) {
            if (!row.initialDialog?.outsideAppDialog)
              violations.push('Expected portaled confirmation geometry independent of .a-dialog.');
            const start = await focusState(); row.focusCycle.push({key: 'initial', ...start});
            if (!start.inside || start.index !== 0) violations.push('Initial focus did not enter Review and save.');
            for (const key of ['Tab', 'Tab', 'Tab', 'Shift+Tab', 'Shift+Tab', 'Shift+Tab']) {
              const before = await focusState();
              await page.keyboard.press(key);
              const after = await focusState();
              row.focusCycle.push({key, ...after});
              const expected = (before.index + (key === 'Tab' ? 1 : -1) + before.count) % before.count;
              if (!after.inside || after.index !== expected) violations.push(key + ' escaped or broke the dialog focus cycle.');
              if (after.index === after.count - 1) {
                await snapshot(row, '-last-button-keyboard-' + row.focusCycle.length);
                const last = (await page.evaluate(observeDOM)).controls.find(item =>
                  item.surface === 'portaled-unsaved-confirmation' && item.name === 'Keep settings open');
                row.keyboardLastButton = last;
                if (!last?.clipping?.fullyVisible)
                  violations.push('Keep settings open is clipped after keyboard focus; inspect scroll-owner/bounds evidence.');
              }
            }
            if (row.initialLastButton && !row.initialLastButton.clipping.fullyVisible &&
              !row.initialLastButton.clipping.scrollOwners.length)
              violations.push('Last confirmation button is initially clipped with no scroll owner.');
          }
          for (const dismissal of ['Keep settings open', 'Review and save']) {
            if (!await dialog.isVisible()) {
              // Re-establish the edited input as the actual origin before the
              // second dismissal. No manual refocus is used AFTER dismissal.
              await input.focus();
              await activate('Reopen draft decision', close);
              await expect(dialog).toBeVisible();
            }
            await activate(dismissal, dialog.getByRole('button', {name: dismissal, exact: true}));
            await expect(dialog).not.toBeVisible();
            await expect(field(name)).toHaveValue(value);
            const restoredFocus = await field(name).evaluate(node => document.activeElement === node);
            const readFocus = () => page.evaluate(() => {
              const node=document.activeElement,r=node.getBoundingClientRect();
              return {tag:node.tagName,id:node.id,name:node.getAttribute('aria-label')||
                node.textContent?.trim().slice(0,160),connected:node.isConnected,
                insideSettings:!!node.closest('.a-dialog'),
                insideVisibleEditor:!!node.closest('.a-settings-page-content:not([hidden])'),
                bounds:{x:r.x,y:r.y,width:r.width,height:r.height},
                visible:!!(r.width&&r.height)&&r.bottom>0&&r.top<innerHeight};
            });
            await page.evaluate(() => new Promise(resolve =>
              requestAnimationFrame(() => requestAnimationFrame(resolve))));
            const afterDismissal=await readFocus();
            await page.keyboard.press('Tab');
            const afterTab=await readFocus();
            row.dismissals.push({action: dismissal, valueRetained: true,
              originalInputFocused: restoredFocus,afterDismissal,afterTab});
            await snapshot(row, '-after-' + slug(dismissal));
            const meaningfulOwner=afterDismissal.connected && afterDismissal.visible &&
              afterDismissal.insideSettings && !['BODY','HTML'].includes(afterDismissal.tag);
            if (short && !meaningfulOwner)
              violations.push(dismissal + ' did not restore focus to a visible current Settings owner.');
          }
          if (violations.length) row.reasonClass = 'observed-short-height-interaction-failure';
          assert.deepEqual(violations, [], 'Short-height unsaved-dialog interaction defects (not corrected by harness).');
          return {saved: false, retainedAfterKeepAndReview: true, shortHeight: short};
        } finally {
          if (await dialog.isVisible().catch(() => false)) await page.keyboard.press('Escape').catch(() => {});
          if (short) await page.setViewportSize(originalViewport);
          row.viewportRestored = page.viewportSize();
        }
      });
    }
  }
  return {page, job, fence, content, active, footer, button, field, summary, expandDisclosure,
    requireControl, activate, snapshot, step, click, disclose, bottom, fill, selectDraft, picker, unsaved};
}

async function appearance(j) {
  const {page, step, button, field, activate, disclose, content, snapshot} = j;
  await step('appearance.gallery.actual-identity', async row => {
    const gallery = content().getByRole('radiogroup', {name: 'Available appearances', exact: true});
    await expect(gallery.getByRole('radio').first()).toBeVisible();
    const original = await page.evaluate(() => {
      const a = window.amplifier.getState().appearance;
      return {scope: a.activeScope, appearanceId: a.effective.appearanceId,
        globalAppearanceId: a.shared?.values?.appearanceId};
    });
    row.original = original;
    await activate('Use actual Global appearance profile', button('Global'));
    await expect(button('Global')).toHaveAttribute('aria-pressed', 'true');
    const choose = async (id, control) => {
      await activate('Choose appearance ' + id, control);
      await expect.poll(() => page.evaluate(() => {
        const a = window.amplifier.getState().appearance;
        return {id: a.effective.appearanceId, settled: a.observedCompositionId === a.composition?.id};
      })).toEqual({id, settled: true});
      await snapshot(row, '-' + slug(id));
    };
    try {
      await choose('reference', button('Restore neutral appearance'));
      const items = await gallery.getByRole('radio').evaluateAll(nodes =>
        nodes.map(node => ({id: node.value, name: node.getAttribute('aria-label')})));
      row.actualGallery = items;
      const purpleCandidate = items.find(item => item.id === 'builtin:default');
      assert.ok(purpleCandidate, 'Included Amplifier/default candidate is present; do not infer identity from color.');
      row.purpleCandidate = {...purpleCandidate, colorVisuallyVerified: false};
      const ordered = [purpleCandidate, ...items.filter(item => item.id !== purpleCandidate.id)];
      for (const item of ordered) {
        // The native radio is deliberately clipped; the full-card label is the
        // actual pointer target, as in the maintained appearance-library journey.
        await gallery.getByRole('radio', {name: item.name, exact: true}).focus();
        await choose(item.id, gallery.locator(`[data-appearance-card=${JSON.stringify(item.id)}]`));
      }
    } finally {
      // Restore through the same visible preference control, not a dispatch.
      const restoreId = original.globalAppearanceId || original.appearanceId;
      const restore = restoreId === 'reference' ? button('Restore neutral appearance') :
        gallery.locator(`[data-appearance-card=${JSON.stringify(restoreId)}]`);
      if (await restore.count()) await choose(restoreId, restore);
      else row.restoreUnavailable = 'Original selection has no visible gallery control; no substitute selected.';
      if (original.scope === 'client') await activate('Restore original browser profile source', button('This browser'));
    }
  });
  await step('appearance.native-density-picker-cancel', async row => {
    const input = field('Density'), original = await input.inputValue();
    await input.scrollIntoViewIfNeeded(); await input.focus();
    await input.press('Alt+ArrowDown'); await snapshot(row, '-native-picker-requested');
    await input.press('Escape'); await expect(input).toHaveValue(original);
    return {valueUnchanged: true, nativePopupPixelsVerified: false};
  });
  await disclose('Advanced');
  await step('appearance.advanced.preference-groups', async row => {
    for (const heading of ['Layout and decorations', 'Conversation display', 'Work visibility and metrics']) {
      const section = content().locator('.a-appearance-preference-group')
        .filter({has: page.getByRole('heading', {name: heading, exact: true})});
      await expect(section).toHaveCount(1); await section.scrollIntoViewIfNeeded();
      await snapshot(row, '-' + slug(heading));
    }
  });
  await disclose('Appearance folder');
  await disclose('Advanced customization');
  await step('appearance.editor.validation-private-draft', async row => {
    await j.fill('Appearance name', 'Unsubmitted layout pass two');
    await j.fill('Reference CSS delta', '#amp-one { --a-accent: #5140ef; }');
    await expect(field('Reference CSS delta')).toHaveValue('#amp-one { --a-accent: #5140ef; }');
    await snapshot(row, '-private-editor');
    // Revert is editor-only for an unsaved draft. Do not preview/save/upload.
    await activate('Revert editor draft', button('Revert editor changes'));
    return {saved: false, previewed: false};
  });
  await step('appearance.library.saved-entry-actions', async row => {
    const groups = content().getByRole('group', {name: /^Actions for /});
    if (!await groups.count()) throw new NotPresent('No saved ZIP entries in the actual disposable fixture; save/import/delete not manufactured.');
    const name = 'Layout pass two synthetic saved look';
    const group = content().getByRole('group', {name: 'Actions for ' + name, exact: true});
    await expect(group).toBeVisible();
    row.synthetic = {kind: 'fixture-owned format-2 ZIP', id: 'saved:layout-pass2-synthetic.zip'};
    await activate('Edit synthetic saved appearance (read only)', group.getByRole('button', {name: 'Edit ' + name, exact: true}));
    await expect(field('Appearance name')).toHaveValue(name);
    await expect(button('Update saved appearance')).toBeVisible();
    await snapshot(row, '-seeded-saved-editor');
    await activate('Review synthetic appearance deletion', group.getByRole('button', {name: 'Delete ' + name, exact: true}));
    const confirmation = content().getByRole('group', {name: 'Delete appearance confirmation', exact: true});
    await expect(confirmation).toBeVisible();
    await expect(confirmation.getByRole('button', {name: 'Confirm delete', exact: true})).toBeVisible();
    await snapshot(row, '-delete-confirmation-before-cancel');
    await activate('Cancel synthetic appearance deletion', confirmation.getByRole('button', {name: 'Cancel deletion', exact: true}));
    await expect(confirmation).not.toBeVisible();
    await expect(group).toBeVisible();
    return {groups: await groups.count(), editedByRead: true, deleteCancelled: true,
      savedOrUpdated: false, exported: false, deleted: false};
  });
}


async function updates(j) {
  const {page, step, activate, button, disclose, content, fence, snapshot} = j;
  await disclose('Important changes');
  await disclose('Changelog');
  await disclose('Advanced details');
  await disclose('Update preferences');
  await disclose('Installation history');
  await disclose('Previous failed attempts');
  await step('updates.diagnostic-receipt', async () => {
    const show = button('View update details');
    if (await visible(show).count()) await activate('View sanitized update receipt', show);
    await expect(content().getByLabel('Sanitized receipt — select and copy to share', {exact: true})).toBeVisible();
  });
  await step('updates.support.pending-copy-geometry', async row => {
    row.synthetic = {report: true, clipboard: true};
    fence.holdDiagnostics = true;
    await page.evaluate(() => {window.layoutPass2Clipboard.mode = 'denied';});
    try {
      await activate('Copy diagnostics (held synthetic read)', button('Copy diagnostics'));
      await expect.poll(() => fence.pending.length).toBeGreaterThan(0);
      await expect(button('Copy diagnostics')).toBeDisabled();
      await expect(button('Save diagnostics')).toBeDisabled();
      // Capture real rendered padding, glyph/icon boxes, gap and focus while pending.
      await snapshot(row, '-pending');
    } finally {
      fence.holdDiagnostics = false; fence.pending.splice(0).forEach(release => release());
    }
    const manual = content().getByRole('textbox', {name: 'Update diagnostic report', exact: true});
    await expect(manual).toBeVisible();
    assert.equal(JSON.parse(await manual.inputValue()).synthetic, true);
    await snapshot(row, '-clipboard-denied');
  });
  await step('updates.support.copy-success', async row => {
    row.synthetic = {report: true, clipboard: true};
    await page.evaluate(() => {window.layoutPass2Clipboard.mode = 'success';});
    await activate('Copy diagnostics (synthetic clipboard)', button('Copy diagnostics'));
    await expect(content().getByRole('status').filter({hasText: 'Diagnostics copied.'})).toBeVisible();
    row.clipboard = await page.evaluate(() => {
      const text = window.layoutPass2Clipboard.last;
      return {writes: window.layoutPass2Clipboard.writes, bytes: new TextEncoder().encode(text).length,
        synthetic: JSON.parse(text).synthetic};
    });
    assert.equal(row.clipboard.synthetic, true);
  });
  await step('updates.support.download', async row => {
    row.synthetic = {report: true};
    const [download] = await Promise.all([page.waitForEvent('download'),
      activate('Save diagnostics (synthetic read)', button('Save diagnostics'))]);
    assert.equal(download.suggestedFilename(), 'amplifier-update-diagnostics.json');
    const filename = slug(j.job.id) + '-amplifier-update-diagnostics.json';
    await download.saveAs(join(output, filename));
    const bytes = await readFile(join(output, filename));
    assert.equal(JSON.parse(bytes).synthetic, true);
    row.download = {filename, suggestedFilename: download.suggestedFilename(), bytes: bytes.length, sha256: hash(bytes)};
    await expect(content().getByRole('status').filter({hasText: 'Diagnostic report saved.'})).toBeVisible();
  });
  await step('updates.sources.expanded-and-filtered', async row => {
    const show = content().getByRole('button', {name: /^Show all \d+ sources?$/});
    await activate('Show actual update sources', show);
    const filter = content().getByRole('searchbox', {name: 'Filter update sources', exact: true});
    await expect(filter).toBeVisible(); await filter.fill('fixture-source-1');
    await snapshot(row, '-filtered'); await filter.fill('');
  });
  await step('updates.disclosure.Source-details', async row => {
    // Each source may appear in available-updates and update-sources. They are
    // distinct intentional disclosure instances, not a uniqueness exception.
    const details = visible(content().locator('.a-source-roles > summary'));
    const count = await details.count();
    if (!count) throw new NotPresent('No actual source package disclosures in the rendered source lists.');
    row.instances = [];
    for (let index = 0; index < count; index++) {
      const control = details.nth(index);
      const identity = await control.evaluate(node => {
        const source = node.closest('li'), list = source?.parentElement;
        return {listId: list?.id || null,
          sourceLabel: source?.querySelector('.a-source-line strong')?.getAttribute('title') || null,
          summary: node.textContent.trim()};
      });
      assert.ok(identity.listId && identity.sourceLabel, 'Each source disclosure has its rendered list/source identity.');
      const opened = await j.expandDisclosure(identity.sourceLabel + ' in ' + identity.listId, control);
      row.instances.push({...identity, ...opened});
      await snapshot(row, '-source-' + index + '-' + slug(identity.listId));
    }
    assert.equal(row.instances.length, count);
  });
  await disclose('Older conversation settings');
}

async function optimization(j) {
  await j.step(j.job.route + '.optimization.conditional-drafts', async row => {
    const section = j.content().getByRole('region', {name: 'Optimize saved session storage', exact: true});
    row.readSeed = {mode: optimizationReadMode,
      source: optimizationReadMode === 'synthetic' ? optimizationReadSource : 'actual installed host read'};
    for (const kind of ['request', 'response']) {
      const input = section.getByRole('checkbox', {
        name: `Expire raw ${kind} diagnostics during this manual optimization`, exact: true});
      await expect(input).toBeVisible();
      // Wait only for the read to settle. A disabled policy/ownership/recovery
      // branch remains evidence, not permission to force-enable an input.
      await expect(section.getByText('Reading or submitting saved storage work…', {exact: true})).toBeHidden();
      if (!await input.isEnabled()) {
        row.disabledPreconditions = await section.evaluate(node => ({
          alerts: [...node.querySelectorAll('[role=alert]')].map(item => item.textContent.trim()),
          fieldsets: [...node.querySelectorAll('fieldset')].map(item => ({
            legend: item.querySelector('legend')?.textContent, disabled: item.disabled})),
        }));
        row.reasonClass = 'actual-disabled-optimization-branch';
        throw new NotPresent('Optimization controls remain disabled after the read: ' +
          JSON.stringify(row.disabledPreconditions) + '; no force or operation creation attempted.');
      }
      const before = await input.isChecked();
      await input.setChecked(!before); await j.snapshot(row, '-' + kind + '-expiry-draft');
      await input.setChecked(before);
    }
    // Native select is nested in its label; option text contributes to the
    // browser's observed accessible name. Retain a unique prefix, not a fake ID.
    const scope = section.getByRole('combobox', {name: /^Saved sessions to review/});
    const before = await scope.inputValue();
    await scope.selectOption('all'); await j.snapshot(row, '-explicit-all-scope-draft');
    await scope.selectOption(before);
    return {choicesSaved: false, previewRequested: false, optimizationApplied: false};
  });
  await j.step(j.job.route + '.optimization.saved-result-branch', async () => {
    const outcomes = j.summary('File outcomes');
    if (!await visible(outcomes).count())
      throw new NotPresent('No saved optimization in this disposable fixture; no preview/apply/recovery operation created to manufacture results.');
    await j.activate('Read existing file outcomes disclosure', outcomes);
  });
}

// Branches requiring meaningful effect-producing setup stay in the denominator.
// Their controls may already be observed in raw snapshots, but that is NOT the
// corresponding pending/result/confirmation state being interaction-qualified.
const conditionalScenes = {
  'ai-connections': [
    ['credential-and-signin', ['API base URL', 'API key', 'Copy sign-in code', 'Continue with ChatGPT',
      'Cancel sign-in', 'Try sign-in again', 'Enable ChatGPT plan access'],
    'No credential-service selection or sign-in initiated. Requires dedicated synthetic authentication states; host credential discovery is fenced.'],
    ['decision-save-feedback', ['Save selection', 'Discard changes'],
    'Decision preferences can be inspected/drafted; no provider support installation or decision-selection save performed.'],
  ],
  'external-agents': [
    ['existing-agent-edit-and-pending', ['Edit', 'Remove connection', 'Discard edits and reload', 'Check the same change'],
    'Fresh base fixture has no saved external-agent binding; no save/remove/unknown mutation is manufactured.'],
  ],
  appearance: [
    ['library-preview-import-results', ['Preview draft', 'Cancel preview', 'Upload ZIP package',
      'Confirm delete', 'Cancel deletion', 'Update saved appearance', 'Export ZIP', 'Retry'],
    'Saved ZIP actions and Delete/Cancel now have a separate synthetic-seeded scene. Import/save/update/export/preview/error result states remain unvisited; no effects manufactured.'],
  ],
  voice: [
    ['configured-voice-and-audio', ['Change voice connection', 'Preview voice', 'Preview fallback voice'],
    'Configured public connection and fallback fields are separately synthetic-seeded. Audio/sample, key-save feedback and call/error states remain unvisited; no key or provider request.'],
  ],
  notifications: [
    ['permission-and-save-feedback', ['Allow browser notifications', 'Save phone connection', 'Copy topic name'],
    'No OS/browser permission requested, phone connection saved or notification delivered. Permission/transport failure branches are not inferred.'],
  ],
  diagnostics: [
    ['destination-delivery-results', ['Test connection', 'Retry failed deliveries', 'Remove destination', 'Export this page', 'Older records'],
    'No server destination saved/tested, queued records sent or export requested; pagination depends on actual fixture records.'],
  ],
  history: [
    ['import-and-cleanup-results', ['Choose a conversation', 'Preview cleanup',
      'Also remove retained legacy app files (shared CLI history is kept)', 'Clean up these conversations'],
    'No transcript imported or cleanup invoked; parsed import and eligible-cleanup results require disposable retained-history seeds.'],
  ],
  storage: [
    ['destructive-review-confirmations', ['Review workspace file cleanup', 'Review chat cleanup',
      'Review session deletion', 'Review deletion of all project sessions', 'Review backup contents',
      'Permanently delete workspace files', 'Permanently delete backup'],
    'Synthetic backup confirmation/Keep is separately exercised without a host token. Workspace/chat/shared-history deletion confirmations remain unvisited; no deletion performed.'],
  ],
  testing: [
    ['connected-runner-results', ['Pause new checks', 'Allow new checks', 'Check connection and controller',
      'Cancel connection check', 'Check saved result', 'Advanced environment limits'],
    'Synthetic runner-specific budget/limits fields are separately exercised. Enrollment progress, cancellation/reconciliation and enable/save results remain unvisited; no SSH or guest.'],
  ],
  providers: [
    ['credential-test-and-signin-results', ['Provider sign-in', 'Reconnect account', 'Test connection',
      'Send test message', 'Check variable', 'Save provider', 'Remove'],
    'Only named fixture reads and draft edits are admitted. No account, credential, test message or provider mutation executed.'],
  ],
  'app-bundles': [
    ['mutation-results', ['Remove bundle', 'Enable'],
    'Configured rows/order are inspectable; no bundle enabled, removed or composition committed.'],
  ],
  'add-bundles': [
    ['discovery-results', ['Filter discovered bundles', 'Use this bundle as', 'Bundle alias', 'Add capability', 'Register bundle'],
    'No repository browse/fetch or bundle addition initiated. Discovery result states require an explicit synthetic discovery seed.'],
  ],
  registries: [
    ['validation-results', ['Validate core contract', 'Also run module behavior tests', 'Validate without saving',
      'Validate & save source', 'Save source override', 'Use composed policy for new chats'],
    'Module/source validation can execute candidate code; no validate/save/reconcile performed to create results.'],
  ],
  'smart-tools': [
    ['installed-and-account-results', ['Finish setup', 'Open setup instructions', 'Installation details',
      'Account identity', 'Accept this account change', 'Retry unfinished installations'],
    'Synthetic installed-package details and MCP/schema scenes are separately exercised. Account changes/OAuth/installation/retry results remain unvisited; no real installation or connection.'],
  ],
  'tool-connections': [
    ['tool-schema-and-result', ['Load tool fields', 'Edit arguments as JSON', 'Use form fields',
      'Run tool', 'Open interactive view', 'Refresh tool definitions'],
    'Preloaded synthetic MCP schema/form/JSON are separately exercised. Tool execution/result/interactive-view states remain unvisited; no MCP transport or tool call.'],
  ],
  updates: [
    ['lifecycle-and-error-variants', ['View work delaying the update', 'Interrupt work and restart',
      'Restore previous components', 'Retry this tool', 'Skip this version'],
    'Seed includes healthy status, historical failure and support report. No update/check/install/rollback/restart or pending-work lifecycle effect is run.'],
  ],
  repair: [
    ['installation-preview-and-recovery', ['Preview installation repair', 'Confirm installation repair',
      'Verify repaired installation', 'Reconcile unfinished repair', 'Check reconciliation status'],
    'No installation/runtime repair, health recovery or private backup created. Unsupported baseline is not a supported/unknown repair-state pass.'],
  ],
  reset: [
    ['reset-preview-and-results', ['Type RESET to apply this selection', 'Apply selected reset', 'Retry older reset backup cleanup'],
    'Synthetic runtime preview/confirmation/clear is separately exercised. Applied-reset/retained recovery/cleanup outcomes remain unvisited; no reset operation created.'],
  ],
};

async function diagnosticAuthenticationDraft(j, row) {
  const {content, field, fill, requireControl, snapshot} = j;
  await expect(j.page.locator('.a-settings-experience')).toHaveAttribute('data-settings-route', 'diagnostics/destination');
  const original = {name: await field('Name').inputValue(), url: await field('Server URL').inputValue()};
  const auth = await requireControl(content().getByRole('combobox', {name: /^Authentication/}));
  const originalMode = await auth.inputValue();
  row.authenticationLocator = {role: 'combobox', namePrefix: 'Authentication', visibleUnique: true};
  try {
    await fill('Name', 'Unsubmitted layout destination');
    await fill('Server URL', 'not-a-url');
    row.validity = await field('Server URL').evaluate(node => ({
      valid: node.checkValidity(), typeMismatch: node.validity.typeMismatch}));
    assert.equal(row.validity.typeMismatch, true);
    await expect(field('Send new selected records to this server')).not.toBeChecked();
    await snapshot(row, '-invalid-url-disabled-destination');
    await auth.selectOption('entra');
    const resource = field('Entra resource URI');
    await expect(resource).toBeVisible();
    await resource.fill('api://layout-not-contacted');
    await snapshot(row, '-entra-private-draft');
    await resource.fill('');
    await auth.selectOption('static');
    await expect(field('API key environment variable')).toBeVisible();
    await snapshot(row, '-static-variable-name-no-check');
  } finally {
    if (await auth.isVisible().catch(() => false)) await auth.selectOption(originalMode);
    await fill('Name', original.name);
    await fill('Server URL', original.url);
  }
  return {saved: false, tested: false, variableChecked: false, forwardingEnabled: false,
    draftStringsRestored: true};
}

// Exactly five gap scenes across four routes. Callbacks are also used for
// declaration without execution; never navigate or seed outside a callback.
async function finalGapRecipe(j) {
  const {job} = j;
  const checkpoint = async (row, since) => {
    const requests = ledger.requests.slice(since).filter(item => item.job === job.id);
    row.requestCheckpoint = requests;
    const unsafe = /^(?:updates\.(?:check|install|rollback|forceRestart|featureInstall)|diagnostics\.(?:configure|test|environment|retry|export)|testing\.(?:capacity\.save|machine\.save|runners\.(?:register|refresh|enable|enrollment\.))|conversation\.|session\.(?:create|select)|smartTools\.(?:call|connect|reconnect|install)|voice\.(?:preview|configure))/;
    assert.deepEqual(requests.filter(item => unsafe.test(item.action || '')), [],
      'Final-gap scene must not even attempt an effect-producing control.');
    assert.equal(requests.filter(item => item.disposition === 'blocked-before-host').length, 0,
      'Unexpected fenced request is evidence of a bad precondition, not successful gap coverage.');
    row.noEffectCheckpoint = {unsafeRequestsAttempted: 0, pixelInspected: false};
    await j.snapshot(row, '-completed-visible-checkpoint');
  };
  if (job.route === 'updates') {
    await j.step('updates.final-gap.installation-history', async row => {
      const since = ledger.requests.length;
      row.synthetic = {seed: 'layout-pass2-synthetic-completed', realCompletedOperation: false};
      const advanced = j.content().locator('[data-part=ecosystem-update-details]');
      await j.expandDisclosure('Advanced details for synthetic history', advanced.locator(':scope > summary'));
      const history = advanced.locator('[data-part=update-completed-batches]');
      await expect(history).toHaveCount(1);
      await j.expandDisclosure('Installation history', history.locator(':scope > summary'));
      await expect(history.locator('li')).toContainText('Included components installed at');
      await expect(history.locator('li')).toContainText('synthetic-layout-component');
      await history.scrollIntoViewIfNeeded();
      row.observedText = await history.innerText();
      await checkpoint(row, since);
    });
    await j.step('updates.final-gap.older-conversation-settings', async row => {
      const since = ledger.requests.length;
      row.synthetic = {seed: 'layout-pass2-synthetic-history', importedOrChangedConversation: false};
      const history = j.content().locator('[data-part=historical-source-settings]');
      await expect(history).toHaveCount(1);
      await j.expandDisclosure('Older conversation settings', history.locator(':scope > summary'));
      const list = history.locator('#historical-source-settings-list');
      await expect(list).toBeVisible();
      await expect(list).toContainText('layout-pass2-older-settings');
      await expect(list).toContainText('Synthetic older conversation settings, presentation only; no conversation changed.');
      await list.scrollIntoViewIfNeeded();
      row.observedText = await history.innerText();
      await checkpoint(row, since);
    });
  } else if (job.route === 'diagnostics') {
    await j.step('diagnostics.final-gap.authentication-entra-draft', async row => {
      const since = ledger.requests.length;
      row.synthetic = {destination: 'unsaved default-disabled UI draft; not a saved server'};
      await j.activate('Add unsaved diagnostic destination', j.button('Add server'));
      try {
        row.draftCheckpoint = await diagnosticAuthenticationDraft(j, row);
      } finally {
        if (await j.page.locator('.a-settings-experience').getAttribute('data-settings-route') === 'diagnostics/destination') {
          if (job.width < 960)
            await j.activate('Back to diagnostic overview without Save', j.page.locator('.a-settings-mobile-head')
              .getByRole('button', {name: 'Back to App diagnostic capture', exact: true}));
          else await j.activate('All destinations without Save', j.button('All destinations'));
        }
      }
      await expect(j.page.locator('.a-settings-experience')).toHaveAttribute('data-settings-route', 'diagnostics');
      await checkpoint(row, since);
    });
  } else if (job.route === 'desktop') {
    await j.step('desktop.final-gap.static-technical-environment', async row => {
      const since = ledger.requests.length;
      row.synthetic = {nullSessionReport: true, nativeOrHostProbe: false};
      await j.activate('Read exact synthetic global Desktop report', j.button('Check desktop and browser setup'));
      const native = j.content().getByRole('region', {name: 'Native screen observation', exact: true});
      await expect(native).toBeVisible();
      await expect(native).toContainText('Static seed, not native observation.');
      await j.expandDisclosure('Technical environment details (static)', native.locator('summary')
        .filter({hasText: /^Technical environment details$/}));
      await expect(native).toContainText('Synthetic layout host');
      await expect(native).toContainText('/synthetic/not-executed/python');
      await expect(native).toContainText('Python 3.13');
      row.observedText = await native.innerText();
      await native.scrollIntoViewIfNeeded();
      const reads = ledger.requests.slice(since).filter(item => item.action === 'desktop.readiness');
      assert.equal(reads.length, 1);
      assert.equal(reads[0].disposition, 'synthetic-route-fulfillment');
      await checkpoint(row, since);
    });
  } else if (job.route === 'testing') {
    await j.step('testing.final-gap.global-runner-overrides', async row => {
      const since = ledger.requests.length;
      row.synthetic = {topLevelCapacityRunnerId: runnerSeed.id, remoteContactOrSave: false};
      const section = j.content().getByRole('region', {name: 'Testing and resources', exact: true});
      await expect(section).toContainText('Synthetic layout runner; not contacted.');
      const advanced = section.locator('details').filter({
        has: j.page.getByText('Advanced limits', {exact: true})});
      await expect(advanced).toHaveCount(1);
      await j.expandDisclosure('Global Advanced limits', advanced.locator(':scope > summary'));
      const overrides = advanced.locator('details');
      await expect(overrides).toHaveCount(1);
      await j.expandDisclosure('Overrides for this runner connection', overrides.locator(':scope > summary'));
      const group = overrides.getByRole('group', {name: 'Runner overrides', exact: true});
      await expect(group).toHaveCount(1);
      const input = group.getByRole('spinbutton', {name: 'Running test environments', exact: true});
      await expect(input).toHaveValue('');
      try {
        await input.fill('3');
        await expect(input).toHaveValue('3');
        await expect(j.button('Save testing limits')).toBeEnabled();
        await input.scrollIntoViewIfNeeded();
        await j.snapshot(row, '-runner-overrides-draft');
      } finally {await input.fill('');}
      await expect(j.button('Save testing limits')).toBeDisabled();
      row.observedGroup = await group.innerText();
      row.capacityRead = ledger.requests.filter(item => item.job === job.id && item.action === 'testing.capacity.read');
      assert.equal(row.capacityRead.length, 1);
      assert.equal(row.capacityRead[0].disposition, 'synthetic-route-fulfillment');
      await checkpoint(row, since);
    });
  }
}

async function recipe(j) {
  const {job, page, step, disclose, click, activate, button, field, content, fill, picker, snapshot} = j;
  switch (job.route) {
    case 'advanced':
      for (const name of ['Bundles & modules', 'Performance & runtime', 'Troubleshooting & recovery']) await disclose(name);
      break;
    case 'privacy':
      await step('privacy.destination-controls', async () => {
        const links = content().locator('[data-settings-destination]:visible');
        assert.ok(await links.count() >= 7); return {destinations: await links.evaluateAll(nodes =>
          nodes.map(node => ({page: node.dataset.settingsDestination, name: node.textContent.trim()})))};
      }); break;
    case 'appearance': await appearance(j); break;
    case 'updates': await updates(j); break;
    case 'ai-connections':
      await disclose('Decision models (optional)');
      await disclose('Add a decision provider');
      await step('ai-connections.connection.detail', async () => {
        await activate('Open first actual fixture connection', content().locator('[data-part=ai-connections] .a-everyday-list button').first());
        await expect(button('Choose model')).toBeVisible();
      });
      await disclose('Account and connection checks');
      await click('Choose model');
      await disclose('More options');
      await step('ai-connections.model.manual-draft', async () => {
        await fill('Model ID', 'unsubmitted-fixture-model'); return {saved: false};
      });
      await step('ai-connections.services.visible-entry', async row => {
        // Re-enter the page via actual controls; no shared-view dispatch reset.
        await openRoute(page, 'ai-connections', activate);
        await activate('Connect another service', button('Connect another service'));
        await expect(content().getByRole('heading', {name: 'Which service would you like to use?', exact: true})).toBeVisible();
        await snapshot(row, '-services');
        return {credentialServiceSelection: 'Not invoked: selection can discover host credentials internally.'};
      }); break;
    case 'external-agents':
      await click('Add external agent');
      await disclose('Working directories');
      await step('external-agents.validation.required', async () => {
        await expect(button('Save connection')).toBeDisabled();
        await fill('Name', 'Unsubmitted fixture agent');
        await expect(button('Save connection')).toBeDisabled();
        await fill('Command', '/fixture/not-executed');
        await fill('Arguments — one per line', 'acp\n--fixture-only');
      });
      await j.unsaved('Name', 'Retained unsaved fixture agent');
      await click('Cancel');
      break;
    case 'workspaces':
      await picker(/^Default workspace folder on /);
      await j.unsaved(/^Default workspace folder on /, '/fixture/not-saved');
      break;
    case 'workspace-starters':
      await step('workspace-starters.filter', async row => {
        const search = content().getByRole('searchbox', {name: 'Find a starter', exact: true});
        await expect(search).toBeVisible(); await search.fill('no-match-layout-pass2');
        await expect(content().getByText('No matching starters.', {exact: true})).toBeVisible();
        await snapshot(row, '-no-matches'); await search.fill('');
      });
      await step('workspace-starters.actual-editor', async () => {
        await activate('Open actual starter', content().locator('[data-collection-id]>button').first());
        await expect(content().getByRole('form', {name: 'Starter editor', exact: true})).toBeVisible();
      });
      for (const name of ['Repositories', 'Guidance & working memory', 'Resource tracking', 'Local workspace Git']) await disclose(name);
      await click('Add repository');
      await step('workspace-starters.repository.validation', async row => {
        const input = await fill('Git URL 1', '');
        row.validity = await input.evaluate(node => ({valid: node.checkValidity(), valueMissing: node.validity.valueMissing}));
        await fill('Repository folder 1', 'draft-child-folder');
        await snapshot(row, '-repository-draft');
        await activate('Remove unsaved repository row', button('Remove repository 1'));
      });
      await click('Cancel edits');
      break;
    case 'voice':
      await disclose('Change voice connection');
      await step('voice.key-validation', async row => {
        row.synthetic = {configuredPublicRead: true, actualKey: false};
        await expect(content().getByRole('region', {name: 'Voice connection', exact: true}))
          .toContainText('Voice key configured');
        const separate = content().getByRole('radio', {name: 'Use a separate key', exact: true});
        await separate.check();
        await expect(field('OpenAI API key')).toBeVisible();
        await expect(button('Save voice connection')).toBeDisabled();
        await snapshot(row, '-private-key-blank-disabled');
        await content().getByRole('radio', {name: 'Use the key on this host (OPENAI_API_KEY)', exact: true}).check();
        return {configuredSceneOnly: true, keyEnteredOrSaved: false};
      });
      await click('Choose a voice');
      await step('voice.picker.search', async row => {
        await fill('Find a voice', 'no-matching-voice-layout-pass2');
        await expect(content().getByText('No voices match your search.', {exact: true})).toBeVisible();
        await snapshot(row, '-no-match'); await fill('Find a voice', '');
      });
      await click('Close voice list');
      await disclose('If the preferred model is unavailable');
      await step('voice.fallback-controls', async row => {
        row.synthetic = {configuredPublicRead: true, seededPreference: 'gpt-live-1 / marin'};
        await expect(field('Fallback model')).toBeVisible();
        await expect(field('Fallback speaking voice')).toBeVisible();
        await expect(button('Preview fallback voice')).toBeVisible();
        await field('Fallback speaking voice').scrollIntoViewIfNeeded();
        await snapshot(row, '-seeded-fallback-lower');
        // No selection or preview: fallback choices auto-save; samples are paid.
        return {selectionChanged: false, speakingVoiceShown: true, sampleRequested: false};
      });
      await disclose('About these voices'); break;
    case 'notifications':
      await disclose('Set up phone notifications');
      await disclose('Server & account');
      await step('notifications.private-draft-and-cancel', async row => {
        await fill(/^Topic name/, 'synthetic-layout-topic-not-sent');
        await fill(/^Access token/, 'synthetic-invalid-layout-token');
        await snapshot(row, '-private-draft');
        await fill(/^Topic name/, ''); await fill(/^Access token/, '');
        return {saved: false, browserPermissionRequested: false};
      }); break;
    case 'permissions':
      await picker('Allowed folders (one per line)');
      await picker('Denied folders (one per line)'); break;
    case 'access-approvals':
      await step('access-approvals.draft-not-granted', async row => {
        const input = field('Access mode'), original = await input.inputValue();
        await input.selectOption('ask'); await snapshot(row, '-ask-draft');
        await input.selectOption(original); return {saved: false, granted: false};
      }); break;
    case 'desktop':
      await disclose('Add browser support');
      await click('Check desktop and browser setup');
      await disclose('Technical environment details');
      await disclose('Computer desktop');
      await step('desktop.no-capture', async () => ({captureRequested: false,
        note: 'Capture desktop, install, Open, Stop/Resume and watch/input are intentionally not activated.'}));
      break;
    case 'diagnostics':
      await step('diagnostics.retention.invalid-draft', async row => {
        const input = field('Index days'), original = await input.inputValue();
        await input.fill('0');
        row.validity = await input.evaluate(node => ({valid: node.checkValidity(), rangeUnderflow: node.validity.rangeUnderflow}));
        await snapshot(row, '-invalid'); await input.fill(original);
        // Restore before blur: valid changes auto-save here.
      });
      await click('Add server');
      await step('diagnostics.destination.private-draft', async row => {
        return diagnosticAuthenticationDraft(j, row);
      });
      await step('diagnostics.destination.back', async () => {
        if (job.width < 960)
          await activate('Back to diagnostic overview', page.locator('.a-settings-mobile-head button').first());
        else await activate('All destinations', button('All destinations'));
      });
      await click('Read records');
      break;
    case 'history':
      await disclose('Advanced import options');
      await step('history.import.validation-without-upload', async row => {
        await fill('Conversation title (optional)', 'Unsubmitted import specimen');
        await snapshot(row, '-import-draft');
        await fill('Conversation title (optional)', '');
        return {uploadPerformed: false, cleanupPreviewInvoked: false};
      }); break;
    case 'storage':
      await optimization(j);
      await disclose('Retention and size');
      await step('storage.cleanup.conditional-draft-fields', async row => {
        for (const name of ['Expire temporary test workspace copies', 'Expire saved operation output']) {
          const input = field(name), before = await input.isChecked();
          await input.setChecked(!before); await snapshot(row, '-' + slug(name));
          await input.setChecked(before);
        }
        return {saved: false};
      });
      await click('Review unused copies');
      await disclose('What stays protected');
      await click('Review saved test sizes');
      for (const name of ['Workspace files', 'Session data', 'App & runtime', 'Backups',
        'Caches & logs', 'Settings & other data']) {
        await step('storage.category.' + slug(name), async row => {
          const category = content().locator('.a-storage-categories>li>button')
            .filter({has: page.getByText(name, {exact: true})});
          await activate('Inspect actual storage category ' + name, category);
          await expect(field('Filter storage')).toBeVisible();
          await field('Filter storage').fill('no-match-layout');
          await snapshot(row, '-filtered'); await field('Filter storage').fill('');
          await activate('Back through Storage breadcrumb', content()
            .getByRole('navigation', {name: 'Storage navigation', exact: true})
            .getByRole('button', {name: 'Storage', exact: true}));
        });
      }
      await step('storage.seeded-backup-review-cancel', async row => {
        row.synthetic = {inventory: true, preview: true, hostDeletionToken: false};
        j.fence.storageReview = true;
        const review = content().getByRole('region', {name: 'Backup cleanup', exact: true});
        try {
          await activate('Refresh to explicitly synthetic backup scene', button('Refresh storage'));
          const backups = content().locator('.a-storage-categories>li>button')
            .filter({has: page.getByText('Backups', {exact: true})});
          await activate('Open synthetic backup category', backups);
          await activate('Review synthetic older backup', content().getByRole('button', {
            name: 'Review cleanup for Synthetic older backup', exact: true}));
          await expect(review).toBeVisible();
          await activate('Read synthetic backup contents review', review.getByRole('button', {name: 'Review backup contents', exact: true}));
          await expect(review.getByRole('button', {name: 'Permanently delete backup', exact: true})).toBeVisible();
          await snapshot(row, '-confirmation-no-delete');
          await activate('Keep synthetic backup', review.getByRole('button', {name: 'Keep backup', exact: true}));
          await expect(review).not.toBeVisible();
          return {reviewCancelled: true, deleteRequested: false};
        } finally {
          // Cancellation only; a failed confirmation scene cannot proceed to a
          // destructive action in cleanup or a subsequent recipe.
          const keep = review.getByRole('button', {name: 'Keep backup', exact: true});
          if (await keep.isVisible().catch(() => false)) await activate('Keep backup during scene exit', keep);
          j.fence.storageReview = false;
        }
      });
      break;
    case 'testing':
      for (const name of ['Advanced limits', 'Overrides for this runner connection', 'Advanced machine budgets',
        'Connect a test machine', 'Advanced connection settings', 'Advanced execution limits']) await disclose(name);
      await step('testing.runner.invalid-private-draft', async row => {
        await fill('Name to show', 'Unsubmitted test machine');
        await expect(button('Connect and check')).toBeDisabled();
        await fill('Saved SSH connection', 'never-connect-layout-specimen');
        await snapshot(row, '-connection-draft');
        await fill('Name to show', ''); await fill('Saved SSH connection', '');
        return {remoteConnectionOrGuestStarted: false};
      });
      await step('testing.seeded-runner-specific-limits', async row => {
        row.synthetic = {runner: runnerSeed.id, remoteContact: false};
        const summary = content().locator('summary').filter({
          hasText: /^Layout pass two synthetic runner · Paused for new checks$/});
        await j.expandDisclosure('Synthetic paused runner', summary);
        const runner = summary.locator('..');
        const limits = runner.getByRole('region', {name: runnerSeed.name + ' environment limits', exact: true});
        await j.expandDisclosure('Synthetic runner environment limits', limits.locator('summary'));
        const max = limits.getByLabel(runnerSeed.name + ': Maximum active test environments', {exact: true});
        await expect(max).toBeEnabled();
        await max.fill('7');
        await snapshot(row, '-environment-draft');
        await activate('Discard synthetic environment-limit draft', limits.getByRole('button', {
          name: 'Discard environment edits and refresh', exact: true}));
        await expect(max).toHaveValue('');
        const resources = runner.getByRole('region', {name: 'Shared machine resources', exact: true});
        await j.expandDisclosure('Synthetic runner machine budgets', resources.locator('summary'));
        const cpu = resources.getByLabel('Reserved CPU cores', {exact: false});
        await cpu.fill('6');
        await snapshot(row, '-machine-budget-draft');
        await activate('Discard synthetic machine-budget draft', resources.getByRole('button', {
          name: 'Discard machine edits and refresh', exact: true}));
        await expect(cpu).toHaveValue('');
        return {runnerRegistered: false, checked: false, enabled: false, budgetsSaved: false};
      });
      await step('testing.execution.downpage-sections', async row => {
        const execution = content().getByRole('region', {name: 'Test execution limits', exact: true});
        const sets = execution.locator('fieldset');
        for (let index = 0; index < await sets.count(); index++) {
          await sets.nth(index).scrollIntoViewIfNeeded();
          await snapshot(row, '-fieldset-' + index);
        }
        return {fieldsetsObserved: await sets.count(), settingsSaved: false};
      }); break;
    case 'model-capacity':
      await click('Use unlimited waiting');
      await j.unsaved('Maximum waiting model calls (0 means no count limit)', '17');
      await click('Discard edits and use saved settings'); break;
    case 'providers':
      await step('providers.actual-detail', async () => {
        await activate('Open actual fixture provider', content().locator('[data-part=provider-settings] [data-collection-id]>button').first());
        await expect(field('Provider instance name')).toBeVisible();
      });
      await disclose('Provider & access');
      await click('Show advanced configuration');
      await step('providers.json.invalid-draft', async row => {
        const input = field('Provider configuration (JSON)'), original = await input.inputValue();
        await input.fill('{');
        await expect(button('Save provider')).toBeDisabled();
        await snapshot(row, '-invalid-json'); await input.fill(original);
      });
      await picker('Module source (optional)');
      await step('providers.preference-order', async row => {
        // Compact detail hides toolbar. Return via explicit hierarchy before order.
        if (job.width < 960) await activate('Back to provider list', page.locator('.a-settings-mobile-head button').first());
        await activate('Provider preference order', button('Preference order'));
        const positions = content().getByRole('combobox', {name: /^Position of /});
        await expect(positions.first()).toBeVisible(); await snapshot(row, '-order');
        await activate('Cancel preference order', button('Cancel'));
      }); break;
    case 'routing':
      await disclose('Profile settings');
      await step('routing.role-and-choice-hierarchy', async row => {
        await activate('Open general role', content().locator('[data-collection-id=general]>button'));
        await snapshot(row, '-role');
        await activate('Open first choice', content().locator('.a-routing-candidates [data-collection-id="0"]>button'));
        await snapshot(row, '-choice');
      });
      await click('Edit choice configuration');
      await step('routing.choice.invalid-json', async row => {
        const input = field('Choice configuration JSON');
        await input.fill('{');
        await activate('Validate unsaved choice JSON', button('Apply choice configuration'));
        await snapshot(row, '-invalid');
        await input.fill('{"provider":"one","model":"fixture-model"}');
      });
      await step('routing.return-to-profile', async () => {
        if (job.width < 960) {
          for (let depth = 0; depth < 3 &&
            await page.locator('.a-settings-experience').getAttribute('data-settings-detail') === 'true'; depth++)
            await activate('Back through routing hierarchy', page.locator('.a-settings-mobile-head button').first());
        }
      });
      await click('Show full profile JSON');
      await step('routing.profile-json', async row => {
        const input = field('Routing matrix JSON'); await expect(input).toBeVisible();
        await input.scrollIntoViewIfNeeded(); await snapshot(row, '-full-profile-json');
        return {profileSavedOrActivated: false};
      }); break;
    case 'app-bundles':
      await step('app-bundles.actual-detail', async () => {
        await activate('Inspect configured bundle', content().locator('[data-settings-page=app-bundles] [data-collection-id]>button').first());
      });
      await step('app-bundles.composition-order', async row => {
        await openRoute(page, 'app-bundles', activate);
        const order = await j.requireControl(button('Composition order'));
        if (!await order.isEnabled()) throw new NotPresent('Actual fixture has fewer than two enabled capabilities; no bundle additions/toggles performed.');
        await activate('Composition order', order); await snapshot(row, '-order');
        await activate('Cancel composition order', button('Cancel'));
      }); break;
    case 'add-bundles':
      await step('add-bundles.empty-validation', async row => {
        await expect(button('Browse')).toBeDisabled();
        const input = await fill('Repository or bundle URL', 'not-a-url');
        row.validity = await input.evaluate(node => ({valid: node.checkValidity(), typeMismatch: node.validity.typeMismatch}));
        await snapshot(row, '-invalid-url'); await input.fill('');
        return {repositoryFetched: false, addPerformed: false};
      }); break;
    case 'defaults':
      await click('Use a bundle source or file');
      await picker('custom-default-bundle-source', () => content().locator('#default-bundle-source'));
      break;
    case 'registries':
      await click('Add module');
      await step('registries.module.invalid-json', async row => {
        const input = field('Module configuration (JSON)'), original = await input.inputValue();
        await input.fill('{'); await expect(button('Save module')).toBeDisabled();
        await snapshot(row, '-invalid-json'); await input.fill(original);
      });
      await picker('Source (optional)');
      await click('Source overrides');
      await step('registries.source-type-drafts', async row => {
        await j.selectDraft('Source type', 'bundle'); await snapshot(row, '-bundle');
        await j.selectDraft('Source type', 'module');
        return {sourceValidatedOrSaved: false};
      });
      await picker('Repository URL or local path'); break;
    case 'smart-tools':
    case 'tool-connections':
      await click('Tool catalog');
      await step(job.route + '.catalog.actual-detail', async row => {
        await expect(content().locator('[data-activity-region=smart-tool-catalog]')).toBeVisible();
        if (job.route === 'smart-tools')
          await activate('Details for actual fixture catalog item', content().getByRole('button', {name: /^Details for /}).first());
        else await activate('Inspect catalog item detail', content().locator('[data-activity-region=smart-tool-catalog] [data-collection-id]>button').first());
        await snapshot(row, '-detail');
      });
      await disclose('Source & installation options');
      await step(job.route + '.catalog.review-without-install', async row => {
        await activate('Select fixture catalog item for review', button('Select for installation'));
        if (job.width < 960)
          await activate('Back to catalog', page.locator('.a-settings-mobile-head button').first());
        await activate('Review installation selection', button('Review & install'));
        await expect(content().getByRole('region', {name: 'Review installation', exact: true})).toBeVisible();
        await snapshot(row, '-review');
        await activate('Cancel installation review', button('Cancel'));
        return {installed: false};
      });
      await step(job.route + '.source-form', async () => {
        await openRoute(page, job.route, activate);
        await activate('Open source draft', button(job.route === 'smart-tools' ? 'Add from GitHub URL' : 'Add from Git'));
      });
      await disclose('Branch & folder options');
      if (job.route === 'tool-connections') {
        await step('tool-connections.Add MCP connection', async () => {
          // tools/source intentionally hides the toolbar, including on wide
          // layouts. Re-enter through visible Advanced → Tool connections first.
          await openRoute(page, 'tool-connections', activate);
          await activate('Add MCP connection from actual home toolbar', button('Add MCP connection'));
          await expect(field('Display name')).toBeVisible();
        });
        await step('tool-connections.transport-branches', async row => {
          await fill('Display name', 'Unsubmitted connector');
          await j.selectDraft('Connection type', 'streamable-http');
          await j.selectDraft('Authorization', 'oauth'); await snapshot(row, '-oauth');
          await j.selectDraft('Authorization', 'environment'); await snapshot(row, '-headers');
          await j.selectDraft('Connection type', 'stdio'); await snapshot(row, '-stdio');
          return {savedOrConnected: false};
        });
        await picker('Working folder (optional)');
      }
      await step(job.route + '.seeded-package.details', async row => {
        row.synthetic = {packageId: 'layout-pass2-package', packageInstalled: false};
        await openRoute(page, job.route, activate);
        await activate('Open synthetic installed-package details',
          content().locator('[data-collection-id="layout-pass2-package"]>button'));
        const detail = content().locator('.a-smart-tools');
        await j.expandDisclosure('Synthetic package installation details',
          detail.locator('summary').filter({hasText: /^Installation details$/}));
        await snapshot(row, '-installation-details');
        return {setupInspected: true, installedOrUninstalled: false};
      });
      await step(job.route + '.seeded-mcp.schema-no-run', async row => {
        row.synthetic = {serverId: 'layout-pass2-mcp', schema: 'layout-pass2-schema', transport: false};
        await openRoute(page, job.route, activate);
        await activate('Open synthetic MCP detail',
          content().locator('[data-collection-id="layout-pass2-mcp"]>button'));
        await expect(content().locator('[data-part=connector-account]')).toContainText('Synthetic identity');
        await snapshot(row, '-account-and-connection');
        await activate('Open preloaded synthetic tool schema', content().locator('.a-smart-list-row').filter({
          has: page.getByText('Synthetic lookup', {exact: true})}));
        const query = content().locator('#smart-tool-field-query');
        await expect(query).toBeVisible();
        await query.fill('Unsaved synthetic lookup draft');
        await snapshot(row, '-schema-form');
        await activate('Edit preloaded schema arguments as JSON', button('Edit arguments as JSON'));
        const json = field('Tool arguments (JSON)');
        await expect(json).toBeVisible();
        await json.fill('{');
        await activate('Validate JSON locally without Run tool', button('Use form fields'));
        await expect(json).toBeVisible();
        await snapshot(row, '-invalid-json-local-error');
        await json.fill('{"query":"Unsaved synthetic lookup draft","limit":5}');
        await activate('Return to schema form without tool execution', button('Use form fields'));
        await expect(query).toHaveValue('Unsaved synthetic lookup draft');
        await expect(button('Run tool')).toBeVisible();
        return {schemaReadSynthetic: true, toolInvoked: false, accountAccepted: false, connected: false};
      });
      break;
    case 'repair':
      await optimization(j);
      await step('repair.supported-controls-without-repair', async () => {
        await expect(button('Create full backup')).toBeVisible();
        await expect(button('Repair runtime')).toBeVisible();
        return {backupRuntimeInstallationRepairInvoked: false};
      }); break;
    case 'reset':
      await step('reset.selection-draft-without-preview-or-apply', async row => {
        const input = field('Rebuild installed runtime');
        await input.check(); await snapshot(row, '-selected'); await input.uncheck();
        await expect(button('Preview selected reset')).toBeDisabled();
        return {previewed: false, resetApplied: false};
      });
      await step('reset.seeded-confirmation-cancel', async row => {
        row.synthetic = {preview: true, operationOrRecoveryPoint: false};
        const selection = field('Rebuild installed runtime');
        const confirmation = field('Type RESET to apply this selection');
        try {
          await selection.check();
          await expect(confirmation).toBeVisible();
          await expect(button('Apply selected reset')).toBeDisabled();
          await confirmation.fill('NOT RESET');
          await expect(button('Apply selected reset')).toBeDisabled();
          await snapshot(row, '-invalid-confirmation');
          await confirmation.fill('RESET');
          await expect(button('Apply selected reset')).toBeEnabled();
          await snapshot(row, '-confirmation-ready-no-apply');
        } finally {
          if (await confirmation.isVisible().catch(() => false)) await confirmation.fill('');
          if (await selection.isChecked()) await selection.uncheck();
        }
        await expect(confirmation).not.toBeVisible();
        return {cancelledBy: 'Clear confirmation and deselect preview parts', resetApplied: false};
      }); break;
    case 'ready-conversations':
      await step('ready-conversations.auto-save-guard', async () => {
        if (!await visible(field('Conversations to keep ready')).count())
          throw new NotPresent('Synthetic settings Runtime does not expose retention; no real runtime started to manufacture it.');
        return {changed: false, reason: 'Blur/Enter auto-saves; census measures without changing readiness.'};
      }); break;
    case 'automation':
    case 'install-app':
      await step(job.route + '.readable-guidance-and-links', async () =>
        ({linksFollowed: false, commandsExecuted: false}));
      break;
    default: throw Error('No explicit second-pass recipe for ' + job.route);
  }
  for (const [name, controls, reason] of conditionalScenes[job.route] || []) {
    await step(job.route + '.conditional.' + name, async row => {
      row.expectedControls = controls;
      row.reasonClass = 'requires-explicit-synthetic-effect-state-seed';
      throw new NotPresent(reason);
    });
  }
}

let fixture, browser, originalError;
try {
  // Predeclare all routes and recipe states BEFORE startup. A startup or visible
  // entry failure cannot silently remove difficult branches from coverage.
  for (const width of [1440, 390, 320, 768]) for (const route of pages) {
    if (width === 320 && !critical.has(route.id) || width === 768 && !nested.has(route.id)) continue;
    const job = {id: width + '.' + route.id, width, route: route.id, title: route.title,
      status: 'unvisited', reason: 'Not attempted', gestures: []};
    ledger.jobs.push(job);
    await declareJourney(job);
  }
  await save();
  for (const name of ['AMPLIFIER_TEST_PYTHON', 'AMPLIFIER_TEST_PACKAGE_ROOT', 'AMPLIFIER_TEST_INSTALLED_PACKAGE'])
    assert.ok(process.env[name] && isAbsolute(process.env[name]), name + ' must be an explicit installed path.');
  assert.equal(await realpath(process.env.AMPLIFIER_TEST_PACKAGE_ROOT),
    await realpath(process.env.AMPLIFIER_TEST_INSTALLED_PACKAGE), 'Both installed-package guards must identify one package.');
  const wheelIdentity = process.env.AMPLIFIER_TEST_WHEEL_IDENTITY || process.env.AMPLIFIER_TEST_INSTALLED_IDENTITY || process.env.WHEEL_IDENTITY;
  assert.ok(wheelIdentity && isAbsolute(wheelIdentity), 'Supply a wheel identity JSON file, not an editable/source assumption.');
  const identityBytes = await readFile(wheelIdentity);
  JSON.parse(identityBytes);
  ledger.identity.wheelIdentity = {path: wheelIdentity, sha256: hash(identityBytes),
    status: 'supplied-identity-file; runtime asset/import checks recorded separately'};
  // stage_web_test_harness carries selected client helpers, not a second source
  // delivery tree. Bind those exact staged bytes and the installed component.
  for (const path of ['.build/web-client/tests/browser-settings.mjs',
    'tests/fixtures/settings_ui_server.py', 'tests/web/fixture-readiness.mjs', 'tests/web/fixture-lifecycle.mjs'])
    ledger.sources[path] = hash(await readFile(join(root, path)));
  ledger.sources.installedClientComponent = hash(await readFile(join(
    process.env.AMPLIFIER_TEST_PACKAGE_ROOT, 'static/client-component.json')));
  // Do not inherit usable account credentials into a test fixture. base.main
  // supplies its own FIXTURE_KEY and private synthetic provider configuration.
  const env = Object.fromEntries(Object.entries(process.env).filter(([name]) =>
    name.startsWith('AMPLIFIER_TEST_') || !/(?:TOKEN|SECRET|PASSWORD|API_KEY|CREDENTIAL)/i.test(name)));
  env.SETTINGS_RELEASE_FIXTURE = '1';
  env.LAYOUT_PASS2_FINAL_GAPS = finalGapMode ? '1' : '0';
  fixture = await startFixture({command: env.AMPLIFIER_TEST_PYTHON,
      args: ['-I', '-B', '-u', '-c', seedProgram, join(root, 'tests/fixtures')],
      env, tag: 'settings-ui', name: 'Layout pass two Settings', timeoutMs: 60000});
  ledger.identity.fixture = {base: fixture.base, seeded: true,
    lifecycle: 'settings_ui_server.serve via startFixture (the same readiness helper used by startSettingsFixture)'};
  browser = await chromium.launch({headless: true,
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ? {executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH} : {}),
    args: process.env.DTU_CHROMIUM_SINGLE_PROCESS === '1' ? ['--no-sandbox', '--no-zygote', '--single-process', '--disable-gpu'] : ['--no-sandbox']});
  for (const job of ledger.jobs) {
    const excludedFinalGap = finalGapMode && (!finalGapRoutes.has(job.route) || ![1440, 390].includes(job.width));
    if (excludedFinalGap || routeFilter && !routeFilter.includes(job.route) || widthFilter && !widthFilter.includes(job.width)) {
      job.reason = excludedFinalGap ? 'Excluded by bounded final-five-gap mode (four routes, 1440/390 only)' :
        'Excluded by focused recovery route/width selection';
      for (const state of ledger.states.filter(row => row.job === job.id)) state.reason = job.reason;
      await save(); continue;
    }
    let context, j, fence;
    try {
      // Fresh browser memory per route isolates irrecoverable/guarded drafts
      // without another host, environment, runtime or fixture launch.
      context = await browser.newContext({viewport: {width: job.width, height: 1000},
        acceptDownloads: true, serviceWorkers: 'allow',
        extraHTTPHeaders: {Authorization: 'Bearer fixture-browser-control-token'}});
      await fenceLayoutServiceWorkers(context);
      await context.addInitScript(() => {
        const data = {mode: 'success', writes: 0, last: ''};
        Object.defineProperty(window, 'layoutPass2Clipboard', {value: data});
        Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {
          writeText: async text => {
            data.writes++;
            if (data.mode === 'denied') throw new DOMException('Synthetic audit clipboard denial', 'NotAllowedError');
            data.last = text;
          },
        }});
      });
      // Fetch fixture identity outside the browser interception, from its
      // exact owned readiness URL. No private host config endpoint is read.
      const identityResponse = await context.request.get(fixture.base + '/fixture/layout-pass2-identity');
      if (!identityResponse.ok()) throw Error('Owned seed identity unavailable; no installed-source fallback is permitted.');
      const identity = {...await identityResponse.json(), base: fixture.base};
      assert.equal(identity.installedPackageVerified, true);
      if (finalGapMode) assert.equal(identity.finalGapSeeds?.enabled, true,
        'Final-gap mode requires the owned startup seed; never substitute actual native/runner reads.');
      assert.equal(await realpath(identity.packageRoot), await realpath(env.AMPLIFIER_TEST_PACKAGE_ROOT));
      assert.equal(identity.serverSha256, hash(await readFile(join(env.AMPLIFIER_TEST_PACKAGE_ROOT, 'server.py'))));
      ledger.identity.installed = identity;
      fence = await installFence(context, job, identity);
      const page = await context.newPage();
      page.setDefaultTimeout(6000);
      page.on('pageerror', error => ledger.errors.push({job: job.id, state: job.currentState, error: errorText(error)}));
      j = makeJourney(page, job, fence);
      const entered = await j.step('route.entry', async row => {
        await page.goto(fixture.base);
        await page.waitForFunction(() => window.amplifier?.getState());
        await openRoute(page, job.route, j.activate);
        await expect(page.locator('.a-settings-experience')).toHaveAttribute('data-settings-page', job.route);
        // Bind observed served assets to the installed package bytes, not merely
        // to an index HTML path. This is not a wheel RECORD verification.
        const paths = await page.locator('script[src],link[rel=stylesheet][href]').evaluateAll(nodes =>
          nodes.map(node => new URL(node.src || node.href).pathname));
        row.assets = [];
        for (const path of paths) {
          assert.ok(/^\/(?:assets\/|[A-Za-z0-9_.-]+\.(?:js|css)$)/.test(path), 'Unexpected installed asset path');
          const response = await context.request.get(fixture.base + path);
          assert.equal(response.status(), 200);
          const served = hash(await response.body());
          assert.equal(served, hash(await readFile(join(env.AMPLIFIER_TEST_PACKAGE_ROOT, 'static', path.slice(1)))));
          row.assets.push({path, sha256: served});
        }
        await j.snapshot(row, '-top');
      });
      if (entered) {
        for (const state of ledger.states.filter(row => row.job === job.id && !row.startedAt)) {
          const isFinalGap = state.name.includes('.final-gap.');
          if (finalGapMode !== isFinalGap && state.name !== 'route.bottom')
            state.reason = finalGapMode ? 'Original campaign state preserved but not rerun in final-five-gap batch.' :
              'Opt-in final-five-gap scene not selected; no inferred coverage.';
        }
        if (finalGapMode) await finalGapRecipe(j);
        else await recipe(j);
        await j.step('route.bottom', row => j.bottom(row));
      } else {
        for (const state of ledger.states.filter(row => row.job === job.id && !row.startedAt))
          state.reason = 'Visible route entry failed; no dispatch fallback or guessed interaction attempted.';
      }
      const states = ledger.states.filter(row => row.job === job.id);
      assert.equal(context.serviceWorkers().length, 0, 'Layout audit must never start a service worker');
      job.browserErrors = ledger.errors.filter(row => row.job === job.id).length;
      job.status = states.some(row => row.status === 'failed') || job.browserErrors ? 'failed' :
        states.some(row => row.status === 'unvisited') ? 'partial' : 'visited';
      job.reason = 'See each state and raw control evidence; visited is not pixel acceptance.';
    } catch (error) {
      job.status = 'failed'; job.reason = errorText(error);
      for (const state of ledger.states.filter(row => row.job === job.id && !row.startedAt))
        state.reason = 'Route startup/interruption: ' + job.reason;
    } finally {
      fence?.pending.splice(0).forEach(release => release());
      if (context) try {await context.close();} catch (error) {
        job.closeError = errorText(error); job.status = 'failed';
      }
      await save();
    }
  }
  ledger.completedAt = new Date().toISOString();
  ledger.summary = {
    jobs: Object.fromEntries(['visited', 'partial', 'failed', 'unvisited'].map(status =>
      [status, ledger.jobs.filter(row => row.status === status).length])),
    states: Object.fromEntries(['visited', 'failed', 'unvisited'].map(status =>
      [status, ledger.states.filter(row => row.status === status).length])),
    browserErrors: ledger.errors.length,
    blockedBeforeHost: ledger.requests.filter(row => row.disposition === 'blocked-before-host').length,
    finalGapScenes: finalGapMode ? Object.fromEntries(['visited', 'failed', 'unvisited'].map(status =>
      [status, ledger.states.filter(row => row.name.includes('.final-gap.') &&
        [1440, 390].some(width => row.job.startsWith(width + '.')) && row.status === status).length])) : null,
    pixelInspected: false, allStatesAccepted: false,
  };
  await save();
  console.log(JSON.stringify({reportPath, ...ledger.summary}));
  // Partial coverage is reported, not silently green. Parent decides how to
  // seed difficult branches; independent routes have already continued.
  if (ledger.jobs.some(row => row.status === 'failed') || ledger.errors.length)
    throw Error('Layout census retained failures; inspect ' + reportPath);
} catch (error) {
  originalError = error; ledger.fatalError = errorText(error); await save(); throw error;
} finally {
  if (fixture) {
    const cleanup = {browser: {status: 'unconfirmed'}, fixture: {status: 'unconfirmed'}};
    const recordRelease = (name, work) => async () => {
      try {const result = await work(); cleanup[name] = {status: 'confirmed', result}; return result;}
      catch (error) {cleanup[name] = {status: 'failed', error: errorText(error)}; throw error;}
    };
    try {
      await finishFixture({label: 'Layout pass two Settings',
        stop: recordRelease('fixture', fixture.stop),
        close: [['browser', recordRelease('browser', () => browser?.close())]], originalError});
      ledger.cleanup = {status: Object.values(cleanup).every(row => row.status === 'confirmed') ? 'confirmed' : 'failed',
        source: 'finishFixture graceful lifecycle; individual releases retained even when an original error wins',
        releases: cleanup};
    } catch (error) {
      ledger.cleanup = {status: 'failed', error: errorText(error), releases: cleanup};
      if (!originalError) throw error;
    } finally {await save();}
  }
}
