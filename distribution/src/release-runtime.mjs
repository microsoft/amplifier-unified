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
async function rootsValid(trees) {
  if (!Array.isArray(trees) || !trees.length || trees.length > 8) fail();
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
export async function inventoryMcpRuntime({trees, python, qualificationReceiptSha256}) {
  if (!sha(qualificationReceiptSha256)) fail();
  keys(python, ['tree', 'path']); relativePath(python.path);
  await rootsValid(trees);
  const captured = [];
  for (const {id, root} of trees) captured.push({id, root, entries: await treeInventory(root)});
  await linksValid(captured);
  const manifest = {schema: 'unified-mcp-runtime-v1', qualificationReceiptSha256, python, trees: captured};
  await verifyMcpRuntime(manifest);
  return manifest;
}
export async function verifyMcpRuntime(manifest) {
  keys(manifest, ['schema', 'qualificationReceiptSha256', 'python', 'trees']);
  if (manifest.schema !== 'unified-mcp-runtime-v1' || !sha(manifest.qualificationReceiptSha256)) fail();
  keys(manifest.python, ['tree', 'path']); relativePath(manifest.python.path);
  await rootsValid(manifest.trees);
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

/** Called only after createRuntimeIdentity verifies the signed full archive.
 * The release digest cannot be embedded in that archive (a hash cycle). Its
 * signed descriptor instead binds the release id/version/revision, exact base
 * configuration bytes, and every external MCP runtime byte through its manifest.
 * No general configuration overlay, source policy, credential or owner changes. */
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
    keys(descriptor, ['schema', 'release', 'baseConfigurationSha256', 'webDirectory', 'mcpRuntime']);
    keys(descriptor.release, ['id', 'version', 'revision']);
    if (descriptor.schema !== 'unified-release-runtime-v1' ||
        !sha(descriptor.baseConfigurationSha256) || hash(configurationBytes) !== descriptor.baseConfigurationSha256 ||
        !isDeepStrictEqual(descriptor.release, Object.fromEntries(['id', 'version', 'revision'].map(key => [key, runtime.identity[key]])))) fail();
    releaseRoot = await realpath(releaseRoot);
    const webDirectory = await packagePath(releaseRoot, descriptor.webDirectory);
    if (!(await lstat(webDirectory)).isDirectory()) fail();
    await regular(await packagePath(releaseRoot, descriptor.webDirectory + '/index.html'), 16 * 1024 * 1024);
    const manifestPath = await packagePath(releaseRoot, descriptor.mcpRuntime);
    const manifestBytes = await regular(manifestPath, 64 * 1024 * 1024), manifest = JSON.parse(manifestBytes);
    const python = await verifyMcpRuntime(manifest);
    // Shallow replacement is intentional: all other owner configuration, state,
    // TLS/access, native engines, and immutable source/bootstrap inputs survive.
    const result = {...configuration, application: {...configuration.application,
      webDirectory, mcp: {...configuration.application.mcp, python}}};
    let verifying;
    const verify = () => verifying ??= (async () => {
      try {
        if (!bytes.equals(await regular(join(releaseRoot, 'release-runtime.json'), 65536)) ||
            !manifestBytes.equals(await regular(manifestPath, 64 * 1024 * 1024))) fail();
        await verifyMcpRuntime(manifest);
      } catch { throw Error('release_runtime_binding_invalid'); }
      finally { verifying = undefined; }
    })();
    return {configuration: result, verify, binding: {schema: descriptor.schema,
      baseConfigurationSha256: descriptor.baseConfigurationSha256,
      mcpRuntimeSha256: hash(manifestBytes), qualificationReceiptSha256: manifest.qualificationReceiptSha256}};
  } catch { throw Error('release_runtime_binding_invalid'); }
}
