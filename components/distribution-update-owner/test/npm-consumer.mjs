import { execFile } from "node:child_process";
import { lstat, readFile, realpath } from "node:fs/promises";
import { isAbsolute, join, resolve } from "node:path";
import { promisify } from "node:util";

const execute = promisify(execFile);
const npmCommand = process.platform === "win32" ? "npm.cmd" : "npm";

// Test tooling only. A cwd is not an installation boundary: npm can walk up to
// a parent package/workspace, or inherit a global/prefix override from the caller.
export async function installConsumer(directory, packages, {
  npm = npmCommand,
  env = process.env,
} = {}) {
  if (!(await lstat(directory)).isDirectory()) {
    throw Error("Consumer must be an owned directory, not a symlink");
  }
  const root = await realpath(directory);
  const manifest = join(root, "package.json");
  if (!(await lstat(manifest)).isFile() || JSON.parse(await readFile(manifest, "utf8")).private !== true) {
    throw Error("Consumer requires its own private package manifest");
  }
  const modules = join(root, "node_modules");
  try {
    if (!(await lstat(modules)).isDirectory() || await realpath(modules) !== modules) {
      throw Error("Consumer module root must stay inside the owned directory");
    }
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  if (!Array.isArray(packages) || !packages.length) throw Error("Local consumer packages required");
  const archives = await Promise.all(packages.map(async file => {
    const path = await realpath(file);
    if (!(await lstat(path)).isFile()) throw Error("Consumer package must be a local archive");
    return path;
  }));
  // Explicit CLI flags override npmrc values; remove inherited workspace
  // selectors as npm rejects a selector combined with --workspaces=false.
  const cleanEnv = Object.fromEntries(Object.entries(env).filter(([key]) =>
    !/^npm_config_(prefix|global|location|workspace|workspaces|include_workspace_root)$/i.test(key)));
  const flags = ["--prefix", root, "--global=false", "--workspaces=false", "--location=project"];
  const options = { cwd: root, env: cleanEnv };
  const prefix = (await execute(npm, ["prefix", ...flags], options)).stdout.trim();
  if (!isAbsolute(prefix) || await realpath(prefix) !== root) {
    throw Error("npm resolved a different consumer prefix");
  }
  const moduleRoot = (await execute(npm, ["root", ...flags], options)).stdout.trim();
  if (!isAbsolute(moduleRoot) || resolve(moduleRoot) !== modules) {
    throw Error("npm resolved a different consumer module root");
  }
  return execute(npm, ["install", ...flags, "--ignore-scripts", "--no-audit",
    "--no-fund", "--omit=dev", ...archives], options);
}
