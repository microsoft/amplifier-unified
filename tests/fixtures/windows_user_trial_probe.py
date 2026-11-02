"""Bounded installed Windows candidate check, NOT public activation acceptance.

Uses the maintained worker_startup_probe's local-module/settings pattern. Only
the provider is scripted; Core, Foundation, loop, context, capture, filesystem,
Worker, RuntimeManager, HTTPS and auth handlers are real. No rendered browser,
Scheduled Task, installer, default bundle or real provider is qualified here.
Run only in the dedicated GitHub Windows job. Never upload its private scratch.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import base64
import configparser
import csv
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from contextvars import ContextVar
import ctypes
import hashlib
import importlib
import importlib.metadata as metadata
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import stat
import struct
import subprocess
import sys
import sysconfig
import tomllib
import types
import uuid
import zipfile


MODULES = {
    "loop-live": "amplifier_module_loop_live",
    "context-simple": "amplifier_module_context_simple",
    "hook-context-intelligence": "amplifier_module_hook_context_intelligence",
    "tool-filesystem": "amplifier_module_tool_filesystem",
}
IMPORTS = {
    "amplifier_web": "amplifier-unified",
    "amplifier_web.runtime": "amplifier-unified",
    "amplifier_web.runtime_worker": "amplifier-unified",
    "amplifier_web.host.session": "amplifier-unified",
    "amplifier_core": "amplifier-core",
    "amplifier_foundation": "amplifier-foundation",
    "amplifier_module_loop_streaming": "amplifier-module-loop-streaming",
    **{value: value.replace("_", "-") for value in MODULES.values()},
}
STAGES = {"private_fixture_creation", "installed_host", "fixture", "credentials_tls",
          "grant_cookie_csrf", "first_worker_turn", "output_snapshot", "stop_tree",
          "host_restart_resume", "private_runtime_project_api", "native_prerequisites",
          "controls", "first", "resume", "complete"}
CHECKS = STAGES | {"privateTlsGrantCookieCsrfReplay", "inheritedAccessLookup",
          "realWorkerToolUtf8History", "hostRestartResumeWithoutReplay",
          "outputSnapshotReadExactBytes", "stopDescendantTreeAndIo",
          "privateRuntimeProjectApi", "maintainedNativePrerequisites",
          "twoFreshHostsAndInstalledWorkerGraphs", "unexpectedPythonNetworkAttempts"}
# Exact maintained native cells, not a file-wide/full-suite gate. Keep explicit
# parameter IDs so a missing forced-cleanup branch cannot hide behind one pass.
NATIVE_CONTROLS = {
    "prepare_private_refuse_unsafe": "test_runtime_updates.py::test_native_windows_prepare_project_private_before_payload_and_refuse_unsafe",
    "auth_converge": "test_windows_auth.py::test_native_credentials_converge_and_existing_read",
    "auth_empty": "test_windows_auth.py::test_native_empty_existing_refused_without_replacement",
    "auth_foreign_acl": "test_windows_auth.py::test_native_foreign_read_acl_never_repaired",
    "auth_alias": "test_windows_auth.py::test_native_case_alias_refused",
    "auth_junction": "test_windows_auth.py::test_native_junction_not_resolved_before_refusal",
    "auth_wrong_leaf_no_disclosure": "test_browser_entry.py::test_native_unrelated_listener_and_proxy_never_receive_bearer[pyloop-wrong-leaf]",
    "auth_redirect_proxy_no_disclosure": "test_browser_entry.py::test_native_unrelated_listener_and_proxy_never_receive_bearer[pyloop-redirect]",
    "runtime_identity_pipes": "test_runtime.py::test_native_windows_runtime_worker_identity_pipes_and_job_cleanup[False]",
    "runtime_forced_cleanup": "test_runtime.py::test_native_windows_runtime_worker_identity_pipes_and_job_cleanup[True]",
    "runtime_cancel_identity": "test_runtime.py::test_native_windows_runtime_cancel_before_identity_reaps_job",
    "optional_full_app_reload": "test_optional_controller_availability.py::test_native_default_selection_full_app_chat_save_reload",
    "read_workspace_utf8": "test_windows_read_files.py::test_native_inherited_workspace_utf8_spaces_and_no_private_acl_requirement",
    "read_sharing_release": "test_windows_read_files.py::test_native_held_handles_block_data_write_leaf_and_parent_rename_then_release",
    "read_hardlink_limit": "test_windows_read_files.py::test_native_missing_directory_hardlink_and_limit_refuse_without_mutation",
    "read_junction": "test_windows_read_files.py::test_native_junctions_refuse_before_resolve_even_when_target_is_inside",
    "resource_hash_corruption": "test_windows_read_files.py::test_native_resource_hash_projection_sync_resolution_and_corruption",
    "resource_publish_overlap": "test_windows_read_files.py::test_resource_publication_of_distinct_blob_during_held_projection",
}
NATIVE_FILES = tuple(sorted({node.split("::")[0] for node in NATIVE_CONTROLS.values()}
                            | {"conftest.py", "test_service.py"}))
TLS_NEGATIVE_MODES = {
    "tests/" + NATIVE_CONTROLS["auth_wrong_leaf_no_disclosure"]: "wrong-leaf",
    "tests/" + NATIVE_CONTROLS["auth_redirect_proxy_no_disclosure"]: "redirect",
}
TLS_NEGATIVE_NODES = frozenset(TLS_NEGATIVE_MODES)
PHASES = frozenset({"setup", "call", "teardown", "outside"})
NETWORK_EVENTS = frozenset({"socket.bind", "socket.connect", "socket.getaddrinfo", "socket.sendto",
                            "socket.gethostbyname", "socket.gethostbyaddr", "socket.getnameinfo"})
# Public producer labels resolved to the *loaded function's exact code object*.
# Unknown/private frames never contribute a name, path, line or fingerprint.
DIAGNOSTIC_FUNCTIONS = {
    "native_read_open": ("amplifier_web.windows_read_files", "_ReadAPI.open_read"),
    "native_read_signature": ("amplifier_web.windows_read_files", "_ReadAPI.signature"),
    "native_read_context": ("amplifier_web.windows_read_files", "open_file"),
    "native_file_info": ("amplifier_web.windows_files", "_API.info"),
    "native_file_check": ("amplifier_web.windows_files", "_API.ok"),
    "output_workspace_file": ("amplifier_web.outputs", "workspace_file"),
    "output_file_bytes": ("amplifier_web.outputs", "file_bytes"),
    "resource_read": ("amplifier_web.resource_files", "read_detached"),
    "resource_project": ("amplifier_web.resource_files", "project_detached"),
    "resource_publish": ("amplifier_web.resource_files", "write_prepared"),
    "resource_atomic": ("amplifier_web.host.storage", "SessionStore._atomic"),
    "optional_app_init": ("amplifier_web.service", "AppService.__init__"),
    "pytest_unused_port": ("pytest_asyncio.plugin", "_unused_port"),
    "socketpair": ("socket", "_fallback_socketpair"),
    "aiohttp_server_close": ("aiohttp.test_utils", "BaseTestServer.close"),
    "aiohttp_client_close": ("aiohttp.test_utils", "TestClient.close"),
    "asyncio_loop_check_closed": ("asyncio.base_events", "BaseEventLoop._check_closed"),
    "asyncio_loop_run_until_complete": ("asyncio.base_events", "BaseEventLoop.run_until_complete"),
    "worker_start": ("amplifier_web.runtime_worker", "Worker.start"),
    "manager_prepare": ("amplifier_web.host.session", "prepare_manager"),
    "manager_dependencies": ("amplifier_web.host.session", "prepare_dependencies"),
    "runtime_start_locked": ("amplifier_web.runtime", "RuntimeManager._start_locked"),
    "runtime_identify": ("amplifier_web.runtime", "_WindowsWorkerOwner.identify"),
    "windows_owned_start": ("amplifier_web.windows_jobs", "WindowsOwnedProcess.start"),
    "windows_owned_spawn": ("amplifier_web.windows_jobs", "WindowsOwnedProcess._start"),
    "generation_acquire": ("amplifier_web.generation_leases", "acquire"),
    "runtime_active_release": ("amplifier_web.updates", "active_release"),
    "runtime_worker_environment": ("amplifier_web.host.config", "worker_environment"),
    "runtime_private_write": ("amplifier_web.host.config", "write_private"),
    "private_file_write": ("amplifier_web.deployment", "write_private"),
    "private_file_read": ("amplifier_web.deployment", "read_private"),
    "private_directory": ("amplifier_web.windows_files", "directory"),
    "private_directory_init": ("amplifier_web.windows_files", "PinnedDirectory.__init__"),
    "private_directory_check": ("amplifier_web.windows_files", "PinnedDirectory._check"),
    "private_api_init": ("amplifier_web.windows_files", "_API.__init__"),
    "private_api_open": ("amplifier_web.windows_files", "_API.open"),
    "private_acl_validate": ("amplifier_web.windows_files", "_API.validate_acl"),
    "private_ace_check": ("amplifier_web.windows_files", "_check_ace"),
    "private_path_local": ("amplifier_web.windows_files", "_local"),
    "private_path_address": ("amplifier_web.windows_files", "address"),
}
DIAGNOSTIC_SOURCES = frozenset(DIAGNOSTIC_FUNCTIONS) | {
    "selected_test", "tls_fixture_scope", "tls_fixture_reserve", "tls_fixture_call",
    "tls_fixture_start", "tls_fixture_bound", "trial_journey", "trial_action",
    "trial_worker", "trial_require_ci", "trial_network_guard", "other"}
ERROR_CLASSES = {kind: kind.__name__ for kind in (
    AssertionError, OSError, PermissionError, FileNotFoundError, NotADirectoryError,
    IsADirectoryError, TimeoutError, ImportError, ModuleNotFoundError, ValueError,
    TypeError, AttributeError, KeyError, RuntimeError, UnicodeError,
    UnicodeEncodeError, UnicodeDecodeError, ExceptionGroup, BaseExceptionGroup, json.JSONDecodeError)}
ERROR_LABELS = frozenset(ERROR_CLASSES.values()) | {
    "AppError", "RuntimeStartupError", "CleanupError", "Failed", "Skipped", "other"}
EXCEPTION_RECORD_LIMIT = 6
TURN_STEPS = frozenset({
    "session_create", "access_read", "access_save", "inherited_access", "conversation_send",
    "wait_parked_history", "provider_identity", "guard_identity", "output_utf8",
    "history_assistant", "history_tool"})
TRIAL_ACTIONS = frozenset({"session.create", "access.get", "access.set", "conversation.send",
                          "outputs.attach", "outputs.read"})
TRIAL_ACTION_CODES = frozenset({"worker_startup_failed", "runtime_pending", "session_busy",
                               "host_shutdown", "other", "none"})
WORKER_EVENTS = frozenset({"runtime.identity", "runtime.ready", "runtime.error", "runtime.parked",
                          "input.delivered", "generation.started", "generation.finished"})


def action_fact(name, status, result):
    """Only fixed response facts, never error prose, IDs or response state."""
    assert name in TRIAL_ACTIONS and type(status) is int and 100 <= status <= 599
    code = result.get("code")
    return {"action": name, "httpStatus": status, "hasError": bool(result.get("error")),
            "code": code if type(code) is str and code in TRIAL_ACTION_CODES else
                    "none" if code is None else "other"}


def worker_event(facts, event):
    """Observe the real publisher, never replace or filter its protocol event."""
    kind = event.get("type")
    if type(kind) is str and kind in WORKER_EVENTS:
        facts["events"][kind] = min(facts["events"].get(kind, 0) + 1, 10**9)
        if kind == "runtime.error" and isinstance(sys.exception(), BaseException):
            facts["errors"] = safe_exception(sys.exception(), diagnostic_codes())
        return True
    return False


def completed_worker_facts(path):
    """Read ONLY after retained job cleanup; absence/incomplete I/O is failure."""
    try:
        facts = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        facts = None
    if (type(facts) is not dict or type(facts.get("diagnosticStatus")) is not str
            or facts["diagnosticStatus"] not in {"PENDING", "PASS", "FAIL"}):
        return {"events": {}, "errors": [], "diagnosticStatus": "FAIL"}
    if facts["diagnosticStatus"] == "PENDING":
        facts["diagnosticStatus"] = "FAIL"
    return facts  # The existing public validator still rejects any extra fields.


def observed_runtime_type(base):
    """Observe the startup boundary after its real cleanup, only in memory.

    No replacement start/identify/spawn path, exception rewriting, I/O or await
    is added to the exception handler. An observer fault cannot replace the
    original startup exception or cancellation.
    """
    class ObservedRuntime(base):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.trial_startup = {"status": "NOT_OBSERVED", "errors": []}

        async def _start_locked(self, *args, **kwargs):
            try:
                result = await super()._start_locked(*args, **kwargs)
            except BaseException as exc:
                try:
                    self.trial_startup = {"status": "RECORDED",
                                          "errors": safe_exception(exc, diagnostic_codes())}
                except BaseException:
                    self.trial_startup = {"status": "FAIL", "errors": []}
                raise
            self.trial_startup = {"status": "PASS", "errors": []}
            return result
    return ObservedRuntime


def diagnostic_codes():
    result = {}
    for label, (module_name, qualname) in DIAGNOSTIC_FUNCTIONS.items():
        value = sys.modules.get(module_name)
        for part in qualname.split("."):
            value = getattr(value, part, None) if value is not None else None
        # contextmanager's generated wrapper is not the boundary implementation.
        value = getattr(value, "__wrapped__", value)
        code = getattr(value, "__code__", None)
        if isinstance(code, types.CodeType):
            result[code] = label
    return result


def safe_frame(frame, codes, line=None):
    code = frame.f_code
    label = next((label for known, label in codes.items() if known is code), "other")
    if label == "other":
        return {"source": "other"}
    offset = (line if type(line) is int else getattr(frame, "f_lineno", code.co_firstlineno)) - code.co_firstlineno
    return {"source": label, "codeSha256": digest(code.co_code),
            "lineOffset": max(0, min(offset, 10**6))}


def safe_exception(exc, codes):
    """Six flat bounded records: exact built-in groups/causes, no repr/str.

    Expand children ONLY for the two exact built-in group classes. A private
    subclass stays 'other', and its arbitrary attributes are never traversed.
    This diagnostic never changes pytest's original failed teardown outcome.
    """
    records, seen, pending = [], set(), [exc]
    while pending and len(records) < EXCEPTION_RECORD_LIMIT:
        exc = pending.pop(0)
        if not isinstance(exc, BaseException) or id(exc) in seen:
            continue
        seen.add(id(exc))
        kind = type(exc)
        label = ERROR_CLASSES.get(kind, "other")
        for module_name, class_name in (
                ("amplifier_web.service", "AppError"), ("_pytest.outcomes", "Failed"),
                ("_pytest.outcomes", "Skipped"), ("amplifier_web.runtime", "RuntimeStartupError"),
                ("amplifier_web.windows_jobs", "CleanupError")):
            module = sys.modules.get(module_name)
            if module is not None and kind is getattr(module, class_name, None):
                label = class_name
        row = {"class": label, "frames": []}
        if kind in ERROR_CLASSES and isinstance(exc, OSError):
            for key in ("errno", "winerror"):
                number = getattr(exc, key, None)
                if type(number) is int and -(2**31) <= number < 2**32:
                    row[key] = number
        tb = exc.__traceback__
        scanned = 0
        while tb is not None and scanned < 128:
            scanned += 1
            fact = safe_frame(tb.tb_frame, codes, tb.tb_lineno)
            if fact["source"] != "other":
                row["frames"].append(fact)
                row["frames"] = row["frames"][-5:]
            tb = tb.tb_next
        records.append(row)
        remaining = EXCEPTION_RECORD_LIMIT - len(records)
        if kind in (ExceptionGroup, BaseExceptionGroup):
            pending.extend(exc.exceptions[:remaining])
        cause = exc.__cause__ if exc.__cause__ is not None else (
            None if exc.__suppress_context__ else exc.__context__)
        if cause is not None:
            pending.append(cause)
        pending = pending[:remaining]
    return records
MARKER = "Windows trial café 日本語"
RESUMED = "Resumed café 日本語"
# Source-attested urllib3 2.x capability probe. We BLOCK its bind, never allow
# it. Verify the exact compiled caller, closed socket and HAS_IPV6=False.
IPV6_PROBE = '''
def _has_ipv6(host: str) -> bool:
    """Returns True if the system can bind an IPv6 address."""
    sock = None
    has_ipv6 = False
    if socket.has_ipv6:
        try:
            sock = socket.socket(socket.AF_INET6)
            sock.bind((host, 0))
            has_ipv6 = True
        except Exception:
            pass
    if sock:
        sock.close()
    return has_ipv6
'''
PROVIDER = r'''
import asyncio, json, os, subprocess, sys
from pathlib import Path
from amplifier_core import ProviderInfo
from amplifier_core.message_models import ChatResponse, TextBlock, ToolCall
__amplifier_module_type__ = "provider"
class Provider:
    name = "fixture"
    def __init__(self, coordinator, config):
        self.coordinator, self.config = coordinator, config
    def get_info(self):
        return ProviderInfo(id="fixture", display_name="Offline fixture", defaults={"model":"fixture"})
    async def list_models(self): return []
    def parse_tool_calls(self, response): return response.tool_calls or []
    async def complete(self, request, **kwargs):
        # Core's module-validator request is not a conversation or tool effect.
        if self.coordinator.session_id == "test-session" or not request.tools:
            return ChatResponse(content=[TextBlock(text="Offline fixture")])
        from amplifier_web.access_mode import read_mode
        assert read_mode(Path(os.environ["AMPLIFIER_WEB_HOME"]),
            Path(self.config["output"]).parent, self.coordinator.session_id) == "full"
        with Path(self.config["calls"]).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"pid":os.getpid(), "access":"full"}) + "\n")
        text = str(next(m.content for m in reversed(request.messages) if m.role == "user"))
        if "WAIT_FOR_STOP" in text:
            child = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(180)"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            Path(self.config["blocked"]).write_text(json.dumps({"pid":child.pid}), encoding="utf-8")
            await asyncio.Event().wait()
        if "RESUME_TRIAL" in text:
            assert any("Windows trial café 日本語" in str(m.content) for m in request.messages)
            return ChatResponse(content=[TextBlock(text="Resumed café 日本語")])
        if not any(m.role == "tool" and m.tool_call_id == "trial-write" for m in request.messages):
            return ChatResponse(content=[], tool_calls=[ToolCall(id="trial-write", name="write_file",
                arguments={"file_path":self.config["output"], "content":"Windows trial café 日本語\n"})])
        return ChatResponse(content=[TextBlock(text="Windows trial café 日本語")])
async def mount(coordinator, config=None):
    await coordinator.mount("providers", Provider(coordinator, config or {}), name="fixture")
'''


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def candidate(source):
    """Clean CI checkout identity, not the pull request's untested head."""
    def git(*args):
        return subprocess.check_output(["git", "-C", str(source), *args], timeout=15).decode().strip()
    assert not git("status", "--porcelain")
    result = {"head": git("rev-parse", "HEAD"), "tree": git("rev-parse", "HEAD^{tree}")}
    assert all(re.fullmatch(r"[0-9a-f]{40,64}", value) for value in result.values())
    return result


