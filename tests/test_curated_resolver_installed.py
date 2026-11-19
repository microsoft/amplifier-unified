"""Opt-in, offline installed Unified -> canonical curated resolver CLI checks.

Run ONLY inside the parent's already prepared container, using
its supplied interpreter ($PYTHON, an absolute path) and non-editable
Unified/Foundation installations:

  UNIFIED_CURATED_RESOLVER=1 UNIFIED_BEHAVIOR_REPOS=/opt/defaults/repos \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$PYTHON" -I -B -m pytest \
  --noconftest --import-mode=importlib -p no:cacheprovider -c /dev/null -q -s \
  /workspace/tests/test_curated_resolver_installed.py

The interpreter also needs pytest and Click; this file never installs anything.
Unset opt-in skips; an incomplete opted-in environment FAILS. The helper must
already exist at the exact prepared skills-repository path below. No uv, source
sys.path injection, copied helper, provider construction, or inference is used.

The producer is real SetupManager.perform("providers.list", ...) reading real
synthetic settings. Only its five allowlisted scalar fields cross into the real
helper subprocess. This test-owned projection is not an app action adapter.
Inventories and credentials are synthetic; "supplied" never means live evidence.
The parent-process audit guard is NOT inherited by python -I children. Explicit
rosters/inventories, isolated homes, and a failing standalone-CLI sentinel check
the intended child path. This fixture does not establish child network
confinement; a parent that claims that boundary must independently verify it.
There is no resolver permission API here: permission refusal, authenticated app
admission, paid-provider transport, and live inventory are NOT qualified.
"""
from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest


CHECKOUT = Path(__file__).resolve().parents[1]
HELPER_RELATIVE = Path(
    "amplifier-bundle-skills/skills/second-opinion/scripts/resolve_provider.py"
)
PRIMARY = "fixture-primary"
SECONDARY = "fixture-secondary"
DISABLED = "fixture-disabled"
SENSITIVE_INPUT = "SYNTHETIC-DO-NOT-REFLECT-SELECTOR"
FAKE_SECRETS = ("fixture-key-a", "fixture-key-b", "fixture-key-c")
ROSTER_FIELDS = {"id", "provider_type", "enabled", "default_model", "priority"}
OMIT = object()
ERRORS = {
    "unknown_id": "The requested provider id is not configured.",
    "unknown_provider": "No enabled provider has the requested type.",
    "disabled_id": "The requested provider id is disabled.",
    "duplicate_id": "Provider configuration contains duplicate provider ids.",
    "duplicate_reviewer": "Reviewer resolves to a duplicate provider and model.",
    "invalid_reviewer": "Reviewer entry has an invalid selector.",
    "invalid_providers": "Host provider metadata has an invalid schema.",
    "malformed_schema": "Provider configuration has an invalid schema.",
    "invalid_inventory": "Model inventory has an invalid schema.",
    "invalid_reviewers": "Reviewer list must be a non-empty array.",
    "empty_reviewers": "Reviewer list must not be empty.",
}


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _evidence(**value):
    print("curated-resolver " + _json(value))


def _installed(distribution, modules):
    """Check loaded module origins AND their actual installed wheel RECORD."""
    dist = importlib.metadata.distribution(distribution)
    assert dist.read_text("WHEEL") and dist.read_text("RECORD"), distribution
    direct = json.loads(dist.read_text("direct_url.json") or "{}")
    assert not direct.get("dir_info", {}).get("editable"), distribution
    entries = {str(entry): entry for entry in dist.files or ()}
    proof = {}
    loaded = {}
    for name in modules:
        spec = importlib.util.find_spec(name)
        assert spec is not None and spec.origin, f"Missing installed {name}"
        relative = name.replace(".", "/")
        relative += "/__init__.py" if spec.submodule_search_locations is not None else ".py"
        assert relative in entries, f"{name} is not recorded in {distribution}"
        entry = entries[relative]
        expected = Path(dist.locate_file(entry)).resolve(strict=True)
        assert not expected.is_relative_to(CHECKOUT), f"{name} resolves inside checkout"
        assert Path(spec.origin).resolve(strict=True) == expected, name
        assert entry.hash and entry.hash.mode == "sha256", relative
        data = expected.read_bytes()
        assert len(data) == entry.size, relative
        assert base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=") == entry.hash.value, relative
        module = importlib.import_module(name)
        assert Path(module.__file__).resolve(strict=True) == expected, name
        loaded[name] = module
        proof[name] = {"origin": str(expected), "sha256": _sha(data)}
    _evidence(distribution=distribution, version=dist.version, installed=proof)
    return loaded


