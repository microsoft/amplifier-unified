"""Prepare a custom configuration on explicit execution, never while browsing.

Offered profiles share the qualified app environment. A workspace override gets
its own immutable candidate; it must never pip-install into that shared runtime.
Identical effective configurations share the same prepared environment afterward.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
import hashlib
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from types import SimpleNamespace


def configuration_key(settings, *roots):
    # Provider IDs, keys, models and prompts are runtime values. They must not
    # create a new Python graph for every conversation or credential rotation.
    from .builtin_behaviors import app_behaviors

    def declarations(value):
        if isinstance(value, dict):
            return {
                key: declarations(item)
                for key, item in value.items()
                if key not in {"config", "instruction", "context", "id", "instance_id"}
                or key == "context"
                and isinstance(item, dict)
                and "module" in item
            }
        if isinstance(value, list):
            return [declarations(item) for item in value]
        return value

    value = {
        "sources": settings.get("sources", {}),
        "bundles": settings.get("bundle", {}).get("added", {}),
        "behaviors": app_behaviors(settings),
        "routing": bool(settings.get("routing")),
        "modules": declarations(settings.get("config", {})),
        "overrides": {
            key: row.get("source")
            for key, row in settings.get("overrides", {}).items()
            if isinstance(row, dict) and row.get("source")
        },
    }
    text = json.dumps(value, sort_keys=True, default=str)
    for root in sorted(
        {str(Path(root).resolve()) for root in roots}, key=len, reverse=True
    ):
        text = text.replace(root, "$APP")
    return hashlib.sha256(text.encode()).hexdigest()


def _plan_sources(plan):
    """Yield source declarations, never paths buried in execution config."""
    if not isinstance(plan, dict):
        return
    for section in ("providers", "tools", "hooks"):
        for row in plan.get(section, []):
            if isinstance(row, dict) and isinstance(row.get("module"), str):
                yield row.get("source")
    session = plan.get("session", {})
    if isinstance(session, dict):
        for section in ("orchestrator", "context"):
            row = session.get(section)
            if isinstance(row, dict) and isinstance(row.get("module"), str):
                yield row.get("source")
    # Top-level context is a map of files, not the session's context module.
    for row in plan.get("spawn", {}).get("tools", []):
        if isinstance(row, dict) and isinstance(row.get("module"), str):
            yield row.get("source")
    for include in plan.get("includes", []):
        yield include.get("bundle") if isinstance(include, dict) else include
    agents = plan.get("agents", {})
    if isinstance(agents, dict):
        for agent in agents.values():
            yield from _plan_sources(agent)


def _local_build_inputs(config, edited, local, bundle):
    from .builtin_behaviors import app_behaviors

    # Settings source maps and module rows have explicit source semantics.
    # Walking arbitrary values (even after dropping "config") would still
    # mistake project_dir, base_path, source_base_paths and resources for code.
    declarations = [
        bundle,
        *config.module_sources.values(),
        *config.bundle_sources.values(),
        *config.settings.get("bundle", {}).get("added", {}).values(),
        *app_behaviors(config.settings),
        *_plan_sources(config.settings.get("config", {})),
        *_plan_sources(edited),
    ]
    inputs = {local.resolve()} if local else set()
    for value in declarations:
        if not isinstance(value, str) or not value.startswith(
            ("/", "./", "../", "~", "file://")
        ):
            continue
        if value.startswith("file://"):
            from amplifier_foundation.paths import parse_uri

            parsed = parse_uri(value)
            path = Path(parsed.path).expanduser()
            if parsed.subpath:
                path = path / parsed.subpath
        else:
            path = Path(value).expanduser()
        if not path.is_absolute():
            path = config.workspace / path
        # Generation-owned sources are already immutable and identified by
        # the parent generation. Hash only external user-owned inputs here.
        if path.exists() and not path.resolve().is_relative_to(config.home.resolve()):
            inputs.add(path.resolve())
    return inputs


def _write_attempt(stage, attempt):
    from .host.config import write_private

    write_private(stage / "profile-attempt.json", json.dumps(attempt))


@asynccontextmanager
async def _profile_attempt(home, generation, selected, stage, index):
    """Retain diagnostics; cancelled to_thread writers may still be running."""
    attempt = {
        "generation": selected,
        "parentGeneration": generation,
        "status": "preparing",
        "errorType": None,
        "startedAt": time.time(),
        "paths": {
            "receipt": str(stage),
            "foundation": str(stage / "foundation"),
            "sharedConfig": str(stage / "shared-config"),
            "preparationProject": str(Path(home) / "runtime" / ("prepare-" + selected)),
            "qualifiedProject": None,
            "profileIndex": str(index),
        },
        "cleanup": {
            "owner": "runtime environment owner",
            "responsibility": (
                "Retain failed/cancelled artifacts. Remove only disposable owned "
                "paths after all preparation threads and processes have stopped; "
                "qualified projects may be shared. Never replay this attempt."
            ),
        },
    }
    try:
        _write_attempt(stage, attempt)
        yield attempt
        attempt.update(status="succeeded", finishedAt=time.time())
        _write_attempt(stage, attempt)
    except BaseException as error:
        attempt.update(
            status="cancelled" if isinstance(error, asyncio.CancelledError) else "failed",
            errorType=type(error).__name__,
            finishedAt=time.time(),
        )
        try:
            _write_attempt(stage, attempt)
        except Exception as recording_error:
            error.add_note(
                f"Profile attempt status write failed ({type(recording_error).__name__}); "
                f"artifacts retained at {stage}. Runtime environment owner must "
                "wait for preparation threads/processes before cleanup."
            )
        # Only these admission markers belong to this coroutine. Do not remove
        # a directory: a cancelled copy/freeze/verification thread can outlive it.
        try:
            for marker in ("validated.json", "profiles-qualified.json"):
                (stage / marker).unlink(missing_ok=True)
            if index.exists() and json.loads(index.read_text()).get("generation") == selected:
                index.unlink()
        except Exception as cleanup_error:
            error.add_note(
                f"Profile admission-marker cleanup failed ({type(cleanup_error).__name__}); "
                f"inspect retained attempt at {stage} before admitting it."
            )
        raise


async def ensure(home, generation, session):
    from .host.config import read_config, write_private
    from .runtime_environment import project_path, receipt_directory
    from .updates import process

    if not generation:
        return generation  # Legacy first-start setup keeps its existing contract.
    receipt = receipt_directory(home, generation)
    descriptor = receipt / "profiles-qualified.json"
    if not descriptor.exists():
        return generation  # A pre-migration generation has not opted into immutability.
    config = read_config(
        session["workspace"],
        home=home,
        session_id=session.get("runtimeSessionId") or session["id"],
        registry_home=receipt / "foundation",
    )
    from .host.bundle_paths import canonical_bundle_reference
    bundle = canonical_bundle_reference(config, session.get("bundle") or config.active_bundle)
    key = configuration_key(config.settings, home, receipt)
    # Explicit local bundles can shadow a named offering. Their content is not
    # represented by the app's Git generation and must be qualified separately.
    from .host.bundle_paths import local_bundle_path

    local = local_bundle_path(config, bundle)
    from .session_files import validate_id

    runtime_id = (
        session.get("runtimeSessionId")
        or session.get("nativeIdentity")
        or session["id"]
    )
    validate_id(runtime_id)
    edited_path = Path(home) / "sessions" / runtime_id / "configuration.json"
    edited = json.loads(edited_path.read_text()) if edited_path.exists() else None
    # Local overrides are mutable user inputs. Content, not merely the path,
    # participates in qualification so edits cannot reuse an obsolete wheel.
    local_inputs = _local_build_inputs(config, edited, local, bundle)
    if local_inputs:
        from amplifier_foundation.modules.preparation import source_signature

        contents = []
        for path in sorted(local_inputs):
            content = await asyncio.to_thread(
                source_signature, path if path.is_dir() else path.parent
            )
            contents.append([str(path), content])
        key = hashlib.sha256((key + json.dumps(contents)).encode()).hexdigest()
    if edited is not None:
        key = hashlib.sha256(
            (key + json.dumps(edited, sort_keys=True)).encode()
        ).hexdigest()
    offered = json.loads(descriptor.read_text())
    if (
        not local_inputs
        and edited is None
        and bundle in offered["profiles"]
        and key == offered["configuration"]
    ):
        return generation
    # The profile key contains no prompts, histories or credentials in plaintext.
    identity = hashlib.sha256(
        json.dumps([generation, bundle, key]).encode()
    ).hexdigest()
    index = Path(home) / "updates/profiles" / (identity + ".json")
    from filelock import AsyncFileLock

    index.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncFileLock(str(index) + ".lock"), AsyncExitStack() as attempts:
        if index.exists():
            selected = json.loads(index.read_text())["generation"]
            target = receipt_directory(home, selected)
            status_file = target / "profile-attempt.json"
            completed = (
                not status_file.exists()  # Previously qualified profiles have no stamp.
                or json.loads(status_file.read_text()).get("status") == "succeeded"
            )
            if completed and (target / "validated.json").exists() and project_path(
                home, selected
            ).exists():
                return selected
        selected = uuid.uuid4().hex
        stage = receipt_directory(home, selected)
        stage.mkdir(parents=True, mode=0o700)
        attempt = await attempts.enter_async_context(
            _profile_attempt(home, generation, selected, stage, index)
        )
        from .update_storage import copy_snapshot

        await asyncio.to_thread(
            copy_snapshot, receipt / "foundation", stage / "foundation"
        )
        registry = stage / "foundation/registry.json"
        if registry.exists():
            write_private(
                registry,
                registry.read_text().replace(
                    str(receipt / "foundation"), str(stage / "foundation")
                ),
            )
        shared = stage / "shared-config"
        shared.mkdir(mode=0o700)
        import yaml

        def relocate(value):
            if isinstance(value, dict):
                return {k: relocate(v) for k, v in value.items()}
            if isinstance(value, list):
                return [relocate(v) for v in value]
            if isinstance(value, str):
                return value.replace(
                    str(receipt / "foundation"), str(stage / "foundation")
                )
            return value

        write_private(
            shared / "settings.yaml", yaml.safe_dump(relocate(config.settings))
        )
        # Only setup uses this private snapshot. Real worker credentials and
        # conversations continue through their ordinary shared authorities.
        keys = config.config_home / "keys.env"
        if keys.exists():
            shutil.copy2(keys, shared / "keys.env")
        for routing in ("routing",):
            source = config.config_home / routing
            if source.is_dir():
                shutil.copytree(source, shared / routing)
        # Preserve relative source semantics while all probe history/output and
        # settings remain in the candidate's private home and shared root.
        workspace = config.workspace
        profile = str(local) if local else bundle
        profiles = stage / "profiles.json"
        write_private(profiles, json.dumps([profile]))
        plan_flags = []
        if edited is not None:
            plan_file = stage / "runtime-plan.json"
            write_private(plan_file, json.dumps(edited))
            plan_flags = ["--runtime-plan", str(plan_file)]
        project = Path(home) / "runtime" / ("prepare-" + selected)
        project.mkdir(parents=True)
        current = project_path(home, generation)
        shutil.copy2(current / "pyproject.toml", project / "pyproject.toml")
        shutil.copy2(current / "uv.lock", project / "uv.lock")
        from .runtime_qualification import freeze, prepare_overrides, verify_recorded
        from .update_sources import store_environment

        uv = shutil.which("uv")
        await process(
            uv,
            "sync",
            "--locked",
            "--project",
            str(project),
            "--python",
            "3.13",
            timeout=900,
        )
        overrides = await prepare_overrides(
            project, stage / "runtime-install-overrides.txt"
        )
        env = {
            **os.environ,
            **store_environment(home),
            "AMPLIFIER_WEB_HOME": str(stage),
            "AMPLIFIER_HOME": str(shared),
            "AMPLIFIER_UNIFIED_RELEASE": "",
            "AMPLIFIER_INSTALL_PREPARATION": uuid.uuid4().hex,
        }
        command = [
            uv,
            "run",
            "--locked",
            "--no-sync",
            "--project",
            str(project),
            "--python",
            "3.13",
            "python",
            str(Path(__file__).with_name("update_probe.py")),
        ]
        await process(
            *command,
            str(workspace),
            profile,
            "--profiles",
            str(profiles),
            "--install-overrides",
            str(overrides),
            *plan_flags,
            env=env,
            timeout=900,
        )

        class Diagnostics:
            async def run(self, phase, function, *args, **kwargs):
                attempt["phase"] = phase
                if "--project" in args:
                    attempt["paths"]["qualifiedProject"] = str(
                        args[args.index("--project") + 1]
                    )
                _write_attempt(stage, attempt)
                return await function(*args, **kwargs)

            def record(self, *args, **kwargs):
                pass

        manager = SimpleNamespace(home=home, diagnostics=Diagnostics())
        final = await freeze(manager, selected, project)
        attempt["paths"]["qualifiedProject"] = str(final)
        _write_attempt(stage, attempt)
        overrides = stage / "runtime-install-overrides.txt"
        command[command.index("--project") + 1] = str(final)
        await process(
            *command,
            str(workspace),
            profile,
            "--global-only",
            "--read-only",
            "--install-overrides",
            str(overrides),
            *plan_flags,
            env=env,
            timeout=900,
        )
        await asyncio.to_thread(verify_recorded, final, stage)
        write_private(
            stage / "validated.json",
            json.dumps(
                {
                    "generationSchema": 1,
                    "workerProtocol": 1,
                    "parentGeneration": generation,
                    "hostVersion": __import__("amplifier_web").__version__,
                    "sources": 1,
                    "createdAt": time.time(),
                }
            ),
        )
        write_private(
            stage / "profiles-qualified.json",
            json.dumps({"profiles": [bundle], "configuration": key}),
        )
        write_private(
            index, json.dumps({"generation": selected, "parentGeneration": generation})
        )
        return selected
