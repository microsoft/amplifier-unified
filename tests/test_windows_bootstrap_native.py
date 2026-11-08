"""Build-boundary tests; actual compiled Windows/clean-user checks are separate."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SPEC = importlib.util.spec_from_file_location("native_bootstrap_builder", SCRIPTS / "windows_bootstrap_native.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def test_storage_is_exact_existing_owner_and_sources_are_complete(tmp_path):
    result = builder.prepare(SCRIPTS, tmp_path / "native")
    body = re.findall(r"Add-Type -TypeDefinition @'\n(.*?)\n'@", (SCRIPTS / "install.ps1").read_text(), re.S)
    assert (tmp_path / "native/InstallStorage.cs").read_bytes() == (body[0] + "\n").encode()
    assert (tmp_path / "native/NativeInstaller.cs").read_bytes() == (SCRIPTS / "windows_bootstrap_native.cs").read_bytes()
    for name, item in result["sources"].items():
        data = (tmp_path / "native" / name).read_bytes()
        assert item == {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    assert not result["compiled"] and not result["signed"] and not result["published"]
    assert result["powershellExecutionOnTarget"] is False
    assert json.loads((tmp_path / "native/build-inputs.json").read_text()) == result


def test_create_new_does_not_replace_prior_artifacts(tmp_path):
    target = tmp_path / "existing"
    target.mkdir()
    (target / "keep").write_bytes(b"prior evidence")
    with pytest.raises(ValueError, match="absent"):
        builder.prepare(SCRIPTS, target)
    assert (target / "keep").read_bytes() == b"prior evidence"
    assert list(target.iterdir()) == [target / "keep"]


@pytest.mark.parametrize("mutation", ["storage", "helper", "license"])
def test_missing_canonical_inputs_fail_before_output(tmp_path, mutation):
    source = tmp_path / "source"
    source.mkdir()
    original = (SCRIPTS / "install.ps1").read_text()
    if mutation == "storage":
        original = original.replace("public sealed class InstallStorage", "public sealed class Unrelated")
    elif mutation == "helper":
        original = original.replace("sha256 = '0cdc5b", "digest = '0cdc5b")
    else:
        original = original.replace("'LICENSE-MIT' =", "'LICENSE-OTHER' =")
    (source / "install.ps1").write_text(original)
    with pytest.raises(ValueError):
        builder.prepare(source, tmp_path / "must-not-exist")
    assert not (tmp_path / "must-not-exist").exists()


def test_pins_come_from_reviewed_capsule_and_helper_sources():
    pins = builder.sources(SCRIPTS)["NativePins.cs"].decode()
    assert 'Source="d9b26e7bb3c5270d377520b82e498111384e931d"' in pins
    assert 'Version="0.65.0"' in pins
    assert 'UvVersion="0.13.7"' in pins
    assert 'UvHash="0cdc5b38fe5a8168ed6a25814f5ba43c21d313dd3187314fccb37b7cec46e021"' in pins
    assert 'GitHash="e3bf78351fe777fba47ddee5950082f8a98bb0f02604fdc7ba3ee2cb68ffa877"' in pins
    assert "[REDACTED]" not in pins


def test_launcher_receipt_is_published_after_audit_before_launcher_reads_it():
    source = (SCRIPTS / "windows_bootstrap_native.cs").read_text()
    audit = source.index('checker,"--audit-source"')
    receipt = source.index('PrivateText(Path.Combine(Storage.Root,"prototype.json")')
    check = source.index('launcher,"--check"')
    shortcut = source.index("var shortcut=Shortcut(")
    assert audit < receipt < check < shortcut
