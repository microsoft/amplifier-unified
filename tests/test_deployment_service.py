from pathlib import Path
import shlex
import sys

import pytest

from amplifier_web import deployment_service
from amplifier_web.cli import _parse


def test_install_refuses_unmanaged_unit(tmp_path, monkeypatch):
    unit = tmp_path / "amplifier-unified.service"
    unit.write_text("[Service]\n")
    monkeypatch.setattr(deployment_service, "unit_path", lambda: unit)
    with pytest.raises(RuntimeError, match="unmanaged"):
        deployment_service.install(tmp_path / "data")


def test_install_replace_backs_up_unmanaged_unit_only_when_explicit(tmp_path, monkeypatch):
    unit = tmp_path / "amplifier-unified.service"
    unit.write_text("[Service]\nExecStart=/old/host\n")
    monkeypatch.setattr(deployment_service, "unit_path", lambda: unit)
    monkeypatch.setattr(deployment_service, "_systemctl", lambda *args, **kwargs: None)
    monkeypatch.setattr(deployment_service.shutil, "which", lambda name: "/usr/bin/amplifier-unified")
    deployment_service.install(tmp_path / "data", replace=True)
    backups = list(tmp_path.glob("amplifier-unified.service.backup-*"))
    assert len(backups) == 1
    assert backups[0].read_text() == "[Service]\nExecStart=/old/host\n"
    assert deployment_service.MARKER in unit.read_text()


def test_generated_exec_start_uses_top_level_options_before_serve(tmp_path, monkeypatch):
    unit = tmp_path / "amplifier-unified.service"
    monkeypatch.setattr(deployment_service, "unit_path", lambda: unit)
    monkeypatch.setattr(deployment_service, "_systemctl", lambda *args, **kwargs: None)
    monkeypatch.setattr(deployment_service.shutil, "which", lambda name: "/usr/bin/amplifier-unified" if name == "amplifier-unified" else None)
    data_dir, workspace = tmp_path / "data", tmp_path / "workspace"
    deployment_service.install(data_dir, workspace)
    exec_start = next(line.removeprefix("ExecStart=") for line in unit.read_text().splitlines()
                      if line.startswith("ExecStart="))
    command = shlex.split(exec_start)
    assert command == ["/usr/bin/amplifier-unified", "--no-open", "--data-dir", str(data_dir),
                       "--workspace", str(workspace), "serve"]
    monkeypatch.setattr(sys, "argv", command)
    args = _parse()
    assert args.command == "serve"
    assert args.data_dir == str(data_dir)
    assert args.workspace == str(workspace)


def test_install_replace_creates_private_timestamped_backup(tmp_path, monkeypatch):
    unit = tmp_path / "amplifier-unified.service"
    monkeypatch.setattr(deployment_service, "unit_path", lambda: unit)
    monkeypatch.setattr(deployment_service, "_systemctl", lambda *args, **kwargs: None)
    monkeypatch.setattr(deployment_service.shutil, "which", lambda name: "/usr/bin/amplifier-unified")
    deployment_service.install(tmp_path / "data")
    with pytest.raises(RuntimeError, match="--replace"):
        deployment_service.install(tmp_path / "data")
    deployment_service.install(tmp_path / "data", replace=True)
    backups = list(tmp_path.glob("amplifier-unified.service.backup-*"))
    assert len(backups) == 1
    assert backups[0].stat().st_mode & 0o777 == 0o600


def test_uninstall_absent_unit_does_not_disable_service(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(deployment_service, "unit_path", lambda: tmp_path / "missing.service")
    monkeypatch.setattr(deployment_service, "_systemctl", lambda *args, **kwargs: calls.append(args))
    deployment_service.uninstall()
    assert calls == []


def test_uninstall_unmanaged_unit_does_not_disable_service(tmp_path, monkeypatch):
    unit = tmp_path / "amplifier-unified.service"
    unit.write_text("[Service]\n")
    calls = []
    monkeypatch.setattr(deployment_service, "unit_path", lambda: unit)
    monkeypatch.setattr(deployment_service, "_systemctl", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(RuntimeError, match="unmanaged"):
        deployment_service.uninstall()
    assert calls == []


def test_uninstall_preserves_unit_when_stop_or_disable_fails(tmp_path, monkeypatch):
    unit = tmp_path / "amplifier-unified.service"
    unit.write_text(deployment_service.MARKER + "\n[Service]\n")
    calls = []
    monkeypatch.setattr(deployment_service, "unit_path", lambda: unit)
    def failing_systemctl(*args, **kwargs):
        calls.append(args)
        raise RuntimeError("systemd user bus is unavailable")
    monkeypatch.setattr(deployment_service, "_systemctl", failing_systemctl)
    with pytest.raises(RuntimeError, match="user bus"):
        deployment_service.uninstall()
    assert unit.exists()
    assert calls == [("disable", "--now", deployment_service.UNIT_NAME)]


def test_current_process_must_have_systemd_invocation_and_our_cgroup(tmp_path, monkeypatch):
    monkeypatch.setattr(deployment_service, "managed_unit", lambda data_dir: True)
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    assert not deployment_service.current_process_is_unit_managed(tmp_path)
    monkeypatch.setenv("INVOCATION_ID", "unit-invocation")
    monkeypatch.setattr(deployment_service.Path, "read_text",
                        lambda path: "0::/user.slice/user-1000.slice/user@1000.service/app.slice/amplifier-unified.service\n")
    assert deployment_service.current_process_is_unit_managed(tmp_path)