"""Pinned preparation and normal HostConfig inputs for the actual Work ACP lane.

Preparation may fetch public sources. Execution consumes this receipt offline;
no resolver, loader, module mount or device backend is patched.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
from urllib.parse import parse_qs, urlsplit

import yaml


RESOURCES = {
    "work": ("amplifier-bundle-work", "b5154aabf0aaed0ad9465f711abb3bfb18dee6f8"),
    "foundation": ("amplifier-foundation", "134abaee59bb1ab408834f0029949350d790ef99"),
    "ui-use": ("amplifier-bundle-ui-use", "5e3b08b8e06a6095314200476d387508ddb32f67"),
    "skills": ("amplifier-bundle-skills", "2f58408a879535df36da5c92d2b073f49271ade4"),
    "imagegen": ("amplifier-bundle-imagegen", "8acd3773102b77378c0d46da53a901f8297a38fc"),
    "computer-use": ("amplifier-bundle-computer-use", "f0cb9d99ff6f1016562a58e310d183fb0cef3386"),
    "context-intelligence": ("amplifier-bundle-context-intelligence", "a80ce69d0e96e9a343501f238dcd867350dd9aa2"),
    "routing-matrix": ("amplifier-bundle-routing-matrix", "9d9d1cb17ac958a011c99b80096034663fd245b3"),
    "modes": ("amplifier-bundle-modes", "a29ebceb72f02f4340290346d5a4fe0cf486dfec"),
}
WORK_TOOLS = (
    "tool-transcript", "tool-skills", "tool-bash", "tool-exec", "tool-web",
    "tool-ui-use", "tool-filesystem", "tool-apply-patch", "tool-search",
    "tool-delegate", "tool-image", "tool-computer-use",
    "tool-context-intelligence-transcript", "tool-mode",
    "tool-context-intelligence-set-filters",
)
WORK_HOOKS = (
    "hook-computer-use", "hooks-mode", "hooks-approval",
    "hook-context-intelligence", "hooks-routing",
)
WORK_AGENTS = {
    "computer-use:computer-operator", "context-intelligence:graph-analyst",
    "context-intelligence:session-navigator", "context-intelligence:server-data-ops",
}
MODULES = (*WORK_TOOLS, *WORK_HOOKS, "loop-live", "context-managed")
AGENT_SOURCES = {
    "tool-context-intelligence-query": (
        "context-intelligence", "modules/tool-context-intelligence-query"),
    "tool-server-data-ops": ("context-intelligence", "modules/tool-server-data-ops"),
    "hook-server-data-ops-lockdown": (
        "context-intelligence", "modules/hook-server-data-ops-lockdown"),
}
TODO_COMMIT = "894c6d3fa5a7a12d075533602817763d785d8963"


def git(root, *args):
    return subprocess.check_output(
        ["git", "--no-optional-locks", "-C", str(root), *args],
        text=True, timeout=20).strip()


def source_record(root, uri, commit):
    assert git(root, "rev-parse", "HEAD") == commit, (uri, root)
    # Record all tracked source bytes, not a hand-picked manifest or mutable main.
    files = subprocess.check_output(
        ["git", "-C", str(root), "ls-files", "-z"], timeout=20).decode().split("\0")
    assert not git(root, "diff", "--name-only", "HEAD"), root
    return {"uri": uri, "commit": commit, "root": str(root), "files": {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in files if name and (root / name).is_file()}}


async def prepare(destination):
    """Use Foundation's ordinary public Git resolver into an owned cache."""
    from amplifier_foundation.sources import SimpleSourceResolver
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    resolver = SimpleSourceResolver(cache_dir=destination / "sources")
    resources, modules, resolved = {}, {}, {}

    async def resolve(uri, commit):
        base = uri.partition("#")[0]
        if base not in resolved:
            result = await resolver.resolve(base)
            root = result.source_root
            resolved[base] = source_record(root, base, commit)
        row = resolved[base]
        assert row["commit"] == commit
        return row

    for name, (repo, commit) in RESOURCES.items():
        uri = f"git+https://github.com/microsoft/{repo}@{commit}"
        resources[name] = await resolve(uri, commit)
    for module in MODULES:
        distribution = ("amplifier-bundle-ui-use" if module == "tool-ui-use"
                        else "amplifier-module-" + module)
        dist = importlib.metadata.distribution(distribution)
        direct = json.loads(dist.read_text("direct_url.json") or "{}")
        commit = direct.get("vcs_info", {}).get("commit_id")
        assert commit and len(commit) == 40, (distribution, direct)
        uri = "git+" + direct["url"] + "@" + commit
        row = await resolve(uri, commit)
        relative = direct.get("subdirectory", "")
        path = Path(row["root"]) / relative
        assert (path / "pyproject.toml").is_file(), path
        modules[module] = {**row, "path": str(path), "distribution": distribution,
                           "version": dist.version, "subdirectory": relative}
    # Normal preparation resolves declarations on the four agents even though
    # this lane never delegates to them. Preserve that closure without mounting
    # extra root tools or pretending these agent-only modules were exercised.
    agents = {name: {**resources[owner], "path": str(Path(resources[owner]["root"]) / relative)}
              for name, (owner, relative) in AGENT_SOURCES.items()}
    todo = await resolve(
        "git+https://github.com/microsoft/amplifier-module-tool-todo@" + TODO_COMMIT,
        TODO_COMMIT)
    agents["tool-todo"] = {**todo, "path": todo["root"]}
    receipt = {"resources": resources, "modules": modules, "agentSources": agents}
    path = destination / "sources.json"
    path.write_text(json.dumps(receipt, indent=2) + "\n")
    return path


