import { constants } from "node:fs";
import { mkdir, open, lstat, realpath } from "node:fs/promises";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { randomUUID } from "node:crypto";
import { connectHostControlFile } from "./host-control.js";
import {
  runSupervisor,
  type SupervisorConfiguration,
  type SupervisorPorts,
} from "./supervisor-cli.js";
import {
  SignedReleaseAdapter,
  type ReleaseAdapterOptions,
} from "./releases.js";
import type { InitialProvisioningPort } from "./lifecycle.js";
import { identity, same, token, type ReleaseIdentity } from "./types.js";

interface Authority {
  schema: "distribution-pristine-installation-v1";
  installationId: string;
  directory: string;
  dataScope: string;
  initial: ReleaseIdentity;
}
export interface PristineInstallation {
  directory: string;
  authorityFile: string;
  dataDirectory: string;
  releaseDirectory: string;
  applicationStateDirectory: string;
  hostDiscoveryFile: string;
  hostTokenFile: string;
  supervisorDiscoveryFile: string;
  supervisorTokenFile: string;
  dataScope: string;
}
const layout = (
  directory: string,
  dataScope: string,
): PristineInstallation => ({
  directory,
  dataScope,
  authorityFile: join(directory, "initial-provisioning.json"),
  dataDirectory: join(directory, "supervisor"),
  releaseDirectory: join(directory, "releases"),
  applicationStateDirectory: join(directory, "application"),
  hostDiscoveryFile: join(directory, "host-control.json"),
  hostTokenFile: join(directory, "host-token"),
  supervisorDiscoveryFile: join(directory, "supervisor.json"),
  supervisorTokenFile: join(directory, "supervisor-token"),
});
async function syncDirectory(directory: string) {
  const handle = await open(directory, constants.O_RDONLY);
  try {
    await handle.sync();
  } finally {
    await handle.close();
  }
}
async function writeOnce(path: string, value: unknown) {
  // Exclusive create is the cross-process claim. An interrupted/partial write
  // still occupies this path and must never automatically reauthorize a spawn.
  const handle = await open(path, "wx", 0o600);
  try {
    await handle.writeFile(JSON.stringify(value) + "\n");
    await handle.sync();
  } finally {
    await handle.close();
  }
  await syncDirectory(dirname(path));
}
async function absent(path: string) {
  try {
    await lstat(path);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return;
    throw error;
  }
  throw Error("initial_namespace_not_pristine");
}
async function readAuthority(path: string, strictOwner = false): Promise<Authority> {
  if (!isAbsolute(path)) throw Error("absolute_config_path_required");
  const directory = dirname(path),
    info = await lstat(directory);
  if (
    !info.isDirectory() ||
    info.isSymbolicLink() ||
    (process.platform !== "win32" && (info.mode & 0o077 || (strictOwner && info.uid !== process.getuid!())))
  )
    throw Error("initial_authority_invalid");
  const handle = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  let value: Authority;
  try {
    const stat = await handle.stat();
    if (
      !stat.isFile() ||
      stat.size > 16384 ||
      (process.platform !== "win32" && (stat.mode & 0o077 || (strictOwner && stat.uid !== process.getuid!())))
    )
      throw Error("initial_authority_invalid");
    value = JSON.parse(await handle.readFile("utf8"));
  } finally {
    await handle.close();
  }
  if (
    !value ||
    value.schema !== "distribution-pristine-installation-v1" ||
    Object.keys(value).some(
      (key) =>
        ![
          "schema",
          "installationId",
          "directory",
          "dataScope",
          "initial",
        ].includes(key),
    ) ||
    value.directory !== (await realpath(directory)) ||
    path !== join(value.directory, "initial-provisioning.json")
  )
    throw Error("initial_authority_invalid");
  token(value.installationId);
  token(value.dataScope);
  identity(value.initial);
  if (strictOwner && Object.keys(value.initial).sort().join(",") !== "digest,id,revision,version")
    throw Error("initial_authority_invalid");
  return value;
}
/** Explicit installer action, not detection. Exclusively allocate a new owned
 * namespace and bind its one initial launch. Existing directories are refused.
 * Installer must use the returned application state/control paths in the actual
 * signed launch configuration. This does not authorize adoption of a service. */
