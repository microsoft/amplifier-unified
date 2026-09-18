from pathlib import Path
import os
import shlex
import shutil
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


def _unit_environment_path(unit):
    assignment = next(line.removeprefix("Environment=") for line in unit.read_text().splitlines()
                      if line.startswith("Environment="))
    # systemd accepts quoted assignments and expands %% to literal %.
    return shlex.split(assignment)[0].removeprefix("PATH=").replace("%%", "%")


@pytest.mark.parametrize("location", ["snap/bin", "custom tools/bin", "literal%h/bin", 'quote"and\\slash/bin'])
def test_installed_service_can_find_uv_from_installer_path(tmp_path, monkeypatch, location):
    uv_dir = tmp_path / location
    uv_dir.mkdir(parents=True)
    uv = uv_dir / "uv"
    # A lexical executable path matters for dispatchers such as /snap/bin/uv.
    dispatcher = tmp_path / "dispatcher"
    dispatcher.write_text("#!/bin/sh\nexit 0\n")
    dispatcher.chmod(0o755)
    uv.symlink_to(dispatcher)
    installer_path = str(uv_dir) + os.pathsep + os.defpath
    monkeypatch.setenv("PATH", installer_path)
    unit = tmp_path / "amplifier-unified.service"
    monkeypatch.setattr(deployment_service, "unit_path", lambda: unit)
    monkeypatch.setattr(deployment_service, "_systemctl", lambda *args, **kwargs: None)
    deployment_service.install(tmp_path / "data", tmp_path)
    service_path = _unit_environment_path(unit)
    assert service_path == installer_path
    assert shutil.which("uv", path=service_path) == str(uv)
    # Exercise the actual runtime discovery, not only the generated text.
    monkeypatch.setenv("PATH", service_path)
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path / "data"))
    from amplifier_web.runtime import RuntimeManager
    assert RuntimeManager()._command()[0] == str(uv)


def test_replacing_service_restarts_it_to_apply_new_environment(tmp_path, monkeypatch):
    unit = tmp_path / "amplifier-unified.service"
    unit.write_text(deployment_service.MARKER + "\n[Service]\nEnvironment=PATH=/old/bin\n")
    calls = []
    monkeypatch.setattr(deployment_service, "unit_path", lambda: unit)
    monkeypatch.setattr(deployment_service, "_systemctl", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setenv("PATH", "/snap/bin:/usr/bin:/bin")
    deployment_service.install(tmp_path / "data", tmp_path, replace=True)
    assert calls == [("daemon-reload",), ("enable", deployment_service.UNIT_NAME),
                     ("restart", deployment_service.UNIT_NAME)]
    assert _unit_environment_path(unit) == "/snap/bin:/usr/bin:/bin"


def test_missing_path_uses_portable_default(tmp_path, monkeypatch):
    monkeypatch.delenv("PATH", raising=False)
    unit = tmp_path / "amplifier-unified.service"
    monkeypatch.setattr(deployment_service, "unit_path", lambda: unit)
    monkeypatch.setattr(deployment_service, "_systemctl", lambda *args, **kwargs: None)
    deployment_service.install(tmp_path / "data", tmp_path)
    assert _unit_environment_path(unit) == f"{Path.home() / '.local' / 'bin'}:/usr/local/bin:/usr/bin:/bin"


@pytest.mark.parametrize("value", ["/bin\n[Service]\nExecStart=/bad", "/bin\r", "/bin\x01"])
def test_path_control_characters_cannot_inject_unit_directives(tmp_path, monkeypatch, value):
    unit = tmp_path / "amplifier-unified.service"
    monkeypatch.setattr(deployment_service, "unit_path", lambda: unit)
    monkeypatch.setenv("PATH", value)
    monkeypatch.setattr(deployment_service, "_systemctl", lambda *args, **kwargs: pytest.fail("invalid PATH"))
    with pytest.raises(ValueError, match="PATH"):
        deployment_service.install(tmp_path / "data", tmp_path)
    assert not unit.exists()


@pytest.mark.parametrize("selection", ["default", "xdg", "explicit"])
def test_service_pins_shell_shared_root_before_systemd_changes_environment(tmp_path, monkeypatch, selection):
    monkeypatch.delenv("AMPLIFIER_SESSION_STATE_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "owner")
    expected = tmp_path / "owner" / ".local" / "state" / "amplifier" / "sessions"
    if selection == "xdg":
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        expected = tmp_path / "state" / "amplifier" / "sessions"
    elif selection == "explicit":
        expected = tmp_path / 'shared %h "state"'
        monkeypatch.setenv("AMPLIFIER_SESSION_STATE_HOME", str(expected))
    unit = tmp_path / "amplifier-unified.service"
    monkeypatch.setattr(deployment_service, "unit_path", lambda: unit)
    monkeypatch.setattr(deployment_service, "_systemctl", lambda *args, **kwargs: None)
    deployment_service.install(tmp_path / "data", tmp_path)
    line = next(row.removeprefix("Environment=") for row in unit.read_text().splitlines()
                if row.startswith('Environment="AMPLIFIER_SESSION_STATE_HOME='))
    resolved = shlex.split(line)[0].split("=", 1)[1].replace("%%", "%")
    assert resolved == str(expected)
    assert not expected.exists(), "Resolving the namespace must not create shared state"


def test_shared_state_control_characters_cannot_inject_unit_directives(tmp_path, monkeypatch):
    monkeypatch.setenv("AMPLIFIER_SESSION_STATE_HOME", str(tmp_path / "bad\npath"))
    unit = tmp_path / "amplifier-unified.service"
    monkeypatch.setattr(deployment_service, "unit_path", lambda: unit)
    with pytest.raises(ValueError, match="control characters"):
        deployment_service.install(tmp_path / "data", tmp_path)
    assert not unit.exists()