import { createHash } from "node:crypto";
import { lstat, readFile, realpath } from "node:fs/promises";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { readSignedChannel, verifyReleaseTree } from "./releases.js";
import {
  identity,
  token,
  type ReleaseIdentity,
  type RunningIdentity,
} from "./types.js";

export interface RuntimeIdentityOptions {
  /** Pass import.meta.url from the actual signed process entrypoint, not a library. */
  entrypointUrl: string | URL;
  /** Publisher keys configured independently of the receipt or requested target. */
  trustedKeys: Record<string, string>;
  /** Actual application readiness, including required owner initialization. */
  isReady(): boolean | Promise<boolean>;
}
export interface VerifiedRuntimeIdentity {
  readonly identity: Readonly<ReleaseIdentity>;
  readonly instanceId: string;
  readonly dataScope: string;
  inspectRunning(): Promise<RunningIdentity>;
}
const MAX_RECEIPT = 16 * 1024 * 1024;
const digest = (data: Buffer) =>
  createHash("sha256").update(data).digest("hex");
const errors = new Set([
  "runtime_launch_invalid",
  "runtime_entrypoint_mismatch",
  "runtime_receipt_invalid",
  "runtime_platform_mismatch",
  "runtime_inventory_mismatch",
  "runtime_identity_changed",
  "runtime_readiness_invalid",
]);
function safeError(error: unknown): Error {
  return Error(
    error instanceof Error && errors.has(error.message)
      ? error.message
      : "runtime_identity_unconfirmed",
  );
}
async function receiptBytes(path: string): Promise<Buffer> {
  const info = await lstat(path);
  if (
    !info.isFile() ||
    info.isSymbolicLink() ||
    info.size > MAX_RECEIPT ||
    (process.platform !== "win32" && (info.mode & 0o077) !== 0)
  )
    throw Error("runtime_receipt_invalid");
  const bytes = await readFile(path);
  if (bytes.length > MAX_RECEIPT) throw Error("runtime_receipt_invalid");
  return bytes;
}

/** Local integrity proof for a supervisor-launched signed installation. No
 * requested target identity is accepted. Not remote attestation against a
 * malicious process or a same-user writer racing executable memory loads. */
export async function createRuntimeIdentity(
  options: RuntimeIdentityOptions,
): Promise<VerifiedRuntimeIdentity> {
  try {
    const launchReceipt = process.env.AMPLIFIER_DISTRIBUTION_RELEASE_RECEIPT;
    if (
      !launchReceipt ||
      !isAbsolute(launchReceipt) ||
      !process.argv[1] ||
      typeof options.isReady !== "function"
    )
      throw Error("runtime_launch_invalid");
    const receiptPath = launchReceipt,
      instanceId = token(process.env.AMPLIFIER_DISTRIBUTION_INSTANCE_ID),
      dataScope = token(process.env.AMPLIFIER_DISTRIBUTION_DATA_SCOPE),
      entryUrl = new URL(options.entrypointUrl);
    if (entryUrl.protocol !== "file:" || entryUrl.search || entryUrl.hash)
      throw Error("runtime_entrypoint_mismatch");
    const candidatePath = dirname(receiptPath);
    const candidateInfo = await lstat(candidatePath);
    if (!candidateInfo.isDirectory() || candidateInfo.isSymbolicLink())
      throw Error("runtime_receipt_invalid");
    const candidate = await realpath(candidatePath),
      root = join(candidate, "package"),
      bytes = await receiptBytes(receiptPath),
      initialReceiptDigest = digest(bytes),
      receipt = JSON.parse(bytes.toString("utf8"));
    if (
      !receipt ||
      receipt.schema !== "distribution-candidate-v1" ||
      typeof receipt.releaseId !== "string" ||
      Object.keys(receipt).some(
        (k) => !["schema", "releaseId", "signed"].includes(k),
      )
    )
      throw Error("runtime_receipt_invalid");
    // Channel expiry affects forward discovery, not a verified installed release
    // or rollback. Signature, complete descriptor and inventory still apply.
    const { channel } = readSignedChannel(
        receipt.signed,
        { ...options.trustedKeys },
        false,
      ),
      selected = channel.releases.find(
        (r) => r.identity.id === receipt.releaseId,
      );
    if (!selected) throw Error("runtime_receipt_invalid");
    const release = selected;
    if (
      (release.platform !== "any" && release.platform !== process.platform) ||
      (release.arch !== "any" && release.arch !== process.arch)
    )
      throw Error("runtime_platform_mismatch");
    const expectedEntry = join(root, release.entrypoint),
      loadedEntry = await realpath(fileURLToPath(entryUrl)),
      processEntry = await realpath(resolve(process.argv[1]));
    if (loadedEntry !== expectedEntry || processEntry !== expectedEntry)
      throw Error("runtime_entrypoint_mismatch");
    const boundIdentity = Object.freeze(identity(release.identity));
    async function verify() {
      const directory = await lstat(candidatePath);
      if (
        !directory.isDirectory() ||
        directory.isSymbolicLink() ||
        (await realpath(candidatePath)) !== candidate ||
        digest(await receiptBytes(receiptPath)) !== initialReceiptDigest
      )
        throw Error("runtime_identity_changed");
      if (!(await verifyReleaseTree(root, release)))
        throw Error("runtime_inventory_mismatch");
    }
    await verify();
    let inspecting: Promise<RunningIdentity> | null = null;
    return Object.freeze({
      identity: boundIdentity,
      instanceId,
      dataScope,
      inspectRunning(): Promise<RunningIdentity> {
        // Join simultaneous readers, but never cache readiness/integrity across
        // completed reads: a changed file must not inherit an old ready proof.
        if (!inspecting)
          inspecting = (async () => {
            try {
              await verify();
              const ready = await options.isReady();
              if (typeof ready !== "boolean")
                throw Error("runtime_readiness_invalid");
              return {
                identity: { ...boundIdentity },
                instanceId,
                dataScope,
                ready,
              };
            } catch (error) {
              throw safeError(error);
            } finally {
              inspecting = null;
            }
          })();
        return inspecting;
      },
    });
  } catch (error) {
    throw safeError(error);
  }
}