def requirements(path):
    """Use the maintained exact dev module sources, not another pinned graph."""
    dev = tomllib.loads(path.read_text(encoding="utf-8"))["dependency-groups"]["dev"]
    selected = [value for value in dev if any(value.startswith(
        module.replace("_", "-") + " @") for module in MODULES.values())]
    assert len(selected) == len(MODULES)
    return selected


INSTALLATION_STAGES = frozenset({"direct_url", "wheel_members", "imports", "graph", "complete"})
INSTALLATION_CHECKS = frozenset({"module_import", "distribution_metadata", "import_origin",
                                "core_version", "foundation_api"})
# Exact public dependency labels only. Never copy arbitrary exception.name,
# exception.path, messages, missing symbols or traceback frames into receipts.
INSTALLATION_MISSING_MODULES = frozenset(IMPORTS) | frozenset({
    "fcntl", "pwd", "grp", "resource", "termios", "pty", "msvcrt", "winreg",
    "yaml", "filelock", "packaging", "cryptography", "aiohttp", "jsonschema",
    "httpx", "six", "idna", "pathspec", "pydantic", "pydantic_core",
    "amplifier_core._engine", "amplifier_foundation.session",
    "amplifier_module_loop_live.job_store", "other",
})
INSTALLATION_IMPORT_FAILURES = frozenset({"module_not_found", "distribution_not_found",
                                        "symbol_or_loader"})


