import {
  parseReleaseNotes,
  type ReleaseNotesPublication,
  type ReleaseNotesWarning,
} from "./release-notes.js";
import {
  createHash,
  createPublicKey,
  verify as verifySignature,
  randomUUID,
} from "node:crypto";
import {
  mkdir,
  readFile,
  writeFile,
  rename,
  rm,
  lstat,
  readdir,
  chmod,
} from "node:fs/promises";
import { createReadStream } from "node:fs";
import { createGunzip } from "node:zlib";
import { Transform } from "node:stream";
import { EventEmitter } from "node:events";
import { join, resolve, posix } from "node:path";
import * as tar from "tar";
import { AvailabilityCache } from "./cache.js";
import { DownloadScheduler } from "./parallel.js";
import {
  identity,
  same,
  token,
  type ReleaseIdentity,
  type PreparedRelease,
  type OperationContext,
  type ReleasePort,
} from "./types.js";
import type { LaunchSpec } from "./lifecycle.js";

export interface ReleaseFile {
  path: string;
  sha256: string;
  bytes: number;
  mode: 420 | 493;
}
export interface ReleaseComponent {
  name: string;
  version: string;
  root: string;
  repository: string;
  ref: string;
  revision: string;
}
export interface ReleaseDescriptor {
  identity: ReleaseIdentity;
  artifact: { url: string; sha256: string; bytes: number };
  entrypoint: string;
  platform: string;
  arch: string;
  files: ReleaseFile[];
  components: ReleaseComponent[];
}
export interface SignedChannel {
  schema: "distribution-signed-channel-v1";
  keyId: string;
  payload: string;
  signature: string;
}
export interface ReleaseChannel {
  schema: "distribution-channel-v1";
  expiresAt: number;
  recommendedId: string | null;
  releases: ReleaseDescriptor[];
  releaseNotes?: ReleaseNotesPublication;
  releaseNotesWarning?: ReleaseNotesWarning;
}
export interface SourceObservation {
  repository: string;
  ref: string;
  revision: string;
  protected: boolean;
}
export interface SourceResolutionContext extends OperationContext {
  /** Bypass completed cached results; concurrent live observations may coalesce. */
  fresh: true;
  reason: "preparation" | "activation";
}
export interface ReleaseAdapterOptions {
  directory: string;
  channelUrl: string;
  trustedKeys: Record<string, string>;
  /** Credential scope is opaque, not an API key or account name. */
  accessScope: string;
  allowedArtifactOrigins: string[];
  /** Test/development loopback HTTP must be opted into explicitly. */
  allowLoopbackHttp?: boolean;
  fetch?: typeof globalThis.fetch;
  /** Resolve configured tracking refs through the public source owner. No pins
   * are changed here; protected overrides are retained and block preparation. */
  resolveSources(
    components: ReleaseComponent[],
    context: SourceResolutionContext,
  ): Promise<SourceObservation[]>;
  launchArgs?: string[];
  launchEnv?: NodeJS.ProcessEnv;
}
const MAX_CHANNEL = 16 * 1024 * 1024,
  MAX_ARCHIVE = 256 * 1024 * 1024,
  MAX_TREE = 1024 * 1024 * 1024;
const sha = (data: Buffer | string) =>
  createHash("sha256").update(data).digest("hex");
function hex(value: unknown): string {
  if (typeof value !== "string" || !/^[a-f0-9]{64}$/.test(value))
    throw Error("invalid_digest");
  return value;
}
function text(value: unknown, max = 512): string {
  if (
    typeof value !== "string" ||
    !value ||
    value.length > max ||
    /[\x00-\x1f\x7f]/.test(value)
  )
    throw Error("invalid_manifest_text");
  return value;
}
export function releasePath(value: unknown): string {
  const path = text(value);
  if (
    path !== path.normalize("NFC") ||
    path !== posix.normalize(path) ||
    path.startsWith("/") ||
    path === "." ||
    path
      .split("/")
      .some(
        (part) =>
          part === ".." ||
          !part ||
          /[\\:]/.test(part) ||
          /[. ]$/.test(part) ||
          /^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/i.test(part),
      )
  )
    throw Error("invalid_release_path");
  return path;
}
function repository(value: string): string {
  const url = new URL(text(value));
  if (
    url.protocol !== "https:" ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  )
    throw Error("invalid_source");
  return url.href;
}
/** Canonical digest binds every installed file, component provenance and entrypoint.
 * Archive hashes are separate to avoid circular package/manifest identities. */
