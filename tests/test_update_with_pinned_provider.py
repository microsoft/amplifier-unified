"""Opt-in: a component update on an installation with an "OpenAI API" connection.

The app composes a provider that has no source of its own from
`amplifier_web/provider_sources.json`. The worker environment gets the same
module from `amplifier_web/runtime_deps/pyproject.toml`. Releases 0.108.0 and
0.109.0 named a fixed commit in the first file and the branch `main` in the
second. On an installation whose profile composes the module from that list,
the update guard (`runtime_source_alignment.verify_checked_selection`) then
refused every component update that prepares a worker environment, with the
reason `protected-runtime-source`, also when the branch still was at that
commit. With those two files four of the five cases fail at that guard. The
fifth passes with them: there the person's settings name the module's branch,
so the profile does not use the list. With the same commit in both files an
installation that an earlier app version made is still refused: it keeps its
earlier reference (`runtime_environment.augmented_manifest`).

Each case runs the product's `UpdateManager.check` and `install`: the real uv
lock and sync, the real collection child process (`update_probe.py`), the real
guard, installation, freeze and profile mounts. The provider list is the
product's file. The worker manifest is a fixture that holds the product's row
for this module. Four more things are fixture seams, as in
`test_partial_update_native.py`: no support pack is installed, the app release
check answers "current", and the shared source store is off (two seams), so
the installation's own Git caches are used.

Run (Python 3.13, after `uv sync --locked --group dev`; about one minute a case):

    git clone https://github.com/microsoft/amplifier-module-provider-openai /tmp/provider-openai
    AMPLIFIER_PINNED_PROVIDER_UPDATE_NATIVE=1 \
    AMPLIFIER_PINNED_PROVIDER_MODULE_REPOSITORY=/tmp/provider-openai \
        uv run --no-sync python -m pytest -q -s tests/test_update_with_pinned_provider.py

The module's address is a real GitHub address. uv asks GitHub itself for the
head of a branch of such an address and does not use the local mirror. The
cases set `UV_NO_GITHUB_FAST_PATH=1`, so that uv reads the mirror as Git does.
Without it a case can run with another commit than the one that it made.

Limits. The module is the real public module at the reference that the product
names; Git reads it from a local mirror. The commit after it is made here (one
added text file), so it is not a commit of the public module. Core, Foundation
and the registry wheels are the repacked payloads of the test environment, as
in `test_partial_update_native.py`. No model call, no tool call, no chat. Linux
only: this says nothing about Windows.
"""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import sys
import tomllib
from types import SimpleNamespace

import pytest
import yaml

from amplifier_web import app_updates, builtin_behaviors, installation_runtime, runtime_environment, update_sources, updates
from amplifier_web.host.config import PRECONFIGURED_BUNDLES
from amplifier_web.runtime import RuntimeManager
from amplifier_web.selected_runtime_dependencies import git_source
from amplifier_web.service import AppService
from amplifier_web.update_command_details import retain
from amplifier_web.update_diagnostics import CommandFailure

from test_effective_behavior_update_native import (
    fixture_core,
    fixture_module,
    fixture_provider,
    git,
    repack_registry_wheels,
)
from test_partial_update_native import (
    CORE_URL,
    FOUNDATION_URL,
    PROVIDER_URL,
    _commit,
    _foundation,
    _init_repo,
    _json,
    _transport,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.environ.get("AMPLIFIER_PINNED_PROVIDER_UPDATE_NATIVE") != "1",
        reason="Opt-in native update run; needs uv, Python 3.13 and a local clone of the provider module",
    ),
]

MODULE = "provider-openai"
PACKAGE = "amplifier-module-provider-openai"
PRODUCT = Path(runtime_environment.__file__).parent
# What every release up to 0.108.0 declared for the worker environment.
BRANCH_ROW = {"git": "https://github.com/microsoft/amplifier-module-provider-openai", "rev": "main"}

CASES = [
    # A new installation: setup prepared the environment from this app version.
    # Another component has an update. The module's branch has not moved.
    "new-installation",
    # An installation that an earlier app version qualified with the module on
    # its branch (the PC Arcade). It takes this app version. Then the branch moves.
    "existing-installation-branch-moved",
    # The same installation; the branch does not move and another component has an update.
    "existing-installation-other-update",
    # Setup of an earlier app version ran after the branch moved, so the
    # environment holds a newer commit than the one that this app version names.
    "newer-environment",
    # The person's settings name the module's branch themselves.
    "settings-name-the-branch",
]