def installation_import_failure(exc, report):
    if report is None:
        return
    if isinstance(exc, metadata.PackageNotFoundError):
        report["installationImportFailure"] = "distribution_not_found"
    elif isinstance(exc, ModuleNotFoundError):
        report["installationImportFailure"] = "module_not_found"
    else:
        report["installationImportFailure"] = "symbol_or_loader"
    missing = getattr(exc, "name", None)
    report["installationMissingModule"] = (
        missing if type(missing) is str and missing in INSTALLATION_MISSING_MODULES else "other")


def record_rows(raw, record):
    """Require a unique registered name and complete SHA-256/size per file."""
    rows = {}
    for row in csv.reader(io.StringIO(raw.decode("utf-8")), strict=True):
        assert len(row) == 3
        name, fingerprint, size = row
        assert name and "\\" not in name and "\x00" not in name
        assert name not in rows and str(PurePosixPath(name)) == name
        if name == record:
            assert fingerprint == size == ""
        else:
            assert re.fullmatch(r"sha256=[A-Za-z0-9_-]{43}", fingerprint)
            assert re.fullmatch(r"0|[1-9][0-9]*", size)
        rows[name] = (fingerprint, size)
    assert record in rows
    return rows


def record_matches(raw, row):
    assert row == ("sha256=" + base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip("="),
                   str(len(raw)))


def candidate_members(archive, dist):
    """Only candidate RECORD may differ; independently verify both inventories.

    Wheel payload and shipped metadata remain byte-exact. Installed additions
    are limited to uv's explicit metadata and the two generated console scripts.
    They get their own required hash/size verification, never wheel provenance.
    """
    info = f"amplifier_unified-{dist.version}.dist-info"
    record = info + "/RECORD"
    names = [entry.filename for entry in archive.infolist() if not entry.is_dir()]
    assert len(names) == len(set(names))
    assert all(not PurePosixPath(name).is_absolute() and ".." not in PurePosixPath(name).parts
               and "\\" not in name and str(PurePosixPath(name)) == name for name in names)
    assert {name.split("/")[0] for name in names if ".dist-info/" in name} == {info}
    wheel_rows = record_rows(archive.read(record), record)
    assert set(wheel_rows) == set(names)
    installed_rows = record_rows(dist.read_text("RECORD").encode("utf-8"), record)
    assert set(wheel_rows) <= set(installed_rows)
    for name in names:
        if name == record:
            continue  # The ONLY shipped member exempted from byte equality.
        raw = archive.read(name)
        record_matches(raw, wheel_rows[name])
        actual = Path(dist.locate_file(name)).read_bytes()
        assert actual == raw
        record_matches(actual, installed_rows[name])

    # PEP 610 + uv markers are installer-owned, not arbitrary extra metadata.
    generated = {info + "/" + name for name in
                 ("direct_url.json", "INSTALLER", "REQUESTED", "uv_cache.json", "uv_build.json")}
    assert not generated & set(wheel_rows)
    assert {info + "/direct_url.json", info + "/INSTALLER"} <= set(installed_rows)
    scripts = configparser.ConfigParser(interpolation=None)
    scripts.optionxform = str
    scripts.read_string(archive.read(info + "/entry_points.txt").decode("utf-8"))
    assert scripts.sections() == ["console_scripts"]
    assert dict(scripts["console_scripts"]) == {
        "amplifier-unified": "amplifier_web.cli:main",
        "amplifier-unified-status": "amplifier_web.status_protocol.cli:main"}
    script_root = Path(sysconfig.get_path("scripts")).resolve()
    assert script_root.is_relative_to(Path(sys.prefix).resolve())
    suffix = ".exe" if sys.platform == "win32" else ""
    launchers = {os.path.relpath(script_root / (name + suffix), dist.locate_file("")).replace(os.sep, "/")
                 for name in scripts["console_scripts"]}
    assert launchers <= set(installed_rows)
    assert set(installed_rows) - set(wheel_rows) <= generated | launchers
    for name in set(installed_rows) - set(wheel_rows):
        path = Path(dist.locate_file(name)).resolve()
        assert path.is_relative_to(Path(sys.prefix).resolve())
        raw = path.read_bytes()
        record_matches(raw, installed_rows[name])
        if name == info + "/INSTALLER":
            assert raw in {b"uv", b"uv\n"}
        elif name == info + "/REQUESTED":
            assert raw == b""
        elif name in {info + "/uv_cache.json", info + "/uv_build.json"}:
            assert len(raw) <= 65536 and isinstance(json.loads(raw), dict)
        elif name == info + "/direct_url.json":
            assert len(raw) <= 65536 and isinstance(json.loads(raw), dict)
        else:
            assert name in launchers and path.parent == script_root
    # Reject even an UNREGISTERED extra file in candidate metadata. Do not
    # enumerate imported package caches: Python may create those independently.
    metadata_root = Path(dist.locate_file(info))
    actual_metadata = {info + "/" + path.relative_to(metadata_root).as_posix()
                       for path in metadata_root.rglob("*") if path.is_file()}
    assert actual_metadata == {name for name in installed_rows if name.startswith(info + "/")}
    return len(names) - 1


def installed(wheel, report=None):
    """Verify noneditable candidate bytes and import origins, never add checkout."""
    def stage(value):
        assert value in INSTALLATION_STAGES
        if report is not None:
            report["installationStage"] = value
    def check(package, value):
        assert package in IMPORTS and value in INSTALLATION_CHECKS
        if report is not None:
            report["installationImport"] = package
            report["installationCheck"] = value
    if report is not None:
        for field in ("installationImport", "installationCheck",
                      "installationImportFailure", "installationMissingModule"):
            report.pop(field, None)
    stage("direct_url")
    dist = metadata.distribution("amplifier-unified")
    direct = json.loads(dist.read_text("direct_url.json") or "{}")
    assert not direct.get("dir_info", {}).get("editable")
    assert direct.get("url") == wheel.resolve().as_uri()
    stage("wheel_members")
    with zipfile.ZipFile(wheel) as archive:
        count = candidate_members(archive, dist)
    assert count > 100
    stage("imports")
    origins = {}
    for package, distribution in IMPORTS.items():
        check(package, "module_import")
        try:
            module = importlib.import_module(package)
        except ImportError as exc:
            installation_import_failure(exc, report)
            raise
        path = Path(module.__file__).resolve()
        check(package, "distribution_metadata")
        try:
            owner = metadata.distribution(distribution)
        except metadata.PackageNotFoundError as exc:
            installation_import_failure(exc, report)
            raise
        check(package, "import_origin")
        expected = {Path(owner.locate_file(f)).resolve() for f in owner.files or ()}
        assert path in expected and path.is_relative_to(Path(sys.prefix).resolve())
        origins[package] = {"origin": "installed", "sha256": digest(path.read_bytes())}
    check("amplifier_core", "core_version")
    try:
        from packaging.version import Version
        assert Version(metadata.version("amplifier-core")) >= Version("2.0.2")
    except ImportError as exc:
        installation_import_failure(exc, report)
        raise
    check("amplifier_foundation", "foundation_api")
    try:
        from amplifier_foundation.session import prepare_session_directory
    except ImportError as exc:
        installation_import_failure(exc, report)
        raise
    assert callable(prepare_session_directory)
    stage("graph")
    if report is not None:
        report.pop("installationImport", None)
        report.pop("installationCheck", None)
    graph = []
    for item in metadata.distributions():
        name, version = item.metadata["Name"], item.version
        assert re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", name)
        assert re.fullmatch(r"[A-Za-z0-9_.+!-]{1,100}", version)
        row = {"name": name, "version": version}
        provenance = json.loads(item.read_text("direct_url.json") or "{}")
        commit = provenance.get("vcs_info", {}).get("commit_id")
        if commit:
            assert re.fullmatch(r"[0-9a-f]{40,64}", commit)
            row["commit"] = commit
        assert not provenance.get("dir_info", {}).get("editable")
        graph.append(row)
    stage("complete")
    return {"wheelSha256": digest(wheel.read_bytes()), "verifiedMembers": count,
            "imports": origins, "graph": sorted(graph, key=lambda row: row["name"].lower())}


