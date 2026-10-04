// Release-specific code is signed; installation state and authority stay in the
// unchanged private composition. These reads never create owners or listeners.
import {constants} from 'node:fs';
import {lstat, open, readdir, readlink, realpath} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {isDeepStrictEqual} from 'node:util';
import {isAbsolute, join, relative, resolve, sep} from 'node:path';

const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const fail = () => { throw Error('release_runtime_binding_invalid'); };
const sha = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
// Declare immutable checkouts individually. Grouping their mutable object-store
// parent to fit a small root cap would include operational lock files. Both the
// publisher and verifier use this bound; every descendant is still inventoried.
const PYTHON_RUNTIME_MAX_TREES = 128;
const keys = (value, names) => {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      Object.keys(value).some(key => !names.includes(key)) ||
      names.some(key => !Object.hasOwn(value, key))) fail();
};
const relativePath = value => {
  if (typeof value !== 'string' || !value || value.includes('\\') || /[\0-\x1f\x7f]/.test(value) ||
      isAbsolute(value) || value.split('/').some(p => !p || p === '.' || p === '..')) fail();
  return value;
};
const contains = (root, path) => {
  const part = relative(root, path);
  return part === '' || (!part.startsWith('..' + sep) && part !== '..' && !isAbsolute(part));
};
async function regular(path, max) {
  const fd = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const info = await fd.stat();
    if (!info.isFile() || info.size > max || (info.mode & 0o022)) fail();
    const bytes = await fd.readFile();
    if (bytes.length > max) fail();
    return bytes;
  } finally { await fd.close(); }
}
async function packagePath(root, path) {
  const result = join(root, relativePath(path));
  if (!contains(root, result) || await realpath(result) !== result) fail();
  return result;
}
async function fileHash(path) {
  const fd = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const before = await fd.stat();
    if (!before.isFile() || (before.mode & 0o022)) fail();
    const digest = createHash('sha256');
    for await (const bytes of fd.createReadStream({autoClose: false})) digest.update(bytes);
    const after = await fd.stat();
    if (before.size !== after.size || before.mtimeMs !== after.mtimeMs || before.ctimeMs !== after.ctimeMs) fail();
    return {kind: 'file', mode: before.mode & 0o777, bytes: before.size, sha256: digest.digest('hex')};
  } finally { await fd.close(); }
}
async function treeInventory(root) {
  const entries = [], files = [];
  const walk = async (directory, prefix = '') => {
    for (const name of (await readdir(directory)).sort()) {
      const path = relativePath(prefix ? prefix + '/' + name : name), absolute = join(root, path);
      const stat = await lstat(absolute);
      if (entries.length >= 200000) fail();
      if (stat.isSymbolicLink()) entries.push({path, kind: 'symlink', target: await readlink(absolute)});
      else if (stat.isDirectory()) {
        if (stat.mode & 0o022) fail();
        entries.push({path, kind: 'directory', mode: stat.mode & 0o777});
        await walk(absolute, path);
      } else if (stat.isFile()) {
        const entry = {path}; entries.push(entry); files.push({entry, absolute});
      }
      else fail();
    }
  };
  await walk(root);
  // Preserve the published directory-first ordering while hashing independent
  // files concurrently. Readiness must verify all bytes without thousands of
  // serialized file reads delaying an otherwise healthy update handoff.
  let next = 0;
  const results = await Promise.allSettled(Array.from({length: Math.min(16, files.length)}, async () => {
    while (next < files.length) {
      const {entry, absolute} = files[next++];
      Object.assign(entry, await fileHash(absolute));
    }
  }));
  const failed = results.find(result => result.status === 'rejected');
  if (failed) throw failed.reason;
  return entries;
}
async function rootsValid(trees, limit = 8) {
  if (!Array.isArray(trees) || !trees.length || trees.length > limit) fail();
  const names = new Set();
  for (const tree of trees) {
    if (!tree || typeof tree.id !== 'string' || !/^[a-z][a-z0-9-]{0,63}$/.test(tree.id) || names.has(tree.id) ||
        typeof tree.root !== 'string' || !isAbsolute(tree.root) || resolve(tree.root) !== tree.root || tree.root === sep ||
        await realpath(tree.root) !== tree.root) fail();
    names.add(tree.id);
    const info = await lstat(tree.root);
    if (!info.isDirectory() || (info.mode & 0o022) || info.uid !== process.getuid()) fail();
    for (const other of trees) if (other !== tree && (contains(tree.root, other.root) || contains(other.root, tree.root))) fail();
  }
}
async function linksValid(trees) {
  for (const tree of trees) for (const entry of tree.entries) if (entry.kind === 'symlink') {
    // Venv Python normally links to an interpreter outside the environment.
    // The complete interpreter prefix must be another inventoried tree; an
    // unverified /usr/bin/python or editable source escape is not accepted.
    const target = await realpath(join(tree.root, entry.path));
    if (!trees.some(candidate => contains(candidate.root, target))) fail();
  }
}

