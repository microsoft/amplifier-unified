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
