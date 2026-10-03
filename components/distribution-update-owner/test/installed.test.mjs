import { installConsumer } from "./npm-consumer.mjs";
import test from "node:test";
import assert from "node:assert/strict";
import {
  mkdtemp,
  mkdir,
  writeFile,
  readFile,
  copyFile,
  rm,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { createHash, randomUUID } from "node:crypto";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
const execute = promisify(execFile),
  packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const npm = process.platform === "win32" ? "npm.cmd" : "npm";
const hash = (data) => createHash("sha256").update(data).digest("hex");
const server = `import http from 'node:http';
import {readFileSync,writeFileSync} from 'node:fs';
const identity=JSON.parse(readFileSync(new URL('./release.json',import.meta.url),'utf8'));
const server=http.createServer((req,res)=>{
  if(req.headers.authorization!=='Bearer '+process.env.READINESS_SECRET){res.writeHead(403);res.end();return}
  res.setHeader('Content-Type','application/json');res.end(JSON.stringify({identity,instanceId:process.env.AMPLIFIER_DISTRIBUTION_INSTANCE_ID,dataScope:process.env.AMPLIFIER_DISTRIBUTION_DATA_SCOPE,ready:true,pid:process.pid}));
});
server.listen(0,'127.0.0.1',()=>writeFileSync(process.env.ENDPOINT_FILE,JSON.stringify({port:server.address().port}),{mode:0o600}));
process.on('SIGTERM',()=>{server.closeAllConnections();server.close(()=>process.exit(0))});
`;

test("independently installed package promotes and rolls back real installed fixture processes", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "distribution-installed-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const packed = JSON.parse(
    (
      await execute(
        npm,
        ["pack", "--ignore-scripts", "--json", "--pack-destination", root],
        { cwd: packageRoot },
      )
    ).stdout,
  )[0];
  const packageFile = join(root, packed.filename),
    packageHash = hash(await readFile(packageFile));
  const consumer = join(root, "consumer");
  await mkdir(consumer);
  await writeFile(
    join(consumer, "package.json"),
    JSON.stringify({
      name: "independent-owner-consumer",
      private: true,
      type: "module",
    }),
  );
  await installConsumer(consumer, [packageFile], { npm });
  const releases = [1, 2].map((i) => ({
      id: `fixture-v${i}`,
      version: `${i}.0.0`,
      revision: String(i).repeat(40),
      digest: hash(`fixture-graph-${i}`),
    })),
    artifacts = {};
  for (const identity of releases) {
    const source = join(root, identity.id + "-source");
    await mkdir(source);
    await writeFile(
      join(source, "package.json"),
      JSON.stringify({
        name: "owned-distribution-fixture",
        version: identity.version,
        type: "module",
        files: ["server.mjs", "release.json"],
      }),
    );
    await writeFile(join(source, "server.mjs"), server);
    await writeFile(join(source, "release.json"), JSON.stringify(identity));
    const tar = JSON.parse(
      (
        await execute(
          npm,
          ["pack", "--ignore-scripts", "--json", "--pack-destination", root],
          { cwd: source },
        )
      ).stdout,
    )[0];
    const file = join(root, tar.filename);
    artifacts[identity.id] = { file, sha256: hash(await readFile(file)) };
  }
  await copyFile(
    join(packageRoot, "test", "installed-worker.mjs"),
    join(consumer, "acceptance.mjs"),
  );
  await copyFile(join(packageRoot, "test", "npm-consumer.mjs"), join(consumer, "npm-consumer.mjs"));
  const config = {
    root,
    npm,
    releases,
    artifacts,
    serverHash: hash(server),
    endpoint: join(root, "endpoint.json"),
    secret: randomUUID(),
    receipt: join(root, "receipt.json"),
  };
  await writeFile(join(root, "config.json"), JSON.stringify(config), {
    mode: 0o600,
  });
  const result = await execute(
    process.execPath,
    [join(consumer, "acceptance.mjs"), join(root, "config.json")],
    { cwd: consumer, timeout: 60000 },
  );
  const receipt = JSON.parse(await readFile(config.receipt, "utf8"));
  assert.equal(receipt.lostReplyReconciledWithoutReplay, true, result.stderr);
  const output = process.env.DISTRIBUTION_OWNER_ACCEPTANCE_DIR;
  if (output) {
    await mkdir(output, { recursive: true, mode: 0o700 });
    await copyFile(packageFile, join(output, packed.filename));
    await writeFile(
      join(output, "acceptance.json"),
      JSON.stringify(
        {
          ...receipt,
          packageArtifact: packed.filename,
          packageSha256: packageHash,
          node: process.version,
          platform: process.platform,
          fixtureArtifacts: Object.fromEntries(
            Object.entries(artifacts).map(([id, artifact]) => [
              id,
              { sha256: artifact.sha256 },
            ]),
          ),
        },
        null,
        2,
      ) + "\n",
    );
  }
});
