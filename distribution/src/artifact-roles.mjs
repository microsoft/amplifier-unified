/** Compose independently prepared artifacts through the common signed adapter.
 * Private writable paths are caller configuration, never signed code metadata.
 * This resolves plans only: no process, owner, journal or installation is created. */
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
  return Object.freeze(resolved);
}
