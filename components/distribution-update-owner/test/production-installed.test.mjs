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
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { createHash } from "node:crypto";
const execute = promisify(execFile),
  component = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const graph = process.env.DISTRIBUTION_GRAPH_ARCHIVE;
test(
  "production factory independently installed with actual signed distribution CLI upgrades and rolls back",
  { skip: !graph, timeout: 120000 },
  async (t) => {
    const root = await mkdtemp(
      join(tmpdir(), "production-supervisor-consumer-"),
    );
    t.after(() => rm(root, { recursive: true, force: true }));
    const expected =
      "daca8bc9df1bfc289a721f3b4a9a767a01113ffbea0c667e7f8b3378cad4600b";
    assert.equal(
      createHash("sha256")
        .update(await readFile(graph))
        .digest("hex"),
      expected,
    );
    const packed = JSON.parse(
      (
        await execute(
          "npm",
          ["pack", "--ignore-scripts", "--json", "--pack-destination", root],
          { cwd: component },
        )
      ).stdout,
    )[0];
    const consumer = join(root, "consumer");
    await mkdir(consumer);
    await writeFile(
      join(consumer, "package.json"),
      JSON.stringify({
        name: "production-supervisor-consumer",
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
      { cwd: consumer },
    );
    await mkdir(join(consumer, "graph"));
    await execute("tar", ["-xzf", graph, "-C", join(consumer, "graph")]);
    await copyFile(
      join(component, "test/production-distribution-worker.mjs"),
      join(consumer, "worker.mjs"),
    );
    const receiptFile = join(root, "receipt.json"),
      config = join(root, "config.json");
    await writeFile(
      config,
      JSON.stringify({
        receipt: receiptFile,
        distributionArchiveSha256: expected,
      }),
    );
    try {
      await execute(process.execPath, [join(consumer, "worker.mjs"), config], {
        cwd: consumer,
        maxBuffer: 1024 * 1024,
      });
    } catch (error) {
      assert.fail((error.stdout ?? "") + "\n" + (error.stderr ?? ""));
    }
    const receipt = JSON.parse(await readFile(receiptFile, "utf8"));
    assert.equal(receipt.actualSignedDistributionCLI, true);
    const out = process.env.DISTRIBUTION_PRODUCTION_ACCEPTANCE_DIR;
    if (out) {
      await mkdir(out, { recursive: true });
      await copyFile(join(root, packed.filename), join(out, packed.filename));
      await writeFile(
        join(out, "production-acceptance.json"),
        JSON.stringify(
          {
            ...receipt,
            artifactSha256: createHash("sha256")
              .update(await readFile(join(root, packed.filename)))
              .digest("hex"),
          },
          null,
          2,
        ) + "\n",
      );
    }
  },
);
