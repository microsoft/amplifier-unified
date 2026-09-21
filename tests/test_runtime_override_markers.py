"""Installer overrides retain the universal resolver's marker graph."""
import shutil
import subprocess
import sys
import zipfile

from packaging.requirements import Requirement
import pytest

from amplifier_web import runtime_qualification as qualification


def project(tmp_path, packages, dependencies):
    root = tmp_path / 'worker'
    root.mkdir()
    (root / 'pyproject.toml').write_text('[project]\nname="fixture-worker"\nversion="1.0"\nrequires-python=">=3.13"\ndependencies=[]\n')
    (root / 'uv.lock').write_text('version=1\nrevision=3\nrequires-python=">=3.13"\n'
        '[[package]]\nname="fixture-worker"\nversion="1.0"\nsource={virtual="."}\ndependencies=[' + dependencies + ']\n' + packages)
    target = tmp_path / 'receipt/runtime-install-overrides.txt'
    target.parent.mkdir()
    return root, target


async def test_export_preserves_transitive_platform_markers_and_version_forks(tmp_path):
    root, target = project(tmp_path, '''
[[package]]
name="windows-parent"
version="1.0"
source={registry="https://pypi.org/simple"}
dependencies=[{name="windows-child"}]
[[package]]
name="windows-child"
version="2.0"
source={registry="https://pypi.org/simple"}
[[package]]
name="platform-fork"
version="3.0"
source={registry="https://pypi.org/simple"}
resolution-markers=["sys_platform == 'win32'"]
[[package]]
name="platform-fork"
version="4.0"
source={registry="https://pypi.org/simple"}
resolution-markers=["sys_platform != 'win32'"]
''', '''{name="windows-parent",marker="sys_platform == 'win32'"},
{name="platform-fork",version="3.0",source={registry="https://pypi.org/simple"},marker="sys_platform == 'win32'"},
{name="platform-fork",version="4.0",source={registry="https://pypi.org/simple"},marker="sys_platform != 'win32'"}''')
    lock = (root / 'uv.lock').read_bytes()
    await qualification.prepare_overrides(root, target)
    requirements = [Requirement(line) for line in target.read_text().splitlines()]
    for platform in ('darwin', 'linux', 'win32'):
        selected = {row.name: str(row.specifier) for row in requirements
                    if not row.marker or row.marker.evaluate({'sys_platform': platform})}
        assert selected == ({'windows-parent':'==1.0','windows-child':'==2.0','platform-fork':'==3.0'}
                            if platform == 'win32' else {'platform-fork':'==4.0'})
    assert (root / 'uv.lock').read_bytes() == lock


async def test_export_preserves_editable_paths_and_exact_git_subdirectory(tmp_path):
    root, target = project(tmp_path, '''
[[package]]
name="amplifier-editable"
version="1.0"
source={editable="../editable module"}
[[package]]
name="amplifier-directory"
version="1.0"
source={directory="../local module"}
[[package]]
name="amplifier-git"
version="1.0"
source={git="https://example.invalid/repository?subdirectory=modules%2Fhooks&branch=main#aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
''', '{name="amplifier-editable"},{name="amplifier-directory"},{name="amplifier-git"}')
    for name in ('editable module', 'local module'):
        (tmp_path / name).mkdir()
        (tmp_path / name / 'pyproject.toml').write_text('[project]\nname="fixture"\nversion="1.0"\n')
    await qualification.prepare_overrides(root, target)
    content = target.read_text()
    assert '-e ' + (tmp_path / 'editable module').as_uri() in content
    assert 'amplifier-directory @ ' + (tmp_path / 'local module').as_uri() in content
    assert 'amplifier-git @ git+https://example.invalid/repository@' + 'a' * 40 + '#subdirectory=modules/hooks' in content


async def test_recorded_policy_is_not_reexported_or_rewritten(tmp_path, monkeypatch):
    root, target = project(tmp_path, '', '')
    await qualification.prepare_overrides(root, target)
    (target.parent / 'runtime-installed.json').write_text('[]')
    before = {path.name:(path.read_bytes(),path.stat().st_mtime_ns) for path in target.parent.iterdir()}
    monkeypatch.setattr('amplifier_web.updates.process', lambda *args, **kwargs: pytest.fail('Frozen policy must not re-export'))
    assert await qualification.prepare_overrides(root, target) == target
    assert {path.name:(path.read_bytes(),path.stat().st_mtime_ns) for path in target.parent.iterdir()} == before
    target.write_text('changed==1.0\n')
    with pytest.raises(ValueError, match='policy changed'):
        await qualification.prepare_overrides(root, target)


async def test_020_policy_keeps_legacy_verification(tmp_path, monkeypatch):
    root, target = project(tmp_path, '''
[[package]]
name="fixture-windows-only"
version="1.0"
source={registry="https://pypi.org/simple"}
''', '{name="fixture-windows-only",marker="sys_platform == \'win32\'"}')
    qualification.lock_overrides(root, target)
    (target.parent / 'runtime-installed.json').write_text('[]')
    old = target.read_bytes()
    assert b'fixture-windows-only==1.0\n' in old and b';' not in old
    monkeypatch.setattr('amplifier_web.updates.process', lambda *args, **kwargs: pytest.fail('Old policy must not re-export'))
    await qualification.prepare_overrides(root, target)
    assert target.read_bytes() == old
    assert not target.with_name('runtime-install-policy.json').exists()


@pytest.mark.skipif(sys.platform == 'win32', reason='This checks that a Windows-only dependency is omitted on other hosts')
async def test_real_uv_install_does_not_force_windows_only_dependency(tmp_path):
    root, target = project(tmp_path, '''
[[package]]
name="fixture-windows-only"
version="1.0"
source={registry="https://pypi.org/simple"}
''', '{name="fixture-windows-only",marker="sys_platform == \'win32\'"}')
    await qualification.prepare_overrides(root, target)
    wheel = tmp_path / 'fixture_parent-1.0-py3-none-any.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        metadata = 'fixture_parent-1.0.dist-info/'
        archive.writestr(metadata + 'METADATA', 'Metadata-Version: 2.3\nName: fixture-parent\nVersion: 1.0\nRequires-Dist: fixture-windows-only>=1.0; sys_platform == "win32"\n')
        archive.writestr(metadata + 'WHEEL', 'Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        archive.writestr(metadata + 'RECORD', '')
    uv = shutil.which('uv')
    venv = tmp_path / 'installed'
    subprocess.run([uv, 'venv', str(venv), '--python', '3.13'], check=True, capture_output=True)
    command = [uv, 'pip', 'install', '--python', str(venv / 'bin/python'), '--no-index', '--overrides']
    old = tmp_path / 'legacy-overrides.txt'
    qualification.lock_overrides(root, old)
    assert subprocess.run([*command, str(old), str(wheel)], capture_output=True).returncode != 0
    subprocess.run([*command, str(target), str(wheel)], check=True, capture_output=True)