def _settings_rows():
    # Deliberately not in priority order; all three share a provider type.
    return [
        {
            "id": identity, "module": "provider-openai", "enabled": enabled,
            "config": {
                "default_model": model, "priority": priority, "api_key": secret,
                "headers": {"Authorization": SENSITIVE_INPUT},
                "fixture_extra": {"not_roster_metadata": True},
            },
        }
        for identity, enabled, model, priority, secret in (
            (SECONDARY, True, "fixture-sage-4", 40, FAKE_SECRETS[1]),
            (DISABLED, False, "fixture-inert-1", 1, FAKE_SECRETS[2]),
            (PRIMARY, True, "fixture-nova-6", 20, FAKE_SECRETS[0]),
        )
    ]


def _inventories():
    return {
        row["id"]: {"models": [
            {"id": model, "source": "supplied", "availability": "unknown"}
            for model in (row["config"]["default_model"], "fixture-shared-9")
        ]}
        for row in _settings_rows()
    }


class _Resolver:
    def __init__(self, helper, workspace, manager, sentinel, guard):
        self.helper = helper
        self.workspace = workspace
        self.manager = manager
        self.sentinel = sentinel
        self.guard = guard

    def roster(self, settings_rows):
        """Real settings -> real public response -> explicit scalar projection."""
        settings = self.manager.store.path(str(self.workspace), "global")
        settings.parent.mkdir(parents=True, exist_ok=True)
        # JSON is a YAML subset. Never call providers.save or write real keys.
        settings.write_text(
            _json({"config": {"providers": settings_rows}}) + "\n", encoding="utf-8"
        )
        settings.chmod(0o600)
        response = asyncio.run(self.manager.perform("providers.list", {
            "workspace": str(self.workspace), "scope": "global",
        }))
        assert response["providersWorkspace"] == str(self.workspace)
        assert type(response["providersLoadedAt"]) in (int, float)
        serialized = _json(response)
        for secret in (*FAKE_SECRETS, SENSITIVE_INPUT):
            assert secret not in serialized, "Producer reflected a synthetic secret"
        roster = []
        for row in response["providers"]:
            assert row["config"]["api_key"] == "[REDACTED]"
            assert row["config"]["headers"]["Authorization"] == "[REDACTED]"
            assert "credential" in row and "fixture_extra" in row["config"]
            # No guessed model/default/active/scope fields or ambient defaults.
            item = {
                "id": row["id"],
                "provider_type": row["module"].removeprefix("provider-"),
                "enabled": row["enabled"],
                "default_model": row["config"]["default_model"],
                "priority": row["config"]["priority"],
            }
            assert set(item) == ROSTER_FIELDS
            assert type(item["enabled"]) is bool and type(item["priority"]) is int
            assert all(type(item[key]) is str for key in ("id", "provider_type", "default_model"))
            assert all(type(value) in (str, int, bool) for value in item.values())
            roster.append(item)
        assert not self.sentinel.exists(), "Producer invoked standalone amplifier"
        return roster

    def run(self, case, roster, options, status, *, models=OMIT):
        """Launch the actual canonical Click entry, not an imported/faked CLI."""
        raw_roster = roster if isinstance(roster, str) else _json(roster)
        # Keep the supplied venv executable spelling: resolving its symlink can
        # accidentally select the base interpreter instead of the prepared venv.
        command = [
            sys.executable, "-I", "-B", str(self.helper),
            "--providers-json", raw_roster, "--cwd", str(self.workspace),
        ]
        if models is not OMIT:
            command += ["--models-json", models if isinstance(models, str) else _json(models)]
        command += options
        assert not self.sentinel.exists(), "Standalone amplifier sentinel already exists"
        before = self.guard["admitted"]
        self.guard["command"] = command
        try:
            result = subprocess.run(
                command, cwd=self.workspace, env=dict(os.environ),
                capture_output=True, text=True, encoding="utf-8", errors="strict",
                timeout=20, check=False,
            )
        finally:
            self.guard["command"] = None
            assert not self.sentinel.exists(), "Resolver invoked standalone amplifier"
        _evidence(
            case=case, command=command[:4],
            flags=[arg for arg in command[4:] if arg.startswith("--")],
            exit_status=result.returncode, stdout_sha256=_sha(result.stdout.encode()),
        )
        assert self.guard["admitted"] == before + 1
        for secret in (*FAKE_SECRETS, SENSITIVE_INPUT):
            assert secret not in result.stdout + result.stderr, "Resolver reflected a synthetic secret"
        assert result.returncode == status, f"{case}: expected exit {status}, got {result.returncode}"
        assert result.stderr == "", f"{case}: resolver must emit safe JSON, not a traceback"
        payload = json.loads(result.stdout)
        assert isinstance(payload, dict), case
        # One JSON document, with no log lines before/after it.
        assert len(result.stdout.strip().splitlines()) == 1, case
        return payload