/** Publisher-side snapshot after isolated qualification; it executes no Python.
 * The signed manifest binds bytes, not a claim that a fixture proves readiness.
 * Seal these owned trees against writes for their lifetime. Like the signed
 * JS tree, this is not attestation against a malicious same-user writer. */
export const inventoryMcpRuntime = options => inventoryRuntime(options, 'unified-mcp-runtime-v1');
export const verifyMcpRuntime = manifest => verifyRuntime(manifest, 'unified-mcp-runtime-v1');
// Keep the established MCP v1 contract, limits and tree ordering unchanged.
export const inventoryPythonRuntime = options => inventoryRuntime(options, 'unified-python-runtime-v1');
export const verifyPythonRuntime = manifest => verifyRuntime(manifest, 'unified-python-runtime-v1');

async function inventoryRuntime({trees, python, qualificationReceiptSha256}, schema) {
  if (!sha(qualificationReceiptSha256)) fail();
  keys(python, ['tree', 'path']); relativePath(python.path);
  await rootsValid(trees, schema === 'unified-python-runtime-v1' ? PYTHON_RUNTIME_MAX_TREES : 8);
  const captured = [];
  for (const {id, root} of trees) captured.push({id, root, entries: await treeInventory(root)});
  await linksValid(captured);
  const manifest = {schema, qualificationReceiptSha256, python, trees: captured};
  await verifyRuntime(manifest, schema);
  return manifest;
}
async function verifyRuntime(manifest, schema) {
  keys(manifest, ['schema', 'qualificationReceiptSha256', 'python', 'trees']);
  if (manifest.schema !== schema || !sha(manifest.qualificationReceiptSha256)) fail();
  keys(manifest.python, ['tree', 'path']); relativePath(manifest.python.path);
  await rootsValid(manifest.trees, schema === 'unified-python-runtime-v1' ? PYTHON_RUNTIME_MAX_TREES : 8);
  for (const tree of manifest.trees) {
    keys(tree, ['id', 'root', 'entries']);
    if (!Array.isArray(tree.entries) || !isDeepStrictEqual(tree.entries, await treeInventory(tree.root))) fail();
  }
  await linksValid(manifest.trees);
  const tree = manifest.trees.find(tree => tree.id === manifest.python.tree);
  if (!tree) fail();
  const python = join(tree.root, manifest.python.path), target = await realpath(python), info = await lstat(target);
  if (!info.isFile() || !(info.mode & 0o111)) fail();
  return python;
}


