import { constants } from "node:fs";
import { open, lstat, realpath, rename } from "node:fs/promises";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { randomUUID } from "node:crypto";
import { createGitSourceResolver } from "@amplifier/unified";
import { validatePortabilityStage } from "./portability-preflight.js";
import {
  SignedReleaseAdapter,
  createPristineInstallation,
  runProductionSupervisor,
  token,
} from "@amplifier/unified-distribution-update-owner";

const object = (value, allowed, label) => {
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.keys(value).some((key) => !allowed.includes(key))
  )
    throw Error("invalid_" + label);
  return value;
};
const absolute = (value) => {
  if (typeof value !== "string" || !isAbsolute(value))
    throw Error("absolute_installation_path_required");
  return resolve(value);
};
async function privateWriteOnce(path, value) {
  const handle = await open(path, "wx", 0o600);
  try {
    await handle.writeFile(JSON.stringify(value) + "\n");
    await handle.sync();
  } finally {
    await handle.close();
  }
  const directory = await open(dirname(path), constants.O_RDONLY);
  try {
    await directory.sync();
  } finally {
    await directory.close();
  }
}
export async function readInstallationConfiguration(path) {
  absolute(path);
  const handle = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const info = await handle.stat();
    if (
      !info.isFile() ||
      info.size > 1024 * 1024 ||
      (process.platform !== "win32" && info.mode & 0o077)
    )
      throw Error("private_installation_configuration_required");
    return JSON.parse(await handle.readFile("utf8"));
  } finally {
    await handle.close();
  }
}

/** Explicit fresh local installation, with a supervisor outside the replaceable
 * signed app. No service detection, prior-state adoption or automatic recovery.
 * Trust and source configuration are installer inputs, never publisher defaults. */