@pytest.fixture(scope="module")
def curated_resolver(tmp_path_factory):
    opt_in = os.environ.get("UNIFIED_CURATED_RESOLVER")
    if opt_in is None:
        pytest.skip("Requires the parent's prepared guest and UNIFIED_CURATED_RESOLVER=1")
    assert opt_in == "1", "UNIFIED_CURATED_RESOLVER must be exactly 1"
    assert Path("/.dockerenv").is_file() or Path("/run/systemd/container").is_file(), (
        "Installed resolver qualification is container-only; do not run on the host"
    )
    assert sys.flags.isolated and not sys.flags.optimize, "Use supplied python -I without -O"
    assert not Path(sys.prefix).resolve().is_relative_to(CHECKOUT), (
        "Install the qualification environment outside the source checkout"
    )
    location = os.environ.get("UNIFIED_BEHAVIOR_REPOS")
    assert location, "Set UNIFIED_BEHAVIOR_REPOS=/opt/defaults/repos; no implicit host lookup"
    repos = Path(location).resolve(strict=True)
    helper = repos / HELPER_RELATIVE
    assert helper.is_file(), f"Missing canonical prepared helper: {helper}"
    assert helper.resolve(strict=True) == helper, "Helper must not escape its exact prepared path"
    helper_hashes = {
        name: _sha(helper.with_name(name).read_bytes())
        for name in ("resolve_provider.py", "provider_discovery.py", "provider_resolution.py")
    }
    _evidence(
        helper=str(helper), helper_sha256=helper_hashes,
        test_sha256=_sha(Path(__file__).read_bytes()),
        child_network_boundary="not qualified by this fixture; no inherited audit hook",
        permission_refusal="not qualified: no resolver permission API",
    )
    root = tmp_path_factory.mktemp("curated-resolver")
    workspace = root / "workspace"
    workspace.mkdir()
    sentinel = root / "standalone-amplifier-was-run"
    bin_dir = root / "bin"
    bin_dir.mkdir()
    spy = bin_dir / "amplifier"
    spy.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' invoked > {shlex.quote(str(sentinel))}\n"
        f"printf '%s\\n' {shlex.quote(SENSITIVE_INPUT)} >&2\n"
        "exit 97\n",
        encoding="utf-8",
    )
    spy.chmod(0o700)
    guard = {"active": True, "command": None, "admitted": 0, "forbidden": []}

    def audit(event, args):
        if not guard["active"]:
            return
        if event == "subprocess.Popen":
            if guard["command"] is not None and list(args[1]) == guard["command"]:
                guard["admitted"] += 1
                return
        elif event == "os.posix_spawn":
            if (guard["command"] is not None and list(args[1]) == guard["command"]
                    and args[0] == sys.executable):
                return
        elif event not in {
            "socket.connect", "socket.sendto", "socket.getaddrinfo",
            "socket.gethostbyname", "socket.gethostbyaddr", "os.system",
            "os.posix_spawn", "os.exec", "os.fork", "os.forkpty",
        }:
            return
        guard["forbidden"].append(event)
        raise RuntimeError(f"Offline producer fixture forbids {event}")

    with pytest.MonkeyPatch.context() as patch:
        # Allowlist, not a finite denylist of known credential variable names.
        for name in tuple(os.environ):
            patch.delenv(name)
        for name, relative in {
            "HOME": "home", "AMPLIFIER_HOME": "home/.amplifier",
            "AMPLIFIER_WEB_HOME": "app", "AMPLIFIER_SESSION_STATE_HOME": "sessions",
            "AMPLIFIER_TERMINAL_HOME": "terminal", "XDG_CONFIG_HOME": "config",
            "XDG_CACHE_HOME": "cache", "XDG_DATA_HOME": "data",
        }.items():
            directory = root / relative
            directory.mkdir(parents=True, exist_ok=True)
            patch.setenv(name, str(directory))
        patch.setenv("PATH", str(bin_dir))
        patch.setenv("LANG", "C.UTF-8")
        patch.setenv("LC_ALL", "C.UTF-8")
        patch.chdir(workspace)
        sys.addaudithook(audit)
        try:
            modules = _installed("amplifier-unified", (
                "amplifier_web", "amplifier_web.setup", "amplifier_web.host.config",
                "amplifier_web.preferences",
            ))
            _installed("amplifier-foundation", (
                "amplifier_foundation", "amplifier_foundation.settings",
            ))
            manager = modules["amplifier_web.setup"].SetupManager(root / "app")
            yield _Resolver(helper, workspace, manager, sentinel, guard)
        finally:
            # Audit hooks cannot be removed; deactivate this one for later tests.
            guard["active"] = False
            assert not sentinel.exists(), "Standalone amplifier must never run"
            assert guard["forbidden"] == [], "Producer attempted forbidden I/O"
            assert helper_hashes == {
                name: _sha(helper.with_name(name).read_bytes()) for name in helper_hashes
            }, "Canonical helper bytes changed during qualification"


