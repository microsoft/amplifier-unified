import test from "node:test";
import {prepareNpmConsumer, installNpmConsumer} from "./npm-consumer.mjs";
import assert from "node:assert/strict";
import {
  mkdtemp,
  mkdir,
  readFile,
  writeFile,
  copyFile,
  rm,
  cp,
} from "node:fs/promises";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { tmpdir } from "node:os";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { createHash } from "node:crypto";
const execute = promisify(execFile),
  here = dirname(fileURLToPath(import.meta.url)),
  distribution = resolve(here, "..");
const archive = process.env.INSTALLER_DISTRIBUTION_ARCHIVE,
  expected = "9b716b1e5f527d851eb373b0c7e273b74e77a0238498d26de7962d775d24f5c7";
const hash = (data) => createHash("sha256").update(data).digest("hex");
test(
  "independently installed entrypoint assembles public production ports, real HTTPS Git source reads, signed launch, update and rollback",
  { skip: !archive, timeout: 180000 },
  async (t) => {
    assert.equal(hash(await readFile(archive)), expected);
    const ownerArchive = process.env.INSTALLER_OWNER_ARCHIVE;
    const ownerHash = process.env.INSTALLER_OWNER_SHA256;
    assert.ok(
      ownerArchive && /^[a-f0-9]{64}$/.test(ownerHash ?? ""),
      "Provide the qualified owner 0.9 artifact and exact digest",
    );
    assert.equal(hash(await readFile(ownerArchive)), ownerHash);
    const root = await mkdtemp(join(tmpdir(), "installer-assembly-"));
    t.after(() => rm(root, { recursive: true, force: true }));
    const extracted = join(root, "extracted");
    await mkdir(extracted);
    await execute("tar", ["-xzf", archive, "-C", extracted]);
    const packageRoot = join(extracted, "package");
    const ownerStage = join(root, "owner");
    await mkdir(ownerStage);
    await execute("tar", ["-xzf", ownerArchive, "-C", ownerStage]);
    const ownerManifest = JSON.parse(
      await readFile(join(ownerStage, "package/package.json"), "utf8"),
    );
    assert.equal(ownerManifest.version, "0.9.0");
    const bundledOwner = join(
      packageRoot,
      "node_modules/@amplifier/unified-distribution-update-owner",
    );
    await rm(bundledOwner, { recursive: true, force: true });
    await cp(join(ownerStage, "package"), bundledOwner, { recursive: true });
    for (const name of ["installation.js", "install-cli.js", "portability-preflight.js"])
      await copyFile(
        join(distribution, "src", name),
        join(packageRoot, "src", name),
      );
    const packagePath = join(packageRoot, "package.json"),
      pkg = JSON.parse(await readFile(packagePath, "utf8"));
    pkg.bin["amplifier-unified-install"] = "./src/install-cli.js";
    pkg.dependencies[ownerManifest.name] = ownerManifest.version;
    await writeFile(packagePath, JSON.stringify(pkg, null, 2) + "\n");
    const packed = JSON.parse(
      (
        await execute(
          "npm",
          ["pack", "--ignore-scripts", "--json", "--pack-destination", root],
          { cwd: packageRoot },
        )
      ).stdout,
    )[0];
    const consumer = join(root, "consumer");
    await mkdir(consumer);
    const installTarget = await prepareNpmConsumer(consumer, root, {name: "installer-consumer"});
    await installNpmConsumer(installTarget, [join(root, packed.filename)], {omitDev: true});
    for (const name of ["installer-worker.mjs", "https-git-fixture.mjs"])
      await copyFile(join(here, name), join(consumer, name));
    const graph = join(root, "graph");
    await mkdir(graph);
    await execute("tar", ["-xzf", join(root, packed.filename), "-C", graph]);
    const receiptFile = join(root, "receipt.json"),
      input = join(root, "fixture.json");
    await writeFile(
      input,
      JSON.stringify({
        receiptFile,
        sourcePackageRoot: join(graph, "package"),
        baseArchiveSha256: expected,
        ownerArchiveSha256: ownerHash,
        assembledArchiveSha256: hash(
          await readFile(join(root, packed.filename)),
        ),
      }),
    );
    try {
      await execute(
        process.execPath,
        [join(consumer, "installer-worker.mjs"), input],
        { cwd: consumer, maxBuffer: 1024 * 1024, timeout: 150000 },
      );
    } catch (error) {
      assert.fail((error.stdout ?? "") + "\n" + (error.stderr ?? ""));
    }
    const receipt = JSON.parse(await readFile(receiptFile, "utf8"));
    assert.equal(receipt.actualInstallerCLI, true);
    assert.equal(receipt.actualHTTPSGitReads, true);
    assert.equal(receipt.actualSignedUpdateAndRollback, true);
    if (process.env.INSTALLER_ACCEPTANCE_DIR) {
      const out = process.env.INSTALLER_ACCEPTANCE_DIR;
      await mkdir(out, { recursive: true });
      await copyFile(join(root, packed.filename), join(out, packed.filename));
      await writeFile(
        join(out, "installer-acceptance.json"),
        JSON.stringify({ ...receipt, ownerArchiveSha256: ownerHash }, null, 2) +
          "\n",
      );
    }
  },
);
