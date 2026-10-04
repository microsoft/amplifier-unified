import test from "node:test";
import assert from "node:assert/strict";
import {
  mkdtemp,
  mkdir,
  writeFile,
  readFile,
  readdir,
  lstat,
  rm,
  cp,
} from "node:fs/promises";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { tmpdir } from "node:os";
import {
  createHash,
  generateKeyPairSync,
  sign,
  randomBytes,
  randomUUID,
} from "node:crypto";
import { createServer } from "node:http";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { once } from "node:events";
const fixture = JSON.parse(await readFile(process.argv[2], "utf8"));
const { WebSocket } = await import(
  new URL("./graph/package/node_modules/ws/wrapper.mjs", import.meta.url)
);
import {
  SignedReleaseAdapter,
  connectHostControlFile,
  releaseDigest,
  createPristineInstallation,
  runProductionSupervisor,
} from "@amplifier/unified-distribution-update-owner";

const execute = promisify(execFile),
  hash = (bytes) => createHash("sha256").update(bytes).digest("hex");

async function inventory(root) {
  const files = [];
  async function visit(prefix = "") {
    for (const name of await readdir(join(root, prefix))) {
      const path = prefix ? prefix + "/" + name : name,
        info = await lstat(join(root, path));
      if (info.isDirectory()) await visit(path);
      else {
        assert.ok(
          info.isFile(),
          "Signed fixture must contain only regular files",
        );
        const bytes = await readFile(join(root, path));
        files.push({
          path,
          sha256: hash(bytes),
          bytes: bytes.length,
          mode: info.mode & 0o777,
        });
      }
    }
  }
  await visit();
  return files.sort((a, b) => a.path.localeCompare(b.path));
}
async function peer(url) {
  const socket = new WebSocket(url.replace(/^http/, "ws") + "/ahp", {
    origin: url,
  });
  await once(socket, "open");
  let next = 0;
  const pending = new Map();
  socket.on("message", (raw) => {
    const row = JSON.parse(raw),
      p = pending.get(row.id);
    if (p) {
      pending.delete(row.id);
      clearTimeout(p.timer);
      row.error ? p.reject(Error(row.error.message)) : p.resolve(row.result);
    }
  });
  const request = (method, params) =>
    new Promise((resolve, reject) => {
      const id = ++next,
        timer = setTimeout(() => {
          pending.delete(id);
          reject(Error("Timed out: " + method));
        }, 15000);
      pending.set(id, { resolve, reject, timer });
      socket.send(JSON.stringify({ jsonrpc: "2.0", id, method, params }));
    });
  await request("initialize", {
    channel: "ahp-root://",
    clientId: randomUUID(),
    protocolVersions: ["0.9.0"],
    initialSubscriptions: ["ahp-root://"],
  });
  return {
    socket,
    action: async (operation, args = {}, commandId = randomUUID()) =>
      (
        await request("x-amplifier/capabilityAction", {
          channel: "ahp-root://",
          version: 1,
          topic: "application-updates",
          operation: "updates.application." + operation,
          args,
          commandId,
        })
      ).result,
  };
}