def read_sources(path):
    receipt = json.loads(Path(path).read_text())
    assert set(receipt["resources"]) == set(RESOURCES)
    assert set(receipt["modules"]) == set(MODULES)
    assert set(receipt["agentSources"]) == {*AGENT_SOURCES, "tool-todo"}
    assert receipt["agentSources"]["tool-todo"]["commit"] == TODO_COMMIT
    checked = set()
    for name, (_, commit) in RESOURCES.items():
        assert receipt["resources"][name]["commit"] == commit, name
    for row in [*receipt["resources"].values(), *receipt["modules"].values(),
                *receipt["agentSources"].values()]:
        if row.get("distribution"):
            dist = importlib.metadata.distribution(row["distribution"])
            direct = json.loads(dist.read_text("direct_url.json") or "{}")
            assert direct.get("vcs_info", {}).get("commit_id") == row["commit"], row["distribution"]
            assert dist.version == row["version"], row["distribution"]
        root = Path(row["root"]).resolve(strict=True)
        if root in checked:
            continue
        checked.add(root)
        assert git(root, "rev-parse", "HEAD") == row["commit"]
        for relative, digest in row["files"].items():
            assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == digest, (root, relative)
    return receipt


def source_settings(receipt):
    """Bind public bundle references with the supported sources.bundles map."""
    resources = receipt["resources"]
    from amplifier_web.builtin_behaviors import resource_root
    manifest_roots = {name: Path(row["root"]) for name, row in resources.items()}
    manifest_roots["unified"] = resource_root()
    repos = {repo: Path(resources[name]["root"]) for name, (repo, _) in RESOURCES.items()}
    bundles = {}
    for name, root in manifest_roots.items():
        if (root / "bundle.md").is_file():
            bundles[name] = str(root / "bundle.md")
        # Include URIs are mapped, not rewritten in a flattened mount plan.
        manifests = [root / "bundle.md", root / "bundle.yaml"]
        for folder in ("behaviors", "bundles"):
            manifests.extend(p for p in (root / folder).rglob("*")
                             if p.suffix in {".yaml", ".yml", ".md"})
        for manifest in manifests:
            if not manifest.is_file():
                continue
            text = manifest.read_text()
            if text.startswith("---\n"):
                text = text.split("---", 2)[1]
            elif manifest.suffix == ".md":
                continue
            value = yaml.safe_load(text) or {}
            if not isinstance(value, dict):
                continue
            for include in value.get("includes", []):
                uri = include.get("bundle", "") if isinstance(include, dict) else include
                if ":" in uri and not uri.startswith(("git+", "file:", "https:")):
                    namespace, relative = uri.split(":", 1)
                    if namespace in manifest_roots:
                        target = manifest_roots[namespace] / relative
                        # HostConfig exact aliases do not apply Foundation's
                        # namespace extension search. Bind the actual file.
                        if not target.exists() and not target.suffix:
                            target = next((target.with_suffix(ext) for ext in (".yaml", ".yml", ".md")
                                           if target.with_suffix(ext).is_file()), target)
                        if target.exists():
                            owner = manifest_roots[namespace]
                            bundles[uri] = owner.as_uri() + "#subdirectory=" + target.relative_to(owner).as_posix()
                if not uri.startswith("git+https://github.com/microsoft/"):
                    continue
                parsed = urlsplit(uri.removeprefix("git+"))
                repo = parsed.path.rsplit("/", 1)[-1].split("@")[0].removesuffix(".git")
                if repo in repos:
                    relative = parse_qs(parsed.fragment).get("subdirectory", [""])[0]
                    bundles[uri] = repos[repo].as_uri() + (
                        "#subdirectory=" + relative if relative else "")
    # These app behaviors are selected by the host rather than a resource include.
    from amplifier_web.builtin_behaviors import DEFAULT_BEHAVIORS, SHELL_BEHAVIOR_URI
    for uri in DEFAULT_BEHAVIORS:
        if uri == SHELL_BEHAVIOR_URI:
            continue  # Version-matched Unified packaged resources, not a fetch.
        parsed = urlsplit(uri.removeprefix("git+"))
        repo = parsed.path.rsplit("/", 1)[-1].split("@")[0]
        bundles[uri] = repos[repo].as_uri() + "#" + parsed.fragment
    return {"bundle": {"active": "work"}, "sources": {
        "bundles": bundles,
        "modules": {name: row["path"] for name, row in {
            **receipt["modules"], **receipt["agentSources"]}.items()}}}


