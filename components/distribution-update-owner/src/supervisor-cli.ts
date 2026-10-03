#!/usr/bin/env node
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
import { token, type PreparedRelease } from "./types.js";

/** Trusted local composition factory: use only public host/source-owner APIs.
 * The factory is operator-selected code, never supplied by a remote RPC. */
export interface SupervisorPorts
  extends Pick<
    OwnedProcessOptions,
    "inspect" | "admitRestart" | "reconcileAdmission"
  > {
  resolveSources: ReleaseAdapterOptions["resolveSources"];
  fetch?: typeof fetch;
  onIdle?: (callback: () => void) => () => void;
}
export interface SupervisorConfiguration {
  schema: "distribution-supervisor-v1";
  dataDirectory: string;
  dataScope: string;
  tokenFile: string;
  discoveryFile: string;
  adapterModule: string;
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
  options: { startInitial?: boolean } = {},
) {
  if (configuration.schema !== "distribution-supervisor-v1")
    throw Error("invalid_supervisor_config");
  for (const path of [
    configuration.dataDirectory,
    configuration.tokenFile,
    configuration.discoveryFile,
    configuration.adapterModule,
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
  const factory = await import(
    pathToFileURL(resolve(configuration.adapterModule)).href
  );
  if (typeof factory.createSupervisorPorts !== "function")
    throw Error("adapter_factory_missing");
  const ports = (await factory.createSupervisorPorts(
    configuration.adapterConfig,
  )) as SupervisorPorts;
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
  const lifecycle = new OwnedProcessLifecycle({
    resolve: (target) => releases.resolveLaunch(target),
    inspect: () => ports.inspect(),
    admitRestart: (context) => ports.admitRestart(context),
    reconcileAdmission: (request) =>
      ports.reconcileAdmission?.(request) ?? Promise.resolve(),
  });
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
  }
  let transport: Awaited<ReturnType<typeof serveSupervisor>> | undefined;
  const owner = new DistributionUpdateOwner({
    directory: resolve(root, "owner"),
    dataScope: configuration.dataScope,
    initial: configuration.initial,
    releases,
    lifecycle,
    onChange: (receipt) => transport?.publish(receipt),
  });
  try {
    if (options.startInitial)
      await lifecycle.startInitial(
        configuration.initial!,
        configuration.dataScope,
      );
    transport = await serveSupervisor({ owner, token: key });
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
      lifecycle,
      url: transport.url,
      async close() {
        unsubscribe?.();
        await transport?.close();
        await owner.close();
        await unlink(configuration.discoveryFile).catch(() => {});
      },
    };
  } catch (error) {
    await transport?.close();
    await owner.close();
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
    const supervisor = await runSupervisor(config, {
      startInitial: args.has("--start-initial"),
    });
    process.stdout.write(JSON.stringify({ ready: true }) + "\n");
    let closing = false;
    const shutdown = async () => {
      if (closing) return;
      closing = true;
      await supervisor.close();
      process.exit(0);
    };
    process.once("SIGTERM", () => void shutdown());
    process.once("SIGINT", () => void shutdown());
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
    process.stderr.write(JSON.stringify({ error: code }) + "\n");
    process.exit(1);
  });
