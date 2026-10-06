"""Installed prompt-assembly acceptance, deliberately not collected by pytest.

Root owns ONE finite run, outside source checkouts:
  LANE_VENV/bin/python -I /absolute/tests/acceptance_instruction_boundary.py --root NEW_ROOT

No explicit install/shell/git commands, input admission, server, or
provider.complete/stream calls; normal Foundation source activation is unchanged.
Ordinary preparation retains normal implicit Context Intelligence composition.
Any source-contents failure is retained, never bypassed/retried. Cold new/resumed
requests remain blocked by the known hook-context-intelligence shared-source
contents change; do not retry cold. This tests actual prompt assembly only.
Actual provider-request sentinels are separate evidence from root's warm harness.
The execution checkout uses supported execution_workspace, not git-worktree
creation/attachment. os.chdir simulates unrelated process cwd, NOT a BashTool.
Snapshot export's required key is supplied via owned synthetic keys.env only.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import importlib
import importlib.metadata
import inspect
import json
import os
from pathlib import Path
import re
import sys
import sysconfig
from urllib.parse import urlsplit
import uuid


PACKAGES = {
    "amplifier_web": "amplifier-unified",
    "amplifier_core": "amplifier-core",
    "amplifier_foundation": "amplifier-foundation",
    "amplifier_module_loop_live": "amplifier-module-loop-live",
    "amplifier_module_context_simple": "amplifier-module-context-simple",
    "amplifier_module_provider_openai": "amplifier-module-provider-openai",
    "openai": "openai",
}
MODULES = ("loop-live", "context-simple", "provider-openai")
LOCAL = ("GLOBAL", "EXEC_PROJECT", "EXEC_ROOT")
DECOY = "ORIGINAL_DECOY"
SYNTHETIC_KEY = "instruction-boundary-synthetic-not-a-real-key"
CREDENTIAL_NAMES = (
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AZURE_OPENAI_API_KEY", "AZURE_API_KEY",
    "GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS",
    "GROQ_API_KEY", "MISTRAL_API_KEY", "DEEPSEEK_API_KEY", "XAI_API_KEY",
    "OPENROUTER_API_KEY", "TOGETHER_API_KEY", "FIREWORKS_API_KEY",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "GITHUB_TOKEN", "GH_TOKEN", "COPILOT_API_KEY", "COPILOT_AGENT_TOKEN",
    "OPENAI_ACCESS_TOKEN", "OPENAI_REFRESH_TOKEN", "CHATGPT_ACCESS_TOKEN",
)


def private_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


class Harness:
    def __init__(self, root):
        self.root, self.session = root, None
        self.original = root / "original-workspace"
        self.checkout = root / "execution-checkout"
        self.config_home = root / "config-home"
        self.local_paths = (self.config_home / "AGENTS.md",
                            self.checkout / ".amplifier/AGENTS.md", self.checkout / "AGENTS.md")
        self.result = {
            "status": "failed", "stage": "preflight", "checks": {}, "origins": {},
            "mounts": {}, "renders": {}, "root": str(root),
            "command_argv": [sys.executable, "-I", str(Path(__file__).resolve()), "--root", str(root)],
            "scope": "Actual installed prompt assembly; no input admitted or provider API requests.",
            "limitations": [
                "execution_workspace checkout only; no git-worktree creation or attachment.",
                "Unrelated process cwd simulation only; no mounted BashTool.",
                "Canonical history check covers an empty no-input transcript.",
                "No cold new/resumed request retry; known CI shared-source contents blocker.",
                "Provider-request sentinels belong to root's separate warm harness, not this result.",
                "No OS egress firewall asserted; root owns DTU isolation and preinstalled dependencies.",
            ],
        }

    def check(self, name, condition):
        self.result["checks"][name] = bool(condition)
        if not condition:
            raise AssertionError(name)

    def credentials_absent(self):
        self.check("ambient_provider_credentials_absent",
                   not any(name in os.environ for name in CREDENTIAL_NAMES))

    def configure(self):
        for name in ("home", "app", "legacy", "state", "capture", "third-folder"):
            (self.root / name).mkdir(mode=0o700)
        for path in (self.config_home, self.original / ".amplifier", self.checkout / ".amplifier"):
            path.mkdir(parents=True, mode=0o700)
        os.environ.update({
            "HOME": str(self.root / "home"), "AMPLIFIER_HOME": str(self.config_home),
            "AMPLIFIER_WEB_HOME": str(self.root / "app"),
            "AMPLIFIER_UNIFIED_IMPORT_HOME": str(self.root / "legacy"),
            "AMPLIFIER_SESSION_STATE_HOME": str(self.root / "state"),
            "AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH": str(self.root / "capture"),
            "AMPLIFIER_RUNTIME_IMMUTABLE": "1",
            "XDG_CONFIG_HOME": str(self.root / "xdg-config"),
            "XDG_CACHE_HOME": str(self.root / "xdg-cache"),
            "XDG_STATE_HOME": str(self.root / "xdg-state"),
            "UV_CACHE_DIR": str(self.root / "uv-cache"),
        })
        private_write(self.config_home / "settings.yaml", "bundle:\n  app: []\n")
        for path in (self.original / "AGENTS.md", self.original / ".amplifier/AGENTS.md"):
            private_write(path, DECOY + "\n")
        for path in (self.root / "third-folder/AGENTS.md",
                     self.root / "third-folder/.amplifier/AGENTS.md"):
            private_write(path, "UNRELATED_CWD_DECOY\n")
        self.instructions()

    def instructions(self, suffix=""):
        for path, sentinel in zip(self.local_paths, LOCAL):
            private_write(path, sentinel + suffix + "\n" +
                          ("@../AGENTS.md\n" if sentinel == "EXEC_PROJECT" else ""))

    def installed(self):
        self.check("isolated_venv", bool(sys.flags.isolated) and sys.prefix != sys.base_prefix)
        self.check("outside_source_cwd", not any(
            (path / "amplifier_web").is_dir() for path in (Path.cwd(), *Path.cwd().parents)))
        self.sites = {Path(sysconfig.get_path(key)).resolve() for key in ("purelib", "platlib")}
        self.check("venv_sitepackages", all("site-packages" in path.parts and
                   path.is_relative_to(Path(sys.prefix).absolute()) for path in self.sites))
        sources, pins = {}, {}
        for package, distribution_name in PACKAGES.items():
            self.result["stage"] = "installed_import:" + package
            module = importlib.import_module(package)
            origin = Path(module.__file__).resolve()
            distribution = importlib.metadata.distribution(distribution_name)
            direct = json.loads(distribution.read_text("direct_url.json") or "{}")
            self.check("installed_noneditable:" + package,
                       any(origin.is_relative_to(site) for site in self.sites) and
                       not direct.get("dir_info", {}).get("editable", False) and
                       origin == Path(distribution.locate_file(package + "/__init__.py")).resolve())
            self.result["origins"][package] = {
                "file": str(origin), "distribution": distribution_name, "version": distribution.version,
            }
            sources[package] = str(origin.parent)
            module_id = package.removeprefix("amplifier_module_").replace("_", "-")
            if module_id in MODULES:
                vcs, url = direct.get("vcs_info", {}), direct.get("url", "")
                commit = vcs.get("commit_id", "")
                parsed = urlsplit(url)
                self.check("immutable_git_provenance:" + module_id,
                           vcs.get("vcs") == "git" and bool(re.fullmatch(r"[0-9a-fA-F]{40}", commit))
                           and parsed.scheme == "https" and bool(parsed.hostname)
                           and not (parsed.username or parsed.password or parsed.query or parsed.fragment))
                subdir = direct.get("subdirectory", "")
                self.check("safe_git_subdirectory:" + module_id, isinstance(subdir, str) and
                           not Path(subdir).is_absolute() and ".." not in Path(subdir).parts)
                pins[module_id] = "git+" + url + "@" + commit + (
                    "#subdirectory=" + subdir if subdir else "")
        self.result["immutable_sources"] = pins
        return sources, pins

    def object_origin(self, label, obj):
        module = importlib.import_module(type(obj).__module__)
        origin = Path(module.__file__).resolve()
        self.check("mounted_sitepackages:" + label,
                   any(origin.is_relative_to(site) for site in self.sites))
        self.result["origins"][label] = {"file": str(origin), "class": type(obj).__qualname__}

    async def mount(self, label, bundle):
        from amplifier_core import AmplifierSession
        from amplifier_foundation.bundle import PreparedBundle
        from amplifier_module_loop_live.runtime import Runtime
        from amplifier_web.host.session import prepare_manager
        import amplifier_web.runtime_worker

        self.result["stage"] = label + ":prepare_manager"
        overrides = Path(amplifier_web.runtime_worker.__file__).with_name("runtime_deps") / "compatibility.txt"
        self.check("installed_compatibility_file", overrides.is_file())
        runtime = Runtime(session_id=str(uuid.uuid4()))
        session, returned, report = await prepare_manager(
            self.original, runtime=runtime, bundle=str(bundle), execution_workspace=self.checkout,
            resume=False, install_overrides=overrides, report_dir=self.root / "reports" / label)
        self.session = session
        coordinator = session.coordinator
        self.result["mounts"][label] = {key: report.get(key) for key in (
            "settings_file", "workspace", "execution_workspace", "session_id", "resumed",
            "tools", "providers", "history_source", "contextIntelligence", "module_load_failures")}
        self.result["mounts"][label]["report_path"] = str(self.root / "reports" / label / "mounted.json")
        self.check(label + ":actual_core_session", isinstance(session, AmplifierSession) and returned is runtime)
        self.check(label + ":actual_foundation_prepared",
                   isinstance(coordinator.get_capability("web.prepared"), PreparedBundle))
        self.check(label + ":settings_home", Path(report["settings_file"]).parent == self.config_home)
        self.check(label + ":workspace", report["workspace"] == str(self.original))
        self.check(label + ":execution_workspace", report["execution_workspace"] == str(self.checkout))
        self.check(label + ":new_no_tools", not report["resumed"] and not coordinator.get("tools"))
        self.boundary(label)
        for kind in ("context", "orchestrator"):
            self.object_origin(label + ":" + kind, coordinator.get(kind))
        self.object_origin(label + ":provider", next(iter(coordinator.get("providers").values())))
        self.check(label + ":context_simple", type(coordinator.get("context")).__module__ ==
                   "amplifier_module_context_simple")
        self.check(label + ":loop_live", type(coordinator.get("orchestrator")).__module__.startswith(
            "amplifier_module_loop_live"))
        self.check(label + ":provider_openai", type(next(iter(coordinator.get("providers").values()))).__module__
                   == "amplifier_module_provider_openai")
        self.check(label + ":factory_registered", callable(getattr(
            coordinator.get("context"), "_system_prompt_factory", None)))
        self.check(label + ":ci_composition", bool(report["contextIntelligence"]["enabled"]) ==
                   (label == "ordinary"))

    def boundary(self, label):
        coordinator = self.session.coordinator
        history = coordinator.get_capability("web.history_workspace")
        execution = coordinator.get_capability("session.working_dir")
        self.result["mounts"][label.split(":")[0]]["capability_excerpt"] = {
            "web.history_workspace": history, "session.working_dir": execution,
        }
        self.check(label + ":history_capability", history == str(self.original))
        self.check(label + ":working_dir_capability", execution == str(self.checkout))

    async def render(self, label, frozen=False):
        self.result["stage"] = label + ":get_messages_for_request"
        context = self.session.coordinator.get("context")
        before = copy.deepcopy(await context.get_messages())
        rows = await context.get_messages_for_request()
        self.check(label + ":canonical_unchanged", await context.get_messages() == before == [])
        systems = [row["content"] for row in rows if row.get("role") == "system"]
        self.check(label + ":system_text", bool(systems) and all(isinstance(text, str) for text in systems))
        text = "\n".join(systems)
        sentinels = (*LOCAL, DECOY, "UNRELATED_CWD_DECOY", "FROZEN_SENTINEL")
        counts = {sentinel: text.count(sentinel) for sentinel in sentinels}
        self.result["renders"][label] = {
            "sha256": hashlib.sha256(text.encode()).hexdigest(), "sentinel_counts": counts,
            "system_excerpt": [sentinel for sentinel in sentinels if counts[sentinel]],
        }
        self.check(label + ":no_decoys", counts[DECOY] == counts["UNRELATED_CWD_DECOY"] == 0)
        self.check(label + ":sentinels", all(counts[name] == (0 if frozen else 1) for name in LOCAL)
                   and counts["FROZEN_SENTINEL"] == (1 if frozen else 0))
        return text

    async def checkpoint(self, label, expected=None):
        from amplifier_web.session_files import sessions_dir

        self.result["stage"] = label + ":checkpoint"
        await self.session.coordinator.get_capability("live.checkpoint")()
        identity = self.session.session_id
        path = sessions_dir(self.original) / identity / "transcript.jsonl"
        wrong = sessions_dir(self.checkout) / identity
        self.check(label + ":native_original_only", path.is_file() and not wrong.exists())
        raw = path.read_bytes()
        self.check(label + ":pure_native", raw == b"" and (expected is None or raw == expected))
        self.result.setdefault("checkpoints", {})[label] = {
            "transcript": str(path), "absent_checkout_session": str(wrong),
            "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
        }
        return raw

    async def close(self):
        if self.session is not None:
            session, self.session = self.session, None
            try:
                await asyncio.wait_for(session.cleanup(), 20)
            except BaseException as exc:
                self.result["cleanup_error_type"] = type(exc).__name__
                self.result["checks"]["cleanup_complete"] = False
                raise

    async def run(self):
        self.credentials_absent()
        self.configure()
        sources, pins = self.installed()
        from amplifier_web.bundles import BundleManager
        from amplifier_web.host.session import is_snapshot, prepare_manager
        from amplifier_foundation.registry import load_bundle
        import yaml

        self.check("supported_prepare_api", all(key in inspect.signature(prepare_manager).parameters
                   for key in ("execution_workspace", "install_overrides", "resume")))
        def declaration(name):
            return {"module": name, "source": sources["amplifier_module_" + name.replace("-", "_")]}
        plan = {
            "session": {"orchestrator": declaration("loop-live"), "context": declaration("context-simple")},
            "providers": [{**declaration("provider-openai"), "config": {
                "api_key": SYNTHETIC_KEY, "base_url": "http://127.0.0.1:9", "model": "gpt-5.4"}}],
            "tools": [], "hooks": [], "agents": {},
        }
        ordinary = self.root / "ordinary.yaml"
        private_write(ordinary, yaml.safe_dump({
            **plan, "bundle": {"name": "instruction-boundary", "version": "1.0.0"},
            "instruction": "Prompt assembly acceptance only. Do not use tools.",
        }, sort_keys=False))
        self.check("ordinary_not_snapshot", not is_snapshot(await load_bundle(str(ordinary))))
        await self.mount("ordinary", ordinary)
        await self.render("ordinary")
        os.chdir(self.root / "third-folder")
        self.boundary("ordinary:unrelated_cwd")
        await self.render("ordinary:unrelated_cwd")
        self.boundary("ordinary:after_render")
        await self.checkpoint("ordinary")
        await self.close()

        self.result["stage"] = "snapshot:export_document"
        exported = BundleManager(self.root / "app").export_document(
            {"sources": {"modules": pins}}, root_bundle=str(ordinary), name="instruction-boundary-frozen",
            effective_plan=plan, resources={"instruction": "FROZEN_SENTINEL", "context": [], "namespaces": []})
        required = exported["requiredEnvironment"]
        self.check("snapshot_owned_credential_reference",
                   required == ["AMPLIFIER_PROVIDERS_0_CONFIG_API_KEY"] and required[0] not in os.environ)
        private_write(self.config_home / "keys.env", required[0] + "=" + SYNTHETIC_KEY + "\n")
        snapshot = self.root / exported["filename"]
        private_write(snapshot, exported["content"])
        loaded = await load_bundle(str(snapshot))
        self.check("actual_export_is_snapshot", is_snapshot(loaded))
        saved = loaded.to_mount_plan()
        self.check("actual_snapshot_immutable_sources", all(
            saved["session"][kind]["source"] == pins[name]
            for kind, name in (("orchestrator", "loop-live"), ("context", "context-simple"))) and
            saved["providers"][0]["source"] == pins["provider-openai"])
        self.result["snapshot"] = {"file": str(snapshot), "version": loaded.version,
                                   "required_environment_names": required}
        await self.mount("snapshot", snapshot)
        self.check("distinct_new_runtime", self.result["mounts"]["ordinary"]["session_id"] !=
                   self.result["mounts"]["snapshot"]["session_id"])
        baseline = await self.render("snapshot", frozen=True)
        native = await self.checkpoint("snapshot")
        for transition in ("create", "edit", "delete"):
            if transition in ("create", "delete"):
                for path in self.local_paths:
                    path.unlink()
            if transition != "delete":
                self.instructions("_" + transition.upper())
            label = "snapshot:" + transition
            self.check(label + ":exact_frozen_render", await self.render(label, frozen=True) == baseline)
            self.boundary(label)
            await self.checkpoint(label, native)
        self.credentials_absent()
        await self.close()


async def execute(root):
    harness, cwd = Harness(root), Path.cwd()
    try:
        await asyncio.wait_for(harness.run(), 180)
        harness.result["status"] = "passed"
    except BaseException as exc:
        # Types and owned diagnostic paths only; never stringify errors or dump env/config.
        harness.result["error_type"] = type(exc).__name__
        types, paths, seen = [], [], set()
        current = exc
        while current is not None and id(current) not in seen and len(seen) < 8:
            seen.add(id(current))
            types.append(type(current).__name__)
            diagnostic = getattr(current, "diagnostic_path", None)
            if isinstance(diagnostic, (str, Path)):
                path = Path(diagnostic).resolve()
                if path.is_relative_to(root):
                    paths.append(str(path))
            current = current.__cause__ or current.__context__
        harness.result.update(error_types=list(dict.fromkeys(types)),
                              diagnostic_paths=list(dict.fromkeys(paths)))
    finally:
        try:
            await harness.close()
            harness.result["checks"]["cleanup_complete"] = "cleanup_error_type" not in harness.result
        except BaseException as exc:
            harness.result["status"] = "failed"
            harness.result["checks"]["cleanup_complete"] = False
            harness.result["cleanup_error_type"] = type(exc).__name__
        os.chdir(cwd)
        private_write(root / "result.json", json.dumps(harness.result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": harness.result["status"], "stage": harness.result["stage"],
                      "result": str(root / "result.json")}), flush=True)
    return 0 if harness.result["status"] == "passed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path, help="New nonexistent private artifact root")
    root = parser.parse_args().root.expanduser().resolve()
    try:
        root.mkdir(parents=True, exist_ok=False, mode=0o700)
    except OSError as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}), flush=True)
        return 1
    os.umask(0o077)
    return asyncio.run(execute(root))


if __name__ == "__main__":
    raise SystemExit(main())