async function bindNativeLauncher(configuration, releaseRoot, descriptor) {
  keys(descriptor, ['engineId', 'baseConfigurationSha256', 'configuration', 'grants', 'qualificationReceiptSha256']);
  if (typeof descriptor.engineId !== 'string' || !descriptor.engineId ||
      !sha(descriptor.baseConfigurationSha256) || !sha(descriptor.qualificationReceiptSha256)) fail();
  const grants = descriptor.grants;
  if (!grants || typeof grants !== 'object' || Array.isArray(grants) ||
      !Object.keys(grants).length || Object.entries(grants).some(([name, value]) =>
        !['adminVoiceCredentials', 'adminGenerations', 'runtimeImmutable'].includes(name) ||
        (name === 'runtimeImmutable' ? value !== true : typeof value !== 'boolean'))) fail();
  const engines = configuration.application?.engines;
  if (!Array.isArray(engines) || configuration.application.nativeAdmin?.engine !== descriptor.engineId ||
      engines.filter(engine => engine.id === descriptor.engineId).length !== 1) fail();
  const index = engines.findIndex(engine => engine.id === descriptor.engineId), engine = engines[index];
  // This is the reviewed native launch shape, not an argument/command override.
  // Other launchers need their own explicit contract rather than loose flag parsing.
  if (!Array.isArray(engine.args) || engine.args.length !== 6 ||
      !isDeepStrictEqual(engine.args.slice(0, 5), ['-I', '-B', '-m', 'amplifier_acp', '--config'])) fail();
  const basePath = engine.args[5];
  if (typeof basePath !== 'string' || !isAbsolute(basePath) || resolve(basePath) !== basePath ||
      await realpath(basePath) !== basePath) fail();
  const privateBase = async () => {
    const bytes = await regular(basePath, 1048576), info = await lstat(basePath);
    if (info.uid !== process.getuid() || (info.mode & 0o077)) fail();
    return bytes;
  };
  const baseBytes = await privateBase();
  if (hash(baseBytes) !== descriptor.baseConfigurationSha256) fail();
  const original = JSON.parse(baseBytes);
  if (!original || typeof original !== 'object' || Array.isArray(original)) fail();
  if (Object.hasOwn(original, 'runtimeImmutable') && typeof original.runtimeImmutable !== 'boolean') fail();
  const candidatePath = await packagePath(releaseRoot, descriptor.configuration);
  const candidateBytes = await regular(candidatePath, 1048576);
  // Signed bytes alone do not authorize arbitrary configuration changes.
  // Homes, roots, source policy, credentials, runtime, and all other native
  // configuration must match the immutable operator-owned base exactly.
  // Sealed runtimes must not gain dependencies during ordinary preparation.
  // This signed policy can only enable immutability, never relax an existing
  // policy. Keep the private base and every other native field byte-bound.
  const candidate = JSON.parse(candidateBytes);
  if (!isDeepStrictEqual(candidate, {...original, ...grants})) fail();
  const updated = [...engines];
  updated[index] = {...engine, args: [...engine.args.slice(0, 5), candidatePath]};
  return {
    engines: updated,
    verify: async () => {
      if (!baseBytes.equals(await privateBase()) ||
          !candidateBytes.equals(await regular(candidatePath, 1048576))) fail();
    },
    binding: {
      engineId: descriptor.engineId,
      baseConfigurationSha256: descriptor.baseConfigurationSha256,
      configurationSha256: hash(candidateBytes),
      grants: {...grants},
      ...(candidate.runtimeImmutable === true ? {runtimeImmutable: true} : {}),
      qualificationReceiptSha256: descriptor.qualificationReceiptSha256,
    },
  };
}


const ownerProfile = 'native-catalog-media-v1';
const ownerModules = {native:'amplifier_acp', catalog:'amplifier_session_catalog', media:'amplifier_unified_media.worker'};
const ownerEntrypoints = {native:'amplifier_acp/__main__.py', catalog:'amplifier_session_catalog/__main__.py', media:'amplifier_unified_media/worker.py'};

function bindOwnerCensus(configuration, descriptor) {
  keys(descriptor, ['profile']);
  if (descriptor.profile !== 'native-message-metadata-v1') fail();
  // The full-owner launcher validates the complete immutable base census both
  // before and after binding. This signed profile adds exactly one independent
  // message pipe fence; it cannot learn authority from observed participants.
  const base = configuration.expectedOwners;
  if (!Array.isArray(base) || !base.length || base.some(id => typeof id !== 'string' || !id) ||
      new Set(base).size !== base.length || base.includes('native-message-metadata')) fail();
  const expectedOwners = Object.freeze([...base, 'native-message-metadata']);
  return {expectedOwners, binding:{profile:descriptor.profile, expectedOwners}};
}

function qualifiedImportPaths(paths, manifest) {
  if (!Array.isArray(paths) || !paths.length ||
      paths.some(path => typeof path !== 'string' || !isAbsolute(path) ||
        resolve(path) !== path || !manifest.trees.some(tree => contains(tree.root, path)))) fail();
}

