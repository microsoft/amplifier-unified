import { lstat, realpath } from "node:fs/promises";
import { dirname, isAbsolute, join, parse, relative, sep } from "node:path";

const inside = (root, path) => {
  const suffix = relative(root, path);
  return !suffix || (suffix !== ".." && !suffix.startsWith(".." + sep) && !isAbsolute(suffix));
};

// This is the same grant composeWorktrees adds after successful construction.
export const worktreeExecutionDirectory = capabilityDirectory =>
  join(capabilityDirectory, "worktrees", "git", "checkouts");

// Walk resolved existing ancestors and prospective missing directories without
// creating them. Resolve each encountered symlink BEFORE interpreting "..";
// lexical normalization could hide an escape or miss a link after a missing tail.
async function prospectiveDirectory(path) {
  const root = parse(path).root;
  let current = await realpath(root);
  for (const segment of path.slice(root.length).split(process.platform === "win32" ? /[\\/]+/ : /\/+/)) {
    if (!segment || segment === ".") continue;
    if (segment === "..") { current = dirname(current); continue; }
    const candidate = join(current, segment);
    let present = true;
    try { await lstat(candidate); }
    catch (error) {
      if (error.code !== "ENOENT") throw error;
      present = false;
    }
    if (!present) { current = candidate; continue; }
    // A dangling symlink is present, so realpath refuses it rather than treating
    // it as a directory that the owner could create.
    current = await realpath(candidate);
    if (!(await lstat(current)).isDirectory()) throw Error("not_directory");
  }
  return current;
}

/** Configuration preflight only: no mkdir, owner construction or authority.
 * Match the runtime portability owner's resolved workspace containment check.
 * Call again immediately before allocating authority after slow preparation;
 * the runtime check remains necessary if the namespace changes afterward. */
export async function validatePortabilityStage(application, { stateDirectory = application.stateDirectory } = {}) {
  // createDistribution constructs this optional owner only for truthy config.
  if (!application.portability) return;
  const stage = application.portability.stageDir;
  if (typeof stage !== "string" || !isAbsolute(stage))
    throw Error("portability_stage_absolute_path_required");
  const roots = application.allowedWorkspaceRoots;
  if (!Array.isArray(roots) || !roots.length)
    throw Error("portability_workspace_roots_required");
  let canonicalStage, canonicalRoots;
  try {
    canonicalRoots = await Promise.all(roots.map(async root => {
      if (typeof root !== "string" || !isAbsolute(root)) throw Error("invalid_root");
      const canonical = await realpath(root);
      if (!(await lstat(canonical)).isDirectory()) throw Error("invalid_root");
      return canonical;
    }));
    if (application.worktrees) {
      if (typeof stateDirectory !== "string" || !isAbsolute(stateDirectory))
        throw Error("invalid_state_directory");
      canonicalRoots.push(await prospectiveDirectory(
        worktreeExecutionDirectory(join(stateDirectory, "capabilities"))));
    }
    canonicalStage = await prospectiveDirectory(stage);
  } catch {
    // Configuration diagnostics must not expose filesystem contents or paths.
    throw Error("portability_stage_or_workspace_unresolvable");
  }
  if (!canonicalRoots.some(root => inside(root, canonicalStage)))
    throw Error("portability_stage_outside_workspace_roots");
}