def validate_public(value):
    """Fail closed before upload, including nested child evidence and metadata."""
    def sha(value, lengths=(64,)):
        assert type(value) is str and len(value) in lengths and re.fullmatch("[0-9a-f]+", value)
    def number(value):
        assert type(value) is int and 0 <= value <= 10**9
    def frame(value):
        assert value["source"] in DIAGNOSTIC_SOURCES
        if value["source"] == "other":
            assert set(value) == {"source"}
        else:
            assert set(value) == {"source", "codeSha256", "lineOffset"}
            sha(value["codeSha256"])
            assert type(value["lineOffset"]) is int and 0 <= value["lineOffset"] <= 10**6
    def error(value):
        assert {"class", "frames"} <= set(value) <= {"class", "frames", "errno", "winerror"}
        assert value["class"] in ERROR_LABELS and type(value["frames"]) is list and len(value["frames"]) <= 5
        for item in value["frames"]:
            frame(item)
            assert item["source"] != "other"
        for field in ("errno", "winerror"):
            if field in value:
                assert type(value[field]) is int and -(2**31) <= value[field] < 2**32
    def network_diagnostics(item):
        if "networkDiagnostics" not in item:
            return
        diagnostics = item["networkDiagnostics"]
        assert set(diagnostics) == {"attempts", "overflow"}
        number(diagnostics["overflow"])
        assert type(diagnostics["attempts"]) is list and len(diagnostics["attempts"]) <= 32
        total = diagnostics["overflow"]
        for attempt in diagnostics["attempts"]:
            assert attempt["event"] in NETWORK_EVENTS
            assert attempt["case"] in {*NATIVE_CONTROLS, "outside"} and attempt["phase"] in PHASES
            number(attempt["count"])
            assert attempt["count"] > 0
            frame({key: val for key, val in attempt.items()
                   if key not in {"event", "case", "phase", "count"}})
            total += attempt["count"]
        assert total == item["network"]["unknownAttempts"]
    def installation(item):
        assert set(item) == {"wheelSha256", "verifiedMembers", "imports", "graph"}
        sha(item["wheelSha256"]); number(item["verifiedMembers"])
        assert set(item["imports"]) == set(IMPORTS)
        for row in item["imports"].values():
            assert set(row) == {"origin", "sha256"} and row["origin"] == "installed"
            sha(row["sha256"])
        for row in item["graph"]:
            assert {"name", "version"} <= set(row) <= {"name", "version", "commit"}
            assert re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", row["name"])
            assert re.fullmatch(r"[A-Za-z0-9_.+!-]{1,100}", row["version"])
            if "commit" in row:
                sha(row["commit"], (40, 64))
    common = {"status", "stage", "checks", "failureKind", "errno", "winerror", "installationStage",
              "installationImport", "installationCheck", "installationImportFailure", "installationMissingModule"}
    def checks(item):
        assert item["status"] in {"PASS", "FAIL"} and item["stage"] in STAGES
        for key, value in item["checks"].items():
            assert key in CHECKS
            if key == "unexpectedPythonNetworkAttempts":
                number(value)
            else:
                assert value in {"PASS", "FAIL"}
        if "failureKind" in item:
            assert item["failureKind"] in {"assertion", "permission", "missing", "timeout", "import", "other"}
        if "installationStage" in item:
            assert item["installationStage"] in INSTALLATION_STAGES
        if "installationImport" in item:
            assert item.get("installationStage") == "imports" and item["installationImport"] in IMPORTS
        if "installationCheck" in item:
            assert "installationImport" in item and item["installationCheck"] in INSTALLATION_CHECKS
        if "installationImportFailure" in item:
            assert "installationImport" in item and item["installationImportFailure"] in INSTALLATION_IMPORT_FAILURES
        if "installationMissingModule" in item:
            assert "installationImportFailure" in item and item["installationMissingModule"] in INSTALLATION_MISSING_MODULES
        for key in {"errno", "winerror"} & item.keys():
            number(item[key])
    assert set(value) <= common | {"candidate", "qualification", "publicAcceptance", "runtimePreparation",
        "outerJobsSettled", "scratchRemoved", "python", "windowsBuild", "hosts", "workers", "nativeControls"}
    checks(value)
    assert set(value["candidate"]) == {"head", "tree"}
    for commit in value["candidate"].values():
        sha(commit, (40, 64))
    assert value["publicAcceptance"] is False
    assert value["qualification"] == "private-adapter-candidate-only"
    assert value["runtimePreparation"] == "separate-private-project-api-plus-preinstalled-worker"
    assert len(value["python"]) == 3
    for version in value["python"]:
        number(version)
    number(value["windowsBuild"])
    for key in {"outerJobsSettled", "scratchRemoved"} & value.keys():
        assert type(value[key]) is bool
    for item in value.get("hosts", []):
        assert set(item) <= common | {"installed", "innerJobsSettled", "publicGateClosed", "network",
                                      "networkDiagnostics", "turnStep", "lastAction", "errors", "workerFacts",
                                      "runtimeStartup"}
        checks(item)
        if "turnStep" in item:
            assert item["turnStep"] in TURN_STEPS
        if "lastAction" in item:
            fact = item["lastAction"]
            assert set(fact) == {"action", "httpStatus", "hasError", "code"}
            assert fact["action"] in TRIAL_ACTIONS and fact["code"] in TRIAL_ACTION_CODES
            assert type(fact["httpStatus"]) is int and 100 <= fact["httpStatus"] <= 599
            assert type(fact["hasError"]) is bool
        if "errors" in item:
            assert type(item["errors"]) is list and 1 <= len(item["errors"]) <= EXCEPTION_RECORD_LIMIT
            for row in item["errors"]:
                error(row)
        if "workerFacts" in item:
            facts = item["workerFacts"]
            assert set(facts) == {"events", "errors", "diagnosticStatus"}
            assert facts["diagnosticStatus"] in {"PENDING", "PASS", "FAIL"}
            if item["status"] == "PASS":
                assert facts["diagnosticStatus"] == "PASS"
            assert set(facts["events"]) <= WORKER_EVENTS
            for count in facts["events"].values():
                number(count)
            assert type(facts["errors"]) is list and len(facts["errors"]) <= EXCEPTION_RECORD_LIMIT
            for row in facts["errors"]:
                error(row)
        if "runtimeStartup" in item:
            facts = item["runtimeStartup"]
            assert set(facts) == {"status", "errors"}
            assert facts["status"] in {"NOT_OBSERVED", "RECORDED", "PASS", "FAIL"}
            assert type(facts["errors"]) is list and len(facts["errors"]) <= EXCEPTION_RECORD_LIMIT
            assert bool(facts["errors"]) == (facts["status"] == "RECORDED")
            for row in facts["errors"]:
                error(row)
            if item["status"] == "PASS":
                assert facts["status"] == "PASS"
        if "installed" in item:
            installation(item["installed"])
        for key in {"innerJobsSettled", "publicGateClosed"} & item.keys():
            assert type(item[key]) is bool
        if "network" in item:
            assert set(item["network"]) == {"blockedOptionalIpv6Probes", "unknownAttempts"}
            for count in item["network"].values():
                number(count)
        network_diagnostics(item)
    for item in value.get("workers", []):
        installation(item)
    if "nativeControls" in value:
        item = value["nativeControls"]
        assert set(item) <= common | {"installed", "selection", "testSources", "network", "networkDiagnostics"}
        checks(item)
        if "installed" in item:
            installation(item["installed"])
        if "testSources" in item:
            assert set(item["testSources"]) == set(NATIVE_FILES)
            for fingerprint in item["testSources"].values():
                sha(fingerprint)
        if "network" in item:
            assert set(item["network"]) == {
                "blockedOptionalIpv6Probes", "unknownAttempts", "infrastructureSocketpairBinds",
                "tlsNegativeReservations", "tlsNegativeListeners", "tlsNegativeConnects"}
            for count in item["network"].values():
                number(count)
        network_diagnostics(item)
        if "selection" in item:
            selection = item["selection"]
            assert {"status", "exitCode", "collectionExact", "cases"} <= set(selection) <= {
                "status", "exitCode", "collectionExact", "cases", "failureFacts"}
            assert selection["status"] in {"PASS", "FAIL"}
            assert type(selection["collectionExact"]) is bool
            number(selection["exitCode"])
            assert set(selection["cases"]) == set(NATIVE_CONTROLS)
            for case in selection["cases"].values():
                assert set(case) == {"status", "collected", "callsPassed", "skips", "failures"}
                assert case["status"] in {"PASS", "FAIL"}
                for key in ("collected", "callsPassed", "skips", "failures"):
                    number(case[key])
                if case["status"] == "PASS":
                    assert (case["collected"], case["callsPassed"], case["skips"], case["failures"]) == (1, 1, 0, 0)
            if selection["status"] == "PASS":
                assert selection["collectionExact"] and selection["exitCode"] == 0
                assert all(case["status"] == "PASS" for case in selection["cases"].values())
                assert not selection.get("failureFacts")
            if "failureFacts" in selection:
                assert type(selection["failureFacts"]) is list and len(selection["failureFacts"]) <= 54
                for fact in selection["failureFacts"]:
                    assert set(fact) == {"case", "phase", "errors"}
                    assert fact["case"] in NATIVE_CONTROLS and fact["phase"] in PHASES - {"outside"}
                    assert type(fact["errors"]) is list and 1 <= len(fact["errors"]) <= EXCEPTION_RECORD_LIMIT
                    for item_error in fact["errors"]:
                        error(item_error)
    return value