test(
  "the packaged signed CLI starts before supervisor discovery and replaces itself through real owner fences",
  { timeout: 120000 },
  async () => {
    const root = await mkdtemp(join(tmpdir(), "signed-unified-cli-")),
      workspace = join(root, "workspace"),
      web = join(root, "web");
    for (const path of [workspace, web]) await mkdir(path);
    await writeFile(
      join(web, "index.html"),
      "<html><head></head><body>Owned static fixture</body></html>",
    );
    const resources = new Map(),
      { publicKey, privateKey } = generateKeyPairSync("ed25519");
    const publisher = createServer((req, res) => {
      const bytes = resources.get(req.url);
      if (!bytes) {
        res.writeHead(404);
        res.end();
      } else res.end(bytes);
    });
    await new Promise((resolve) => publisher.listen(0, "127.0.0.1", resolve));
    const origin = "http://127.0.0.1:" + publisher.address().port,
      keys = { fixture: publicKey.export({ type: "spki", format: "pem" }) };
    let owner, lifecycle, supervisor, client, a, b, current;
    try {
      const original = join(dirname(fileURLToPath(import.meta.url)), "graph");
      async function candidate(version) {
        const directory = join(root, "source-" + version);
        await mkdir(directory);
        await cp(join(original, "package"), join(directory, "package"), {
          recursive: true,
        });
        const packageRoot = join(directory, "package"),
          entry = join(packageRoot, "src/cli.js");
        await writeFile(
          entry,
          (await readFile(entry, "utf8")) +
            "\n// Signed fixture revision " +
            version +
            "\n",
        );
        const path = join(root, "release-" + version + ".tgz");
        await execute("tar", ["-czf", path, "-C", directory, "package"], {
          env: { ...process.env, COPYFILE_DISABLE: "1" },
        });
        const bytes = await readFile(path),
          files = await inventory(packageRoot),
          components = [];
        for (const file of files.filter(
          (row) =>
            row.path === "package.json" ||
            /\bnode_modules\/(?:@[^/]+\/)?[^/]+\/package.json$/.test(row.path),
        )) {
          const pkg = JSON.parse(
              await readFile(join(packageRoot, file.path), "utf8"),
            ),
            componentRoot =
              file.path === "package.json" ? "" : dirname(file.path);
          components.push({
            name: pkg.name,
            version: pkg.version,
            root: componentRoot,
            repository:
              "https://fixture.invalid/" +
              encodeURIComponent(pkg.name) +
              ".git",
            ref: "main",
            revision: (componentRoot ? "a" : String(version)).repeat(40),
          });
        }
        const release = {
          identity: {
            id: "fixture-" + version,
            version: version + ".0.0",
            revision: String(version).repeat(40),
            digest: "",
          },
          artifact: {
            url: origin + "/release-" + version + ".tgz",
            sha256: hash(bytes),
            bytes: bytes.length,
          },
          entrypoint: "src/cli.js",
          platform: "any",
          arch: "any",
          files,
          components,
        };
        release.identity.digest = releaseDigest(release);
        resources.set("/release-" + version + ".tgz", bytes);
        return release;
      }
      const first = await candidate(1),
        second = await candidate(2);
      const publish = (release) => {
        current = release;
        const payload = Buffer.from(
          JSON.stringify({
            schema: "distribution-channel-v1",
            generatedAt: Date.now(),
            expiresAt: Date.now() + 600000,
            recommendedId: release.identity.id,
            releases: [first, second],
          }),
        );
        resources.set(
          "/channel.json",
          Buffer.from(
            JSON.stringify({
              schema: "distribution-signed-channel-v1",
              keyId: "fixture",
              payload: payload.toString("base64"),
              signature: sign(null, payload, privateKey).toString("base64"),
            }),
          ),
        );
      };
      publish(first);
      const reserve = createServer();
      await new Promise((resolve) => reserve.listen(0, "127.0.0.1", resolve));
      const port = reserve.address().port;
      await new Promise((resolve) => reserve.close(resolve));
      const installation = await createPristineInstallation({
        directory: join(root, "installation"),
        dataScope: "fixture-scope",
        initial: first.identity,
      });
      const configPath = join(root, "distribution.json"),
        hostDiscovery = installation.hostDiscoveryFile,
        supervisorDiscovery = installation.supervisorDiscoveryFile;
      await writeFile(
        configPath,
        JSON.stringify({
          account: "fixture",
          stateDirectory: installation.applicationStateDirectory,
          webDirectory: web,
          defaultWorkspace: workspace,
          allowedWorkspaceRoots: [workspace],
          gateway: { port },
          engines: [
            {
              id: "unused",
              command: process.execPath,
              args: [
                "-e",
                'throw Error("Passive startup must not start agents")',
              ],
            },
          ],
          supervision: {
            trustedKeys: keys,
            discoveryFile: supervisorDiscovery,
            hostControl: {
              discoveryFile: hostDiscovery,
              tokenFile: installation.hostTokenFile,
            },
          },
        }),
      );
      client = connectHostControlFile(hostDiscovery, "fixture-scope");
      const sourceReasons = [];
      const resolveSources = async (components, context) => {
        assert.equal(context.fresh, true);
        sourceReasons.push(context.reason);
        context.signal.throwIfAborted();
        const observed = await (await fetch(origin + "/sources")).json();
        assert.ok(components.length > 1);
        return observed;
      };
      const releaseOptions = {
        directory: installation.releaseDirectory,
        channelUrl: origin + "/channel.json",
        trustedKeys: keys,
        accessScope: "fixture",
        allowedArtifactOrigins: [origin],
        allowLoopbackHttp: true,
        launchArgs: ["--config", configPath],
      };
      resources.set(
        "/sources",
        Buffer.from(
          JSON.stringify(
            current.components.map(({ repository, ref, revision }) => ({
              repository,
              ref,
              revision,
              protected: false,
            })),
          ),
        ),
      );
      const releases = new SignedReleaseAdapter({
        ...releaseOptions,
        resolveSources,
      });
      const initial = await releases.prepare(first.identity, {
        commandId: "prepare-initial",
        signal: new AbortController().signal,
      });
      await assert.rejects(client.inspect(), /host_control_unavailable/);
      const supervisorConfiguration = {
        schema: "distribution-supervisor-v1",
        dataDirectory: installation.dataDirectory,
        dataScope: "fixture-scope",
        tokenFile: installation.supervisorTokenFile,
        discoveryFile: supervisorDiscovery,
        hostDiscoveryFile: hostDiscovery,
        provisioningAuthorityFile: installation.authorityFile,
        initial,
        release: releaseOptions,
      };
      resources.set(
        "/sources",
        Buffer.from(
          JSON.stringify(
            current.components.map(({ repository, ref }) => ({
              repository,
              ref,
              revision: "f".repeat(40),
              protected: false,
            })),
          ),
        ),
      );
      await assert.rejects(
        runProductionSupervisor(supervisorConfiguration, {
          resolveSources,
          startInitial: true,
        }),
        /source_advanced/,
      );
      await assert.rejects(
        readFile(join(installation.directory, "initial-provisioning.claim")),
        { code: "ENOENT" },
      );
      resources.set(
        "/sources",
        Buffer.from(
          JSON.stringify(
            current.components.map(({ repository, ref, revision }) => ({
              repository,
              ref,
              revision,
              protected: false,
            })),
          ),
        ),
      );
      supervisor = await runProductionSupervisor(supervisorConfiguration, {
        resolveSources,
        startInitial: true,
      });
      ({ owner, lifecycle } = supervisor);
      owner.setPreferences("manual-only", {
        autoCheck: false,
        autoInstall: false,
        intervalMs: 1000,
      });
      await owner.waitFor("manual-only");
      await assert.rejects(
        runProductionSupervisor(supervisorConfiguration, {
          resolveSources,
          startInitial: true,
        }),
        /initial_launch_already_attempted/,
      );
      const before = await client.inspect();
      assert.equal(before.identity.id, first.identity.id);
      assert.equal(before.ready, true);
      a = await peer("http://127.0.0.1:" + port);
      publish(second);
      resources.set(
        "/sources",
        Buffer.from(
          JSON.stringify(
            current.components.map(({ repository, ref, revision }) => ({
              repository,
              ref,
              revision,
              protected: false,
            })),
          ),
        ),
      );
      const checked = await a.action("check", {}, "check");
      assert.ok(checked.receipt);
      assert.equal((await owner.waitFor("check")).status, "succeeded");
      const admitted = await a.action("install", {}, "install");
      assert.ok(admitted.receipt);
      assert.equal((await owner.waitFor("install")).status, "succeeded");
      a.socket.terminate();
      a = undefined;
      const after = await client.inspect();
      assert.equal(after.identity.id, second.identity.id);
      assert.notEqual(after.instanceId, before.instanceId);
      assert.equal(after.ready, true);
      b = await peer("http://127.0.0.1:" + port);
      const exact = await b.action("receipt", { commandId: "install" });
      assert.equal(exact.receipt.status, "succeeded");
      assert.ok(
        exact.receipt.activation.completedAt >=
          exact.receipt.activation.startedAt,
      );
      assert.equal(sourceReasons.filter((r) => r === "activation").length, 1);
      assert.equal(exact.replayed, false);
      assert.equal((await b.action("running")).identity.id, second.identity.id);
      const rollback = await b.action(
        "rollback",
        { expectedCurrentId: second.identity.id },
        "rollback",
      );
      assert.ok(rollback.receipt);
      assert.equal((await owner.waitFor("rollback")).status, "succeeded");
      assert.equal(owner.receipt("rollback").activation, undefined);
      assert.equal(sourceReasons.filter((r) => r === "activation").length, 1);
      assert.equal((await client.inspect()).identity.id, first.identity.id);
      const claim = JSON.parse(
        await readFile(
          join(installation.directory, "initial-provisioning.claim"),
          "utf8",
        ),
      );
      assert.equal(claim.targetDigest, first.identity.digest);
      await writeFile(
        fixture.receipt,
        JSON.stringify(
          {
            schema: "production-supervisor-installed-v1",
            explicitDurablePristineAuthority: true,
            duplicateProvisioningRefused: true,
            strictMissingHost: true,
            actualSignedDistributionCLI: true,
            actualUpgradeAndRollback: true,
            exactReceiptFromReplacement: true,
            allSignedComponentsChecked: true,
            forwardActivationSourcesReobserved: true,
            rollbackSkipsFreshness: true,
            staleInitialSourcesRefusedBeforeClaim: true,
            componentCount: first.components.length,
            node: process.version,
            platform: process.platform,
            sourceResolver: "independent-fixture-source-endpoint",
            nativeGenerationCurrencyQualified: false,
            serviceAdoptionQualified: false,
            distributionArchiveSha256: fixture.distributionArchiveSha256,
          },
          null,
          2,
        ),
      );
    } finally {
      a?.socket.terminate();
      b?.socket.terminate();
      await supervisor?.close();
      await lifecycle?.close();
      client?.close();
      publisher.closeAllConnections();
      await new Promise((resolve) => publisher.close(resolve));
      await rm(root, { recursive: true, force: true });
    }
  },
);
