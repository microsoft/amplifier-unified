import test from "node:test";
import assert from "node:assert/strict";
import {
  mkdtemp,
  mkdir,
  readFile,
  writeFile,
  copyFile,
  rm,
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
    const root = await mkdtemp(join(tmpdir(), "installer-assembly-"));
    t.after(() => rm(root, { recursive: true, force: true }));
    const extracted = join(root, "extracted");
    await mkdir(extracted);
    await execute("tar", ["-xzf", archive, "-C", extracted]);
    const packageRoot = join(extracted, "package");
    for (const name of ["installation.js", "install-cli.js"])
      await copyFile(
        join(distribution, "src", name),
        join(packageRoot, "src", name),
      );
    const packagePath = join(packageRoot, "package.json"),
      pkg = JSON.parse(await readFile(packagePath, "utf8"));
    pkg.bin["amplifier-unified-install"] = "./src/install-cli.js";
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
    await writeFile(
      join(consumer, "package.json"),
      JSON.stringify({
        name: "installer-consumer",
        private: true,
        type: "module",
      }),
    );
    await execute(
      "npm",
      [
        "install",
        "--ignore-scripts",
        "--no-audit",
        "--no-fund",
        "--omit=dev",
        join(root, packed.filename),
      ],
      { cwd: consumer, maxBuffer: 1024 * 1024 },
    );
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
        JSON.stringify(receipt, null, 2) + "\n",
      );
    }
  },
);