def assert_composition(bundle, config):
    plan = bundle.to_mount_plan()
    assert bundle.name == "work" and bundle.version == "0.4.0"
    assert {row["module"] for row in plan["tools"]} == set(WORK_TOOLS)
    assert {row["module"] for row in plan["hooks"]} == set(WORK_HOOKS)
    assert set(plan["agents"]) == WORK_AGENTS
    assert len(config.app_bundles) == 5
    assert plan["session"]["orchestrator"]["module"] == "loop-live"
    context = plan["session"]["context"]
    assert context["module"] == "context-managed"
    assert context["config"]["engine"] == "boundary"
    assert context["config"]["token_meter"] == "actual"
    skills = next(row["config"] for row in plan["tools"] if row["module"] == "tool-skills")
    assert skills["skills"][:5] == [
        ".amplifier/skills", ".agents/skills", "~/.amplifier/skills",
        "~/.agents/skills", "@work-skills:skills"]
    assert skills["visibility"]["visibility_token_budget"] == 2500
    return plan


def bind_skill_sources(plan, receipt):
    """Same source list/order and discovery budget, exact offline addresses."""
    skills = next(row["config"] for row in plan["tools"] if row["module"] == "tool-skills")
    roots = {repo: Path(receipt["resources"][name]["root"])
             for name, (repo, _) in RESOURCES.items()}
    sources, bindings = [], []
    for value in skills["skills"]:
        selected = value
        if value.startswith("git+"):
            parsed = urlsplit(value.removeprefix("git+"))
            repo = parsed.path.rsplit("/", 1)[-1].split("@")[0].removesuffix(".git")
            assert repo in roots, value
            relative = parse_qs(parsed.fragment).get("subdirectory", [""])[0]
            selected = str(roots[repo] / relative)
            assert Path(selected).is_dir(), selected
            bindings.append({"declared": value, "selected": selected})
        sources.append(selected)
    return sources, bindings