function qualifiedEntrypoint(moduleFile, role, manifest) {
  keys(moduleFile, ['tree','path']); relativePath(moduleFile.path);
  const entrypoint=ownerEntrypoints[role];
  if (moduleFile.path !== entrypoint && !moduleFile.path.endsWith('/'+entrypoint)) fail();
  const tree=manifest.trees.find(tree => tree.id === moduleFile.tree);
  if (!tree?.entries.some(entry => entry.path === moduleFile.path && entry.kind === 'file')) fail();
}

function qualifyImportCensus(census, manifest) {
  keys(census, ['kind','argvPrefix','importPaths','entrypoints','editableInstalls']);
  if (census.kind !== 'isolated-python-import-census' ||
      !isDeepStrictEqual(census.argvPrefix, ['-I','-B','-c']) ||
      census.editableInstalls !== false || !Array.isArray(census.entrypoints) ||
      census.entrypoints.length !== 3) fail();
  qualifiedImportPaths(census.importPaths, manifest);
  const entries=new Map();
  for (const entry of census.entrypoints) {
    keys(entry, ['role','moduleFile']);
    if (!Object.hasOwn(ownerModules, entry.role) || entries.has(entry.role)) fail();
    qualifiedEntrypoint(entry.moduleFile, entry.role, manifest);
    entries.set(entry.role, entry.moduleFile);
  }
  return entries;
}

function qualifyOwnerRuntime(receipt, manifest) {
  const separateEvidence=receipt.schema === 'unified-python-runtime-qualification-v2';
  keys(receipt, ['schema','profile','python','launches',
    ...(separateEvidence ? ['importCensus','nativeMountedOriginsReceiptSha256'] : [])]);
  if ((!separateEvidence && receipt.schema !== 'unified-python-runtime-qualification-v1') ||
      receipt.profile !== ownerProfile || !isDeepStrictEqual(receipt.python, manifest.python) ||
      !Array.isArray(receipt.launches) || receipt.launches.length !== 3) fail();
  const census=separateEvidence ? qualifyImportCensus(receipt.importCensus, manifest) : null;
  if (separateEvidence && !sha(receipt.nativeMountedOriginsReceiptSha256)) fail();
  const roles=new Set();
  for (const launch of receipt.launches) {
    keys(launch, separateEvidence
      ? ['role','module','argvPrefix','readiness','moduleFile','moduleFileEvidence','workerImportPaths','noRuntimeWrites']
      : ['role','module','flags','moduleFile','importPaths','noRuntimeWrites','editableInstalls']);
    if (!Object.hasOwn(ownerModules, launch.role) || roles.has(launch.role) ||
        launch.module !== ownerModules[launch.role] || launch.noRuntimeWrites !== true) fail();
    roles.add(launch.role);
    // Always bind the -m entrypoint, not merely its package __init__. In v2,
    // entrypoint resolution and actual worker introspection remain distinct.
    qualifiedEntrypoint(launch.moduleFile, launch.role, manifest);
    if (!separateEvidence) {
      if (!isDeepStrictEqual(launch.flags, ['-I','-B']) || launch.editableInstalls !== false) fail();
      qualifiedImportPaths(launch.importPaths, manifest);
      continue;
    }
    if (!isDeepStrictEqual(launch.argvPrefix, ['-I','-B','-m',launch.module]) ||
        launch.readiness !== 'protocol-response' ||
        !['entrypoint-resolution','worker-inspection'].includes(launch.moduleFileEvidence) ||
        !isDeepStrictEqual(launch.moduleFile, census.get(launch.role))) fail();
    // A -c import census is NOT a measurement of a running -m worker's sys.path.
    // Some owners expose loaded modules but no path list. Keep that absence
    // explicit; never fill it with the census or a permitted-path superset.
    if (launch.workerImportPaths !== null) qualifiedImportPaths(launch.workerImportPaths, manifest);
  }
}

function catalogLauncher(args) {
  // One reviewed catalog launch shape. Preserve all values, including data
  // roots; this binding cannot append flags, change scans, or redirect state.
  const flags = ['--db','--home','--app-home','--workspace','--scan-interval','--workspace-check-interval'];
  if (!Array.isArray(args) || args.length !== 17 ||
      !isDeepStrictEqual(args.slice(0,5), ['-I','-B','-m','amplifier_session_catalog','serve'])) fail();
  for (let i=0;i<flags.length;i++) {
    const value=args[6+2*i];
    if (args[5+2*i] !== flags[i] || typeof value !== 'string' ||
        (i<4 ? !isAbsolute(value) || resolve(value) !== value : value !== '0')) fail();
  }
}