@pytest.fixture
def host_roster(curated_resolver):
    roster = curated_resolver.roster(_settings_rows())
    assert roster == [
        {"id": DISABLED, "provider_type": "openai", "enabled": False,
         "default_model": "fixture-inert-1", "priority": 1},
        {"id": PRIMARY, "provider_type": "openai", "enabled": True,
         "default_model": "fixture-nova-6", "priority": 20},
        {"id": SECONDARY, "provider_type": "openai", "enabled": True,
         "default_model": "fixture-sage-4", "priority": 40},
    ]
    return roster


def _error(code, *, index=None, message=None):
    result = {"ok": False, "code": code, "message": message or ERRORS[code]}
    if index is not None:
        result["index"] = index
    return result


def _assert_batch(payload, count, total):
    assert set(payload) == {
        "ok", "concurrency", "resolved_count", "reviewers",
        "discovery_status", "inventory_unavailable_provider_ids",
    }
    assert payload["ok"] is (count == total)
    assert payload["resolved_count"] == count
    assert payload["concurrency"] == 2
    assert payload["discovery_status"] == "supplied"
    assert payload["inventory_unavailable_provider_ids"] == []
    assert len(payload["reviewers"]) == total
    assert [row["index"] for row in payload["reviewers"]] == list(range(total))


def _assert_success(row, *, identity, model, default, resolution):
    assert row["ok"] is True
    assert row["provider_id"] == identity and row["provider_type"] == "openai"
    assert row["model"] == model and row["configured_default_model"] == default
    assert row["resolution"] == resolution
    assert row["config_scope"] == "host"  # Helper's disclosure, not invented host scope.
    assert row["settings_checked"] is True
    assert row["mounted_checked"] is False and row["execution_verified"] is False
    assert row["endpoint_selection"] == "configured_priority"
    assert row["inventory_source"] == "supplied"
    assert row["model_availability"] == "unverified"
    assert row["discovery_status"] == "supplied"
    assert row["inventory_unavailable_provider_ids"] == []
    assert "Model availability is unverified; inventory discovery is not execution proof." in row["disclosure"]


def _batch(resolver, case, roster, selectors, status):
    return resolver.run(case, roster, [
        "--reviewers-json", _json(selectors), "--concurrency", "2",
    ], status, models=_inventories())


def test_ordered_two_endpoint_success_and_configured_defaults(curated_resolver, host_roster):
    result = _batch(curated_resolver, "ordered-two-endpoint-success", host_roster, [
        {"id": SECONDARY, "model": "fixture-sage-4"}, {"provider": "openai"},
    ], 0)
    _assert_batch(result, 2, 2)
    _assert_success(result["reviewers"][0], identity=SECONDARY, model="fixture-sage-4",
                    default="fixture-sage-4", resolution="model_match")
    _assert_success(result["reviewers"][1], identity=PRIMARY, model="fixture-nova-6",
                    default="fixture-nova-6", resolution="provider_default")


