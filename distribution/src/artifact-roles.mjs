/** Compose independently prepared artifacts through the common signed adapter.
 * Private writable paths are caller configuration, never signed code metadata.
 * This resolves plans only: no process, owner, journal or installation is created. */
import {realpath} from 'node:fs/promises';
import {relative, isAbsolute, sep} from 'node:path';

export async function resolveArtifactRoles(roles) {
  if (!Array.isArray(roles) || !roles.length || roles.length > 32 ||
      new Set(roles.map(role => role?.name)).size !== roles.length)
    throw Error('artifact_roles_invalid');
  const resolved = {};
  for (const role of roles) {
    if (!role || !/^[a-z][a-z0-9-]{0,63}$/.test(role.name) ||
        typeof role.adapter?.resolveRole !== 'function') throw Error('artifact_roles_invalid');
    resolved[role.name] = await role.adapter.resolveRole(role.target, role.interface, role.writableRoots ?? {});
  }
  // A role's own check cannot protect another role's immutable code. Resolve
  // aliases before comparing the complete composition, then return no plans
  // if any writable directory contains code or lives inside a code directory.
  const codeRoots = await Promise.all(Object.values(resolved).map(role => realpath(role.root)));
  const contains = (root, path) => {
    const part = relative(root, path);
    return part === '' || (part !== '..' && !part.startsWith('..' + sep) && !isAbsolute(part));
  };
  for (const role of roles) for (const path of Object.values(role.writableRoots ?? {})) {
    const writable = await realpath(path);
    if (codeRoots.some(code => contains(code, writable) || contains(writable, code)))
      throw Error('artifact_writable_code_overlap');
  }
  return Object.freeze(resolved);
}