async function bindOwnerRuntime(configuration, releaseRoot, descriptor, nativeEngineId) {
  keys(descriptor, ['profile','engineId','manifest','qualificationReceipt','mediaMode']);
  if (descriptor.profile !== ownerProfile || descriptor.engineId !== nativeEngineId ||
      descriptor.mediaMode !== 'installed') fail();
  const app=configuration.application, engines=app?.engines;
  if (!Array.isArray(engines) || app.nativeAdmin?.engine !== descriptor.engineId ||
      engines.filter(engine => engine.id === descriptor.engineId).length !== 1) fail();
  const engine=engines.find(engine => engine.id === descriptor.engineId), catalog=app.catalogProcess, media=app.media;
  if (!catalog || !media || typeof engine.command !== 'string' || !isAbsolute(engine.command) ||
      resolve(engine.command) !== engine.command || catalog.command !== engine.command || media.python !== engine.command ||
      media.command != null || media.broker != null ||
      (media.pythonMode != null && media.pythonMode !== 'installed') ||
      !Array.isArray(engine.args) || engine.args.length !== 6 ||
      !isDeepStrictEqual(engine.args.slice(0,5), ['-I','-B','-m','amplifier_acp','--config'])) fail();
  catalogLauncher(catalog.args);
  const manifestPath=await packagePath(releaseRoot, descriptor.manifest);
  const manifestBytes=await regular(manifestPath,64*1024*1024), manifest=JSON.parse(manifestBytes);
  const python=await verifyPythonRuntime(manifest);
  const receiptPath=await packagePath(releaseRoot,descriptor.qualificationReceipt);
  const receiptBytes=await regular(receiptPath,4*1024*1024);
  if (hash(receiptBytes) !== manifest.qualificationReceiptSha256) fail();
  qualifyOwnerRuntime(JSON.parse(receiptBytes),manifest);
  return {python, engineId:descriptor.engineId,
    verify:async()=>{
      if (!manifestBytes.equals(await regular(manifestPath,64*1024*1024)) ||
          !receiptBytes.equals(await regular(receiptPath,4*1024*1024))) fail();
      await verifyPythonRuntime(manifest);
    },
    binding:{profile:ownerProfile,engineId:descriptor.engineId,mediaMode:'installed',
      manifestSha256:hash(manifestBytes),qualificationReceiptSha256:hash(receiptBytes)},
  };
}

/** Called only after createRuntimeIdentity verifies the signed full archive.
 * The release digest cannot be embedded in that archive (a hash cycle). Its
 * signed descriptor instead binds the release id/version/revision, exact base
 * configuration bytes, and every external MCP runtime byte through its manifest.
 * Only enumerated native launcher grants may change in v2; never a general
 * configuration overlay, source policy, credential value or owner change.
 * V3 additionally selects one inventoried Python environment for the existing
 * native, catalog and installed-mode media slots; no arbitrary launcher edits. */
