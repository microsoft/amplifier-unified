#!/usr/bin/env node
import { startupFailureFrom } from "./startup-diagnostics.js";
import {
  readFile,
  writeFile,
  mkdir,
  lstat,
  rename,
  unlink,
} from "node:fs/promises";
import {
  dirname,
  isAbsolute,
  resolve,
  basename,
  relative,
  sep,
} from "node:path";
import { watch as watchDirectory, type FSWatcher } from "node:fs";
import { pathToFileURL } from "node:url";
import { randomBytes, randomUUID } from "node:crypto";
import { DistributionUpdateOwner } from "./owner.js";
import { PosixOwnedProcessLifecycle } from "./posix-lifecycle.js";
import { ServiceLifecycleOwner } from "./service-owner.js";
import type { ServiceHostPort } from "./service-types.js";
import type { ServiceReceipt } from "./service-types.js";
import {
  SignedReleaseAdapter,
  type ReleaseAdapterOptions,
} from "./releases.js";
import {
  OwnedProcessLifecycle,
  type OwnedProcessOptions,
} from "./lifecycle.js";
import {
  SupervisorClient,
  serveSupervisor,
  type SupervisorConnection,
} from "./transport.js";
import { token, type PreparedRelease, type OperationContext } from "./types.js";

/** Trusted local composition factory: use only public host/source-owner APIs.
 * The factory is operator-selected code, never supplied by a remote RPC. */
export interface SupervisorPorts
  extends Pick<
    OwnedProcessOptions,
    "inspect" | "admitRestart" | "reconcileAdmission" | "initialProvisioning"
  > {
  resolveSources: ReleaseAdapterOptions["resolveSources"];
  fetch?: typeof fetch;
  onIdle?: (callback: () => void) => () => void;
  close?: () => void | Promise<void>;
  qualifyInitial?: (
    target: PreparedRelease,
    context: OperationContext,
  ) => Promise<void>;
  service?: ServiceHostPort;
}
export interface SupervisorConfiguration {
  schema: "distribution-supervisor-v1";
  dataDirectory: string;
  dataScope: string;
  tokenFile: string;
  discoveryFile: string;
  adapterModule?: string;
  adapterConfig?: unknown;
  release: Pick<
    ReleaseAdapterOptions,
    | "directory"
    | "channelUrl"
    | "trustedKeys"
    | "accessScope"
    | "allowedArtifactOrigins"
    | "allowLoopbackHttp"
    | "launchArgs"
    | "launchEnv"
  >;
  initial?: PreparedRelease;
  /** Explicit trusted installation binding. Omission keeps service operations
   * unavailable. The host must independently declare complete stop coverage. */
  serviceLifecycle?: { installationId: string; ownerId: string };
}
async function privateBytes(path: string, max = 1024 * 1024): Promise<Buffer> {
  const info = await lstat(path);
  if (
    !info.isFile() ||
    info.isSymbolicLink() ||
    info.size > max ||
    (process.platform !== "win32" && (info.mode & 0o077) !== 0)
  )
    throw Error("private_file_required");
  return readFile(path);
}
export async function connectSupervisorFile(
  file: string,
): Promise<SupervisorClient> {
  return new SupervisorClient(await readSupervisorConnection(file));
}
async function readSupervisorConnection(
  file: string,
): Promise<SupervisorConnection> {
  if (!isAbsolute(file)) throw Error("absolute_config_path_required");
  const value = JSON.parse((await privateBytes(file)).toString("utf8"));
  if (
    !value ||
    value.schema !== "distribution-supervisor-connection-v1" ||
    Object.keys(value).some(
      (k) => !["schema", "url", "tokenFile"].includes(k),
    ) ||
    typeof value.url !== "string" ||
    typeof value.tokenFile !== "string" ||
    !isAbsolute(value.tokenFile)
  )
    throw Error("invalid_supervisor_connection");
  return {
    url: value.url,
    token: (await privateBytes(value.tokenFile, 128)).toString("utf8").trim(),
  };
}
/** Construct during child composition, before initial provisioning publishes
 * discovery. Construction is passive; an unavailable RPC fails immediately and
 * is never queued or retried. Observation connections follow private discovery. */