export async function installProductionDistribution(configuration) {
  object(
    configuration,
    [
      "schema",
      "directory",
      "dataScope",
      "release",
      "sourceTracking",
      "application",
      "releaseId",
      "serviceLifecycle",
    ],
    "installation_configuration",
  );
  if (configuration.schema !== "unified-installation-v1")
    throw Error("invalid_installation_configuration");
  if (configuration.serviceLifecycle !== undefined) {
    object(configuration.serviceLifecycle, ["enabled"], "service_lifecycle_configuration");
    if (configuration.serviceLifecycle.enabled !== true) throw Error("explicit_service_lifecycle_opt_in_required");
  }
  const directory = absolute(configuration.directory),
    dataScope = token(configuration.dataScope);
  const parent = await realpath(dirname(directory));
  if (
    directory !== join(parent, directory.slice(dirname(directory).length + 1))
  )
    throw Error("canonical_installation_directory_required");
  try {
    await lstat(directory);
    throw Error("installation_already_exists");
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  const release = object(
    configuration.release,
    [
      "channelUrl",
      "trustedKeys",
      "accessScope",
      "allowedArtifactOrigins",
      "allowLoopbackHttp",
    ],
    "release_configuration",
  );
  const sourceTracking = object(
    configuration.sourceTracking,
    ["sources", "git", "env", "timeoutMs", "deadlineMs", "concurrency"],
    "source_configuration",
  );
  const application = configuration.application;
  if (
    !application ||
    typeof application !== "object" ||
    Array.isArray(application)
  )
    throw Error("invalid_application_configuration");
  if (
    ["stateDirectory", "supervision", "applicationUpdates"].some((key) =>
      Object.hasOwn(application, key),
    ) ||
    (application.quiescence &&
      (Object.hasOwn(application.quiescence, "instanceId") ||
        Object.hasOwn(application.quiescence, "dataScope")))
  )
    throw Error("installation_owned_configuration_conflict");
  if (
    typeof application.account !== "string" ||
    !application.account ||
    !Array.isArray(application.engines) ||
    !application.engines.length ||
    !Array.isArray(application.allowedWorkspaceRoots) ||
    !application.allowedWorkspaceRoots.length
  )
    throw Error("invalid_application_configuration");
  for (const path of [
    application.webDirectory,
    application.defaultWorkspace,
    ...application.allowedWorkspaceRoots,
  ])
    absolute(path);
  if (
    !["127.0.0.1", "localhost", "::1"].includes(
      application.gateway?.host ?? "127.0.0.1",
    )
  )
    throw Error("local_installer_requires_loopback_gateway");
  await validatePortabilityStage(application, { stateDirectory: join(directory, "application") });
  const resolveSources = createGitSourceResolver(sourceTracking);
  const probe = new SignedReleaseAdapter({
    ...release,
    directory: join(directory, "releases"),
    resolveSources,
  });
  const context = {
    commandId: "installer-selection",
    signal: new AbortController().signal,
  };
  const catalog = await probe.check({ ...context, fresh: true });
  const selectedId =
    configuration.releaseId === undefined
      ? catalog.recommendedId
      : token(configuration.releaseId, 100);
  const selected = catalog.releases.find((row) => row.id === selectedId);
  if (!selected) throw Error("release_not_found");
  // Recheck namespace containment after release selection, before allocation.
  await validatePortabilityStage(application, { stateDirectory: join(directory, "application") });
  const installation = await createPristineInstallation({
    directory,
    dataScope,
    initial: selected,
  });
  // Opt-in binds to this freshly allocated installation; never adopt a previous service.
  const serviceLifecycle = configuration.serviceLifecycle
    ? {installationId: (await readInstallationConfiguration(installation.authorityFile)).installationId, ownerId: randomUUID()}
    : undefined;
  const attemptFile = join(directory, "installer-attempt.json");
  let phase = "allocated";
  const record = async (status) => {
    const temporary = attemptFile + "." + randomUUID();
    await privateWriteOnce(temporary, {
      schema: "unified-installation-attempt-v1",
      phase,
      status,
      dataScope,
      releaseId: selected.id,
      updatedAt: Date.now(),
      workReplayed: false,
    });
    await rename(temporary, attemptFile);
    const handle = await open(directory, constants.O_RDONLY);
    try {
      await handle.sync();
    } finally {
      await handle.close();
    }
  };
  await record("preparing");
  try {
    const applicationFile = join(directory, "application.json"),
      supervisorFile = join(directory, "supervisor-configuration.json");
    const concreteApplication = {
      ...application,
      stateDirectory: installation.applicationStateDirectory,
      applicationUpdates: {},
      supervision: {
        ...(serviceLifecycle ? {serviceLifecycle} : {}),
        trustedKeys: release.trustedKeys,
        discoveryFile: installation.supervisorDiscoveryFile,
        hostControl: {
          discoveryFile: installation.hostDiscoveryFile,
          tokenFile: installation.hostTokenFile,
        },
      },
    };
    await privateWriteOnce(applicationFile, concreteApplication);
    // Persist the exact trusted inputs before any process effect. Private input may
    // include secrets, so no raw configuration or exception detail reaches stdout.
    await privateWriteOnce(
      join(directory, "installer-input.json"),
      configuration,
    );
    const releaseOptions = {
      ...release,
      directory: installation.releaseDirectory,
      launchArgs: ["--config", applicationFile],
    };
    const releases = new SignedReleaseAdapter({
      ...releaseOptions,
      resolveSources,
    });
    phase = "preparing";
    await record("preparing");
    const initial = await releases.prepare(selected, {
      ...context,
      commandId: "installer-preparation",
    });
    const supervisorConfiguration = {
      schema: "distribution-supervisor-v1",
      ...(serviceLifecycle ? {serviceLifecycle} : {}),
      dataDirectory: installation.dataDirectory,
      dataScope,
      tokenFile: installation.supervisorTokenFile,
      discoveryFile: installation.supervisorDiscoveryFile,
      hostDiscoveryFile: installation.hostDiscoveryFile,
      provisioningAuthorityFile: installation.authorityFile,
      initial,
      release: releaseOptions,
    };
    await privateWriteOnce(supervisorFile, supervisorConfiguration);
    phase = "launch_requested";
    await record("unknown");
    const supervisor = await runProductionSupervisor(supervisorConfiguration, {
      resolveSources,
      startInitial: true,
    });
    phase = "ready";
    await record("ready");
    return {
      installation,
      applicationFile,
      supervisorFile,
      supervisor,
      initial,
    };
  } catch (error) {
    await record(
      ["launch_requested", "ready"].includes(phase)
        ? "unknown"
        : "failed-before-launch",
    ).catch(() => {});
    throw error;
  }
}