def product_sources():
    composed = json.loads((PRODUCT / "provider_sources.json").read_text())[MODULE]
    manifest = tomllib.loads((PRODUCT / "runtime_deps/pyproject.toml").read_text())
    return git_source(composed), manifest["tool"]["uv"]["sources"][PACKAGE]


def write_manifest(path, requirements, sources):
    path.write_text(
        '[project]\nname="pinned-provider-update-worker"\nversion="1"\nrequires-python="==3.13.*"'
        + "\ndependencies=" + json.dumps(requirements)
        + "\n[tool.uv]\nenvironments="
        + json.dumps([f"sys_platform == 'linux' and platform_machine == '{platform.machine()}'"])
        + '\noverride-dependencies=["amplifier-module-loop-streaming==1.0.0",'
        + '"amplifier-module-context-simple==1.0.0","amplifier-bundle-context-intelligence==0.2.0"]'
        + "\n[tool.uv.sources]\n"
        + "\n".join(json.dumps(name) + "=" + runtime_environment.toml_value(source)
                    for name, source in sources.items()) + "\n"
    )


@pytest.mark.parametrize("case", CASES)
async def test_a_component_update_completes_with_an_openai_api_connection(tmp_path, monkeypatch, case):
    assert sys.platform.startswith("linux") and sys.version_info[:2] == (3, 13)
    real_uv = os.environ.get("AMPLIFIER_PINNED_PROVIDER_UPDATE_UV") or shutil.which("uv")
    public = Path(os.environ["AMPLIFIER_PINNED_PROVIDER_MODULE_REPOSITORY"])
    assert real_uv and Path(real_uv).is_file() and Path("/usr/bin/git").is_file() and public.exists()
    root = Path(os.environ.get("AMPLIFIER_PINNED_PROVIDER_UPDATE_ROOT") or tmp_path)
    work = root / case
    work.mkdir(parents=True)
    home, shared, envhome = work / "app", work / "shared", work / "process-home"
    for path in (home, shared, envhome):
        path.mkdir()
    monkeypatch.setenv("HOME", str(envhome))
    monkeypatch.setenv("AMPLIFIER_HOME", str(shared))
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(home))
    for key in ("AMPLIFIER_UNIFIED_RELEASE", "AMPLIFIER_SOURCE_STORE",
                "UV_PROJECT_ENVIRONMENT", "UV_OFFLINE", "UV_NO_CONFIG"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(envhome / ".gitconfig"))
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    # Git reads only the local mirrors: an address without a mirror fails here.
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    # The module's address is a real GitHub address. Keep uv's branch lookup on
    # the same local transport as Git.
    monkeypatch.setenv("UV_NO_GITHUB_FAST_PATH", "1")
    monkeypatch.setenv("UV_NO_INDEX", "1")
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    monkeypatch.setenv("UV_CACHE_DIR", str(work / "uv-cache"))
    wheels = work / "wheels"
    repack_registry_wheels(wheels)
    monkeypatch.setenv("UV_FIND_LINKS", str(wheels))
    config = work / "uv.toml"
    config.write_text("no-index=true\nfind-links=[" + json.dumps(str(wheels)) + "]\n")
    binpath = work / "bin"
    binpath.mkdir()
    uv = binpath / "uv"
    uv.write_text("#!/bin/sh\nexec " + shlex.quote(real_uv) + " --config-file "
                  + shlex.quote(str(config)) + ' "$@"\n')
    uv.chmod(0o755)
    # This process has another HOME now. uv still finds Python 3.13 on the path.
    monkeypatch.setenv("PATH", os.pathsep.join(
        [str(binpath), str(Path(real_uv).parent), str(Path(sys.base_prefix) / "bin"), os.environ["PATH"]]))

    composed, declared = product_sources()
    url = composed["url"]
    pinned = updates.pinned(composed["ref"])
    mirror = work / "provider-module.git"
    git(work, "clone", "--bare", public, mirror)
    start = git(mirror, "rev-parse", composed["ref"] if pinned else "refs/heads/main")
    # A later commit of the branch, made here: the product's rule must not
    # depend on which commit the public branch has today.
    clone = work / "provider-module-work"
    git(work, "clone", mirror, clone)
    git(clone, "checkout", "--detach", start)
    git(clone, "config", "user.name", "Pinned provider fixture")
    git(clone, "config", "user.email", "pinned-provider@example.invalid")
    (clone / "fixture-later-commit.txt").write_text("A later commit of the module's branch\n")
    moved = _commit(clone, "A later commit of the module's branch")
    git(clone, "push", mirror, moved + ":refs/heads/fixture-later")

    def branch(revision):
        git(mirror, "update-ref", "refs/heads/main", revision)

    branch(moved if case == "newer-environment" else start)

    core = fixture_core(work / "core", wheels)
    foundation = work / "foundation"
    _foundation(foundation)
    provider = fixture_provider(work / "provider", ["amplifier-core @ git+" + CORE_URL + "@main"])
    _init_repo(provider)
    _commit(provider, "No-model qualification provider")
    transport_file = work / "transport.json"
    _json(transport_file, {CORE_URL: core.as_uri(), FOUNDATION_URL: foundation.as_uri(),
                           PROVIDER_URL: provider.as_uri(), url: mirror.as_uri()})
    _transport(binpath, transport_file, work / "no-fetch-failure.json", home)

    skills = fixture_module(work / "skills", wheels, "amplifier-module-tool-skills", "amplifier_module_tool_skills")
    context = fixture_module(work / "context-hook", wheels, "amplifier-module-hook-context-intelligence",
                             "amplifier_module_hook_context_intelligence")
    requirements = [
        "packaging", "amplifier-core", "amplifier-foundation", "amplifier-module-loop-live",
        "amplifier-module-context-managed", "amplifier-module-hooks-session-naming",
        "amplifier-module-tool-skills @ " + skills.as_uri(),
        "amplifier-module-hook-context-intelligence @ " + context.as_uri(),
        PACKAGE,
    ]
    manifest = work / "worker.toml"

    def declare(row):
        write_manifest(manifest, requirements, {
            "amplifier-core": {"git": CORE_URL, "rev": "main"},
            "amplifier-foundation": {"git": FOUNDATION_URL, "branch": "main"},
            PACKAGE: row,
        })

    earlier_app = case.startswith("existing-installation") or case == "newer-environment"
    declare(BRANCH_ROW if earlier_app else declared)
    monkeypatch.setattr(runtime_environment, "manifest_path", lambda: manifest)
    monkeypatch.setattr(app_updates, "installed_extras", lambda: [])
    # The feature-detection seam of test_partial_update_native: owned Git caches, no shared store.
    monkeypatch.setattr(update_sources, "store_environment", lambda home: {})
    monkeypatch.setattr(update_sources, "worker_supports_shared", lambda project: False)

    # Setup's own step for a new installation: it prepares the environment and
    # writes its record (updates/baseline-runtime) before any AI connection.
    baseline_inputs = {
        "case": case, "providerStart": start, "providerLater": moved,
        "manifestSha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
    }

    def save_baseline_inputs(**facts):
        try:
            _json(work / "baseline-inputs.json", {**baseline_inputs, **facts})
        except (OSError, ValueError, TypeError):
            pass  # Optional evidence must not change the preparation outcome.

    save_baseline_inputs()
    try:
        prepared = await installation_runtime.prepare(home)
    except CommandFailure as error:
        # Use the product's bounded, twice-redacted LOCAL ONLY detail writer.
        # Never put installer text in stdout, JUnit or the shared test report.
        # The original exception and install-failure phase remain authoritative.
        diagnostics = SimpleNamespace(
            manager=SimpleNamespace(home=home, directory=home / "updates"),
            state={"attemptId": "baseline-runtime"})
        retained = retain(diagnostics, error, "pinned-provider-baseline", "initial-runtime-preparation")
        status = retained.get("localDetailStatus", "absent")
        safe = {"localDetailStatus": status if status in {"saved", "unavailable", "absent"} else "unavailable"}
        slot = retained.get("localDetailSlot")
        if status == "saved" and type(slot) is int and 0 <= slot < 16:
            safe["localDetailSlot"] = slot
        save_baseline_inputs(**safe)
        raise
    assert prepared.get("ok") and prepared.get("prepared") and prepared.get("generation") is None, prepared
    seed = runtime_environment.project_path(home)
    site = next((seed / ".venv/lib").glob("python*/site-packages"))
    # A local module source of the settings must not be below the app's folder:
    # a stage reads the settings with its own folder as the app's folder.
    managed = work / "context-managed" / "amplifier_module_context_managed"
    shutil.copytree(site / "amplifier_module_context_managed", managed)
    profile = work / "profile.yaml"
    profile.write_text(yaml.safe_dump({
        "bundle": {"name": "fixture"},
        "session": {"orchestrator": {"module": "loop-streaming"}, "context": {"module": "context-simple"}},
        "providers": [{"module": "provider-update-fixture", "source": "git+" + PROVIDER_URL + "@main",
                       "config": {"default_model": "fixture"}}],
    }))
    settings = shared / "settings.yaml"

    def save_settings(*, names_branch):
        modules = {"context-simple": str(managed),
                   "tool-skills": str(skills), "hook-context-intelligence": str(context)}
        if names_branch:
            modules[MODULE] = "git+" + url + "@main"
        settings.write_text(yaml.safe_dump({
            "bundle": {"active": "fixture", "added": {"fixture": str(profile)}, "app": []},
            "web_bundles": {
                "excluded": list(builtin_behaviors.DEFAULT_BEHAVIORS),
                "entries": [{"name": name, "uri": uri, "role": "standalone", "enabled": False}
                            for name, uri in PRECONFIGURED_BUNDLES.items()],
            },
            # An "OpenAI API" connection as Settings saves it: no source of its own.
            "config": {"providers": [{"id": "openai", "module": MODULE,
                                      "config": {"default_model": "fixture-model", "api_key": "not-a-key"}}]},
            "sources": {"modules": modules},
        }))

    # An earlier app version composed the module from its branch. A settings
    # line that names the branch composes the same reference.
    save_settings(names_branch=case.startswith("existing-installation") or case == "settings-name-the-branch")

    def other_component_update(text):
        (core / "fixture-revision.txt").write_text(text + "\n")
        _commit(core, text)

    other_component_update("Another component has an update")

    async def application_current():
        return {"id": "application", "label": "Synthetic current host", "status": "current"}

    monkeypatch.setattr(app_updates, "check", application_current)
    runtime = RuntimeManager()
    app = AppService(home, runtime, workspace=work)
    runtime.home = home
    manager = updates.UpdateManager(app)
    app.update_manager = manager
    releases = manager.directory / "releases"
    report = {"case": case, "productComposes": composed, "productDeclares": declared, "start": start, "later": moved}

    def outcome():
        state = app.state["updates"]
        failure = manager.diagnostics.state.get("lastFailure") or {}
        return {"phase": state.get("phase"), "error": state.get("error"),
                "failure": {key: failure.get(key) for key in ("phase", "reason", "package", "failureLocation")}}

    def module_rows():
        return [{key: row.get(key) for key in ("kind", "status", "ref", "current", "latest", "eligible")}
                for row in manager.inventory if row.get("package") == PACKAGE]

    try:
        if case.startswith("existing-installation"):
            await manager.check(fresh=True)
            await manager.install()
            report["earlierAppVersion"] = outcome()
            assert app.state["updates"]["phase"] == "installed", (
                "Fixture: the earlier app version did not qualify its generation", report)
            if manager.cleanup_task:
                await manager.cleanup_task
            report["earlierGeneration"] = updates.active_release(home)["current"]
            save_settings(names_branch=False)
        if earlier_app:
            # The installation takes this app version.
            declare(declared)
        if case == "existing-installation-branch-moved":
            branch(moved)
        elif case == "existing-installation-other-update":
            other_component_update("Another component has a second update")
        active_before = updates.active_release(home).get("current")
        before = set(path.name for path in releases.iterdir()) if releases.exists() else set()
        await manager.check(fresh=True)
        report["checked"] = {"module": module_rows(), "phase": app.state["updates"].get("phase"), "updates": sorted(
            {str(row.get("package") or row.get("label") or row.get("id")) for row in manager.inventory
             if row.get("status") == "update"})}
        await manager.install()
        report["update"] = outcome()
        report["newStages"] = {path.name[:12]: sorted(entry.name for entry in path.iterdir())
                               for path in releases.iterdir() if path.name not in before}
        active = updates.active_release(home).get("current")
        report["activeChanged"] = active != active_before
        if active and active != active_before:
            stage = releases / active
            project = runtime_environment.project_path(home, active)
            selection = json.loads((stage / "selected-source-declarations.json").read_text())["packages"][PACKAGE]
            report["generation"] = {
                "composed": {key: selection.get(key) for key in ("status", "ref", "revision")},
                "checked": {key: json.loads((stage / "runtime-sources.json").read_text())[PACKAGE].get(key)
                            for key in ("ref", "revision")},
                "installed": runtime_environment.installed_sources(project)[PACKAGE].get("current"),
            }
    finally:
        try:
            _json(work / "report.json", report)
            print("\nPINNED-PROVIDER " + json.dumps(report))
        finally:
            await app.close()

    assert report["update"]["phase"] == "installed", (
        "The component update did not complete: " + json.dumps(report["update"]))
    generation = report["generation"]
    # One module, one source: a profile composes the reference that the update
    # checked, and the new environment holds the commit that the update checked.
    # (The saved collection is from before a moved branch was fetched into the
    # stage, so its commit is not compared here.)
    assert generation["composed"]["ref"] == generation["checked"]["ref"], generation
    assert generation["installed"] == generation["checked"]["revision"], generation