export function connectSupervisorFileLazy(file: string): SupervisorClient {
  if (!isAbsolute(file)) throw Error("absolute_config_path_required");
  return new SupervisorClient({
    connect: () => readSupervisorConnection(file),
    onChange: (notify) => watchSupervisorDiscovery(file, notify),
  });
}
function watchSupervisorDiscovery(
  file: string,
  notify: () => void,
): () => void {
  let closed = false,
    watcher: FSWatcher | undefined;
  let watchedDirectory: string | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let arming = false,
    again = false;
  const schedule = () => {
    if (closed || timer) return;
    // File rename/write bursts produce one bounded observation wakeup. No
    // recurring filesystem poll and no command is triggered by this watcher.
    timer = setTimeout(() => {
      timer = undefined;
      if (closed) return;
      notify();
      void arm();
    }, 25);
    timer.unref();
  };
  async function arm() {
    if (closed) return;
    if (arming) {
      again = true;
      return;
    }
    arming = true;
    try {
      // If provisioning has not created the directory, watch its nearest
      // existing ancestor, then descend when the directory appears.
      let directory = dirname(file);
      while (true) {
        try {
          const info = await lstat(directory);
          if (info.isDirectory()) break;
        } catch (error) {
          if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
        }
        const parent = dirname(directory);
        if (parent === directory) throw Error("discovery_watch_unavailable");
        directory = parent;
      }
      if (closed) return;
      if (watcher && watchedDirectory === directory) return;
      const expected =
        directory === dirname(file)
          ? basename(file)
          : relative(directory, file).split(sep)[0];
      const next = watchDirectory(
        directory,
        { persistent: false },
        (_event, name) => {
          if (name === null || name.toString() === expected) schedule();
        },
      );
      next.on("error", () => {
        if (watcher === next) {
          watcher.close();
          watcher = undefined;
          watchedDirectory = undefined;
        }
        schedule();
      });
      watcher?.close();
      watcher = next;
      watchedDirectory = directory;
      // Re-read discovery AFTER attaching its watch. It may have been created
      // or replaced between ancestor selection and watch registration. Without
      // this wake a healthy old SSE connection can miss replacement forever.
      notify();
      if (directory !== dirname(file)) {
        try {
          if ((await lstat(dirname(file))).isDirectory()) again = true;
        } catch {
          /* The ancestor watch will report later directory creation. */
        }
      }
    } catch {
      // Transport observation reconnection still re-resolves discovery using
      // bounded backoff if native filesystem notifications are unavailable.
    } finally {
      arming = false;
      if (again && !closed) {
        again = false;
        void arm();
      }
    }
  }
  void arm();
  return () => {
    closed = true;
    if (timer) clearTimeout(timer);
    watcher?.close();
  };
}
export async function runSupervisor(
  configuration: SupervisorConfiguration,
  options: { startInitial?: boolean; ports?: SupervisorPorts } = {},
) {
  if (configuration.schema !== "distribution-supervisor-v1")
    throw Error("invalid_supervisor_config");
  for (const path of [
    configuration.dataDirectory,
    configuration.tokenFile,
    configuration.discoveryFile,
    configuration.release.directory,
  ])
    if (typeof path !== "string" || !isAbsolute(path))
      throw Error("absolute_config_path_required");
  const root = resolve(configuration.dataDirectory);
  token(configuration.dataScope);
  await mkdir(root, { recursive: true, mode: 0o700 });
  if ((await lstat(root)).isSymbolicLink())
    throw Error("unsafe_supervisor_directory");
  let key: string;
  try {
    key = (await privateBytes(configuration.tokenFile, 128))
      .toString("utf8")
      .trim();
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    key = randomBytes(32).toString("hex");
    await writeFile(configuration.tokenFile, key + "\n", {
      flag: "wx",
      mode: 0o600,
    });
  }
  let ports = options.ports;
  if (!ports) {
    if (
      typeof configuration.adapterModule !== "string" ||
      !isAbsolute(configuration.adapterModule)
    )
      throw Error("absolute_config_path_required");
    const factory = await import(
      pathToFileURL(resolve(configuration.adapterModule)).href
    );
    if (typeof factory.createSupervisorPorts !== "function")
      throw Error("adapter_factory_missing");
    ports = (await factory.createSupervisorPorts(
      configuration.adapterConfig,
    )) as SupervisorPorts;
  }
  const selectedPorts = ports;
  if (
    typeof ports.inspect !== "function" ||
    typeof ports.admitRestart !== "function" ||
    typeof ports.resolveSources !== "function"
  )
    throw Error("adapter_ports_missing");
  const releases = new SignedReleaseAdapter({
    ...configuration.release,
    resolveSources: ports.resolveSources,
    fetch: ports.fetch,
  });
  const serviceBinding = configuration.serviceLifecycle;
  if (serviceBinding) {
    token(serviceBinding.installationId);
    token(serviceBinding.ownerId);
    if (!ports.service) throw Error("service_host_port_required");
  }
  const lifecycleOptions: OwnedProcessOptions = {
    resolve: async (target) => {
      const launch = await releases.resolveLaunch(target);
      return serviceBinding
        ? {
            ...launch,
            env: {
              ...launch.env,
              AMPLIFIER_DISTRIBUTION_INSTALLATION_ID:
                serviceBinding.installationId,
              AMPLIFIER_DISTRIBUTION_OWNER_ID: serviceBinding.ownerId,
            },
          }
        : launch;
    },
    inspect: () => ports.inspect(),
    admitRestart: (context) => ports.admitRestart(context),
    reconcileAdmission: ports.reconcileAdmission
      ? (request) => ports.reconcileAdmission!(request)
      : undefined,
    initialProvisioning: ports.initialProvisioning,
  };
  const lifecycle = serviceBinding
    ? new PosixOwnedProcessLifecycle({
        ...lifecycleOptions,
        ownerId: serviceBinding.ownerId,
      })
    : new OwnedProcessLifecycle(lifecycleOptions);
  // Explicit first launch is one-shot provisioning, not a supervisor-restart
  // policy. A retained ledger must be reconciled before any new process effects.
  if (options.startInitial) {
    if (!configuration.initial) throw Error("initial_release_required");
    try {
      await lstat(resolve(root, "owner", "updates.sqlite3"));
      throw Error("initial_launch_already_attempted");
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
    if (
      !(await releases.verify(configuration.initial, {
        commandId: "initial-verification",
        signal: new AbortController().signal,
      }))
    )
      throw Error("initial_release_unverified");
    await ports.qualifyInitial?.(configuration.initial, {
      commandId: "initial-source-verification",
      signal: new AbortController().signal,
    });
  }
  let transport: Awaited<ReturnType<typeof serveSupervisor>> | undefined;
  let service: ServiceLifecycleOwner | undefined;
  const owner = new DistributionUpdateOwner({
    directory: resolve(root, "owner"),
    dataScope: configuration.dataScope,
    initial: configuration.initial,
    releases,
    lifecycle,
    mutationBlocked: () => service?.blocksUpdates() ?? false,
    onChange: (receipt) => transport?.publish(receipt),
  });
  try {
    await owner.loadInstalledReleaseNotes();
    if (serviceBinding && lifecycle instanceof PosixOwnedProcessLifecycle) {
      service = new ServiceLifecycleOwner({
        directory: resolve(root, "service"),
        ...serviceBinding,
        dataScope: configuration.dataScope,
        host: ports.service!,
        lifecycle,
        releases,
        currentRelease: () => owner.qualifiedCurrent(),
        updateMutationBlocked: () => owner.blocksServiceStop(),
        onChange: (receipt) => transport?.publishService(receipt),
      });
    }
    if (options.startInitial)
      await lifecycle.startInitial(
        configuration.initial!,
        configuration.dataScope,
      );
    transport = await serveSupervisor({ owner, service, token: key });
    const discovery = {
      schema: "distribution-supervisor-connection-v1",
      url: transport.url,
      tokenFile: resolve(configuration.tokenFile),
    };
    await mkdir(dirname(configuration.discoveryFile), {
      recursive: true,
      mode: 0o700,
    });
    const temporary = configuration.discoveryFile + "." + randomUUID();
    await writeFile(temporary, JSON.stringify(discovery) + "\n", {
      flag: "wx",
      mode: 0o600,
    });
    await rename(temporary, configuration.discoveryFile);
    const unsubscribe = ports.onIdle?.(() => owner.notifyIdle());
    owner.start();
    return {
      owner,
      service,
      lifecycle,
      url: transport.url,
      /** Qualified foreground shutdown. A refusal/unknown keeps this authority
       * reachable; never orphan an active child by merely closing transport. */
      async stopService(commandId: string) {
        if (!service) throw Error("service_lifecycle_not_configured");
        const observation = await service.inspect();
        if (observation.state === "stopped")
          return service.receipt(observation.stoppedReceiptId)!;
        if (observation.state !== "running")
          throw Error("process_ownership_unproven");
        service.stop({
          commandId: token(commandId),
          expected: observation.identity,
        });
        return service.waitFor(commandId);
      },
      async close() {
        if (service && !["stopped", "retired"].includes((await service.inspect()).state))
          throw Error("confirmed_service_stop_required");
        unsubscribe?.();
        await transport?.close();
        await owner.close();
        await service?.close();
        await selectedPorts.close?.();
        await unlink(configuration.discoveryFile).catch(() => {});
      },
    };
  } catch (error) {
    await transport?.close();
    await owner.close();
    await service?.close();
    await selectedPorts.close?.();
    throw error;
  }
}
function argumentsMap(values: string[]): Map<string, string> {
  const result = new Map<string, string>();
  for (let i = 0; i < values.length; i++) {
    const key = values[i];
    if (!key.startsWith("--") || result.has(key))
      throw Error("invalid_arguments");
    if (key === "--start-initial") {
      result.set(key, "true");
      continue;
    }
    const value = values[++i];
    if (!value || value.startsWith("--")) throw Error("invalid_arguments");
    result.set(key, value);
  }
  return result;
}
/** Register before launch. Signals request the same qualified service stop as
 * explicit callers; repeated signals cannot force an uncertain/busy process.
 * A caller controls its own final exit only after the confirmed child exit. */
export function attachSupervisorSignalHandlers(options: {
  current():
    | {
        stopService(commandId: string): Promise<ServiceReceipt>;
        close(): Promise<void>;
      }
    | undefined;
  onStopped(): void;
  onRefused(code: string): void;
}) {
  let pending = false;
  const signal = () => {
    if (pending) return;
    const supervisor = options.current();
    if (!supervisor) {
      options.onRefused("service_starting_or_unconfirmed");
      return;
    }
    pending = true;
    void (async () => {
      try {
        const receipt = await supervisor.stopService(
          "service-signal:" + randomUUID(),
        );
        if (receipt.status !== "stopped") {
          options.onRefused(
            receipt.status === "refused"
              ? "service_stop_refused"
              : "service_stop_unconfirmed",
          );
          return;
        }
        await supervisor.close();
        dispose();
        options.onStopped();
      } catch (error) {
        options.onRefused(
          error instanceof Error &&
            error.message === "service_lifecycle_not_configured"
            ? error.message
            : "service_stop_unconfirmed",
        );
      } finally {
        pending = false;
      }
    })();
  };
  const dispose = () => {
    process.off("SIGINT", signal);
    process.off("SIGTERM", signal);
  };
  process.on("SIGINT", signal);
  process.on("SIGTERM", signal);
  return dispose;
}
async function main() {
  const [command, ...rest] = process.argv.slice(2),
    args = argumentsMap(rest);
  const required = (key: string) => {
    const value = args.get(key);
    if (!value) throw Error("missing_argument");
    return value;
  };
  if (command === "serve") {
    if (
      [...args.keys()].some(
        (key) => !["--config", "--start-initial"].includes(key),
      )
    )
      throw Error("invalid_arguments");
    const config = JSON.parse(
      (await privateBytes(required("--config"))).toString("utf8"),
    ) as SupervisorConfiguration;
    let supervisor: Awaited<ReturnType<typeof runSupervisor>> | undefined;
    attachSupervisorSignalHandlers({
      current: () => supervisor,
      onStopped: () => process.exit(0),
      onRefused: (code) =>
        process.stderr.write(
          JSON.stringify({ status: "refused", code }) + "\n",
        ),
    });
    supervisor = await runSupervisor(config, {
      startInitial: args.has("--start-initial"),
    });
    process.stdout.write(JSON.stringify({ ready: true }) + "\n");
    return;
  }
  if (command === "call") {
    if (
      [...args.keys()].some(
        (key) =>
          !["--connection", "--operation", "--command-id", "--args"].includes(
            key,
          ),
      )
    )
      throw Error("invalid_arguments");
    const client = await connectSupervisorFile(required("--connection"));
    try {
      const raw = args.get("--args") ?? "{}";
      if (raw.length > 8192) throw Error("request_limit");
      const result = await client.rpc(
        required("--operation"),
        JSON.parse(raw),
        args.get("--command-id"),
      );
      process.stdout.write(JSON.stringify(result) + "\n");
    } finally {
      client.close();
    }
    return;
  }
  if (command === "watch") {
    if ([...args.keys()].some((key) => key !== "--connection"))
      throw Error("invalid_arguments");
    const client = await connectSupervisorFile(required("--connection"));
    client.subscribe((event) =>
      process.stdout.write(JSON.stringify(event) + "\n"),
    );
    const shutdown = () => {
      client.close();
      process.exit(0);
    };
    process.once("SIGINT", shutdown);
    process.once("SIGTERM", shutdown);
    return;
  }
  throw Error("usage_serve_call_or_watch");
}
if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(resolve(process.argv[1])).href
)
  void main().catch((error) => {
    const code =
      error instanceof Error && /^[a-z_]{1,100}$/.test(error.message)
        ? error.message
        : "supervisor_failed";
    const startupFailure = startupFailureFrom(error);
    process.stderr.write(JSON.stringify({ error: code, ...(startupFailure ? {startupFailure} : {}) }) + "\n");
    process.exit(1);
  });