export async function bindReleaseConfiguration({configuration, configurationBytes, runtime, releaseRoot, source = false}) {
  if (source) {
    // This is deliberately still exact: successor support must never weaken
    // manual-source/bootstrap authority or reuse a consumed recovery permit.
    if (!isDeepStrictEqual(runtime.identity, configuration.release.prepared.identity)) throw Error('prepared_release_identity_mismatch');
    return {configuration, verify: async () => {}, binding: null};
  }
  let bytes;
  try { bytes = await regular(join(releaseRoot, 'release-runtime.json'), 65536); }
  catch (error) {
    if (error.code !== 'ENOENT') throw Error('release_runtime_binding_invalid');
    if (!isDeepStrictEqual(runtime.identity, configuration.release.prepared.identity)) throw Error('release_runtime_binding_required');
    return {configuration, verify: async () => {}, binding: null};
  }
  try {
    // An explicit command/broker bypasses mcp.python; never claim the signed
    // runtime is active while launching a different executable.
    if (configuration.application?.mcp?.command != null || configuration.application?.mcp?.broker != null) fail();
    const descriptor = JSON.parse(bytes);
    const ownerRuntime = descriptor.schema === 'unified-release-runtime-v3';
    const nativeGrants = ownerRuntime || descriptor.schema === 'unified-release-runtime-v2';
    const ownerCensus = ownerRuntime && Object.hasOwn(descriptor, 'ownerCensus');
    keys(descriptor, ['schema', 'release', 'baseConfigurationSha256', 'webDirectory', 'mcpRuntime', ...(nativeGrants ? ['nativeLauncher'] : []), ...(ownerRuntime ? ['ownerRuntime'] : []), ...(ownerCensus ? ['ownerCensus'] : [])]);
    keys(descriptor.release, ['id', 'version', 'revision']);
    if ((!nativeGrants && descriptor.schema !== 'unified-release-runtime-v1') ||
        !sha(descriptor.baseConfigurationSha256) || hash(configurationBytes) !== descriptor.baseConfigurationSha256 ||
        !isDeepStrictEqual(descriptor.release, Object.fromEntries(['id', 'version', 'revision'].map(key => [key, runtime.identity[key]])))) fail();
    const census = ownerCensus ? bindOwnerCensus(configuration, descriptor.ownerCensus) : null;
    releaseRoot = await realpath(releaseRoot);
    const webDirectory = await packagePath(releaseRoot, descriptor.webDirectory);
    if (!(await lstat(webDirectory)).isDirectory()) fail();
    await regular(await packagePath(releaseRoot, descriptor.webDirectory + '/index.html'), 16 * 1024 * 1024);
    const manifestPath = await packagePath(releaseRoot, descriptor.mcpRuntime);
    const manifestBytes = await regular(manifestPath, 64 * 1024 * 1024), manifest = JSON.parse(manifestBytes);
    const python = await verifyMcpRuntime(manifest);
    const native = nativeGrants ? await bindNativeLauncher(configuration, releaseRoot, descriptor.nativeLauncher) : null;
    const owner = ownerRuntime ? await bindOwnerRuntime(configuration, releaseRoot, descriptor.ownerRuntime, descriptor.nativeLauncher.engineId) : null;
    const engines = native?.engines;
    const ownerEngines = owner ? engines.map(engine => engine.id === owner.engineId ? {...engine, command:owner.python} : engine) : null;
    // Shallow replacement is intentional: all other owner configuration, state,
    // TLS/access and immutable source/bootstrap inputs survive. V2 changes only
    // the native --config path after validating its exact allowlisted delta.
    // V3 additionally binds the three fixed Python slots and installed media
    // mode. Preserve the venv path: realpath(python) would lose its environment.
    const result = {...configuration, application: {...configuration.application,
      webDirectory, mcp: {...configuration.application.mcp, python},
      ...(native ? {engines: ownerEngines ?? engines} : {}),
      ...(owner ? {
        catalogProcess:{...configuration.application.catalogProcess,command:owner.python},
        media:{...configuration.application.media,python:owner.python,pythonMode:'installed'},
      } : {})}};
    let verifying;
    const verify = () => verifying ??= (async () => {
      try {
        if (!bytes.equals(await regular(join(releaseRoot, 'release-runtime.json'), 65536)) ||
            !manifestBytes.equals(await regular(manifestPath, 64 * 1024 * 1024))) fail();
        await verifyMcpRuntime(manifest);
        await native?.verify();
        await owner?.verify();
      } catch { throw Error('release_runtime_binding_invalid'); }
      finally { verifying = undefined; }
    })();
    return {configuration: result, verify, ...(census ? {expectedOwners:census.expectedOwners} : {}), binding: {schema: descriptor.schema,
      baseConfigurationSha256: descriptor.baseConfigurationSha256,
      mcpRuntimeSha256: hash(manifestBytes), qualificationReceiptSha256: manifest.qualificationReceiptSha256,
      ...(native ? {nativeLauncher: native.binding} : {}), ...(owner ? {ownerRuntime:owner.binding} : {}),
      ...(census ? {ownerCensus:census.binding} : {})}};
  } catch { throw Error('release_runtime_binding_invalid'); }
}
