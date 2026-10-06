"""Bounded installed receiving-boundary qualification (not a pytest module).

Run with the candidate venv's Python -I -B, outside the product checkout.
Provision each NEW --root once before entering a loopback-only network namespace:
  python -I -B THIS_FILE --group 1 --phase provision --root NEW_ROOT --overrides FILE
Then run --phase execute in that namespace. Group 2 execute starts ordinary,
frozen and resumed phases in separate interpreters, preserving snapshot bytes.
No production patches, import-path prepends, capture replacements or model keys.
The sibling acceptance script supplies unchanged HTTP/receipt test helpers only.
All result files are private; an incomplete boundary is BLOCKED, not waived.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import importlib.metadata as metadata
import importlib.util
import json
import os
from pathlib import Path
import shlex
import signal
import socket
import subprocess
import sys
import sysconfig
import traceback
import uuid


HOOK = ("git+https://github.com/microsoft/amplifier-bundle-context-intelligence"
        "@a80ce69d0e96e9a343501f238dcd867350dd9aa2"
        "#subdirectory=modules/hook-context-intelligence")
PINS = {
    "amplifier-core": "2.0.1",
    "amplifier-foundation": "ab87882027bc5cb3aa74a6f6de539560b2e4d264",
    "amplifier-module-loop-live": "bdd76badc58091ec47b55bb92cdf9e6f5bcc8e26",
    "amplifier-module-context-simple": "c7ec55db96888200ba619adedc857278daf787c5",
    "amplifier-module-provider-openai": "cab74f3fac672747da688a0ceb4ce231a9d1a6a3",
    "amplifier-module-tool-bash": "083f72fcac41424d4dd07947c612ff695bef0938",
    "amplifier-module-hook-context-intelligence": "a80ce69d0e96e9a343501f238dcd867350dd9aa2",
    "openai": "3.24.0",
    "uv": "0.12.23",
}
MODULES = {
    "context-simple": "amplifier_module_context_simple",
    "loop-live": "amplifier_module_loop_live",
    "provider-openai": "amplifier_module_provider_openai",
    "tool-bash": "amplifier_module_tool_bash",
}
MARKERS = ("GLOBAL", "PROJECT", "ROOT")


def helper():
    # Load a test helper by filename, never add product sources to sys.path.
    path = Path(__file__).with_name("acceptance_unified_foundations.py")
    spec = importlib.util.spec_from_file_location("foundation_acceptance_helpers", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


old = helper()
write = old.private_write


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(argv, **kwargs):
    return subprocess.run(argv, check=True, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=60, **kwargs).stdout


def environment(root):
    os.environ.update({
        "HOME": str(root / "home"), "AMPLIFIER_HOME": str(root / "native"),
        "AMPLIFIER_WEB_HOME": str(root / "app"),
        "AMPLIFIER_SESSION_STATE_HOME": str(root / "ownership"),
        "AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH": str(root / "capture"),
        "AMPLIFIER_SOURCE_STORE": str(root / "app/source-store"),
        "AMPLIFIER_RUNTIME_IMMUTABLE": "1",
        "XDG_CONFIG_HOME": str(root / "xdg-config"),
        "XDG_CACHE_HOME": str(root / "xdg-cache"),
        "XDG_STATE_HOME": str(root / "xdg-state"),
        "UV_CACHE_DIR": str(root / "uv-cache"),
        "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
    })


def installed():
    assert sys.flags.isolated and sys.flags.dont_write_bytecode
    assert sys.prefix != sys.base_prefix and not os.environ.get("PYTHONPATH")
    assert not any((p / "amplifier_web").is_dir() for p in (Path.cwd(), *Path.cwd().parents))
    sites = {Path(sysconfig.get_path(k)).resolve() for k in ("purelib", "platlib")}
    result = {}
    for name, pin in PINS.items():
        dist = metadata.distribution(name)
        direct = json.loads(dist.read_text("direct_url.json") or "{}")
        actual = direct.get("vcs_info", {}).get("commit_id") or dist.version
        assert actual == pin, (name, actual, pin)
        assert not direct.get("dir_info", {}).get("editable")
        result[name] = {"version": dist.version, "pin": actual, "direct_url": direct}
    sources = {}
    for name, package in {**MODULES, "application": "amplifier_web"}.items():
        dist = metadata.distribution("amplifier-unified" if name == "application" else
                                     "amplifier-module-" + name)
        path = Path(dist.locate_file(package + "/__init__.py")).resolve()
        assert path.is_file() and any(path.is_relative_to(site) for site in sites)
        sources[name] = str(path.parent)
        result.setdefault(dist.metadata["Name"], {}).update(file=str(path), sha256=digest(path.read_bytes()))
    return sources, result


def module_pins():
    pins = {}
    for name in ("loop-live", "context-simple", "provider-openai"):
        direct = json.loads(metadata.distribution("amplifier-module-" + name).read_text("direct_url.json"))
        pins[name] = "git+" + direct["url"] + "@" + direct["vcs_info"]["commit_id"]
        if direct.get("subdirectory"):
            pins[name] += "#subdirectory=" + direct["subdirectory"]
    return pins


def inventories(root):
    objects = {}
    for git in (root / "app/source-store/objects").glob("*/*/.git"):
        obj = git.parent
        tracked = run(["git", "-C", str(obj), "ls-files", "-z"]).split("\0")
        extra = run(["git", "-C", str(obj), "ls-files", "--others", "-z"]).split("\0")
        files = {name: digest((obj / name).read_bytes()) for name in tracked
                 if name and (obj / name).is_file()}
        objects[str(obj)] = {
            "HEAD": run(["git", "-C", str(obj), "rev-parse", "HEAD"]).strip(),
            "tracked_diff": run(["git", "-C", str(obj), "diff", "--name-only"]),
            "tracked_digest": digest(json.dumps(files, sort_keys=True).encode()),
            "extras": [name for name in extra if name and name != ".amplifier_cache_meta.json"],
        }
    # Bytecode in site-packages is different evidence from shared-object extras.
    site = Path(sysconfig.get_path("purelib"))
    return {"objects": objects, "site_bytecode_count": len(list(site.rglob("*.pyc")))}


def settings(root):
    import yaml
    write(root / "native/settings.yaml", yaml.safe_dump({
        "bundle": {"app": []}, "sources": {"modules": {"hook-context-intelligence": HOOK}},
    }, sort_keys=False))


def instructions(root, version):
    paths = (root / "native/AGENTS.md", root / "execution checkout/.amplifier/AGENTS.md",
             root / "execution checkout/AGENTS.md")
    for path, name in zip(paths, MARKERS):
        write(path, f"{name}-v{version}\n" + ("@../AGENTS.md\n" if name == "PROJECT" else ""))


def plan(sources, tools=False):
    def row(name):
        return {"module": name, "source": sources[name]}
    return {
        "bundle": {"name": "receiving-boundaries", "version": "1.0.0"},
        "session": {"orchestrator": row("loop-live"), "context": row("context-simple")},
        "providers": [{**row("provider-openai"), "config": {
            "api_key": "offline-synthetic-key", "base_url": "http://127.0.0.1:9/v1",
            "default_model": old.MODEL, "use_streaming": False, "max_retries": 0}}],
        "tools": [{**row("tool-bash"), "config": {"managed_processes": True}}] if tools else [],
        "hooks": [], "agents": {}, "instruction": "Receiving boundary acceptance. Do not call a model.",
    }


async def provision(root, overrides, group):
    import yaml
    from amplifier_foundation.modules import ModuleActivator
    sources, origins = installed()
    for name in ("home", "native", "app", "workspace", "ownership"):
        (root / name).mkdir()
    environment(root)
    settings(root)
    write(root / "qualification-overrides.txt", overrides.read_text())
    # A real disposable checkout/worktree, never a production chat attachment.
    repository = root / "cwd repository"
    repository.mkdir()
    run(["git", "init", str(repository)])
    write(repository / "seed.txt", "disposable receiving-boundary fixture\n")
    run(["git", "-C", str(repository), "add", "seed.txt"])
    run(["git", "-C", str(repository), "-c", "user.name=Acceptance Fixture",
         "-c", "user.email=fixture@example.invalid", "commit", "-m", "Fixture"])
    run(["git", "-C", str(repository), "worktree", "add", "--detach", str(root / "execution checkout")])
    (root / "execution checkout/.amplifier").mkdir()
    instructions(root, 1)
    write(root / "workspace/AGENTS.md", "ORIGINAL_WORKSPACE_DECOY\n")
    write(root / "home/.amplifier/AGENTS.md", "PHYSICAL_HOME_DECOY\n")
    ordinary = plan(sources, group == 3)
    if group == 2:
        ordinary["instruction"] += "\n@~/authored-literal.md"
        write(root / "home/authored-literal.md", "EXPLICIT_AUTHORED_TILDE\n")
    instruction = ordinary.pop("instruction")
    # Foundation YAML is a mount plan; authored instruction belongs in the
    # Markdown body, not an ignored YAML instruction field.
    write(root / "ordinary.md", "---\n" + yaml.safe_dump(ordinary, sort_keys=False) +
          "---\n" + instruction + "\n")
    write(root / "ordinary-plan.json", json.dumps(ordinary))
    requested = {"hook-context-intelligence": HOOK, **module_pins()}
    activated = {}
    # Normal Source/activation provisioning only; no mount or provider preimport.
    activator = ModuleActivator(cache_dir=root / "app/foundation/cache", strict=True,
                                install_overrides=root / "qualification-overrides.txt")
    for name, uri in requested.items():
        activated[name] = str(await activator.activate(name, uri))
    activator.finalize()
    record = {"status": "READY", "group": group, "origins": origins,
              "requested_sources": requested, "effective_sources": activated,
              "inventory": inventories(root), "pid": os.getpid()}
    assert all(not row["extras"] and not row["tracked_diff"]
               for row in record["inventory"]["objects"].values())
    write(root / "provision.json", json.dumps(record, indent=2) + "\n")


class Cold(old.Harness):
    def __init__(self, root):
        super().__init__(root)
        self.result["limitations"] = [
            "Explicit pinned CI source, installed candidate Python -I -B worker only.",
            "Default uv launcher is NOT qualified; no production bytecode policy repair.",
            "No account, audible voice, tool or delegated model claims."]
        self.result["check_scopes"] = {name: {"status": "not_started"} for name in
                                      ("preflight", "first_input", "cold_resume", "instruction_refresh", "cleanup")}
        self.result["qualification_scope"] = "GROUP1 cold/resume receiving boundaries"

    def service(self, app, workspace):
        from amplifier_web.runtime import RuntimeManager
        from amplifier_web.service import AppService
        service = AppService(app, workspace=workspace)
        self.services.append(service)
        runtime = RuntimeManager(command=[sys.executable, "-I", "-B", "-m", "amplifier_web.runtime_worker"],
                                 app_bridge=service.app_bridge, startup_timeout=120)
        runtime.home = app
        service.runtime = runtime
        self.runtimes.append(runtime)
        return service, runtime

    async def run(self):
        self.credentials_absent()
        _, origins = installed()
        self.result["qualified_origins"] = origins
        environment(self.root)
        # This group's request helper uses workspace; the other group owns the
        # separate execution-worktree/CWD proof.
        for v in (1,):
            old.private_write(self.root / "workspace/.amplifier/AGENTS.md", f"PROJECT-v{v}\n@../AGENTS.md\n")
            old.private_write(self.root / "workspace/AGENTS.md", f"ROOT-v{v}\n")
        self.sources = self.installed_sources()
        self.scope("preflight", "completed")
        before = inventories(self.root)
        self.result["source_before"] = before
        self.scope("first_input", "in_progress")
        base = await self.server()
        bundle = self.bundle(self.sources, "provider-openai", {
            "api_key": "offline-synthetic-key", "base_url": base, "default_model": old.MODEL,
            "use_streaming": False, "max_retries": 0, "timeout": 10}, "cold.yaml")
        service, runtime = self.service(self.root / "app", self.root / "workspace")
        sid = str(uuid.uuid4())
        await service.dispatch("session.create", {"id": sid, "bundle": str(bundle)}, include_state=False)
        await service.dispatch("session.naming", {"id": sid, "automatic": False}, include_state=False)
        self.check("naming_disabled", service._session(sid).get("autoName") is False)
        self.stage("first_input")
        first_id, second_id = f"{self.root.name}-first", f"{self.root.name}-second"
        first_text, second_text = f"First receiving request {self.root.name}.", f"Second receiving request {self.root.name}."
        self.result["session_id"] = sid
        await self.send(service, runtime, sid, first_id, first_text)
        self.check("first_one_post", len(self.posts()) == 1)
        self.instruction_request(0, 1)
        await self.capacity(service, sid, "first", 1)
        report = await self.start(service, runtime, sid)
        mounted = json.loads((self.root / "app/runtime-reports" / sid / "mounted.json").read_text())
        self.result["mounted_report"] = mounted
        self.check("implicit_ci_preserved", mounted["contextIntelligence"]["enabled"])
        path, rows = self.transcript(self.root / "workspace", sid)
        self.canonical_input(rows, first_id, first_text)
        self.scope("first_input", "completed")
        self.scope("cold_resume", "in_progress")
        self.stage("cold_resume_no_input")
        process = runtime.workers[sid]["process"]
        await runtime.stop(sid)
        self.check("first_worker_stopped", process.returncode is not None)
        raw = path.read_bytes()
        await service.close()
        service, runtime = self.service(self.root / "app", self.root / "workspace")
        report = await self.start(service, runtime, sid)
        self.check("cold_resumed", report["resumed"] is True)
        self.check("resume_zero_posts_no_replay", len(self.posts()) == 1 and path.read_bytes() == raw)
        self.scope("cold_resume", "completed")
        self.scope("instruction_refresh", "in_progress")
        self.stage("adjacent_resumed_input")
        self.instructions(2)
        await self.send(service, runtime, sid, second_id, second_text)
        self.check("second_exactly_one_post", len(self.posts()) == 2)
        self.instruction_request(1, 2)
        await self.capacity(service, sid, "second", 2)
        _, rows = self.transcript(self.root / "workspace", sid)
        self.canonical_input(rows, first_id, first_text)
        self.canonical_input(rows, second_id, second_text)
        self.result["source_after"] = inventories(self.root)
        self.check("shared_sources_unchanged", before["objects"] == self.result["source_after"]["objects"])
        self.scope("instruction_refresh", "completed")
        self.result["status"] = "passed"


class Mounted:
    def __init__(self, root, phase):
        self.root, self.phase, self.session, self.runtime = root, phase, None, None
        self.result = {"status": "BLOCKED", "stage": "preflight", "phase": phase,
                       "pid": os.getpid(), "checks": {}, "limitations": []}

    def check(self, name, value):
        self.result["checks"][name] = bool(value)
        if not value:
            raise AssertionError(name)

    async def mount(self, bundle, resume=False, sid=None):
        from amplifier_web.host.session import prepare_manager
        from amplifier_module_loop_live.runtime import Runtime
        from amplifier_core import AmplifierSession
        from amplifier_foundation.bundle import PreparedBundle
        self.result["stage"] = "prepare_manager"
        self.runtime = Runtime(session_id=sid or str(uuid.uuid4()))
        self.session, returned, report = await prepare_manager(
            self.root / "workspace", runtime=self.runtime, bundle=str(bundle),
            execution_workspace=self.root / "execution checkout", resume=resume,
            install_overrides=self.root / "qualification-overrides.txt",
            report_dir=self.root / "reports" / self.phase)
        self.check("real_core", isinstance(self.session, AmplifierSession))
        coordinator = self.session.coordinator
        self.check("real_prepared", isinstance(coordinator.get_capability("web.prepared"), PreparedBundle))
        self.result["mount"] = {k: report.get(k) for k in (
            "settings_file", "workspace", "execution_workspace", "resumed", "session_id",
            "tools", "providers", "contextIntelligence", "history_source")}
        self.boundary()
        origins = {}
        for name, obj in (("provider", next(iter(coordinator.get("providers").values()))),
                          ("context", coordinator.get("context")), ("orchestrator", coordinator.get("orchestrator"))):
            module = sys.modules[type(obj).__module__]
            origins[name] = str(Path(module.__file__).resolve())
        self.result["effective_implementation_origins"] = origins

    def boundary(self):
        coordinator = self.session.coordinator
        self.check("history_home", coordinator.get_capability("web.history_workspace") == str(self.root / "workspace"))
        self.check("execution_CWD", coordinator.get_capability("session.working_dir") == str(self.root / "execution checkout"))
        self.check("settings_home", Path(self.result["mount"]["settings_file"]).parent == self.root / "native")

    async def render(self):
        context = self.session.coordinator.get("context")
        before = copy.deepcopy(await context.get_messages())
        rows = await context.get_messages_for_request()
        self.check("factory_not_renderer_only", callable(getattr(context, "_system_prompt_factory", None)))
        self.check("canonical_unchanged", await context.get_messages() == before)
        text = "\n".join(row["content"] for row in rows if row.get("role") == "system")
        self.check("no_decoys", "ORIGINAL_WORKSPACE_DECOY" not in text and "PHYSICAL_HOME_DECOY" not in text)
        return text

    async def checkpoint(self):
        from amplifier_web.session_files import sessions_dir
        await self.session.coordinator.get_capability("live.checkpoint")()
        path = sessions_dir(self.root / "workspace") / self.session.session_id / "transcript.jsonl"
        self.check("native_original_not_CWD", path.is_file() and not (
            sessions_dir(self.root / "execution checkout") / self.session.session_id).exists())
        return path.read_bytes()

    async def close(self):
        if self.session:
            session, self.session = self.session, None
            await asyncio.wait_for(session.cleanup(), 30)

    async def ordinary(self):
        import yaml
        from amplifier_web.bundles import BundleManager
        self.result["stage"] = "ordinary"
        bundle_bytes = (self.root / "ordinary.md").read_bytes()
        await self.mount(self.root / "ordinary.md")
        paths = [self.root / "native/AGENTS.md", self.root / "execution checkout/.amplifier/AGENTS.md",
                 self.root / "execution checkout/AGENTS.md"]
        for transition in ("present", "delete", "create", "edit", "recreate"):
            if transition in ("delete", "recreate"):
                for path in paths:
                    path.unlink(missing_ok=True)
            if transition not in ("present", "delete"):
                instructions(self.root, transition)
            text = await self.render()
            self.check("literal_tilde_" + transition, text.count("EXPLICIT_AUTHORED_TILDE") == 1)
            self.check("ordinary_" + transition, all(
                text.count(name + ("-v1" if transition == "present" else "-v" + transition)) ==
                (0 if transition == "delete" else 1) for name in MARKERS))
        for path in paths[:2]:
            path.unlink()
        root_only = await self.render()
        self.check("root_only_no_home_fallback", "GLOBAL-v" not in root_only and
                   "PROJECT-v" not in root_only and root_only.count("ROOT-vrecreate") == 1)
        self.check("authored_bundle_unchanged", (self.root / "ordinary.md").read_bytes() == bundle_bytes
                   and b"@~/authored-literal.md" in bundle_bytes)
        self.check("ordinary_empty_native", await self.checkpoint() == b"")
        # Actual exporter freezes a named, self-contained instruction and exact pins.
        effective = json.loads((self.root / "ordinary-plan.json").read_text())
        pins = module_pins()
        exported = BundleManager(self.root / "app").export_document(
            {"sources": {"modules": pins}}, root_bundle=str(self.root / "ordinary.md"),
            name="receiving-frozen", effective_plan=effective,
            resources={"instruction": "FROZEN_RECEIVING_SENTINEL", "context": [], "namespaces": []})
        self.check("required_external_key", exported["requiredEnvironment"] == ["AMPLIFIER_PROVIDERS_0_CONFIG_API_KEY"])
        write(self.root / "snapshot.yaml", exported["content"])
        write(self.root / "native/keys.env", exported["requiredEnvironment"][0] + "=offline-synthetic-key\n")
        write(self.root / "snapshot-receipt.json", json.dumps({
            "sha256": digest(exported["content"].encode()), "pins": pins,
            "requiredEnvironment": exported["requiredEnvironment"]}, indent=2))
        self.result["status"] = "PASS"

    async def frozen(self):
        self.check("provider_not_preimported", "amplifier_module_provider_openai" not in sys.modules)
        snapshot = self.root / "snapshot.yaml"
        receipt = json.loads((self.root / "snapshot-receipt.json").read_text())
        before = snapshot.read_bytes()
        self.check("snapshot_exact_transfer", digest(before) == receipt["sha256"])
        sid = (self.root / "frozen-session-id").read_text().strip() if self.phase == "resume" else None
        await self.mount(snapshot, resume=self.phase == "resume", sid=sid)
        self.check("frozen_resume", self.result["mount"]["resumed"] is (self.phase == "resume"))
        if self.phase == "frozen":
            write(self.root / "frozen-session-id", self.session.session_id)
        baseline = await self.render()
        self.check("frozen_sentinel", baseline.count("FROZEN_RECEIVING_SENTINEL") == 1)
        native = await self.checkpoint()
        self.check("no_input_history", native == b"")
        paths = [self.root / "native/AGENTS.md", self.root / "execution checkout/.amplifier/AGENTS.md",
                 self.root / "execution checkout/AGENTS.md"]
        for transition in ("delete", "create", "edit", "recreate"):
            if transition in ("delete", "recreate"):
                for path in paths:
                    path.unlink(missing_ok=True)
            if transition != "delete":
                instructions(self.root, transition)
            self.check("frozen_exact_" + transition, await self.render() == baseline)
            self.boundary()
            self.check("history_exact_" + transition, await self.checkpoint() == native)
        self.check("export_exact_bytes", snapshot.read_bytes() == before)
        self.result["render_sha256"] = digest(baseline.encode())
        self.result["snapshot"] = receipt
        self.result["status"] = "PASS"

    async def activation_policy(self):
        from amplifier_foundation.bundle import Bundle, PreparedBundle
        before = dict(os.environ)
        self.result["stage"] = "declared_and_lazy_activation"
        events = []
        for name in ("declared", "lazy"):
            fixture = self.root / ("policy-" + name)
            write(fixture / "requirements.txt", "six==0.0.0\n")
            # Requirement-only modules need no editable installation or SDK.
            bundle = Bundle(name="policy-" + name, tools=[{
                "module": "tool-policy-" + name, "source": str(fixture)}] if name == "declared" else [])
            prepared = await bundle.prepare(
                strict=True, install_deps=True, refresh_dependencies=True,
                cache_dir=self.root / ("policy-cache-" + name),
                install_overrides=self.root / "qualification-overrides.txt",
                progress_callback=lambda action, detail: events.append({"action": action, "module": detail}))
            self.check("actual_prepared_policy_" + name, isinstance(prepared, PreparedBundle))
            if name == "lazy":
                resolved = await prepared.resolver.async_resolve("tool-policy-lazy", str(fixture))
                self.check("actual_lazy_source", resolved.resolve() == fixture)
            else:
                self.check("actual_declared_source", prepared.resolver.resolve("tool-policy-declared").resolve() == fixture)
            self.check("qualified_override_wins_" + name, metadata.version("six") == "1.17.0")
        self.check("real_dependency_installations", sum(e["action"] == "installing" for e in events) == 2)
        self.check("activation_preserves_parent_environment", dict(os.environ) == before)
        self.result["activation_receipt"] = {
            "requested_requirement": "six==0.0.0", "selected_override": "six==1.17.0",
            "effective_version": metadata.version("six"), "events": events,
            "installer_policy_file_sha256": digest((self.root / "qualification-overrides.txt").read_bytes()),
            "scope": "Actual Foundation declared/lazy dependency activation; not a delegated model call"}

    async def bash(self):
        from amplifier_web.runtime_controls import RuntimeControls
        before = dict(os.environ)
        await self.mount(self.root / "ordinary.md")
        controls = RuntimeControls(self.session, self.runtime)
        tools = self.session.coordinator.get("tools")
        self.check("actual_mounted_BashTool", "bash" in tools and
                   type(tools["bash"]).__module__ == "amplifier_module_tool_bash")
        self.result["bash_origin"] = str(Path(sys.modules[type(tools["bash"]).__module__].__file__).resolve())
        receipts = []

        async def invoke(arguments):
            # Supported host dispatch: schema, pre/approval, tool, post, checkpoint.
            returned = await controls.invoke({"name": "bash", "arguments": arguments})
            receipts.append(returned)
            self.result["tool_receipts"] = receipts
            return returned["result"]

        def command(expression):
            return shlex.join([sys.executable, "-I", "-B", "-c", "import os; print(bool(" + expression + "))"])

        self.result["stage"] = "foreground_no_app_override"
        returned = await invoke({"command": command("'UV_OVERRIDE' not in os.environ")})
        self.check("foreground_no_app_pin", returned["success"] and returned["output"]["stdout"].strip() == "True")
        override, constraint = self.root / "legitimate-old-looking-overrides.txt", self.root / "caller-constraints.txt"
        write(override, "six==1.17.0\n")
        write(constraint, "six>=1.0\n")
        os.environ["UV_OVERRIDE"], os.environ["UV_CONSTRAINT"] = str(override), str(constraint)
        caller = dict(os.environ)
        expression = (f"os.environ.get('UV_OVERRIDE') == {str(override)!r} and "
                      f"os.environ.get('UV_CONSTRAINT') == {str(constraint)!r} and "
                      f"os.getcwd() == {str(self.root / 'execution checkout')!r}")
        returned = await invoke({"command": command(expression)})
        self.check("legitimate_exact_caller_environment", returned["success"] and returned["output"]["stdout"].strip() == "True")
        self.check("parent_unchanged_after_foreground", dict(os.environ) == caller)
        returned = await invoke({"action": "start", "command": command(expression) + "; sleep 20", "timeout": 30})
        self.check("managed_start", returned["success"])
        identity = returned["output"]["process_id"]
        try:
            for action in ("status", "read", "wait"):
                result = await invoke({"action": action, "process_id": identity,
                                       **({"wait_ms": 1000} if action == "wait" else {})})
                self.check("managed_" + action, result["success"])
        finally:
            returned = await invoke({"action": "terminate", "process_id": identity})
            self.check("managed_terminate", returned["success"])
        failed = await invoke({"command": "exit 7"})
        self.check("failed_command_real_exit", not failed["success"] and failed["output"]["returncode"] == 7)
        concurrent = await asyncio.gather(invoke({"command": command(expression)}),
                                          invoke({"command": command(expression)}))
        self.check("concurrent_caller_no_leak", all(r["success"] and r["output"]["stdout"].strip() == "True" for r in concurrent))
        self.check("parent_unchanged_after_failure_concurrency", dict(os.environ) == caller)
        await self.activation_policy()
        # Public child initialization mounts real tools, but does not run an LLM
        # delegate. Keep that receiving gap separate rather than invent a call.
        prepared = self.session.coordinator.get_capability("web.prepared")
        child = await prepared.create_session(parent_id=self.session.session_id,
                                              session_cwd=self.root / "execution checkout")
        try:
            self.check("actual_structured_child_mounted", "bash" in child.coordinator.get("tools"))
            self.result["structured_child"] = {"session_id": child.session_id, "parent_id": child.parent_id,
                                                "model_executed": False}
        finally:
            await child.cleanup()
        self.boundary()
        await self.close()
        await self.mount(self.root / "ordinary.md")
        self.check("parent_unchanged_after_remount", dict(os.environ) == caller)
        self.result["boundaries"] = {
            "mounted_foreground_managed_failure_concurrency": "PASS",
            "real_delegated_tool_child": "BLOCKED: no model-free delegate receiving API established",
            "lazy_conflicting_dependency_activation": "PASS",
            "legacy_background_and_cancellation": "BLOCKED: not executed"}
        self.result["status"] = "BLOCKED"
        # Only this private fixture's two deliberate caller variables are restored.
        for key in ("UV_OVERRIDE", "UV_CONSTRAINT"):
            if key in before:
                os.environ[key] = before[key]
            else:
                os.environ.pop(key, None)


async def execute(args):
    root = args.root
    if args.phase == "provision":
        root.mkdir(parents=True, exist_ok=False, mode=0o700)
        await provision(root, args.overrides, args.group)
        return 0
    environment(root)
    sources, origins = installed()  # metadata only, not provider imports
    assert json.loads((root / "provision.json").read_text())["status"] == "READY"
    if args.group == 2 and args.phase == "execute":
        phases = []
        for phase in ("ordinary", "frozen", "resume"):
            argv = [sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                    "--root", str(root), "--group", "2", "--phase", phase]
            with (root / (phase + ".log")).open("w") as log:
                child = await asyncio.create_subprocess_exec(*argv, cwd=root.parent, stdin=asyncio.subprocess.DEVNULL,
                                                            stdout=log, stderr=asyncio.subprocess.STDOUT)
                try:
                    code = await asyncio.wait_for(child.wait(), 180)
                except BaseException:
                    child.terminate()
                    await child.wait()
                    raise
            phases.append({"phase": phase, "argv": argv, "nested_exit": code})
            if code:
                break
        write(root / "result.json", json.dumps({"status": "PASS" if len(phases) == 3 and
              all(p["nested_exit"] == 0 for p in phases) else "BLOCKED", "phases": phases,
              "unreached": [p for p in ("ordinary", "frozen", "resume") if p not in {r["phase"] for r in phases}]}, indent=2))
        return 0 if len(phases) == 3 and all(p["nested_exit"] == 0 for p in phases) else 1
    harness = Cold(root) if args.group == 1 else Mounted(root, args.phase)
    outcome = "BLOCKED"
    try:
        if args.group == 1:
            await harness.run()
            outcome = "PASS"
        else:
            harness.result["qualified_origins"] = origins
            harness.result["source_before"] = inventories(root)
            await (harness.bash() if args.group == 3 else
                   harness.ordinary() if args.phase == "ordinary" else harness.frozen())
            outcome = harness.result["status"]
    except BaseException as exc:
        harness.result["error_type"] = type(exc).__name__
        write(root / (args.phase + "-traceback.txt"), traceback.format_exc())
        if args.group == 1:
            harness.record_failure(exc)
        if isinstance(exc, AssertionError):
            outcome = "FAIL"
    finally:
        try:
            if args.group == 1:
                await harness.cleanup()
                if not harness.result["checks"].get("cleanup_complete"):
                    outcome = "FAIL"
            else:
                await harness.close()
                harness.result["checks"]["cleanup_complete"] = True
            harness.result["source_after_cleanup"] = inventories(root)
        except BaseException as exc:
            harness.result["cleanup_error_type"] = type(exc).__name__
            outcome = "FAIL"
        harness.result["status"] = outcome
        name = "result.json" if args.group != 2 else args.phase + "-result.json"
        write(root / name, json.dumps(harness.result, indent=2, default=str) + "\n")
    print(json.dumps({"group": args.group, "phase": args.phase, "status": outcome,
                      "stage": harness.result["stage"]}), flush=True)
    return 0 if outcome == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--group", type=int, choices=(1, 2, 3), required=True)
    parser.add_argument("--phase", choices=("provision", "execute", "ordinary", "frozen", "resume"), required=True)
    parser.add_argument("--overrides", type=Path)
    args = parser.parse_args()
    args.root = args.root.resolve()
    if args.phase == "provision" and args.overrides is None:
        parser.error("provision requires explicit --overrides")
    os.umask(0o077)
    async def bounded():
        task = asyncio.current_task()
        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(sig, task.cancel)
        return await asyncio.wait_for(execute(args), 540)
    return asyncio.run(bounded())


if __name__ == "__main__":
    raise SystemExit(main())