def test_priority_and_disabled_filter_with_same_supplied_model(curated_resolver, host_roster):
    result = _batch(curated_resolver, "priority-not-list-order-or-disabled", host_roster, [
        {"provider": "openai", "model": "fixture-shared-9"},
        {"id": SECONDARY, "model": "fixture-shared-9"},
    ], 0)
    _assert_batch(result, 2, 2)
    for row, identity, default in zip(result["reviewers"], (PRIMARY, SECONDARY),
                                      ("fixture-nova-6", "fixture-sage-4"), strict=True):
        _assert_success(row, identity=identity, model="fixture-shared-9",
                        default=default, resolution="model_match")


def test_mixed_valid_unknown_disabled_duplicate_exit_one(curated_resolver, host_roster):
    result = _batch(curated_resolver, "mixed-exit-one", host_roster, [
        {"id": SECONDARY}, {"id": SENSITIVE_INPUT}, {"id": DISABLED}, {"id": SECONDARY},
    ], 1)
    _assert_batch(result, 1, 4)
    _assert_success(result["reviewers"][0], identity=SECONDARY, model="fixture-sage-4",
                    default="fixture-sage-4", resolution="exact_id_default")
    assert result["reviewers"][1:] == [
        _error("unknown_id", index=1), _error("disabled_id", index=2),
        _error("duplicate_reviewer", index=3),
    ]


def test_no_success_exit_two_preserves_all_error_rows(curated_resolver, host_roster):
    result = _batch(curated_resolver, "no-success-exit-two", host_roster, [
        {"id": SENSITIVE_INPUT}, {"id": DISABLED}, None,
    ], 2)
    _assert_batch(result, 0, 3)
    assert result["reviewers"] == [
        _error("unknown_id", index=0), _error("disabled_id", index=1),
        _error("invalid_reviewer", index=2),
    ]


def test_provider_listing_keeps_only_scalar_allowlist(curated_resolver, host_roster):
    result = curated_resolver.run(
        "safe-provider-list", host_roster, ["--list-providers"], 0, models={}
    )
    assert result == {
        "ok": True,
        "providers": [{**row, "priority": str(row["priority"])} for row in host_roster],
    }


def test_real_empty_provider_settings_never_fall_back_to_cli(curated_resolver):
    roster = curated_resolver.roster([])
    assert roster == []
    result = _batch(curated_resolver, "empty-host-roster", roster, [
        {"id": PRIMARY}, {"provider": "openai"},
    ], 2)
    _assert_batch(result, 0, 2)
    assert result["reviewers"] == [
        _error("unknown_id", index=0), _error("unknown_provider", index=1),
    ]
    assert curated_resolver.run(
        "empty-provider-list", roster, ["--list-providers"], 0, models={}
    ) == {"ok": True, "providers": []}


@pytest.mark.parametrize("kind,code", [
    ("invalid-json", "invalid_providers"),
    ("null", "invalid_providers"),
    ("not-array", "invalid_providers"),
    ("not-object-row", "invalid_providers"),
    ("extra-secret", "invalid_providers"),
    ("missing-default", "invalid_providers"),
    ("nested-id", "invalid_providers"),
    ("nested-provider-type", "malformed_schema"),
    ("nested-default", "malformed_schema"),
    ("nested-priority", "invalid_providers"),
    ("boolean-priority", "invalid_providers"),
    ("nonboolean-enabled", "malformed_schema"),
    ("duplicate-id", "duplicate_id"),
])
def test_malformed_host_rosters_safe_json_never_cli(curated_resolver, host_roster, kind, code):
    roster = copy.deepcopy(host_roster)
    message = None
    if kind == "invalid-json":
        roster = "{" + SENSITIVE_INPUT
        message = "Structured input must be valid JSON."
    elif kind == "null":
        roster = "null"
    elif kind == "not-array":
        roster = {"providers": roster, "secret": SENSITIVE_INPUT}
    elif kind == "not-object-row":
        roster = [SENSITIVE_INPUT]
    elif kind == "extra-secret":
        roster[1]["api_key"] = SENSITIVE_INPUT
    elif kind == "missing-default":
        del roster[1]["default_model"]
    elif kind.startswith("nested-"):
        field = {"nested-id": "id", "nested-provider-type": "provider_type",
                 "nested-default": "default_model", "nested-priority": "priority"}[kind]
        roster[1][field] = {"secret": SENSITIVE_INPUT}
    elif kind == "boolean-priority":
        roster[1]["priority"] = True
    elif kind == "nonboolean-enabled":
        roster[1]["enabled"] = SENSITIVE_INPUT
    elif kind == "duplicate-id":
        roster.append(copy.deepcopy(roster[1]))
    result = _batch(curated_resolver, "roster-" + kind, roster, [{"id": PRIMARY}], 2)
    assert result == _error(code, message=message)