export async function createPristineInstallation(options: {
  directory: string;
  dataScope: string;
  initial: ReleaseIdentity;
  /** Plan identity before signing configuration bytes. This is not adoption:
   * the namespace and authority still use exclusive, write-once allocation. */
  plannedInstallationId?: string;
}): Promise<PristineInstallation> {
  if (!isAbsolute(options.directory))
    throw Error("absolute_config_path_required");
  const dataScope = token(options.dataScope),
    initial = identity(options.initial);
  const installationId = options.plannedInstallationId === undefined ? randomUUID() : options.plannedInstallationId;
  if (typeof installationId !== "string" ||
      !/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(installationId))
    throw Error("invalid_planned_installation_id");
  await mkdir(options.directory, { mode: 0o700 });
  const directory = await realpath(options.directory);
  await syncDirectory(dirname(directory));
  const result = layout(directory, dataScope);
  await writeOnce(result.authorityFile, {
    schema: "distribution-pristine-installation-v1",
    installationId,
    directory,
    dataScope,
    initial,
  } satisfies Authority);
  return result;
}
/** Read-only original-installation binding for a signed child composition.
 * A consumed initial claim is evidence of origin, never authority to launch
 * again. Current/replacement launch custody stays with the existing supervisor. */
export async function inspectPristineInstallation(authorityFile: string) {
  // This stricter read contract belongs to the new full-owner binding only;
  // legacy allocation/recovery behavior is not changed by the inspector.
  for (const path of [dirname(authorityFile), authorityFile]) {
    const stat = await lstat(path);
    if (stat.isSymbolicLink() || (process.platform !== "win32" &&
        ((stat.mode & 0o077) || stat.uid !== process.getuid!())))
      throw Error("initial_authority_invalid");
  }
  const authority = await readAuthority(authorityFile, true);
  const path = join(authority.directory, "initial-provisioning.claim");
  const handle = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const stat = await handle.stat();
    if (!stat.isFile() || stat.size > 16384 ||
        (process.platform !== "win32" && ((stat.mode & 0o077) || stat.uid !== process.getuid!())))
      throw Error("initial_claim_invalid");
    const claim = JSON.parse(await handle.readFile("utf8"));
    if (!claim || Object.keys(claim).sort().join(",") !==
        "claimedAt,commandId,dataScope,installationId,instanceId,kind,schema,targetDigest" ||
        claim.schema !== "distribution-initial-claim-v1" || claim.kind !== "pristine-installation" ||
        claim.installationId !== authority.installationId || claim.dataScope !== authority.dataScope ||
        claim.targetDigest !== authority.initial.digest || !Number.isSafeInteger(claim.claimedAt) || claim.claimedAt <= 0)
      throw Error("initial_claim_invalid");
    token(claim.commandId); token(claim.instanceId);
    return Object.freeze({...layout(authority.directory, authority.dataScope),
      installationId: authority.installationId, initial: Object.freeze(identity(authority.initial)),
      initialInstanceId: claim.instanceId as string});
  } finally { await handle.close(); }
}
export interface ProductionSupervisorPortsOptions {
  dataScope: string;
  dataDirectory: string;
  releaseDirectory: string;
  hostDiscoveryFile: string;
  provisioningAuthorityFile?: string;
  /** Mandatory independent observation by the configured source owner. Never
   * echo the publisher's requested revisions as observations. Native workers'
   * generation/source currency belongs to their separate owner. */
  resolveSources: ReleaseAdapterOptions["resolveSources"];
  fetch?: typeof fetch;
}
/** No process probing or launch during construction/inspection. Only the explicit
 * initial-provisioning port may authorize absence, once, before a first spawn. */
