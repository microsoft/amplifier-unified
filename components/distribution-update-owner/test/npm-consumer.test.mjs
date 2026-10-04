import test from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, mkdir, readFile, realpath, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import { installConsumer } from "./npm-consumer.mjs";

const execute = promisify(execFile);
const npm = process.platform === "win32" ? "npm.cmd" : "npm";
async function fixture(t) {
  const root = await realpath(await mkdtemp(join(tmpdir(), "npm-consumer-isolation-")));
  t.after(() => rm(root, { recursive: true, force: true }));
  const consumer = join(root, "consumer");
  await mkdir(consumer);
  await writeFile(join(consumer, "package.json"), JSON.stringify({ private: true, type: "module" }));
  return { root, consumer };
}

test("consumer install overrides inherited global/workspace selection and preserves parent state", async t => {
  const { root, consumer } = await fixture(t);
  const source = join(root, "source");
  await mkdir(source);
  await writeFile(join(source, "package.json"), JSON.stringify({
    name: "owned-consumer-fixture", version: "1.0.0", type: "module", exports: "./index.js",
    scripts: { postinstall: "node -e \"require('node:fs').writeFileSync('should-not-run', 'bad')\"" },
  }));
  await writeFile(join(source, "index.js"), "export default 'owned-consumer';\n");
  const packed = JSON.parse((await execute(npm, ["pack", "--ignore-scripts", "--json",
    "--prefix", source, "--global=false", "--workspaces=false", "--pack-destination", root], { cwd: source })).stdout)[0];
  await mkdir(join(root, "node_modules"));
  const sentinels = new Map([
    ["package.json", JSON.stringify({ name: "parent-must-not-change", private: true, workspaces: ["consumer"] })],
    ["package-lock.json", "parent lock must not change\n"],
    ["node_modules/sentinel", "parent modules must not change\n"],
  ]);
  for (const [path, bytes] of sentinels) await writeFile(join(root, path), bytes);
  const npmrc = join(root, "owned.npmrc"), other = join(root, "wrong-prefix");
  await writeFile(npmrc, `global=true\nprefix=${other}\nworkspaces=true\n`);
  await installConsumer(consumer, [join(root, packed.filename)], { npm, env: {
    ...process.env, npm_config_userconfig: npmrc, npm_config_prefix: other,
    npm_config_global: "true", NPM_CONFIG_WORKSPACES: "true", npm_config_workspace: "missing-workspace",
    npm_config_location: "global", npm_config_offline: "true",
  } });
  for (const [path, bytes] of sentinels) assert.equal(await readFile(join(root, path), "utf8"), bytes);
  const installed = join(consumer, "node_modules/owned-consumer-fixture");
  assert.equal(await realpath(installed), installed);
  const result = await execute(process.execPath, ["--input-type=module", "-e",
    "import value from 'owned-consumer-fixture'; console.log(value); console.log(import.meta.resolve('owned-consumer-fixture'));"], { cwd: consumer });
  assert.match(result.stdout, /owned-consumer\n/);
  assert.ok(result.stdout.includes("consumer/node_modules/owned-consumer-fixture/index.js"));
  await assert.rejects(readFile(join(installed, "should-not-run")), { code: "ENOENT" });
  await assert.rejects(readFile(join(other, "package.json")), { code: "ENOENT" });
});

test("missing local manifest refuses before npm can select the parent", async t => {
  const { root, consumer } = await fixture(t);
  await rm(join(consumer, "package.json"));
  await writeFile(join(root, "package.json"), '{"private":true}\n');
  await assert.rejects(installConsumer(consumer, [], { npm: "/must-not-run-npm" }), { code: "ENOENT" });
});

for (const wrong of ["prefix", "root"]) test(`unexpected npm ${wrong} refuses before install`, { skip: process.platform === "win32" }, async t => {
  const { root, consumer } = await fixture(t);
  const script = join(root, "npm-fixture.mjs"), marker = join(root, "install-ran"), archive = join(root, "fixture.tgz");
  await writeFile(archive, "not consumed when root verification fails");
  await writeFile(script, `#!/usr/bin/env node
import {writeFileSync} from 'node:fs';
const command=process.argv[2],prefix=process.argv[process.argv.indexOf('--prefix')+1];
if(command==='install'){writeFileSync(${JSON.stringify(marker)},'unsafe');process.exit(0);}
const wrong=${JSON.stringify(wrong)};
console.log(command===wrong?${JSON.stringify(root)}:(command==='prefix'?prefix:prefix+'/node_modules'));
`, { mode: 0o700 });
  await assert.rejects(installConsumer(consumer, [archive], { npm: script }), /npm resolved a different consumer/);
  await assert.rejects(readFile(marker), { code: "ENOENT" });
});

test("symlinked module roots cannot redirect a consumer install", { skip: process.platform === "win32" }, async t => {
  const { root, consumer } = await fixture(t);
  await mkdir(join(root, "parent-modules"));
  await symlink(join(root, "parent-modules"), join(consumer, "node_modules"));
  await assert.rejects(installConsumer(consumer, [], { npm: "/must-not-run-npm" }), /module root must stay inside/);
});