class NetworkGuard:
    """Deny and retain every unrecognized attempt, even if a library catches it.

    The only allowed network is this process's one reserved TLS listener and
    numeric connects to that exact port. Worker processes allow no network.
    This is Python-audit coverage, not an adversarial native-code sandbox.
    """
    def __init__(self, log):
        self.log, self.port, self.reserving = Path(log), None, False
        self.blocked = 0
        self.probe_socket = None
        self.probes = 0
        self.pair_depth = self.pair_binds = 0
        self.tls_scope = ContextVar("trial_tls_negative_case", default=None)
        self.tls_binding = None
        self.tls_reservations = self.tls_connects = 0
        self.tls_endpoints = []
        self.case = "outside"
        self.phase = "outside"
        self.codes = {}
        self.network_facts = {}
        self.network_overflow = 0
        # Read metadata/source WITHOUT importing urllib3 or any app module.
        self.probe_path = Path(metadata.distribution("urllib3").locate_file(
            "urllib3/util/connection.py")).resolve()
        source = self.probe_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_has_ipv6")
        assert ast.dump(function) == ast.dump(ast.parse(IPV6_PROBE).body[0])
        compiled = compile(source, str(self.probe_path), "exec", dont_inherit=True)
        self.probe_code = next(c for c in compiled.co_consts if isinstance(c, types.CodeType)
                               and c.co_name == "_has_ipv6")

    def control_socketpair(self, event, args):
        # pytest-asyncio constructs fresh Windows event loops. Their synchronous
        # stdlib self-pipe is NOT app networking. Only our scoped socketpair
        # wrapper and the exact loaded stdlib fallback may create this one pair.
        if not self.pair_depth or event not in {"socket.bind", "socket.connect"}:
            return False
        frame = sys._getframe(1)
        for _ in range(8):
            if frame is None:
                return False
            if frame.f_code is socket._fallback_socketpair.__code__:
                if (frame.f_globals.get("__name__") != "socket"
                        or Path(frame.f_globals["__file__"]).resolve() != Path(socket.__file__).resolve()):
                    return False
                if event == "socket.bind":
                    valid = args[0] is frame.f_locals.get("lsock") and args[1] == ("127.0.0.1", 0)
                    if valid:
                        self.pair_binds += 1
                    return valid
                return (args[0] is frame.f_locals.get("csock")
                        and args[1] == frame.f_locals["lsock"].getsockname()[:2]
                        and args[1][0] == "127.0.0.1")
            frame = frame.f_back
        return False

    def control_tls_network(self, event, args):
        if event == "socket.bind" and self.tls_binding is not None:
            stream, address = self.tls_binding
            return args[0] is stream and args[1] == address
        scope = self.tls_scope.get()
        if event != "socket.connect" or scope is None:
            return False
        for endpoint in scope.endpoints:
            # Both the pinned listener and the proxy/redirect sink are reachable:
            # disclosure must fail the original test's oracle, not be hidden by
            # this guard. No different listener at the same port is admitted.
            if (endpoint.server.started and not endpoint.server.closed
                    and endpoint.stream.fileno() >= 0
                    and args[1] == endpoint.stream.getsockname()[:2]
                    and args[1][0] == "127.0.0.1"):
                self.tls_connects += 1
                return True
        return False

    def audit(self, event, args):
        if event not in NETWORK_EVENTS:
            return
        address = args[1] if event in {"socket.bind", "socket.connect"} else None
        if self.control_socketpair(event, args):
            return
        if self.control_tls_network(event, args):
            return
        if event == "socket.bind" and address == ("::1", 0):
            frame = sys._getframe(1)
            if (self.probes == 0 and frame.f_code == self.probe_code
                    and frame.f_globals.get("__name__") == "urllib3.util.connection"
                    and Path(frame.f_globals["__file__"]).resolve() == self.probe_path
                    and args[0].family == socket.AF_INET6 and frame.f_locals.get("host") == "::1"):
                self.probes += 1
                self.probe_socket = args[0]
                raise PermissionError("Blocked attested optional IPv6 capability probe")
        if event == "socket.bind" and self.reserving and address == ("127.0.0.1", 0):
            self.reserving = False
            return
        if event == "socket.connect" and isinstance(address, tuple) and self.port is not None:
            if address[:2] == ("127.0.0.1", self.port):
                return
        self.blocked += 1
        codes = {**diagnostic_codes(), **self.codes}
        frame = sys._getframe(1)
        origin = {"source": "other"}
        for _ in range(32):
            if frame is None:
                break
            fact = safe_frame(frame, codes)
            if fact["source"] != "other":
                origin = fact
                break
            frame = getattr(frame, "f_back", None)
        attribution = {"event": event, "case": self.case, "phase": self.phase, **origin}
        key = json.dumps(attribution, sort_keys=True)
        if key in self.network_facts:
            self.network_facts[key]["count"] += 1
        elif len(self.network_facts) < 32:
            self.network_facts[key] = {**attribution, "count": 1}
        else:
            self.network_overflow += 1
        # Never retain host names, paths, arguments or exception prose.
        with self.log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"event": event, "blocked": True}) + "\n")
        raise PermissionError("Offline trial network boundary")

    def network_diagnostics(self):
        return {"attempts": list(self.network_facts.values()), "overflow": self.network_overflow}

    def verify(self):
        if self.probes:
            assert self.probe_socket.fileno() == -1
            assert sys.modules["urllib3.util.connection"].HAS_IPV6 is False
        assert self.blocked == 0
        return {"blockedOptionalIpv6Probes": self.probes, "unknownAttempts": self.blocked}


@contextmanager
def control_socketpairs(guard):
    """Narrow interpreter self-pipe allowance; no DNS or arbitrary loopback."""
    original = socket.socketpair
    def pair(*args, **kwargs):
        assert not args and not kwargs  # asyncio uses the ordinary IPv4 default
        guard.pair_depth += 1
        left = right = None
        try:
            left, right = original()
            assert left.getsockname() == right.getpeername()
            assert right.getsockname() == left.getpeername()
            assert left.getsockname()[0] == right.getsockname()[0] == "127.0.0.1"
            return left, right
        except BaseException:
            for stream in (left, right):
                if stream is not None:
                    stream.close()
            raise
        finally:
            guard.pair_depth -= 1
    socket.socketpair = pair
    try:
        yield
    finally:
        socket.socketpair = original


@contextmanager
def native_tls_cases(guard, target, source_hash):
    """Transport-only observation for the two unchanged disclosure falsifiers.

    The exact copied test code owns the case; its nested catcher/redirect
    functions own the two TestServers. Admit each actual socket and its retained
    endpoint, not a global loopback window. Requests, TLS pinning, redirects,
    proxies and the test's bearer-disclosure assertions remain unchanged.
    """
    import pytest
    from aiohttp.test_utils import BaseTestServer, TestServer
    source = target / "tests/test_browser_entry.py"
    raw = source.read_bytes()
    assert digest(raw) == source_hash
    name = "test_native_unrelated_listener_and_proxy_never_receive_bearer"
    declaration = next(part for part in ast.parse(raw).body
                       if isinstance(part, ast.AsyncFunctionDef) and part.name == name)
    assert {part.name for part in declaration.body if isinstance(part, ast.AsyncFunctionDef)} == {"catcher", "redirect"}
    scopes = {}
    def scope_for(item):
        assert item.nodeid in TLS_NEGATIVE_NODES
        function = item.obj
        # pytest rewrites assertions, so raw compile(source) is not the loaded
        # code object. Bind to this exact selected module/function after rewrite,
        # with the independently staged source hash and exact parameter checked.
        assert (function.__name__ == name and function.__code__.co_name == name
                and Path(function.__code__.co_filename) == source
                and Path(function.__globals__["__file__"]) == source
                and sys.modules[function.__module__].__dict__ is function.__globals__
                and function.__globals__[name] is function and digest(source.read_bytes()) == source_hash
                and item.callspec.params["mode"] == TLS_NEGATIVE_MODES[item.nodeid])
        if item.nodeid not in scopes:
            handlers = {part for part in function.__code__.co_consts if isinstance(part, types.CodeType)
                        and part.co_name in {"catcher", "redirect"}}
            assert len(handlers) == 2
            scopes[item.nodeid] = types.SimpleNamespace(
                globals=function.__globals__, handlers=handlers, port=None, endpoints=[])
        return scopes[item.nodeid]

    def bound(stream, address):
        assert guard.tls_binding is None
        guard.tls_binding = (stream, address)
        try:
            stream.bind(address)
        finally:
            guard.tls_binding = None

    class Controls:
        def reserve(self, item):
            # Only these two selected cases request this fixture. Retain no
            # ambient unused-port allowance and never bind a nonnumeric host.
            scope = scope_for(item)
            assert scope.port is None
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
                bound(stream, ("127.0.0.1", 0))
                scope.port = stream.getsockname()[1]
            guard.tls_reservations += 1
            return scope.port

        @pytest.fixture
        def unused_tcp_port(self, request):
            return self.reserve(request.node)

        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_call(self, item):
            if item.nodeid not in TLS_NEGATIVE_NODES:
                yield
                return
            scope = scope_for(item)
            assert scope.port is not None and not scope.endpoints
            token = guard.tls_scope.set(scope)
            try:
                yield
            finally:
                guard.tls_scope.reset(token)

    original = BaseTestServer.start_server
    async def start(server, *args, **kwargs):
        scope = guard.tls_scope.get()
        if scope is None:
            return await original(server, *args, **kwargs)  # bind stays denied
        if server.started:
            assert any(endpoint.server is server for endpoint in scope.endpoints)
            return await original(server, *args, **kwargs)  # aiohttp_client cleanup registration
        assert type(server) is TestServer and server.host == "127.0.0.1" and not server.started
        assert len(scope.endpoints) < 2
        # The first server is the ephemeral HTTP sink; the second is the
        # explicitly reserved wrong-leaf or trusted-redirect HTTPS listener.
        expected_port = 0 if not scope.endpoints else scope.port
        assert (server.port or 0) == expected_port
        assert bool(kwargs.get("ssl")) == bool(scope.endpoints)
        routes = tuple(server.app.router.routes())
        assert routes and all(route.handler.__code__ in scope.handlers
            and route.handler.__globals__ is scope.globals for route in routes)
        original_factory = server.socket_factory
        streams = []
        def factory(host, port, family):
            assert not streams and host == "127.0.0.1" and port == expected_port and family == socket.AF_INET
            stream = socket.socket(family, socket.SOCK_STREAM)
            streams.append(stream)
            try:
                bound(stream, (host, port))
                return stream
            except BaseException:
                stream.close()
                raise
        server.socket_factory = factory
        try:
            await original(server, *args, **kwargs)
            assert len(streams) == 1 and streams[0].fileno() >= 0
            assert streams[0].getsockname()[:2] == (server.host, server.port)
            endpoint = types.SimpleNamespace(server=server, stream=streams[0])
            scope.endpoints.append(endpoint)
            guard.tls_endpoints.append(endpoint)
        except BaseException:
            await server.close()
            for stream in streams:
                stream.close()
            raise
        finally:
            server.socket_factory = original_factory
    BaseTestServer.start_server = start
    guard.codes.update({function.__code__: label for function, label in (
        (scope_for, "tls_fixture_scope"), (bound, "tls_fixture_bound"),
        (Controls.reserve, "tls_fixture_reserve"), (Controls.pytest_runtest_call, "tls_fixture_call"),
        (start, "tls_fixture_start"))})
    try:
        yield Controls()
    finally:
        BaseTestServer.start_server = original


def sanitize(root):
    keep = {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "COMSPEC", "TEMP", "TMP",
            "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS"}
    environment = {key: value for key, value in os.environ.items() if key.upper() in keep}
    environment.update({
        "HOME": str(root / "home"), "USERPROFILE": str(root / "home"),
        "APPDATA": str(root / "home" / "roaming"), "LOCALAPPDATA": str(root / "home" / "local"),
        "AMPLIFIER_HOME": str(root / "shared"), "AMPLIFIER_WEB_HOME": str(root / "app"),
        "AMPLIFIER_UNIFIED_IMPORT_HOME": str(root / "legacy"),
        "AMPLIFIER_SESSION_STATE_HOME": str(root / "ownership"),
        "AMPLIFIER_TERMINAL_HOME": str(root / "terminal"),
        "AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH": str(root / "capture"),
        "AMPLIFIER_RUNTIME_IMMUTABLE": "1", "UV_OFFLINE": "1", "PYTHONUTF8": "1",
        "WINDOWS_TRIAL_ROOT": str(root),
        "GITHUB_ACTIONS": "true",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    })
    os.environ.clear()
    os.environ.update(environment)


def private_directory(path, root):
    from amplifier_web.windows_files import directory
    with directory(path, owned_root=root, create=True):
        pass