export function createProductionSupervisorPorts(
  options: ProductionSupervisorPortsOptions,
): SupervisorPorts {
  token(options.dataScope);
  for (const path of [
    options.dataDirectory,
    options.releaseDirectory,
    options.hostDiscoveryFile,
    ...(options.provisioningAuthorityFile
      ? [options.provisioningAuthorityFile]
      : []),
  ])
    if (typeof path !== "string" || !isAbsolute(path))
      throw Error("absolute_config_path_required");
  if (typeof options.resolveSources !== "function")
    throw Error("source_resolver_required");
  const host = connectHostControlFile(
    options.hostDiscoveryFile,
    options.dataScope,
  );
  const initialProvisioning: InitialProvisioningPort | undefined =
    options.provisioningAuthorityFile
      ? {
          async claim(request) {
            const authority = await readAuthority(
                options.provisioningAuthorityFile!,
              ),
              paths = layout(authority.directory, authority.dataScope);
            if (
              request.previousInstanceId !== null ||
              request.dataScope !== authority.dataScope ||
              options.dataScope !== authority.dataScope ||
              !same(request.target.identity, authority.initial) ||
              request.target.handle !== "release:" + authority.initial.digest ||
              resolve(options.dataDirectory) !== paths.dataDirectory ||
              resolve(options.releaseDirectory) !== paths.releaseDirectory ||
              resolve(options.hostDiscoveryFile) !== paths.hostDiscoveryFile
            )
              throw Error("initial_authority_mismatch");
            request.signal.throwIfAborted();
            // Absence is merely an additional guard inside the explicitly allocated
            // namespace, never the source of provisioning authority.
            await absent(paths.hostDiscoveryFile);
            await absent(paths.applicationStateDirectory);
            const proof = {
              kind: "pristine-installation" as const,
              installationId: authority.installationId,
              commandId: token(request.commandId),
              instanceId: token(request.instanceId),
              dataScope: authority.dataScope,
              targetDigest: authority.initial.digest,
            };
            await writeOnce(
              join(authority.directory, "initial-provisioning.claim"),
              {
                schema: "distribution-initial-claim-v1",
                ...proof,
                claimedAt: Date.now(),
              },
            );
            return proof;
          },
        }
      : undefined;
  return {
    inspect: async () => {
      const actual = await host.inspect();
      if (!actual) throw Error("host_control_unavailable");
      return actual;
    },
    observeStatus: host.observeStatus,
    admitRestart: host.admitRestart,
    reconcileAdmission: host.reconcileAdmission,
    inspectAdmissionFence: host.inspectAdmissionFence,
    inspectAdmissionAbort: host.inspectAdmissionAbort,
    abortAdmission: host.abortAdmission,
    service: host.service,
    verifyRecoveryFence: host.verifyRecoveryFence,
    onIdle: (callback) => host.onIdle(callback),
    close: () => host.close(),
    initialProvisioning,
    resolveSources: options.resolveSources,
    fetch: options.fetch,
  };
}
export interface ProductionSupervisorConfiguration
  extends Omit<SupervisorConfiguration, "adapterModule" | "adapterConfig"> {
  hostDiscoveryFile: string;
  provisioningAuthorityFile?: string;
}
/** Public composition without an arbitrary operator adapterModule. A real source
 * resolver is still required; this factory cannot establish publisher freshness
 * or native-generation currency by trusting the manifest being installed. */
export async function runProductionSupervisor(
  configuration: ProductionSupervisorConfiguration,
  options: {
    resolveSources: ReleaseAdapterOptions["resolveSources"];
    fetch?: typeof fetch;
    startInitial?: boolean;
  },
) {
  if (options.startInitial && !configuration.provisioningAuthorityFile)
    throw Error("initial_authority_required");
  if (
    configuration.serviceLifecycle &&
    configuration.provisioningAuthorityFile
  ) {
    const authority = await readAuthority(
      configuration.provisioningAuthorityFile,
    );
    if (
      configuration.serviceLifecycle.installationId !== authority.installationId
    )
      throw Error("service_owner_binding_conflict");
  }
  const ports = createProductionSupervisorPorts({
    dataScope: configuration.dataScope,
    dataDirectory: configuration.dataDirectory,
    releaseDirectory: configuration.release.directory,
    hostDiscoveryFile: configuration.hostDiscoveryFile,
    provisioningAuthorityFile: configuration.provisioningAuthorityFile,
    resolveSources: options.resolveSources,
    fetch: options.fetch,
  });
  // Reobserve source currency at the first-launch boundary as well as normal
  // forward preparation. A previously prepared candidate is not a standing pin.
  const releases = new SignedReleaseAdapter({
    ...configuration.release,
    resolveSources: options.resolveSources,
    fetch: options.fetch,
  });
  ports.qualifyInitial = async (target, context) => {
    const qualified = await releases.prepare(target.identity, context);
    if (
      !same(qualified.identity, target.identity) ||
      qualified.handle !== target.handle
    )
      throw Error("initial_release_unverified");
  };
  try {
    return await runSupervisor(configuration, {
      ports,
      startInitial: options.startInitial,
    });
  } catch (error) {
    await ports.close?.();
    throw error;
  }
}
