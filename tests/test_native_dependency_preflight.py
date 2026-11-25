"""Preparation guards for the exact native-provider source/API mismatch."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "native_dependency_preflight", ROOT / "scripts/native_dependency_preflight.py")
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


def selection(commit="c8104f327b9286bff49c589f804969a5a9d0ba9d"):
    url = "https://github.com/microsoft/amplifier-module-provider-openai"
    installed = {"version": "1.0.0", "source": {
        "url": url, "vcs_info": {"vcs": "git", "commit_id": commit, "requested_revision": "main"}}}
    locked = {"version": "1.0.0", "source": {"git": url + "?rev=main#c8104f327b9286bff49c589f804969a5a9d0ba9d"}}
    return installed, locked


@pytest.mark.parametrize("change", ["commit", "url", "requested_revision", "version", "editable"])
def test_preflight_rejects_unlocked_provider_even_with_same_distribution_version(change):
    installed, locked = selection()
    if change == "commit":
        installed["source"]["vcs_info"]["commit_id"] = "2b35fa334fb2fb2c5c948842e8c7bd691ce5e834"
    elif change == "url":
        installed["source"]["url"] += "-fork"
    elif change == "requested_revision":
        installed["source"]["vcs_info"]["requested_revision"] = "other"
    elif change == "version":
        installed["version"] = "0.9.0"
    else:
        installed["source"] = {"url": "file:///tmp/provider", "dir_info": {"editable": True}}
    with pytest.raises(RuntimeError, match="differs from uv.lock"):
        preflight.verify_locked("amplifier-module-provider-openai", installed, locked)


def test_preflight_accepts_locked_provider_and_registry_dependencies():
    preflight.verify_locked("amplifier-module-provider-openai", *selection())
    preflight.verify_locked("openai", {"version": "3.36.1", "source": None},
                            {"version": "3.36.1", "source": {"registry": "https://pypi.org/simple"}})
    with pytest.raises(RuntimeError, match="registry source"):
        preflight.verify_locked("openai", {"version": "3.36.1", "source": {"url": "file:///tmp/sdk"}},
                                {"version": "3.36.1", "source": {"registry": "https://pypi.org/simple"}})


def test_missing_policy_export_fails_and_preserves_resolved_identity(tmp_path, monkeypatch):
    installed, locked = selection()
    lock = tmp_path / "uv.lock"
    lock.write_text('[[package]]\nname = "amplifier-module-provider-openai"\nversion = "1.0.0"\n'
                    f'source = {{ git = "{locked["source"]["git"]}" }}\n')
    monkeypatch.setattr(preflight, "DISTRIBUTIONS", ("amplifier-module-provider-openai",))
    monkeypatch.setattr(preflight, "LOCKED", ("amplifier-module-provider-openai",))
    monkeypatch.setattr(preflight, "EXPORTS", {
        "amplifier_module_provider_openai._capabilities": ("get_request_policy",)})
    monkeypatch.setattr(preflight.metadata, "distribution", lambda name: SimpleNamespace(
        version="1.0.0", read_text=lambda path: json.dumps(installed["source"])))
    monkeypatch.setattr(preflight.importlib, "import_module", lambda name: SimpleNamespace(__file__=__file__))
    output = tmp_path / "evidence.json"
    with pytest.raises(AttributeError, match="get_request_policy"):
        preflight.main(["--root", str(tmp_path), "--output", str(output)])
    record = json.loads(output.read_text())
    assert not record["passed"] and record["lockedSelection"]
    assert record["errorType"] == "AttributeError"
    assert record["distributions"]["amplifier-module-provider-openai"] == installed
    assert "requiredExports" not in record


def test_preflight_checks_all_native_binding_and_admission_exports():
    assert preflight.EXPORTS["amplifier_module_provider_openai._capabilities"] == ("get_request_policy",)
    assert preflight.EXPORTS["amplifier_module_provider_openai.native"] == (
        "NativeResponsesProvider", "NativeSendAdmission", "NativeOutboundEnvelope", "NATIVE_REQUEST")
    assert preflight.EXPORTS["amplifier_module_loop_live.scope"] == ("LIVE_OWNER", "NATIVE_REQUEST")