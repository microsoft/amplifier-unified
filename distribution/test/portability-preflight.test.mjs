import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, symlink, readlink, lstat, readdir, rm, realpath } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, parse } from "node:path";
import { validatePortabilityStage, worktreeExecutionDirectory } from "../src/portability-preflight.js";
import { installProductionDistribution } from "../src/installation.js";

async function fixture(t) {
  const root = await realpath(await mkdtemp(join(tmpdir(), "stage-preflight-")));
  const workspace = join(root, "workspace");
  await mkdir(workspace);
  t.after(() => rm(root, { recursive: true, force: true }));
  return { root, workspace, app: stageDir => ({
    allowedWorkspaceRoots: [workspace], portability: { stageDir }
  }) };
}

test("optional absent/disabled portability does not add a workspace requirement", async () => {
  for (const portability of [undefined, null, false])
    await validatePortabilityStage({ portability });
});

test("existing and future stage paths use whole segments and never create directories", async t => {
  const f = await fixture(t), future = join(f.workspace, "future", "stage");
  await validatePortabilityStage(f.app(f.workspace));
  await validatePortabilityStage(f.app(future));
  assert.deepEqual(await readdir(f.workspace), []);
  await assert.rejects(lstat(future), { code: "ENOENT" });
  await assert.rejects(validatePortabilityStage(f.app(join(f.workspace + "-sibling", "stage"))),
    /portability_stage_outside_workspace_roots/);
  await validatePortabilityStage({ portability: { stageDir: future },
    allowedWorkspaceRoots: [parse(f.root).root] });
});

test("existing symlinks resolve like the owner, including an authorized root alias", async t => {
  const f = await fixture(t), alias = join(f.root, "alias"), stage = join(f.workspace, "alias");
  await symlink(f.workspace, alias);
  await symlink(f.workspace, stage);
  await validatePortabilityStage(f.app(join(stage, "future")));
  await validatePortabilityStage({ portability: { stageDir: join(alias, "future") },
    allowedWorkspaceRoots: [alias] });
  assert.equal(await readlink(stage), f.workspace);
  assert.deepEqual(await readdir(f.workspace), ["alias"]);
});

test("escaping, dangling and non-directory ancestors refuse without repairing them", async t => {
  const f = await fixture(t), escape = join(f.workspace, "escape"), dangling = join(f.workspace, "dangling"), file = join(f.workspace, "file");
  await symlink(f.root, escape);
  await symlink(join(f.root, "missing"), dangling);
  await writeFile(file, "retained");
  await assert.rejects(validatePortabilityStage(f.app(join(escape, "missing", "stage"))),
    /portability_stage_outside_workspace_roots/);
  for (const path of [dangling, join(dangling, "stage"), file, join(file, "stage")])
    await assert.rejects(validatePortabilityStage(f.app(path)), /portability_stage_or_workspace_unresolvable/);
  assert.equal(await readlink(dangling), join(f.root, "missing"));
  assert.deepEqual((await readdir(f.workspace)).sort(), ["dangling", "escape", "file"]);
  await assert.rejects(lstat(join(f.root, "missing")), { code: "ENOENT" });
});

test("nonabsolute stage is refused", async t => {
  const f = await fixture(t);
  for (const stage of [undefined, "", "relative"])
    await assert.rejects(validatePortabilityStage(f.app(stage)), /portability_stage_absolute_path_required/);
});

test("generic public installer refuses invalid stage before release IO or pristine allocation", async t => {
  const f = await fixture(t), directory = join(f.root, "installation");
  const escape = join(f.workspace, "escape");
  await symlink(f.root, escape);
  // Unusable release/source inputs intentionally prove the path refusal precedes
  // adapter construction, network access, and createPristineInstallation.
  const configuration = {
    schema: "unified-installation-v1", directory, dataScope: "stage-fixture",
    release: {}, sourceTracking: {},
    application: { account: "fixture", engines: [{ id: "unused" }],
      webDirectory: f.root, defaultWorkspace: f.workspace, allowedWorkspaceRoots: [f.workspace] }
  };
  for (const stageDir of [join(directory, "stage"), join(escape, "future")]) {
    configuration.application.portability = { stageDir };
    await assert.rejects(installProductionDistribution(configuration), /portability_stage_outside_workspace_roots/);
    await assert.rejects(lstat(directory), { code: "ENOENT" });
  }
  assert.deepEqual((await readdir(f.root)).sort(), ["workspace"]);
});


test("configured worktree execution root grants future stage without allocating its owner", async t => {
  const f = await fixture(t), stateDirectory = join(f.root, "installation", "application");
  const executionRoot = worktreeExecutionDirectory(join(stateDirectory, "capabilities"));
  const application = { ...f.app(join(executionRoot, "future", "stage")), worktrees: { python: "unused" } };
  await validatePortabilityStage(application, { stateDirectory });
  await assert.rejects(lstat(join(f.root, "installation")), { code: "ENOENT" });
  await assert.rejects(validatePortabilityStage({ ...application, worktrees: false }, { stateDirectory }),
    /portability_stage_outside_workspace_roots/);
  await assert.rejects(validatePortabilityStage({ ...application, portability: { stageDir: executionRoot + "-sibling" } },
    { stateDirectory }), /portability_stage_outside_workspace_roots/);
});

test("derived worktree root resolves aliases, but an escaping descendant link never expands its grant", async t => {
  const f = await fixture(t), stateDirectory = join(f.root, "state");
  const executionRoot = worktreeExecutionDirectory(join(stateDirectory, "capabilities"));
  await mkdir(executionRoot, { recursive: true });
  const escape = join(executionRoot, "escape");
  await symlink(f.root, escape);
  const application = { ...f.app(join(escape, "future")), stateDirectory, worktrees: { python: "unused" } };
  await assert.rejects(validatePortabilityStage(application), /portability_stage_outside_workspace_roots/);
  assert.equal(await readlink(escape), f.root);
  await assert.rejects(lstat(join(f.root, "future")), { code: "ENOENT" });
  const alias = join(f.root, "state-alias");
  await symlink(stateDirectory, alias);
  await validatePortabilityStage({ ...application, stateDirectory: alias,
    portability: { stageDir: join(executionRoot, "future") } });
});


test("parent segments follow real symlink authority even after missing future directories", async t => {
  const f = await fixture(t), outside = join(f.root, "outside");
  await mkdir(outside);
  await symlink(outside, join(f.workspace, "alias"));
  for (const stage of [
    f.workspace + "/alias/../future",
    f.workspace + "/missing/../alias/future"
  ]) await assert.rejects(validatePortabilityStage(f.app(stage)), /portability_stage_outside_workspace_roots/);
  await validatePortabilityStage(f.app(f.workspace + "/missing/../future/"));
  assert.deepEqual(await readdir(f.workspace), ["alias"]);
  await assert.rejects(lstat(join(f.workspace, "missing")), { code: "ENOENT" });
});