@pytest.mark.parametrize("raw,code,message", [
    ("{" + SENSITIVE_INPUT, "invalid_reviewers", "Structured input must be valid JSON."),
    ("null", "invalid_reviewers", None),
    ("{}", "invalid_reviewers", None),
    ("[]", "empty_reviewers", None),
], ids=["invalid-json", "null", "object", "empty-array"])
def test_malformed_or_empty_batch_safe_json(curated_resolver, host_roster, raw, code, message):
    result = curated_resolver.run(
        "invalid-batch-" + code, host_roster, ["--reviewers-json", raw], 2,
        models=_inventories(),
    )
    assert result == _error(code, message=message)


def test_list_models_unknown_inventory_is_unavailable_not_known_empty(curated_resolver, host_roster):
    # Intentionally omit models-json: an explicit host roster must not discover
    # another application's inventory/configuration via standalone amplifier.
    unknown = curated_resolver.run(
        "inventory-unknown", host_roster, ["--list-models", PRIMARY], 0
    )
    assert unknown == {
        "ok": True, "provider_id": PRIMARY, "models": [],
        "discovery_status": "unavailable", "inventory_unavailable_provider_ids": [PRIMARY],
    }
    known_empty = curated_resolver.run(
        "inventory-known-empty", host_roster, ["--list-models", PRIMARY], 0,
        models={PRIMARY: {"models": []}},
    )
    assert known_empty == {
        "ok": True, "provider_id": PRIMARY, "models": [],
        "discovery_status": "supplied", "inventory_unavailable_provider_ids": [],
    }


def test_list_models_retains_supplied_source_and_availability(curated_resolver, host_roster):
    result = curated_resolver.run(
        "inventory-supplied", host_roster, ["--list-models", SECONDARY], 0,
        models=_inventories(),
    )
    assert result == {
        "ok": True, "provider_id": SECONDARY,
        "models": _inventories()[SECONDARY]["models"],
        "discovery_status": "supplied", "inventory_unavailable_provider_ids": [],
    }


@pytest.mark.parametrize("kind", [
    "invalid-json", "not-mapping", "models-not-array", "record-not-object",
    "source-list", "source-object", "source-invalid-string",
    "availability-list", "availability-object", "availability-invalid-string",
])
def test_list_models_malformed_inventory_is_safe_json(curated_resolver, host_roster, kind):
    models = _inventories()
    message = None
    if kind == "invalid-json":
        models = "{" + SENSITIVE_INPUT
        message = "Structured input must be valid JSON."
    elif kind == "not-mapping":
        models = [SENSITIVE_INPUT]
    elif kind == "models-not-array":
        models[PRIMARY]["models"] = {"secret": SENSITIVE_INPUT}
    elif kind == "record-not-object":
        models[PRIMARY]["models"] = [None]
    else:
        field, variant = kind.split("-", 1)
        models[PRIMARY]["models"][0][field] = {
            "list": [SENSITIVE_INPUT], "object": {"secret": SENSITIVE_INPUT},
            "invalid-string": SENSITIVE_INPUT,
        }[variant]
    # No xfail, coercion, or local canonical fix: a helper TypeError/traceback is
    # a real upstream failure for the parent to repair in its owning repository.
    result = curated_resolver.run(
        "inventory-" + kind, host_roster, ["--list-models", PRIMARY], 1, models=models
    )
    assert result == _error("invalid_inventory", message=message)
