"""First-run and repaired services use the same helper discovery as a terminal."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from amplifier_web import host_dependencies as dependencies


def executable(path, body='echo uv-fixture'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\n' + body + '\n'); path.chmod(0o755)
    return path


@pytest.fixture
def isolated_tools(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('PATH', str(tmp_path / 'empty'))
    monkeypatch.setattr(sys, 'platform', 'linux')
    return tmp_path


@pytest.mark.parametrize('folder', ['.local/bin', '.cargo/bin'])
def test_runtime_finds_helper_installed_after_service_started(isolated_tools, monkeypatch, folder):
    from amplifier_web.runtime import RuntimeManager
    dependencies.prepare_environment()
    with pytest.raises(dependencies.MissingHostDependency, match='setup helper'):
        RuntimeManager()._command()
    uv = executable(isolated_tools / folder / 'uv')
    assert RuntimeManager()._command()[0] == str(uv)
    assert shutil.which('uv') == str(uv)
    assert subprocess.check_output([str(uv), '--version'], text=True).strip() == 'uv-fixture'


def test_existing_path_and_dispatcher_identity_take_precedence(isolated_tools, monkeypatch):
    directory = isolated_tools / 'custom tools'
    dispatcher = executable(isolated_tools / 'dispatcher', 'echo "$0"')
    directory.mkdir(); uv = directory / 'uv'; uv.symlink_to(dispatcher)
    executable(isolated_tools / '.local/bin/uv', 'exit 9')
    monkeypatch.setenv('PATH', str(directory))
    dependencies.prepare_environment()
    assert dependencies.require_uv() == str(uv)
    assert subprocess.check_output([dependencies.require_uv()], text=True).strip() == str(uv)
    before = os.environ['PATH']; dependencies.prepare_environment()
    assert os.environ['PATH'] == before


def test_nonexecutables_and_unlisted_workspace_helpers_are_not_adopted(isolated_tools, monkeypatch):
    uv = executable(isolated_tools / '.local/bin/uv'); uv.chmod(0o600)
    executable(isolated_tools / 'workspace/uv')
    monkeypatch.chdir(isolated_tools / 'workspace')
    with pytest.raises(dependencies.MissingHostDependency, match='same user'):
        dependencies.require_uv()


def test_cli_normalizes_old_service_environment_before_generation_delegation(isolated_tools, monkeypatch):
    from amplifier_web import cli, application_generations
    uv = executable(isolated_tools / '.local/bin/uv')
    monkeypatch.setattr(sys, 'argv', ['amplifier-unified', '--version'])
    observed = []
    monkeypatch.setattr(application_generations, 'delegate', lambda: observed.append(shutil.which('uv')))
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 0 and observed == [str(uv)]


def test_reset_discovers_helper_without_ordinary_startup_dependencies(isolated_tools, monkeypatch):
    from amplifier_web import cli, reset
    uv = executable(isolated_tools / '.local/bin/uv')
    monkeypatch.setattr(sys, 'argv', ['amplifier-unified', 'reset', '--dry-run'])
    observed = []
    monkeypatch.setattr(dependencies, 'prepare_environment', lambda: pytest.fail('Repair must not require ordinary startup'))
    monkeypatch.setattr(reset, 'run', lambda args: observed.append((reset.find_uv(), args.dry_run)))
    cli.main()
    assert observed == [(str(uv), True)]


def test_doctor_names_missing_helpers_and_uses_linux_guidance(isolated_tools):
    from amplifier_web.installation_health import doctor
    result = doctor(isolated_tools / 'app')
    messages = ' '.join(row['message'] for row in result['checks'] if not row['ok'])
    assert not result['ok']
    assert 'setup helper (uv)' in messages and 'sudo apt-get install git' in messages
    assert 'xcode' not in messages


GUARD = 'NoDefaultCurrentDirectoryInExePath'


@pytest.fixture
def consumer_shell(isolated_tools, monkeypatch):
    """A Windows consumer installation's layout; path logic only, not native."""
    root = isolated_tools / 'install'
    (root / 'app').mkdir(parents=True)
    (root / 'consumer-install.json').write_text('{}')
    bash = root / 'git/bin/bash.exe'
    bash.parent.mkdir(parents=True)
    bash.write_bytes(b'MZ')
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(root / 'app'))
    # Recorded so the guard that preparation sets does not outlive the test.
    monkeypatch.setenv(GUARD, 'recorded')
    monkeypatch.delenv(GUARD)
    return root


def test_windows_installation_shell_precedes_the_wsl_launcher_at_every_host_start(consumer_shell, monkeypatch):
    shell = str(consumer_shell / 'git/bin')
    # The saved task PATH of an installation made before setup supplied a shell.
    saved = os.pathsep.join(['/windows/system32', '/install/git/cmd'])
    monkeypatch.setenv('PATH', saved)
    assert dependencies.private_shell() == shell
    dependencies.prepare_environment()
    entries = os.environ['PATH'].split(os.pathsep)
    assert entries[:3] == [shell, '/windows/system32', '/install/git/cmd']
    dependencies.prepare_environment()
    assert os.environ['PATH'].split(os.pathsep).count(shell) == 1
    assert os.environ['PATH'].split(os.pathsep)[0] == shell


@pytest.mark.parametrize('missing', ['platform', 'home', 'receipt', 'bash'])
def test_only_this_windows_installations_complete_shell_is_added(consumer_shell, monkeypatch, missing):
    if missing == 'platform':
        monkeypatch.setattr(sys, 'platform', 'linux')
    elif missing == 'home':
        monkeypatch.delenv('AMPLIFIER_WEB_HOME')
    elif missing == 'receipt':
        (consumer_shell / 'consumer-install.json').unlink()
    else:
        # An installation that still has MinGit: no bash.exe, nothing is added.
        (consumer_shell / 'git/bin/bash.exe').unlink()
    monkeypatch.setenv('PATH', '/windows/system32')
    assert dependencies.private_shell() is None
    assert str(consumer_shell / 'git/bin') not in dependencies.tool_path().split(os.pathsep)


def test_windows_hosts_never_resolve_a_program_from_the_current_workspace(consumer_shell, monkeypatch):
    # Python's own Windows lookup tries the current folder first unless this
    # documented Windows variable exists; workers inherit the host environment.
    assert GUARD not in os.environ
    dependencies.prepare_environment()
    assert os.environ[GUARD] == '1'
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.delenv(GUARD)
    dependencies.prepare_environment()
    assert GUARD not in os.environ