export function releaseDigest(
  value: Omit<ReleaseDescriptor, "identity" | "artifact"> & {
    identity: Pick<ReleaseIdentity, "id" | "version" | "revision">;
  },
): string {
  return sha(
    JSON.stringify({
      id: value.identity.id,
      version: value.identity.version,
      revision: value.identity.revision,
      entrypoint: value.entrypoint,
      platform: value.platform,
      arch: value.arch,
      // Digest ordering must be identical across publisher and client locales,
      // and must not depend on the caller's JSON property insertion order.
      files: value.files
        .map((f) => ({
          path: f.path,
          sha256: f.sha256,
          bytes: f.bytes,
          mode: f.mode,
        }))
        .sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0)),
      components: value.components
        .map((c) => ({
          name: c.name,
          version: c.version,
          root: c.root,
          repository: c.repository,
          ref: c.ref,
          revision: c.revision,
        }))
        .sort((a, b) => (a.root < b.root ? -1 : a.root > b.root ? 1 : 0)),
    }),
  );
}
function descriptor(value: ReleaseDescriptor): ReleaseDescriptor {
  const id = identity(value?.identity),
    entrypoint = releasePath(value.entrypoint);
  if (
    !["any", "darwin", "linux", "win32"].includes(value.platform) ||
    !["any", "arm64", "x64"].includes(value.arch) ||
    !Array.isArray(value.files) ||
    !value.files.length ||
    value.files.length > 50000 ||
    !Array.isArray(value.components) ||
    !value.components.length ||
    value.components.length > 500
  )
    throw Error("manifest_limit");
  let bytes = 0;
  const files = value.files.map((file) => {
    const path = releasePath(file.path);
    if (
      !Number.isSafeInteger(file.bytes) ||
      file.bytes < 0 ||
      file.bytes > 128 * 1024 * 1024 ||
      ![420, 493].includes(file.mode)
    )
      throw Error("invalid_release_file");
    bytes += file.bytes;
    return {
      path,
      sha256: hex(file.sha256),
      bytes: file.bytes,
      mode: file.mode,
    };
  });
  if (
    bytes > MAX_TREE ||
    new Set(files.map((file) => file.path.toLowerCase())).size !==
      files.length ||
    !files.some((file) => file.path === entrypoint) ||
    !/[.]([cm]?js)$/.test(entrypoint)
  )
    throw Error("manifest_limit");
  const components = value.components.map((c) => {
    text(c.name, 200);
    text(c.version, 80);
    text(c.ref, 200);
    if (!/^[a-f0-9]{40,64}$/.test(c.revision)) throw Error("invalid_source");
    return {
      name: c.name,
      version: c.version,
      root: c.root === "" ? "" : releasePath(c.root),
      repository: repository(c.repository),
      ref: c.ref,
      revision: c.revision,
    };
  });
  if (
    new Set(components.map((c) => c.root)).size !== components.length ||
    !components.some((c) => c.root === "")
  )
    throw Error("component_inventory_invalid");
  for (const c of components)
    if (
      !files.some(
        (f) => f.path === (c.root ? c.root + "/" : "") + "package.json",
      )
    )
      throw Error("component_inventory_invalid");
  const artifact = {
    url: text(value.artifact.url, 2048),
    sha256: hex(value.artifact.sha256),
    bytes: value.artifact.bytes,
  };
  if (
    !Number.isSafeInteger(artifact.bytes) ||
    artifact.bytes < 1 ||
    artifact.bytes > MAX_ARCHIVE
  )
    throw Error("archive_limit");
  const result = {
    identity: id,
    artifact,
    entrypoint,
    platform: value.platform,
    arch: value.arch,
    files,
    components,
  };
  if (releaseDigest(result) !== id.digest)
    throw Error("release_digest_mismatch");
  return result;
}
export function readSignedChannel(
  value: unknown,
  keys: Record<string, string>,
  requireFresh = true,
): { envelope: SignedChannel; channel: ReleaseChannel } {
  const input = value as SignedChannel;
  if (
    input?.schema !== "distribution-signed-channel-v1" ||
    typeof input.payload !== "string" ||
    input.payload.length > MAX_CHANNEL ||
    typeof input.signature !== "string" ||
    !Object.hasOwn(keys, input.keyId)
  )
    throw Error("channel_untrusted");
  const payload = Buffer.from(input.payload, "base64"),
    signature = Buffer.from(input.signature, "base64");
  if (payload.toString("base64") !== input.payload || signature.length !== 64)
    throw Error("channel_untrusted");
  const key = createPublicKey(keys[input.keyId]);
  if (
    key.asymmetricKeyType !== "ed25519" ||
    !verifySignature(null, payload, key, signature)
  )
    throw Error("channel_untrusted");
  const parsed = JSON.parse(payload.toString("utf8")) as ReleaseChannel;
  if (
    parsed?.schema !== "distribution-channel-v1" ||
    !Number.isSafeInteger(parsed.expiresAt) ||
    (requireFresh && parsed.expiresAt <= Date.now()) ||
    !Array.isArray(parsed.releases) ||
    parsed.releases.length > 20
  )
    throw Error("channel_expired_or_invalid");
  const releases = parsed.releases.map(descriptor);
  if (
    new Set(releases.map((r) => r.identity.id)).size !== releases.length ||
    (parsed.recommendedId !== null &&
      !releases.some((r) => r.identity.id === parsed.recommendedId))
  )
    throw Error("channel_invalid");
  // Optional editorial data must not disable an otherwise trusted update.
  // The enclosing signature is already verified; never consume unsigned notes.
  let releaseNotes: ReleaseNotesPublication | undefined;
  let releaseNotesWarning: ReleaseNotesWarning = "release_notes_unavailable";
  if (parsed.releaseNotes !== undefined) {
    try {
      releaseNotes = parseReleaseNotes(parsed.releaseNotes);
      releaseNotesWarning = null;
    } catch {
      releaseNotesWarning = "release_notes_invalid";
    }
  }
  return {
    envelope: {
      schema: input.schema,
      keyId: token(input.keyId),
      payload: input.payload,
      signature: input.signature,
    },
    channel: {
      schema: parsed.schema,
      expiresAt: parsed.expiresAt,
      recommendedId: parsed.recommendedId,
      releases,
      ...(releaseNotes ? { releaseNotes } : {}),
      releaseNotesWarning,
    },
  };
}
async function privateDir(path: string) {
  await mkdir(path, { recursive: true, mode: 0o700 });
  const info = await lstat(path);
  if (!info.isDirectory() || info.isSymbolicLink())
    throw Error("unsafe_release_directory");
  await chmod(path, 0o700);
}
async function boundedFile(path: string, limit: number): Promise<Buffer> {
  const info = await lstat(path);
  if (!info.isFile() || info.isSymbolicLink() || info.size > limit)
    throw Error("invalid_local_file");
  return readFile(path);
}
async function treeFiles(
  root: string,
  prefix = "",
  result: string[] = [],
): Promise<string[]> {
  for (const dirent of await readdir(join(root, prefix), {
    withFileTypes: true,
  })) {
    const path = prefix ? prefix + "/" + dirent.name : dirent.name;
    releasePath(path);
    if (dirent.isSymbolicLink()) throw Error("local_source_changes");
    if (dirent.isDirectory()) await treeFiles(root, path, result);
    else if (dirent.isFile()) result.push(path);
    else throw Error("local_source_changes");
    if (result.length > 50000) throw Error("local_source_changes");
  }
  return result;
}
export async function verifyReleaseTree(
  root: string,
  release: ReleaseDescriptor,
): Promise<boolean> {
  try {
    const rootInfo = await lstat(root);
    if (!rootInfo.isDirectory() || rootInfo.isSymbolicLink()) return false;
    const files = await treeFiles(root);
    if (files.length !== release.files.length) return false;
    const expected = new Map(release.files.map((f) => [f.path, f]));
    for (const path of files) {
      const f = expected.get(path);
      if (!f) return false;
      const data = await boundedFile(join(root, path), f.bytes);
      if (data.length !== f.bytes || sha(data) !== f.sha256) return false;
      const info = await lstat(join(root, path));
      if (process.platform !== "win32" && (info.mode & 0o777) !== f.mode)
        return false;
    }
    // Enumerate every actual installed package, not just named top-level entries.
    const packages = files.filter(
      (path) =>
        path === "package.json" ||
        /\bnode_modules\/(?:@[^/]+\/)?[^/]+\/package.json$/.test(path),
    );
    if (packages.length !== release.components.length) return false;
    for (const c of release.components) {
      const path = (c.root ? c.root + "/" : "") + "package.json";
      if (!packages.includes(path)) return false;
      const pkg = JSON.parse(await readFile(join(root, path), "utf8"));
      if (pkg.name !== c.name || pkg.version !== c.version) return false;
    }
    return true;
  } catch {
    return false;
  }
}
async function validateArchive(file: string, release: ReleaseDescriptor) {
  const expected = new Map(release.files.map((f) => ["package/" + f.path, f])),
    seen = new Set<string>();
  await new Promise<void>((done, reject) => {
    const input = createReadStream(file),
      unzip = createGunzip();
    let expanded = 0,
      settled = false;
    const limit = new Transform({
      transform(chunk, _, callback) {
        expanded += chunk.length;
        callback(
          expanded > MAX_TREE + 128 * 1024 * 1024
            ? Error("archive_expansion_limit")
            : null,
          chunk,
        );
      },
    });
    const finish = (error?: Error) => {
      if (settled) return;
      settled = true;
      input.destroy();
      unzip.destroy();
      limit.destroy();
      if (error) reject(error);
      else done();
    };
    const parser = new tar.Parser({
      strict: true,
      maxMetaEntrySize: 1024 * 1024,
      onReadEntry: (entry) => {
        try {
          if (entry.type === "Directory") {
            const path = entry.path.replace(/\/$/, "");
            if (path !== "package" && !path.startsWith("package/"))
              throw Error("archive_inventory_mismatch");
            releasePath(path);
            entry.resume();
            return;
          }
          const f = expected.get(entry.path);
          if (
            entry.type !== "File" ||
            !f ||
            seen.has(entry.path) ||
            entry.size !== f.bytes ||
            (entry.mode! & 0o7777) !== f.mode
          )
            throw Error("archive_inventory_mismatch");
          seen.add(entry.path);
          const hash = createHash("sha256");
          let size = 0;
          entry.on("data", (data) => {
            size += data.length;
            hash.update(data);
          });
          entry.on("end", () => {
            if (size !== f.bytes || hash.digest("hex") !== f.sha256)
              parser.abort(Error("archive_inventory_mismatch"));
          });
        } catch {
          parser.abort(Error("archive_inventory_mismatch"));
        }
      },
    });
    for (const stream of [input, unzip, limit, parser])
      (stream as EventEmitter).on("error", (error: Error) => finish(error));
    parser.on("end", () =>
      finish(
        seen.size === expected.size
          ? undefined
          : Error("archive_inventory_mismatch"),
      ),
    );
    input
      .pipe(unzip)
      .pipe(limit)
      .pipe(parser as unknown as NodeJS.WritableStream);
  });
}