def canonical_temp(value):
    """Canonicalize ONLY the existing TEMP, checking both chains, never ACL repair."""
    original = Path(value).absolute()
    canonical = original.resolve(strict=True)
    for path in (original, canonical):
        for component in (*reversed(path.parents), path):
            info = component.lstat()
            assert stat.S_ISDIR(info.st_mode) and not (getattr(info, "st_file_attributes", 0) & 0x400)
    before, after = original.stat(), canonical.stat()
    assert (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
    return canonical


def settled(owners):
    """Never equate swallowed RuntimeManager.close errors to cleanup proof."""
    return all(owner.result is not None and owner.result.active_processes == 0
               and owner.job.tree_empty and owner.job.io_finalized for owner in owners)


async def until(predicate):
    async with asyncio.timeout(60):
        while not predicate():
            await asyncio.sleep(.05)


def child_handle(pid):
    # Retain a read-only exact process handle, never signal an integer PID.
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE only
    assert handle and kernel.WaitForSingleObject(handle, 0) == 258
    return kernel, handle


async def worker(wheel):
    require_ci()
    root = Path(os.environ["WINDOWS_TRIAL_ROOT"])
    guard = NetworkGuard(root / ("network-worker-" + str(os.getpid()) + ".jsonl"))
    sys.addaudithook(guard.audit)
    from amplifier_web import runtime_worker as implementation
    from amplifier_web.runtime_worker import Worker, configure_protocol
    configure_protocol()
    # Per-event snapshots stay PENDING until ordinary Worker.run cleanup ends.
    # A missing/truncated final write therefore cannot look like completed proof.
    facts = {"events": {}, "errors": [], "diagnosticStatus": "PENDING"}
    diagnostic_failed = False
    original_publish = implementation.publish
    def observed_publish(event):
        nonlocal diagnostic_failed
        try:
            if worker_event(facts, event):
                write_json(root / "worker-facts.json", facts)
        except BaseException:
            # Observation has no authority to change publisher behavior. Keep
            # original cancellation/errors outside this diagnostic-only catch.
            diagnostic_failed = True
        return original_publish(event)
    implementation.publish = observed_publish
    # Record again AFTER the real mount, not only before Foundation source loading.
    owner = Worker()
    original_start = owner.start
    async def observed_start(*args, **kwargs):
        await original_start(*args, **kwargs)
        if owner.session is not None:
            evidence = installed(wheel)
            guard.verify()
            write_json(root / ("installed-worker-" + str(os.getpid()) + ".json"), evidence)
    owner.start = observed_start
    try:
        await owner.run()
        guard.verify()
    finally:
        implementation.publish = original_publish
        facts["diagnosticStatus"] = "FAIL" if diagnostic_failed else "PASS"
        try:
            write_json(root / "worker-facts.json", facts)
        except BaseException:
            diagnostic_failed = True
        # Never raise from finally: preserve the exact original run/publisher
        # exception (including cancellation). Normal returns still fail below.
    assert not diagnostic_failed


async def worker_entry(wheel):
    """Retain early bootstrap errors too; never replace run/cancellation failure."""
    try:
        return await worker(wheel)
    except BaseException as exc:
        try:
            path = Path(os.environ["WINDOWS_TRIAL_ROOT"]) / "worker-facts.json"
            facts = completed_worker_facts(path)
            facts["diagnosticStatus"] = "FAIL"
            if not facts["errors"]:
                facts["errors"] = safe_exception(exc, {
                    **diagnostic_codes(), worker.__code__: "trial_worker",
                    require_ci.__code__: "trial_require_ci",
                    NetworkGuard.__init__.__code__: "trial_network_guard"})
            write_json(path, facts)
        except BaseException:
            pass  # Missing final diagnostics remains a failed qualification.
        raise


async def journey(root, wheel, report, guard, phase):
    from aiohttp import web, ClientSession, TCPConnector, DummyCookieJar
    from amplifier_web import auth, browser_entry as entry, server, tls
    from amplifier_web.deployment import load_server_config
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.builtin_behaviors import DEFAULT_BEHAVIORS
    from amplifier_web.session_files import sessions_dir
    from amplifier_foundation.session.history import SessionHistoryStore

    home, workspace = root / "app", root / "workspace café 日本語"
    report["stage"] = "fixture"
    modules = {key: str(Path(importlib.import_module(value).__file__).parent)
               for key, value in MODULES.items()}
    settings = {"bundle": {"app": []}, "web_bundles": {"excluded": list(DEFAULT_BEHAVIORS)},
                "sources": {"modules": modules}}
    from amplifier_web.deployment import write_private
    write_private(root / "shared" / "settings.yaml", json.dumps(settings), owned_root=root / "shared")
    provider = root / "provider" / "amplifier_module_provider_fixture"
    if phase == "first":
        private_directory(provider, root)
        (provider / "__init__.py").write_text(PROVIDER, encoding="utf-8")
    output, blocked = workspace / "trial.txt", root / "blocked.json"
    bundle = root / "bundle.yaml"
    write_json(bundle, {
        "bundle": {"name": "windows-trial"},
        "session": {"orchestrator": {"module": "loop-live", "config": {
            "use_streaming": False, "min_delay_between_calls_ms": 0}},
            "context": {"module": "context-simple", "source": modules["context-simple"]}},
        "providers": [{"module": "provider-fixture", "source": str(provider.parent),
                       "config": {"output": str(output), "blocked": str(blocked),
                                  "calls": str(root / "provider-calls.jsonl")}}],
        "tools": [{"module": "tool-filesystem", "source": modules["tool-filesystem"],
                   "config": {"allowed_write_paths": [str(workspace)]}}],
    })
    command = [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--worker", str(wheel)]
    runtime = observed_runtime_type(RuntimeManager)(
        command=command, startup_timeout=90, retention={"prewarm_on_select": False})
    runtime.home = home
    runner = app = reservation = None
    original_reader = entry.read_owner_control_token
    owners = []
    worker_facts_path = root / "worker-facts.json"
    # Reset only this bounded observation before a fresh host can start a worker.
    # No worker from the preceding host is alive here (outer owned-job proof).
    write_json(worker_facts_path, {"events": {}, "errors": [], "diagnosticStatus": "PENDING"})
    try:
        if phase == "first":
            report["stage"] = "private_runtime_project_api"
            from amplifier_web.runtime_environment import prepare_project
            from amplifier_web.windows_files import directory
            content = b'[project]\nname="trial-preparation"\nversion="0.0.0"\n'
            project = prepare_project(home, content=content)
            with directory(project, owned_root=home) as pinned:
                assert pinned.read_private("pyproject.toml", limit=1024) == content
            assert prepare_project(home, content=content) == project
            report["checks"]["privateRuntimeProjectApi"] = "PASS"
        report["stage"] = "credentials_tls"
        reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        guard.reserving = True
        reservation.bind(("127.0.0.1", 0))
        guard.port = reservation.getsockname()[1]
        reservation.listen(32)
        reservation.setblocking(False)
        config = load_server_config(home, overrides={"port": guard.port, "bind": ["127.0.0.1"]})
        config = tls.setup_local_ca(home, config)
        app = await server.create_app(home, workspace=workspace, runtime=runtime, voice=False,
            background_updates=False, background_work=False, preload_providers=False, server_config=config)
        runtime.app_bridge = app["service"].app_bridge
        assert app["browser_entry"] is None
        try:
            original_reader(home, entry.current_owner_sid())
        except entry.BrowserEntryUnavailable:
            report["publicGateClosed"] = True
        else:
            raise AssertionError("Public gate changed; re-review qualification boundary")
        # Same explicit candidate seam as test_native_actual_app_tls_grant_cookie_and_replay.
        # This in-process fixture changes the reader selection, not the source
        # gate, stored settings or credential initialization implementation.
        entry.read_owner_control_token = entry._read_private_owner_control_token
        entry.initialize(app)
        assert app["browser_entry"] is not None
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.SockSite(runner, reservation, ssl_context=tls.ssl_context(home, config)).start()
        _, pin = entry._owner_probe_targets(home, config)
        origin = f"https://127.0.0.1:{guard.port}"
        async with ClientSession(connector=TCPConnector(ssl=pin), trust_env=False,
                                 cookie_jar=DummyCookieJar()) as client:
            report["stage"] = "grant_cookie_csrf"
            response = await client.get(origin + "/api/state")
            assert response.status == 401
            await response.read()
            url = await entry.owner_browser_url(home, config)
            assert "#ticket=" in url and app["control_token"] not in url
            ticket = url.split("#ticket=")[1]
            response = await client.get(origin + "/login")
            assert response.status == 200
            csrf = response.cookies[auth.CSRF_COOKIE].value
            await response.read()
            headers = {"Origin": origin, "Cookie": f"{auth.CSRF_COOKIE}={csrf}"}
            response = await client.post(origin + entry.REDEEM_PATH, headers=headers,
                data={"csrf": "wrong", "ticket": ticket}, allow_redirects=False)
            assert response.status == 403
            await response.read()
            data = {"csrf": csrf, "ticket": ticket}
            response = await client.post(origin + entry.REDEEM_PATH, headers=headers, data=data, allow_redirects=False)
            assert response.status == 303
            cookie = response.cookies[auth.SESSION_COOKIE]
            assert cookie["secure"] and cookie["httponly"] and cookie["samesite"] == "Strict"
            await response.read()
            response = await client.post(origin + entry.REDEEM_PATH, headers=headers, data=data, allow_redirects=False)
            assert response.status == 403
            await response.read()
            headers = {"Origin": origin, "Cookie": f"{auth.SESSION_COOKIE}={cookie.value}"}
            response = await client.get(origin + "/api/state", headers=headers)
            assert response.status == 200
            await response.read()
            report["checks"]["privateTlsGrantCookieCsrfReplay"] = "PASS"

            async def action(name, args, identity):
                response = await client.post(origin + "/api/actions", headers=headers,
                    json={"action": name, "args": args, "id": identity})
                result = await response.json()
                report["lastAction"] = action_fact(name, response.status, result)
                assert response.status == 200 and not result.get("error")
                return result
            guard.codes.update({action.__code__: "trial_action", journey.__code__: "trial_journey"})

            service = app["service"]
            if phase == "first":
                report["stage"] = "first_worker_turn"
                report["turnStep"] = "session_create"
                await action("session.create", {"bundle": str(bundle)}, "trial-create")
                sid = service._session()["id"]
                report["turnStep"] = "access_read"
                current = (await action("access.get", {"sessionId": sid}, "access-read"))["result"]
                report["turnStep"] = "access_save"
                await action("access.set", {"sessionId": sid, "scope": "workspace", "mode": "full",
                    "expectedRevision": current["revision"]}, "access-save")
            else:
                report["stage"] = "host_restart_resume"
                saved = json.loads((root / "checkpoint.json").read_text(encoding="utf-8"))
                sid = saved["sid"]
                assert service._session(sid)["id"] == sid
                assert auth.control_token(home) == saved["control"]
                assert auth.session_secret(home) == saved["secret"]
                output_read = (await action("outputs.read", {"sessionId": sid, "id": saved["output"]},
                                           "read-after-host-restart"))["result"]
                assert output_read["sha256"] == digest((MARKER + "\n").encode())
                assert output_read["text"] == MARKER + "\n"
            report["turnStep"] = "inherited_access"
            inherited = (await action("access.get", {"sessionId": sid}, "access-inherited-" + phase))["result"]
            assert inherited["mode"] == "full" and inherited["source"] == "workspace"
            assert inherited["overrides"]["session"]["mode"] == "inherit"
            report["checks"]["inheritedAccessLookup"] = "PASS"
            report["turnStep"] = "conversation_send"
            await action("conversation.send", {"sessionId": sid,
                "text": "FIRST_TRIAL café 日本語" if phase == "first" else "RESUME_TRIAL"}, "trial-" + phase)
            directory = sessions_dir(workspace) / sid
            expected = MARKER if phase == "first" else RESUMED
            def completed():
                if not runtime.workers.get(sid, {}).get("parked"):
                    return False
                return any(m["role"] == "assistant" and expected in str(m.get("content"))
                           for m in SessionHistoryStore(directory).load_messages())
            report["turnStep"] = "wait_parked_history"
            await until(completed)
            row = runtime.workers[sid]
            owners.append(row["windows_owner"])
            calls = [json.loads(line) for line in (root / "provider-calls.jsonl").read_text(encoding="utf-8").splitlines()]
            report["turnStep"] = "provider_identity"
            assert calls[-1] == {"pid": row["process"].pid, "access": "full"}
            report["turnStep"] = "guard_identity"
            assert row["process"].pid != row["windows_owner"].job.identity.guard_pid
            report["turnStep"] = "output_utf8"
            assert output.read_text(encoding="utf-8") == MARKER + "\n"
            history = SessionHistoryStore(directory).load_messages()
            if phase == "resume":
                assert any(m["role"] == "assistant" and RESUMED in str(m.get("content")) for m in history)
                assert sum(m["role"] == "user" and "FIRST_TRIAL" in str(m.get("content")) for m in history) == 1
                report["checks"]["hostRestartResumeWithoutReplay"] = "PASS"
                return
            report["turnStep"] = "history_assistant"
            assert any(m["role"] == "assistant" and MARKER in str(m.get("content")) for m in history)
            report["turnStep"] = "history_tool"
            assert any(m["role"] == "tool" for m in history)
            report["checks"]["realWorkerToolUtf8History"] = "PASS"
            report["stage"] = "output_snapshot"
            record = (await action("outputs.attach", {"sessionId": sid, "kind": "file", "title": "Trial",
                "path": "trial.txt", "expectedSha256": digest(output.read_bytes())}, "trial-output"))["result"]
            read = (await action("outputs.read", {"sessionId": sid, "id": record["id"]}, "trial-output-read"))["result"]
            assert read["text"] == MARKER + "\n" and read["sha256"] == digest(output.read_bytes())
            response = await client.get(origin + read["downloadUrl"], headers=headers)
            assert response.status == 200 and await response.read() == output.read_bytes()
            report["checks"]["outputSnapshotReadExactBytes"] = "PASS"
            write_private(root / "checkpoint.json", json.dumps({"sid": sid, "output": record["id"],
                "control": auth.control_token(home), "secret": auth.session_secret(home)}), owned_root=root)
            report["stage"] = "stop_tree"
            await action("conversation.send", {"sessionId": sid, "text": "WAIT_FOR_STOP"}, "trial-wait")
            await until(blocked.exists)
            kernel, handle = child_handle(json.loads(blocked.read_text())["pid"])
            try:
                await runtime.stop(sid)
                assert kernel.WaitForSingleObject(handle, 5000) == 0
            finally:
                kernel.CloseHandle(handle)
            assert owners[-1].job.tree_empty and owners[-1].job.io_finalized
            assert owners[-1].result.active_processes == 0
            report["checks"]["stopDescendantTreeAndIo"] = "PASS"
    finally:
        entry.read_owner_control_token = original_reader
        # Include a worker that failed before its first parked observation.
        for row in runtime.workers.values():
            if row.get("windows_owner") is not None and row["windows_owner"] not in owners:
                owners.append(row["windows_owner"])
        if runner is not None:
            await runner.cleanup()
        elif app is not None:
            await app["service"].close()
        else:
            await runtime.close()
        if reservation is not None:
            reservation.close()
        # RuntimeManager.close gathers exceptions; do not mistake that for proof.
        report["innerJobsSettled"] = settled(owners)
        assert report["innerJobsSettled"]
        report["runtimeStartup"] = runtime.trial_startup
        report["workerFacts"] = completed_worker_facts(worker_facts_path)
        if sys.exception() is None:
            assert report["runtimeStartup"]["status"] == "PASS"
            assert report["workerFacts"]["diagnosticStatus"] == "PASS"


def require_ci():
    # An intentional CI-only listener must never be created by local authoring.
    assert sys.platform == "win32" and os.environ.get("GITHUB_ACTIONS") == "true"
    assert sys.version_info[:2] == (3, 13) and sys.implementation.name == "cpython"
    assert struct.calcsize("P") == 8 and not sysconfig.get_config_var("Py_GIL_DISABLED")


def failure(report, exc):
    # No exception messages, traceback symbols, paths, SIDs or credentials.
    report["status"] = "FAIL"
    report["checks"].setdefault(report["stage"], "FAIL")
    kinds = {AssertionError: "assertion", PermissionError: "permission",
             FileNotFoundError: "missing", TimeoutError: "timeout",
             ImportError: "import", ModuleNotFoundError: "import"}
    report["failureKind"] = kinds.get(type(exc), "other")
    for key in ("errno", "winerror"):
        value = getattr(exc, key, None)
        if type(value) is int:
            report[key] = value


class NativeResults:
    """Retain only fixed labels/counts, never pytest error prose or locations."""
    def __init__(self, guard=None):
        self.node_labels = {"tests/" + node: label for label, node in NATIVE_CONTROLS.items()}
        self.collected = {label: 0 for label in NATIVE_CONTROLS}
        self.phases = {label: [] for label in NATIVE_CONTROLS}
        self.unexpected = self.collection_errors = 0
        self.guard = guard
        self.codes = {}
        self.failure_facts = []

    def pytest_collection_finish(self, session):
        for item in session.items:
            label = self.node_labels.get(item.nodeid)
            if label is None:
                self.unexpected += 1
            else:
                self.collected[label] += 1
                function = getattr(item, "obj", None)
                code = getattr(function, "__code__", None)
                if isinstance(code, types.CodeType):
                    self.codes[code] = "selected_test"
        if self.guard is not None:
            self.guard.codes.update(self.codes)

    @contextmanager
    def _phase(self, item, phase):
        if self.guard is not None:
            self.guard.case = self.node_labels.get(item.nodeid, "outside")
            self.guard.phase = phase
        try:
            yield
        finally:
            if self.guard is not None:
                self.guard.case = self.guard.phase = "outside"

    # Registered as tryfirst hookwrappers below: attribute actual phase entry
    # before fixtures/finalizers run, including teardown after failed setup.
    def pytest_runtest_setup(self, item):
        with self._phase(item, "setup"):
            yield

    def pytest_runtest_call(self, item):
        with self._phase(item, "call"):
            yield

    def pytest_runtest_teardown(self, item, nextitem):
        with self._phase(item, "teardown"):
            yield

    def pytest_runtest_makereport(self, item, call):
        label = self.node_labels.get(item.nodeid)
        if label is not None and call.when in PHASES - {"outside"} and call.excinfo is not None:
            if len(self.failure_facts) < 54:
                codes = {**diagnostic_codes(), **self.codes,
                         **(self.guard.codes if self.guard is not None else {})}
                self.failure_facts.append({"case": label, "phase": call.when,
                                          "errors": safe_exception(call.excinfo.value, codes)})
        # Do not replace pytest's report or touch its exception/outcome.
        return None

    def pytest_collectreport(self, report):
        if report.failed or report.skipped:
            self.collection_errors += 1

    def pytest_runtest_logreport(self, report):
        label = self.node_labels.get(report.nodeid)
        if (label is None or report.when not in {"setup", "call", "teardown"}
                or report.outcome not in {"passed", "failed", "skipped"}
                or hasattr(report, "wasxfail")):
            self.unexpected += 1
            return
        self.phases[label].append((report.when, report.outcome))

    def finish(self, exit_code):
        cases = {}
        for label, phases in self.phases.items():
            cases[label] = {
                "status": "PASS" if self.collected[label] == 1 and phases == [
                    ("setup", "passed"), ("call", "passed"), ("teardown", "passed")] else "FAIL",
                "collected": self.collected[label],
                "callsPassed": phases.count(("call", "passed")),
                "skips": sum(outcome == "skipped" for _, outcome in phases),
                "failures": sum(outcome == "failed" for _, outcome in phases),
            }
        exact = not self.unexpected and not self.collection_errors and all(
            count == 1 for count in self.collected.values())
        result = {"status": "PASS" if int(exit_code) == 0 and exact and all(
                    row["status"] == "PASS" for row in cases.values()) else "FAIL",
                "exitCode": int(exit_code), "collectionExact": exact, "cases": cases}
        if self.failure_facts:
            result["failureFacts"] = self.failure_facts
        return result


def stage_controls(root, source):
    """Copy only reviewed tests/helpers: never put checkout production on sys.path.

    The runtime native test inserts its copied parent into a child interpreter.
    That directory contains tests ONLY; the actual app still comes from the wheel.
    """
    target = root / "native-controls"
    private_directory(target / "tests", root)
    hashes = {}
    for name in NATIVE_FILES:
        raw = (source / name).read_bytes()
        (target / "tests" / name).write_bytes(raw)
        assert (target / "tests" / name).read_bytes() == raw
        hashes[name] = digest(raw)
    assert set(path.name for path in target.iterdir()) == {"tests"}
    return target, hashes


def native_controls(wheel):
    """Synchronous pytest entrypoint in its own contained interpreter."""
    require_ci()
    root = Path(os.environ["WINDOWS_TRIAL_ROOT"])
    report = {"status": "FAIL", "stage": "native_prerequisites", "checks": {}}
    guard = None
    try:
        with (root / "diagnostics-controls.log").open("w", encoding="utf-8") as log:
            with redirect_stdout(log), redirect_stderr(log):
                guard = NetworkGuard(root / "network-controls.jsonl")
                sys.addaudithook(guard.audit)
                report["installed"] = installed(wheel, report)
                target, report["testSources"] = stage_controls(root, Path(__file__).parents[1])
                import pytest
                pytest.hookimpl(tryfirst=True)(NativeResults.pytest_runtest_makereport)
                for phase in ("setup", "call", "teardown"):
                    pytest.hookimpl(hookwrapper=True, tryfirst=True)(
                        getattr(NativeResults, "pytest_runtest_" + phase))
                results = NativeResults(guard)
                # Register declared plugins BEFORE our narrow fixture override.
                # Passing them only via -p loads them after plugins=[...] and
                # silently selects pytest-asyncio's unscoped port reservation.
                declared_plugins = [importlib.import_module(name) for name in
                                    ("pytest_asyncio.plugin", "aiohttp.pytest_plugin")]
                with control_socketpairs(guard), native_tls_cases(
                        guard, target, report["testSources"]["test_browser_entry.py"]) as tls_controls:
                    result = pytest.main([
                        "-q", "-p", "no:cacheprovider", "-p", "pytest_asyncio.plugin",
                        "-p", "aiohttp.pytest_plugin", "-o", "asyncio_mode=auto",
                        "--rootdir", str(target), "--confcutdir", str(target),
                        "--basetemp", str(root / "native-test-tmp"),
                        *[str(target / "tests" / node) for node in NATIVE_CONTROLS.values()]],
                        plugins=[*declared_plugins, results, tls_controls])
                report["selection"] = results.finish(result)
                assert installed(wheel, report) == report["installed"]
                guard.verify()
                assert report["selection"]["status"] == "PASS"
                assert guard.tls_reservations == 2 and len(guard.tls_endpoints) == 4
                assert all(endpoint.server.closed and endpoint.stream.fileno() == -1
                           for endpoint in guard.tls_endpoints)
                report["checks"]["maintainedNativePrerequisites"] = "PASS"
        report.update(status="PASS", stage="complete")
    except BaseException as exc:
        failure(report, exc)
    finally:
        if guard is not None:
            report["network"] = {
                "blockedOptionalIpv6Probes": guard.probes, "unknownAttempts": guard.blocked,
                "infrastructureSocketpairBinds": guard.pair_binds,
                "tlsNegativeReservations": guard.tls_reservations,
                "tlsNegativeListeners": len(guard.tls_endpoints),
                "tlsNegativeConnects": guard.tls_connects}
            report["networkDiagnostics"] = guard.network_diagnostics()
    write_json(root / "controls.json", report)
    return 0 if report["status"] == "PASS" else 1


def host(wheel, phase):
    require_ci()
    root = Path(os.environ["WINDOWS_TRIAL_ROOT"])
    report = {"status": "FAIL", "stage": "installed_host", "checks": {}}
    guard = None
    try:
        # Start the loop before auditing; Windows Proactor loop wakeup socket
        # setup is interpreter infrastructure, not the application under test.
        async def qualified():
            nonlocal guard
            guard = NetworkGuard(root / ("network-host-" + phase + ".jsonl"))
            sys.addaudithook(guard.audit)
            report["installed"] = installed(wheel, report)
            await journey(root, wheel, report, guard, phase)
            report["network"] = guard.verify()
        with (root / ("diagnostics-" + phase + ".log")).open("w", encoding="utf-8") as log:
            with redirect_stdout(log), redirect_stderr(log):
                asyncio.run(qualified())
        report.update(status="PASS", stage="complete")
    except BaseException as exc:
        failure(report, exc)
        report["errors"] = safe_exception(exc, {**diagnostic_codes(),
            **(guard.codes if guard is not None else {}), journey.__code__: "trial_journey"})
    finally:
        if guard is not None:
            report["network"] = {"blockedOptionalIpv6Probes": guard.probes, "unknownAttempts": guard.blocked}
            report["networkDiagnostics"] = guard.network_diagnostics()
    write_json(root / ("host-" + phase + ".json"), report)
    return 0 if report["status"] == "PASS" else 1


async def hosts(root, wheel, report):
    """Two fresh hosts use one DB, each inside the existing owned-job primitive."""
    from amplifier_web.windows_jobs import WindowsOwnedProcess
    for phase in ("controls", "first", "resume"):
        report["stage"] = phase
        # Unknown launch/cleanup means preserve scratch, not a blind rmtree.
        report["outerJobsSettled"] = False
        command = ([sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                    "--controls", str(wheel)] if phase == "controls" else
                   [sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                    "--host", str(wheel), "--phase", phase])
        job = await WindowsOwnedProcess.start(command,
            cwd=str(root), env=dict(os.environ), trusted_python=sys._base_executable)
        async def drain(stream):
            while await stream.read(65536):
                pass  # No raw child diagnostic output reaches GitHub logs.
        readers = [asyncio.create_task(drain(stream)) for stream in (job.stdout, job.stderr)]
        try:
            result = await asyncio.wait_for(job.wait(), 150)
        finally:
            # close is idempotent and keeps the native lifeline on uncertainty.
            result = await job.close()
            await asyncio.gather(*readers)
            report["outerJobsSettled"] = (
                result.active_processes == 0 and job.tree_empty and job.io_finalized)
        observed = root / ("controls.json" if phase == "controls" else "host-" + phase + ".json")
        observation = json.loads(observed.read_text(encoding="utf-8"))
        if phase == "controls":
            report["nativeControls"] = observation
            assert report["outerJobsSettled"] and result.payload_returncode == 0
            assert observation["status"] == "PASS" and observation["selection"]["status"] == "PASS"
            continue
        report.setdefault("hosts", []).append(observation)
        assert report["outerJobsSettled"] and result.payload_returncode == 0
        assert observation["status"] == "PASS" and observation["innerJobsSettled"]
    observations = list(root.glob("installed-worker-*.json"))
    assert len(observations) == 2
    report["workers"] = [json.loads(path.read_text(encoding="utf-8")) for path in observations]
    reference = report["hosts"][0]["installed"]
    assert report["nativeControls"]["installed"] == reference
    assert report["hosts"][1]["installed"] == reference
    assert all(row == reference for row in report["workers"])
    calls = [json.loads(line) for line in (root / "provider-calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len({row["pid"] for row in calls}) == 2
    report["checks"]["twoFreshHostsAndInstalledWorkerGraphs"] = "PASS"
    report["checks"]["unexpectedPythonNetworkAttempts"] = sum(
        len(path.read_text(encoding="utf-8").splitlines()) for path in root.glob("network-*.jsonl"))
    assert report["checks"]["unexpectedPythonNetworkAttempts"] == 0


def run(wheel, receipt):
    require_ci()
    root = None
    created = False
    retained = json.loads(receipt.read_text(encoding="utf-8"))["candidate"]
    assert set(retained) == {"head", "tree"} and all(
        re.fullmatch(r"[0-9a-f]{40,64}", value) for value in retained.values())
    report = dict(candidate=retained, status="FAIL", stage="private_fixture_creation", checks={},
                  qualification="private-adapter-candidate-only", publicAcceptance=False,
                  runtimePreparation="separate-private-project-api-plus-preinstalled-worker",
                  outerJobsSettled=True,
                  python=list(sys.version_info[:3]), windowsBuild=sys.getwindowsversion().build)
    try:
        root = canonical_temp(os.environ["TEMP"]) / ("ut-" + uuid.uuid4().hex[:12])
        assert not root.exists()
        private_directory(root, root)
        created = True
        sanitize(root)
        for folder in ("home", "home/roaming", "home/local", "app", "workspace café 日本語", "shared"):
            private_directory(root / folder, root)
        asyncio.run(hosts(root, wheel, report))
        report.update(status="PASS", stage="complete")
    except BaseException as exc:
        failure(report, exc)
    finally:
        # A caught attempt is still a failed policy check. Publish counts only.
        attempts = sum(len(path.read_text(encoding="utf-8").splitlines())
                       for path in root.glob("network-*.jsonl")) if created else 0
        report["checks"]["unexpectedPythonNetworkAttempts"] = attempts
        if attempts:
            report["status"] = "FAIL"
        if created and root.exists() and report["outerJobsSettled"]:
            try:
                shutil.rmtree(root)
                report["scratchRemoved"] = True
            except OSError:
                report["scratchRemoved"] = False
                report["status"] = "FAIL"
        elif root is not None and root.exists():
            report["scratchRemoved"] = False
            report["status"] = "FAIL"
        try:
            validate_public(report)
        except (AssertionError, TypeError, KeyError, ValueError):
            # Never upload a malformed or free-form child record.
            report = {"candidate": retained, "status": "FAIL", "publicAcceptance": False}
        write_json(receipt, report)
    return 0 if report["status"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--requirements", type=Path)
    parser.add_argument("--run", type=Path)
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--host", type=Path)
    parser.add_argument("--controls", type=Path)
    parser.add_argument("--phase", choices=("first", "resume"))
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    if args.candidate:
        write_json(args.receipt, {"candidate": candidate(args.candidate), "status": "NOT_RUN",
                                 "publicAcceptance": False})
    elif args.requirements:
        print("\n".join(requirements(args.requirements)))
    elif args.worker:
        asyncio.run(worker_entry(args.worker.resolve()))
    elif args.run:
        return run(args.run.resolve(), args.receipt.resolve())
    elif args.host:
        assert args.phase is not None
        return host(args.host.resolve(), args.phase)
    elif args.controls:
        return native_controls(args.controls.resolve())
    else:
        parser.error("Choose candidate, requirements, worker or run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())