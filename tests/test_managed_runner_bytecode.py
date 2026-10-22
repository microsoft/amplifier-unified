"""Real isolated-interpreter coverage of executable bytecode identity."""
import importlib.util
import json
from pathlib import Path
import py_compile
import subprocess
import venv

import pytest

from amplifier_web import managed_runner_updates as updates
from amplifier_web import task_environments as environments
from amplifier_web import test_environment_setup as private


@pytest.fixture
def runtime(tmp_path):
    prefix = tmp_path / 'runtime'
    venv.EnvBuilder(with_pip=False, symlinks=True).create(prefix)
    python = prefix / 'bin/python'
    base = Path(subprocess.check_output(
        [str(python), '-I', '-B', '-c',
         "import sysconfig; print(sysconfig.get_path('purelib'))"], text=True).strip())
    for module, distribution in (
        ('digital_twin_universe', 'digital-twin-universe'),
        ('amplifier_bundle_digital_twin_universe', 'amplifier-bundle-digital-twin-universe'),
    ):
        package = base / module
        package.mkdir()
        (package / '__init__.py').write_text("VALUE = 'original'\n")
        metadata = base / (module + '-0.1.dist-info')
        metadata.mkdir()
        (metadata / 'METADATA').write_text(f'Metadata-Version: 2.1\nName: {distribution}\nVersion: 0.1\n')
    return {'python': str(python)}, base


def bytecode(source, *, sourceless):
    target = (source.with_suffix('.pyc') if sourceless
              else Path(importlib.util.cache_from_source(str(source))))
    py_compile.compile(str(source), cfile=str(target), doraise=True,
                       invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
    if sourceless:
        source.unlink()
    return target


@pytest.mark.parametrize('engine', [False, True])
@pytest.mark.parametrize('sourceless', [False, True])
def test_existing_executable_bytecode_is_hashed_without_importing_package(runtime, tmp_path, engine, sourceless):
    installation, base = runtime
    module = 'amplifier_bundle_digital_twin_universe' if engine else 'digital_twin_universe'
    source = base / module / '__init__.py'
    target = bytecode(source, sourceless=sourceless)
    probe = Path(updates.__file__).with_name('managed_runner_identity.py')
    command = [installation['python'], '-I', '-B', str(probe), *(['--incus-engine'] if engine else [])]
    before = json.loads(subprocess.check_output(command))
    marker = tmp_path / 'changed-code-ran'
    injected = tmp_path / 'injected.py'
    injected.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\nVALUE = 'changed'\n")
    py_compile.compile(str(injected), cfile=str(target), doraise=True,
                       invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
    # Demonstrate that -I -B alone does load the altered executable bytecode.
    subprocess.run([installation['python'], '-I', '-B', '-c', f'import {module}'], check=True)
    assert marker.read_text() == 'ran'
    marker.unlink()
    after = json.loads(subprocess.check_output(command))
    assert before['runtimeSha256'] != after['runtimeSha256']
    assert not marker.exists(), 'identity collection must not execute the runner package'


@pytest.mark.parametrize('sourceless', [False, True])
def test_normal_dispatch_preserves_identity_and_rejects_changed_bytecode(runtime, tmp_path, monkeypatch, sourceless):
    installation, base = runtime
    source = base / 'digital_twin_universe/__init__.py'
    target = bytecode(source, sourceless=sourceless)
    home = tmp_path / 'state'
    home.mkdir()
    (home / 'managed-runner-updates').mkdir(mode=0o700)
    config = {**installation, 'installationId': 'a' * 24,
              'commit': environments.DTU_COMMIT, 'runnerQualificationRequired': True}
    identity = updates.runtime_identity(config)
    private.write_json(updates.qualification_path(home, config), {
        'version': 1, 'normalized': config, 'runtime': identity})
    row = {'sessionId': 'owned', 'id': 'owned-resource'}
    folder = environments.storage.directory(home, row)
    folder.mkdir(parents=True)
    helper = tmp_path / 'controlled_worker.py'
    helper.write_text("import json; import digital_twin_universe; print(json.dumps({'ok': True}))")
    original = subprocess.Popen
    launches = []
    def launch(argv, *args, **kwargs):
        if Path(argv[-1]).name == 'test_environment_worker.py':
            assert argv[1:3] == ['-I', '-B']
            launches.append(argv)
            argv = [*argv[:-1], str(helper)]
        return original(argv, *args, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', launch)
    assert environments.invoke(config, home, row, 'prepare', {}) == {'ok': True}
    assert len(launches) == 1
    assert updates.runtime_identity(config) == identity
    injected = tmp_path / 'injected.py'
    injected.write_text("VALUE = 'changed'\n")
    py_compile.compile(str(injected), cfile=str(target), doraise=True,
                       invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
    with pytest.raises(updates.RunnerUpdateError):
        environments.invoke(config, home, row, 'prepare', {})
    assert len(launches) == 1, 'changed bytes must be refused before normal backend dispatch'



def test_registered_source_export_does_not_write_runtime_bytecode(runtime, tmp_path):
    from amplifier_web import registered_test_runners as runners
    installation, base = runtime
    (base / 'digital_twin_universe/lib.py').write_text(
        "class Snapshot:\n"
        "    def model_dump(self, **kwargs): return {'snapshot_commit': 'owned'}\n"
        "def export_repository_snapshot(source, target): return Snapshot()\n")
    before = updates.runtime_identity(installation)
    assert runners.export_source(installation, tmp_path, str(tmp_path)) == {'snapshot_commit': 'owned'}
    assert updates.runtime_identity(installation) == before
    assert not list(base.rglob('*.pyc'))