/** Public signed-release adapter. No registry-wide reset, in-place install,
 * install script, dependency resolution, or native-generation mutation. */
export class SignedReleaseAdapter implements ReleasePort {
  private cache = new AvailabilityCache(1, MAX_CHANNEL);
  private downloads = new DownloadScheduler(4);
  private cached: { envelope: SignedChannel; channel: ReleaseChannel } | null =
    null;
  private readonly root: string;
  constructor(private readonly options: ReleaseAdapterOptions) {
    this.root = resolve(options.directory);
    token(options.accessScope);
    this.url(options.channelUrl, false);
  }
  private url(value: string, artifact: boolean): string {
    const url = new URL(value);
    if (
      url.username ||
      url.password ||
      url.hash ||
      !(
        url.protocol === "https:" ||
        (this.options.allowLoopbackHttp &&
          url.protocol === "http:" &&
          ["127.0.0.1", "[::1]"].includes(url.hostname))
      )
    )
      throw Error("release_url_denied");
    if (artifact && !this.options.allowedArtifactOrigins.includes(url.origin))
      throw Error("artifact_origin_denied");
    return url.href;
  }
  private async fetchBytes(
    url: string,
    limit: number,
    signal: AbortSignal,
  ): Promise<Buffer> {
    const response = await (this.options.fetch ?? fetch)(url, {
      signal,
      redirect: "error",
    });
    if (!response.ok || !response.body) throw Error("release_fetch_failed");
    const reader = response.body.getReader(),
      chunks: Uint8Array[] = [];
    let size = 0;
    try {
      while (true) {
        signal.throwIfAborted();
        const chunk = await reader.read();
        if (chunk.done) break;
        size += chunk.value.length;
        if (size > limit) throw Error("download_limit");
        chunks.push(chunk.value);
      }
    } finally {
      await reader.cancel().catch(() => {});
    }
    return Buffer.concat(chunks, size);
  }
  async check(context: OperationContext & { fresh: boolean }) {
    const raw = await this.cache.get(
      this.options.channelUrl,
      this.options.accessScope,
      async () =>
        JSON.parse(
          (
            await this.fetchBytes(
              this.url(this.options.channelUrl, false),
              MAX_CHANNEL,
              context.signal,
            )
          ).toString("utf8"),
        ),
      { fresh: context.fresh, ttlMs: 300000 },
    );
    this.cached = readSignedChannel(raw, this.options.trustedKeys);
    return {
      releases: this.cached.channel.releases.map((r) => r.identity),
      recommendedId: this.cached.channel.recommendedId,
      ...(this.cached.channel.releaseNotes
        ? { releaseNotes: this.cached.channel.releaseNotes }
        : {}),
      releaseNotesWarning: this.cached.channel.releaseNotesWarning,
    };
  }
  private async observeSources(
    selected: ReleaseDescriptor,
    context: OperationContext,
    reason: SourceResolutionContext["reason"],
  ): Promise<void> {
    context.signal.throwIfAborted();
    const tracking = [
      ...new Map(
        selected.components.map((c) => [c.repository + "#" + c.ref, c]),
      ).values(),
    ];
    const sources = await this.options.resolveSources(tracking, {
      ...context,
      fresh: true,
      reason,
    });
    context.signal.throwIfAborted();
    for (const c of selected.components) {
      const match = sources.filter(
        (s) => s.repository === c.repository && s.ref === c.ref,
      );
      if (match.length !== 1 || match[0].protected !== false)
        throw Error("source_preserved");
      if (match[0].revision !== c.revision) throw Error("source_advanced");
    }
  }
  /** Fresh, read-only forward activation check. Do not fold this into verify():
   * retained rollback bytes remain valid when refs advance or the network fails.
   * Never prepare/download here or mutate the old qualification receipt. */
  async qualifyActivation(
    target: PreparedRelease,
    context: OperationContext,
  ): Promise<void> {
    const retained = await this.installed(target);
    await this.check({ ...context, fresh: true });
    const selected = this.cached!.channel.releases.find((r) =>
      same(r.identity, retained.release.identity),
    );
    if (!selected) throw Error("release_superseded");
    // The verified digest binds the complete descriptor, including its inventory.
    // Current source observations must match those exact retained signed bytes.
    await this.observeSources(selected, context, "activation");
  }
  async prepare(
    release: ReleaseIdentity,
    context: OperationContext,
  ): Promise<PreparedRelease> {
    // Re-resolve the channel and tracking refs on forward preparation; old
    // installed receipts remain reproducibility/rollback evidence, not pins.
    await this.check({ ...context, fresh: true });
    const signed = this.cached!,
      selected = signed.channel.releases.find((r) => same(r.identity, release));
    if (!selected) throw Error("release_superseded");
    if (
      (selected.platform !== "any" && selected.platform !== process.platform) ||
      (selected.arch !== "any" && selected.arch !== process.arch)
    )
      throw Error("release_platform_mismatch");
    await this.observeSources(selected, context, "preparation");
    const target = { identity: release, handle: "release:" + release.digest };
    const directory = this.location(target);
    await privateDir(this.root);
    await privateDir(join(this.root, "releases"));
    await privateDir(join(this.root, "artifacts"));
    await privateDir(join(this.root, "staging"));
    try {
      await lstat(directory);
      if (!(await this.verify(target, context)))
        throw Error("local_source_changes");
      return target;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
    const stage = join(this.root, "staging", randomUUID());
    await privateDir(stage);
    try {
      const archive = join(
        this.root,
        "artifacts",
        selected.artifact.sha256 + ".tgz",
      );
      await this.downloads.run(
        [
          {
            target: archive,
            run: async (signal) => {
              try {
                const existing = await boundedFile(
                  archive,
                  selected.artifact.bytes,
                );
                if (
                  existing.length !== selected.artifact.bytes ||
                  sha(existing) !== selected.artifact.sha256
                )
                  throw Error("local_source_changes");
                return;
              } catch (error) {
                if ((error as NodeJS.ErrnoException).code !== "ENOENT")
                  throw error;
              }
              const bytes = await this.fetchBytes(
                this.url(selected.artifact.url, true),
                selected.artifact.bytes,
                signal,
              );
              if (
                bytes.length !== selected.artifact.bytes ||
                sha(bytes) !== selected.artifact.sha256
              )
                throw Error("artifact_digest_mismatch");
              const temporary = join(stage, "archive.tgz");
              await writeFile(temporary, bytes, { flag: "wx", mode: 0o400 });
              await rename(temporary, archive);
            },
          },
        ],
        context.signal,
      );
      await validateArchive(archive, selected);
      context.signal.throwIfAborted();
      const content = join(stage, "package");
      await privateDir(content);
      await tar.x({
        file: archive,
        cwd: content,
        strip: 1,
        strict: true,
        preservePaths: false,
        chmod: true,
        filter: (_, entry) =>
          "type" in entry &&
          (entry.type === "File" || entry.type === "Directory"),
      });
      if (!(await verifyReleaseTree(content, selected)))
        throw Error("candidate_inventory_mismatch");
      context.signal.throwIfAborted();
      await writeFile(
        join(stage, "receipt.json"),
        JSON.stringify({
          schema: "distribution-candidate-v1",
          releaseId: release.id,
          signed: signed.envelope,
        }),
        { flag: "wx", mode: 0o600 },
      );
      await rename(stage, directory);
      return target;
    } finally {
      await rm(stage, { recursive: true, force: true });
    }
  }
  private location(target: PreparedRelease): string {
    identity(target.identity);
    if (target.handle !== "release:" + target.identity.digest)
      throw Error("candidate_handle_invalid");
    return join(this.root, "releases", target.identity.digest);
  }
  async notes(target: PreparedRelease) {
    const directory = this.location(target);
    const receipt = JSON.parse(
      (
        await boundedFile(join(directory, "receipt.json"), MAX_CHANNEL)
      ).toString("utf8"),
    );
    if (
      receipt.schema !== "distribution-candidate-v1" ||
      receipt.releaseId !== target.identity.id
    )
      throw Error("candidate_receipt_invalid");
    const { channel } = readSignedChannel(
      receipt.signed,
      this.options.trustedKeys,
      false,
    );
    if (!channel.releases.some((r) => same(r.identity, target.identity)))
      throw Error("candidate_receipt_invalid");
    return {
      releaseNotes: channel.releaseNotes,
      releaseNotesWarning: channel.releaseNotesWarning,
    };
  }
  async installed(
    target: PreparedRelease,
  ): Promise<{ root: string; release: ReleaseDescriptor }> {
    const directory = this.location(target),
      info = await lstat(directory);
    if (!info.isDirectory() || info.isSymbolicLink())
      throw Error("local_source_changes");
    const receipt = JSON.parse(
      (
        await boundedFile(join(directory, "receipt.json"), MAX_CHANNEL)
      ).toString("utf8"),
    );
    if (
      receipt.schema !== "distribution-candidate-v1" ||
      receipt.releaseId !== target.identity.id
    )
      throw Error("candidate_receipt_invalid");
    const { channel } = readSignedChannel(
        receipt.signed,
        this.options.trustedKeys,
        false,
      ),
      release = channel.releases.find((r) => same(r.identity, target.identity));
    if (!release) throw Error("candidate_receipt_invalid");
    return { root: join(directory, "package"), release };
  }
  async verify(
    target: PreparedRelease,
    context: OperationContext,
  ): Promise<boolean> {
    try {
      context.signal.throwIfAborted();
      const installed = await this.installed(target);
      return await verifyReleaseTree(installed.root, installed.release);
    } catch {
      return false;
    }
  }
  async resolveLaunch(target: PreparedRelease): Promise<LaunchSpec> {
    if (
      !(await this.verify(target, {
        commandId: "launch-verification",
        signal: new AbortController().signal,
      }))
    )
      throw Error("local_source_changes");
    const installed = await this.installed(target);
    return {
      command: process.execPath,
      args: [
        join(installed.root, installed.release.entrypoint),
        ...(this.options.launchArgs ?? []),
      ],
      cwd: installed.root,
      env: {
        ...this.options.launchEnv,
        AMPLIFIER_DISTRIBUTION_RELEASE_RECEIPT: join(
          this.location(target),
          "receipt.json",
        ),
      },
    };
  }
}
