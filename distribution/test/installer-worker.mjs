import assert from "node:assert/strict";
import {
  mkdtemp,
  mkdir,
  writeFile,
  readFile,
  readdir,
  lstat,
  cp,
  rm,
  realpath,
} from "node:fs/promises";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { tmpdir } from "node:os";
import { createHash, generateKeyPairSync, sign, randomUUID } from "node:crypto";
import { createServer } from "node:http";
import { execFile, spawn } from "node:child_process";
import { promisify } from "node:util";
import { once } from "node:events";
import { createRequire } from "node:module";
import { createHTTPSGitFixture } from "./https-git-fixture.mjs";
const fixture = JSON.parse(await readFile(process.argv[2], "utf8")),
  execute = promisify(execFile),
  hash = (bytes) => createHash("sha256").update(bytes).digest("hex");
const appEntry = import.meta.resolve("@amplifier/unified"),
  packageRoot = dirname(dirname(fileURLToPath(appEntry))),
  require = createRequire(appEntry);
const { connectSupervisorFile, connectHostControlFile, releaseDigest } =
  await import(require.resolve("@amplifier/unified-distribution-update-owner"));
const { WebSocket } = require("ws");
async function inventory(root) {
  const files = [];
  async function visit(prefix = "") {
    for (const name of await readdir(join(root, prefix))) {
      const path = prefix ? prefix + "/" + name : name,
        info = await lstat(join(root, path));
      if (info.isDirectory()) await visit(path);
      else {
        assert.ok(info.isFile());
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
          reject(Error("Fixture request timed out"));
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
    action: async (operation, args = {}, commandId = randomUUID()) => {
      try {
        return (
          await request("x-amplifier/capabilityAction", {
            channel: "ahp-root://",
            version: 1,
            topic: "application-updates",
            operation: "updates.application." + operation,
            args,
            commandId,
          })
        ).result;
      } catch (error) {
        throw Error(operation + ": " + error.message);
      }
    },
  };
}
const root = await realpath(
    await mkdtemp(join(tmpdir(), "installer-live-fixture-")),
  ),
  git = await createHTTPSGitFixture(),
  resources = new Map();
const { publicKey, privateKey } = generateKeyPairSync("ed25519"),
  keys = { fixture: publicKey.export({ type: "spki", format: "pem" }) };
const publisher = createServer((req, res) => {
  const bytes = resources.get(req.url);
  if (!bytes) {
    res.writeHead(404);
    res.end();
  } else res.end(bytes);
});
await new Promise((resolve) => publisher.listen(0, "127.0.0.1", resolve));
const origin = "http://127.0.0.1:" + publisher.address().port;
let installer,
  supervisor,
  host,
  a,
  b,
  stderr = "";
try {
  const workspace = join(root, "workspace"),
    web = join(root, "web");
  await mkdir(workspace);
  await mkdir(web);
  await writeFile(
    join(web, "index.html"),
    "<html><body>Owned installer fixture</body></html>",
  );
  async function candidate(version, revision) {
    const directory = join(root, "candidate-" + version);
    await mkdir(directory);
    await cp(fixture.sourcePackageRoot, join(directory, "package"), {
      recursive: true,
    });
    const content = join(directory, "package"),
      entry = join(content, "src/cli.js");
    await writeFile(
      entry,
      (await readFile(entry, "utf8")) +
        "\n// Installer fixture revision " +
        version +
        "\n",
    );
    const path = join(root, "release-" + version + ".tgz");
    await execute("tar", ["-czf", path, "-C", directory, "package"], {
      env: { ...process.env, COPYFILE_DISABLE: "1" },
    });
    const bytes = await readFile(path),
      files = await inventory(content),
      components = [];
    for (const file of files.filter(
      (row) =>
        row.path === "package.json" ||
        /\bnode_modules\/(?:@[^/]+\/)?[^/]+\/package.json$/.test(row.path),
    )) {
      const pkg = JSON.parse(await readFile(join(content, file.path), "utf8"));
      components.push({
        name: pkg.name,
        version: pkg.version,
        root: file.path === "package.json" ? "" : dirname(file.path),
        repository: git.repository,
        ref: "main",
        revision,
      });
    }
    const release = {
      identity: {
        id: "installer-fixture-" + version,
        version: version + ".0.0",
        revision,
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
  const first = await candidate(1, git.first),
    second = await candidate(2, git.second);
  const publish = (release) => {
    const payload = Buffer.from(
      JSON.stringify({
        schema: "distribution-channel-v1",
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
  const configuration = {
    schema: "unified-installation-v1",
    directory: join(root, "installation"),
    dataScope: "fixture-installation",
    release: {
      channelUrl: origin + "/channel.json",
      trustedKeys: keys,
      accessScope: "fixture",
      allowedArtifactOrigins: [origin],
      allowLoopbackHttp: true,
    },
    sourceTracking: {
      sources: [{ repository: git.repository, ref: "main" }],
      env: git.env,
    },
    application: {
      account: "fixture",
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
            'throw Error("No agent should start during installer qualification")',
          ],
        },
      ],
    },
  };
  const configFile = join(root, "install.json"),
    cli = join(packageRoot, "src/install-cli.js");
  // Configuration conflicts are refused before allocation, without exception text.
  for (const conflict of [
    "stateDirectory",
    "supervision",
    "applicationUpdates",
  ]) {
    const bad = {
      ...configuration,
      directory: join(root, "conflict-" + conflict),
      application: { ...configuration.application, [conflict]: {} },
    };
    await writeFile(configFile, JSON.stringify(bad), { mode: 0o600 });
    await assert.rejects(
      execute(process.execPath, [cli, "--config", configFile]),
      (error) =>
        error.code === 1 && /installation_unconfirmed/.test(error.stderr),
    );
    await assert.rejects(lstat(bad.directory), { code: "ENOENT" });
  }
  await writeFile(configFile, JSON.stringify(configuration), { mode: 0o600 });
  installer = spawn(process.execPath, [cli, "--config", configFile], {
    detached: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  installer.stderr.on("data", (chunk) => (stderr += chunk));
  const ready = await Promise.race([
    once(installer.stdout, "data").then(([bytes]) =>
      JSON.parse(bytes.toString().trim()),
    ),
    once(installer, "exit").then(() => {
      throw Error("Installer exited: " + stderr);
    }),
    new Promise((_, reject) =>
      setTimeout(
        () => reject(Error("Fixture initial launch timeout")),
        20000,
      ).unref(),
    ),
  ]);
  assert.equal(ready.ready, true);
  assert.equal(ready.serviceLifecycle, "not-configured");
  const dir = configuration.directory,
    app = JSON.parse(await readFile(join(dir, "application.json"), "utf8"));
  assert.equal(app.stateDirectory, join(dir, "application"));
  assert.equal(app.supervision.discoveryFile, join(dir, "supervisor.json"));
  assert.equal(
    app.supervision.hostControl.discoveryFile,
    join(dir, "host-control.json"),
  );
  for (const name of [
    "application.json",
    "installer-input.json",
    "supervisor-configuration.json",
    "initial-provisioning.json",
    "initial-provisioning.claim",
    "installer-attempt.json",
  ])
    assert.equal((await lstat(join(dir, name))).mode & 0o077, 0);
  assert.equal(
    JSON.parse(await readFile(join(dir, "installer-attempt.json"), "utf8"))
      .status,
    "ready",
  );
  supervisor = await connectSupervisorFile(join(dir, "supervisor.json"));
  host = connectHostControlFile(
    join(dir, "host-control.json"),
    "fixture-installation",
  );
  const before = await host.inspect();
  assert.equal(before.identity.id, first.identity.id);
  const claim = await readFile(join(dir, "initial-provisioning.claim"));
  await assert.rejects(
    execute(process.execPath, [cli, "--config", configFile]),
    (error) => error.code === 1,
  );
  assert.deepEqual(
    await readFile(join(dir, "initial-provisioning.claim")),
    claim,
  );
  assert.equal((await host.inspect()).instanceId, before.instanceId);
  // Stop is explicitly unavailable until host/platform ownership is qualified.
  installer.kill("SIGTERM");
  for (
    let i = 0;
    i < 50 && !stderr.includes("service_lifecycle_not_configured");
    i++
  )
    await new Promise((resolve) => setTimeout(resolve, 10));
  assert.match(stderr, /service_lifecycle_not_configured/);
  assert.equal((await host.inspect()).instanceId, before.instanceId);
  a = await peer("http://127.0.0.1:" + port);
  await git.advance(git.second);
  publish(second);
  const wait = async (id) => {
    for (let i = 0; i < 1000; i++) {
      const r = await supervisor.owner.receipt(id);
      if (["succeeded", "failed", "unknown"].includes(r?.status)) return r;
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    throw Error("Fixture update deadline");
  };
  await a.action("check", {}, "check");
  assert.equal((await wait("check")).status, "succeeded");
  await a.action("install", {}, "install");
  const installed = await wait("install");
  assert.equal(installed.status, "succeeded", JSON.stringify(installed));
  assert.ok(installed.activation.completedAt);
  a.socket.terminate();
  a = undefined;
  const after = await host.inspect();
  assert.equal(after.identity.id, second.identity.id);
  assert.notEqual(after.instanceId, before.instanceId);
  b = await peer("http://127.0.0.1:" + port);
  const receipt = await b.action("receipt", { commandId: "install" });
  assert.equal(receipt.receipt.status, "succeeded");
  await b.action("reconcile", { commandId: "install" });
  const sourceRequests = git.requests;
  resources.delete("/channel.json");
  await b.action(
    "rollback",
    { expectedCurrentId: second.identity.id },
    "rollback",
  );
  assert.equal((await wait("rollback")).status, "succeeded");
  assert.equal((await host.inspect()).identity.id, first.identity.id);
  assert.equal(git.requests, sourceRequests);
  await writeFile(
    fixture.receiptFile,
    JSON.stringify(
      {
        schema: "unified-installer-acceptance-v1",
        node: process.version,
        platform: process.platform,
        actualInstallerCLI: true,
        actualHTTPSGitReads: true,
        gitProtocolFixture:
          "real git ls-remote over isolated HTTPS read-only local repository",
        actualSignedUpdateAndRollback: true,
        exactReplacementReceipt: true,
        duplicateInstallationRefused: true,
        ownedConfigurationConflictsRefused: true,
        privateConcreteConfiguration: true,
        serviceStopRefusedWithoutOwner: true,
        passiveInstallReconciliationBeforeRollback: true,
        existingServiceAdoptionQualified: false,
        platformServiceQualified: false,
        nativePythonRegistryCurrencyQualified: false,
        componentCount: first.components.length,
        sourceRequests,
        baseArchiveSha256: fixture.baseArchiveSha256,
        assembledArchiveSha256: fixture.assembledArchiveSha256,
      },
      null,
      2,
    ),
  );
} catch (error) {
  console.error(
    JSON.stringify({
      failure: error.message,
      diagnostics: supervisor
        ? await supervisor.owner.diagnostics().catch(() => null)
        : null,
    }),
  );
  throw error;
} finally {
  a?.socket.terminate();
  b?.socket.terminate();
  supervisor?.close();
  host?.close();
  if (
    installer &&
    installer.exitCode === null &&
    installer.signalCode === null
  ) {
    const exited = once(installer, "exit");
    try {
      process.kill(-installer.pid, "SIGKILL");
    } catch {}
    await exited;
  }
  publisher.closeAllConnections();
  await new Promise((resolve) => publisher.close(resolve));
  await git.close();
  await rm(root, { recursive: true, force: true });
}
