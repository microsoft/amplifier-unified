"""Deterministic adapter contracts; no Docker, network, install or desktop calls.

These checks do not replace the manual installed-wheel qualification. They are
safe to run in its owned preparation environment before admitting Docker work.
"""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import xml.etree.ElementTree as ET
import zipfile

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('default_installed_adapter', ROOT / 'scripts/qualify_default_installed.py')
assert SPEC and SPEC.loader
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)


def fake_wheel(path, name, requirements=(), payload=None):
    with zipfile.ZipFile(path, 'w') as archive:
        info = 'Name: ' + name + '\nVersion: 1.0\n'
        info += ''.join('Requires-Dist: ' + row + '\n' for row in requirements)
        archive.writestr(name + '-1.0.dist-info/METADATA', info)
        for relative, data in (payload or {}).items():
            archive.writestr(relative, data)
    return path


def test_unselected_direct_extras_do_not_require_unrelated_packages(tmp_path):
    wheel = fake_wheel(tmp_path / 'host.whl', 'amplifier-unified', [
        'amplifier-foundation @ https://example.invalid/foundation',
        'amplifier-app-tui @ https://example.invalid/tui ; extra == "tui"'])
    adapter.check_direct_requirements([wheel], ['amplifier-unified', 'amplifier-foundation'], '3.13.16')


def test_selected_ci_client_direct_dependency_must_be_pinned(tmp_path):
    wheel = fake_wheel(tmp_path / 'ci.whl', 'amplifier-bundle-context-intelligence', [
        'missing-library @ https://example.invalid/library ; extra == "client"'])
    with pytest.raises(ValueError, match='Undeclared active'):
        adapter.check_direct_requirements([wheel], ['amplifier-bundle-context-intelligence'], '3.13.16')


def test_source_inventory_checks_links_with_explicit_owned_boundary(tmp_path):
    root = tmp_path / '.venv'
    root.mkdir()
    python = tmp_path / 'python'
    python.write_text('fake interpreter bytes')
    (root / 'python').symlink_to(python)
    with pytest.raises(ValueError, match='symlink escapes'):
        adapter.inventory(root)
    assert adapter.inventory(root, boundary=tmp_path)['python'] == {'link': str(python)}


@pytest.mark.parametrize('drift', ['changed', 'missing', 'extra'])
def test_packaged_payload_must_equal_source_and_resources(tmp_path, drift):
    source = tmp_path / 'candidate'
    source.mkdir()
    (source / 'pkg').mkdir()
    (source / 'pkg/__init__.py').write_text('value=1\n')
    (source / 'resource.txt').write_text('resource\n')
    (source / 'pyproject.toml').write_text('''[tool.hatch.build.targets.wheel]
packages = ["pkg"]
[tool.hatch.build.targets.wheel.force-include]
"resource.txt" = "pkg/resource.txt"
''')
    payload = {'pkg/__init__.py': 'value=1\n', 'pkg/resource.txt': 'resource\n'}
    wheel = fake_wheel(tmp_path / 'good.whl', 'example', payload=payload)
    assert adapter.verify_candidate_wheel(wheel, source)['pkg/resource.txt'] == adapter.digest(source / 'resource.txt')
    if drift == 'changed':
        payload['pkg/resource.txt'] = 'wrong\n'
    elif drift == 'missing':
        payload.pop('pkg/resource.txt')
    else:
        payload['pkg/unexpected.bin'] = 'extra'
    wheel = fake_wheel(tmp_path / 'bad.whl', 'example', payload=payload)
    with pytest.raises(ValueError, match='payload differs'):
        adapter.verify_candidate_wheel(wheel, source)


def test_pins_require_exact_sources(tmp_path):
    data = json.loads((ROOT / 'scripts/default-installed-pins.json').read_text())
    assert adapter.pins(ROOT / 'scripts/default-installed-pins.json')['core']['version'] == '2.0.2'
    data['repositories'][0]['commit'] = 'main'
    path = tmp_path / 'pins.json'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='Exact repository'):
        adapter.pins(path)


def test_environment_contains_no_caller_credentials(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-must-not-leak')
    monkeypatch.setenv('SSH_AUTH_SOCK', '/synthetic/socket')
    monkeypatch.setenv('DISPLAY', ':7')
    env = adapter.clean_env()
    assert not {'OPENAI_API_KEY', 'SSH_AUTH_SOCK', 'DISPLAY', 'PYTHONPATH'} & env.keys()
    assert env['GIT_CONFIG_GLOBAL'] == '/dev/null'


def test_host_refusal_happens_before_any_execution(monkeypatch, tmp_path):
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    monkeypatch.setattr(adapter, 'command', lambda *a, **k: pytest.fail('No process may start'))
    with pytest.raises(ValueError, match='GitHub-hosted'):
        adapter.hosted_run(tmp_path / 'work', tmp_path / 'output', tmp_path / 'loop')
    assert list(tmp_path.iterdir()) == []


def test_unexpected_case_count_cannot_publish_success(monkeypatch, tmp_path):
    monkeypatch.setattr(adapter, 'GUEST', tmp_path)
    monkeypatch.setattr(adapter, 'in_container', lambda: True)
    monkeypatch.setattr(adapter.os, 'getuid', lambda: 65532)
    (tmp_path / 'prepared-evidence').mkdir()
    (tmp_path / 'evidence').mkdir()
    (tmp_path / 'run').mkdir()
    monkeypatch.setattr(adapter, 'command', lambda *a, **k: None)

    def run(*args, **kwargs):
        (tmp_path / 'evidence/junit.xml').write_text('<testsuites><testsuite><testcase/></testsuite></testsuites>')
        return subprocess.CompletedProcess(args[0], 0)

    monkeypatch.setattr(adapter.subprocess, 'run', run)
    with pytest.raises(ValueError, match='all 12'):
        adapter.guest_test()
    result = json.loads((tmp_path / 'evidence/result.json').read_text())
    assert result['passed'] is False and result['attestation'] is True


def test_workflow_is_manual_and_bridge_does_not_change_normal_matrix():
    # BaseLoader retains YAML's 'on' key rather than interpreting it as Boolean.
    workflow = yaml.load((ROOT / '.github/workflows/default-installed.yml').read_text(), Loader=yaml.BaseLoader)
    assert set(workflow['on']) == {'workflow_dispatch', 'workflow_call'}
    assert workflow['permissions'] == {'contents': 'read'}
    steps = workflow['jobs']['default-installed']['steps']
    private = next(s for s in steps if s.get('with', {}).get('repository') == 'microsoft/amplifier-module-loop-live')
    assert private['with']['persist-credentials'] == 'false'
    assert private['with']['ref'] == '${{ steps.pins.outputs.loop }}'
    bridge = yaml.load((ROOT / '.github/workflows/integration-checks.yml').read_text(), Loader=yaml.BaseLoader)
    assert bridge['on']['workflow_dispatch']['inputs']['default_installed']['default'] == 'false'
    assert bridge['jobs']['default-installed']['if'] == "github.event_name == 'workflow_dispatch' && (inputs.default_installed || inputs.root_guest || inputs.installed_pwa)"
    assert bridge['jobs']['plan']['if'] == "github.event_name != 'workflow_dispatch' || !(inputs.default_installed || inputs.root_guest || inputs.installed_pwa)"
    assert bridge['jobs']['default-installed']['uses'] == './.github/workflows/default-installed.yml'
    assert bridge['on']['workflow_dispatch']['inputs']['desktop_safety']['default'] == 'false'
    assert bridge['jobs']['default-installed']['with']['desktop_safety'] == '${{ inputs.desktop_safety }}'
    assert workflow['on']['workflow_call']['inputs']['desktop_safety']['default'] == 'false'


@pytest.mark.parametrize('name,kind', [('../escape', 'file'), ('link', 'link'), ('/absolute', 'file')])
def test_evidence_extraction_rejects_paths_and_links(tmp_path, name, kind):
    archive = tmp_path / 'evidence.tar'
    with tarfile.open(archive, 'w') as tar:
        member = tarfile.TarInfo(name)
        if kind == 'link':
            member.type = tarfile.SYMTYPE
            member.linkname = '/outside'
            tar.addfile(member)
        else:
            member.size = 1
            tar.addfile(member, io.BytesIO(b'x'))
    with pytest.raises(ValueError, match='Unsafe evidence'):
        adapter.extract_evidence(archive, tmp_path / 'extracted')
    assert not (tmp_path / 'extracted').exists()


def test_evidence_archive_retains_exact_regular_bytes(tmp_path):
    archive = tmp_path / 'evidence.tar'
    with tarfile.open(archive, 'w') as tar:
        member = tarfile.TarInfo('./result.json')
        payload = b'{"passed":false}\n'
        member.size = len(payload)
        tar.addfile(member, io.BytesIO(payload))
    adapter.extract_evidence(archive, tmp_path / 'extracted')
    assert (tmp_path / 'extracted/result.json').read_bytes() == payload


def test_container_execution_contract_keeps_evidence_until_collection():
    # Structural guard, not Docker/runtime acceptance. Actual tmpfs readback and
    # daemon cleanup still require the manual hosted run.
    source = (ROOT / 'scripts/qualify_default_installed.py').read_text()
    assert "'--network=none'" in source and "'--read-only'" in source
    assert "'--cap-drop=ALL'" in source and "'--user=65532:65532'" in source
    assert 'exit-code.tmp /opt/defaults/evidence/exit-code' in source
    assert "['docker', 'exec', test, 'tar'" in source
    assert "test + ':' + str(GUEST / 'evidence')" not in source
    assert source.index("state['preparationEvidenceRetained'] = True") < source.index("effect(['docker', 'stop'")
    assert "except (subprocess.TimeoutExpired, KeyboardInterrupt, SystemExit, OSError):" in source


def desktop_junit(path, group, bad=None):
    root = ET.Element('testsuite')
    for name, count in adapter.DESKTOP_CASES[group].items():
        for index in range(count):
            case = ET.SubElement(root, 'testcase', classname='tests.test_default_desktop_safety',
                                 name=f'{name}[{index}]')
    if bad:
        ET.SubElement(case, bad)
    ET.ElementTree(root).write(path)
    return root


@pytest.mark.parametrize('bad', [None, 'failure', 'skipped'])
def test_desktop_group_keeps_original_count_and_failed_verdict(tmp_path, bad):
    path = tmp_path / 'junit.xml'
    desktop_junit(path, 'preserved-22', bad)
    report = adapter.desktop_result(path, 'preserved-22', 0)
    assert len(report['cases']) == 22
    assert report['passed'] is (bad is None)
    if bad:
        assert report['cases'][-1]['outcome'] == bad


@pytest.mark.parametrize('change', ['missing', 'duplicate'])
def test_desktop_group_requires_every_unique_case(tmp_path, change):
    path = tmp_path / 'junit.xml'
    root = desktop_junit(path, 'remote-defaults')
    if change == 'missing':
        root.remove(root[-1])
    else:
        root.append(ET.fromstring(ET.tostring(root[-1])))
    ET.ElementTree(root).write(path)
    with pytest.raises(ValueError, match='Missing desktop|duplicate'):
        adapter.desktop_result(path, 'remote-defaults', 0)


def test_desktop_only_does_not_rerun_baseline(monkeypatch, tmp_path):
    monkeypatch.setattr(adapter, 'GUEST', tmp_path)
    monkeypatch.setattr(adapter, 'in_container', lambda: True)
    monkeypatch.setattr(adapter.os, 'getuid', lambda: 65532)
    (tmp_path / 'prepared-evidence').mkdir()
    (tmp_path / 'evidence').mkdir()
    (tmp_path / 'run').mkdir()
    monkeypatch.setattr(adapter, 'command', lambda *a, **k: None)
    monkeypatch.setattr(adapter.subprocess, 'run', lambda *a, **k: pytest.fail('No baseline subprocess'))
    calls = []
    monkeypatch.setattr(adapter, 'run_desktop_safety', lambda *a: calls.append(a))
    adapter.guest_test(desktop_safety=True)
    result = json.loads((tmp_path / 'evidence/result.json').read_text())
    assert len(calls) == 1 and result['passed'] and result['attestation']
    assert result['stage'] == 'desktop-safety' and result['baselineExecuted'] is False
    assert result['testExit'] is result['checkExit'] is None
    assert not (tmp_path / 'evidence/junit.xml').exists()


def test_failed_preserved_group_does_not_erase_remote_discriminator(monkeypatch, tmp_path):
    monkeypatch.setattr(adapter, 'GUEST', tmp_path)
    calls = []

    def run(argv, **kwargs):
        junit = Path(next(a for a in argv if a.startswith('--junitxml=')).split('=', 1)[1])
        group = 'preserved-22' if 'preserved-22' in str(junit) else 'remote-defaults'
        calls.append(group)
        desktop_junit(junit, group, 'failure' if group == 'preserved-22' else None)
        return subprocess.CompletedProcess(argv, 1 if group == 'preserved-22' else 0)

    monkeypatch.setattr(adapter.subprocess, 'run', run)
    with pytest.raises(ValueError, match='Desktop safety check failed'):
        adapter.run_desktop_safety(Path('/synthetic/python'), {}, tmp_path)
    report = json.loads((tmp_path / 'desktop-safety-result.json').read_text())
    assert calls == ['preserved-22', 'remote-defaults']
    assert report['passed'] is False and report['baselineExecuted'] is False
    assert not report['groups']['preserved-22']['passed']
    assert report['groups']['remote-defaults']['passed']


@pytest.mark.parametrize('flags,expected', [
    ({}, 'ic13'), ({'desktop_safety': True}, 'desktop-safety'),
    ({'root_guest': True}, 'root-guest'), ({'installed_pwa': True}, 'installed-pwa'),
    ({'root_guest': True, 'installed_pwa': True}, None),
    ({'desktop_safety': True, 'root_guest': True}, None),
    ({'desktop_safety': True, 'installed_pwa': True}, None),
])
def test_fixed_stages_are_explicit_and_mutually_exclusive(flags, expected):
    if expected is None:
        with pytest.raises(ValueError, match='only one'):
            adapter.selected_stage(**flags)
    else:
        assert adapter.selected_stage(**flags) == expected


def fixed_junit(path, stage):
    file, names = adapter.FIXED_CASES[stage]
    root = ET.Element('testsuite')
    for name in names:
        ET.SubElement(root, 'testcase', classname='tests.' + file.removesuffix('.py'), name=name)
    ET.ElementTree(root).write(path)
    return root


@pytest.mark.parametrize('stage', ['root-guest', 'installed-pwa'])
@pytest.mark.parametrize('change', ['none', 'missing', 'duplicate', 'extra', 'parameter', 'wrong-module',
                                    'skipped', 'error', 'failure', 'counter', 'exit'])
def test_fixed_stage_requires_exact_successful_identities(tmp_path, stage, change):
    path = tmp_path / 'junit.xml'
    root = fixed_junit(path, stage)
    if change == 'missing':
        root.remove(root[-1])
    elif change == 'duplicate':
        root.append(ET.fromstring(ET.tostring(root[-1])))
    elif change == 'extra':
        ET.SubElement(root, 'testcase', classname=root[0].get('classname'), name='test_other')
    elif change == 'parameter':
        root[-1].set('name', root[-1].get('name') + '[unexpected]')
    elif change == 'wrong-module':
        root[-1].set('classname', 'other.' + root[-1].get('classname'))
    elif change in {'skipped', 'error', 'failure'}:
        ET.SubElement(root[-1], change)
    elif change == 'counter':
        root.set('skipped', '1')
    ET.ElementTree(root).write(path)
    if change in {'missing', 'duplicate', 'extra', 'parameter', 'wrong-module'}:
        with pytest.raises(ValueError, match='identities'):
            adapter.fixed_result(path, stage, 0)
    else:
        result = adapter.fixed_result(path, stage, 1 if change == 'exit' else 0)
        assert result['passed'] is (change == 'none')
        assert len(result['cases']) == (8 if stage == 'root-guest' else 1)


def test_container_default_options_remain_unprivileged_and_unchanged():
    expected = ['--network=none', '--user=65532:65532', '--cap-drop=ALL',
                '--security-opt=no-new-privileges', '--cpus=2', '--memory=4g',
                '--pids-limit=256', '--read-only']
    for folder in ('home', 'tmp', 'run', 'cache', 'evidence'):
        expected += ['--tmpfs', '/opt/defaults/' + folder + ':rw,uid=65532,gid=65532,mode=0700,size=512m']
    assert adapter.test_container_options('ic13') == expected
    assert adapter.test_container_options('desktop-safety') == expected
    root = adapter.test_container_options('root-guest')
    assert '--user=0:0' in root
    assert {v for v in root if v.startswith('--cap-add=')} == {
        '--cap-add=KILL', '--cap-add=SETUID', '--cap-add=SETGID'}
    assert '/opt/defaults/tmp:rw,uid=0,gid=0,mode=0755,size=512m' in root
    assert '/opt/defaults/evidence:rw,uid=0,gid=0,mode=0700,size=512m' in root
    pwa = adapter.test_container_options('installed-pwa')
    assert pwa == expected + ['--tmpfs', '/tmp:rw,uid=0,gid=0,mode=1777,size=512m']
    assert not any('privileged' in v or 'pid=host' in v or v == '--mount' for v in root + pwa)


@pytest.mark.parametrize('stage', ['ic13', 'desktop-safety', 'root-guest', 'installed-pwa'])
@pytest.mark.parametrize('change', ['none', 'privileged', 'host-pid', 'capability', 'bind', 'mount', 'user'])
def test_actual_container_inspection_refuses_widened_stage(stage, change):
    root = stage == 'root-guest'
    mounts = [str(adapter.GUEST / name) for name in ('home', 'tmp', 'run', 'cache', 'evidence')]
    if stage == 'installed-pwa':
        mounts.append('/tmp')
    host = {'NetworkMode': 'none', 'ReadonlyRootfs': True, 'Privileged': False, 'Binds': None,
            'PidMode': '', 'CapDrop': ['ALL'], 'CapAdd': ['SETUID', 'SETGID', 'KILL'] if root else None,
            'Memory': 4 * 1024 ** 3, 'NanoCpus': 2 * 10 ** 9, 'PidsLimit': 256,
            'SecurityOpt': ['no-new-privileges'], 'Tmpfs': {}}
    info = {'HostConfig': host, 'Config': {'User': '0:0' if root else '65532:65532'},
            'Mounts': [{'Type': 'tmpfs', 'Destination': path} for path in mounts]}
    if change == 'privileged':
        host['Privileged'] = True
    elif change == 'host-pid':
        host['PidMode'] = 'host'
    elif change == 'capability':
        host['CapAdd'] = [*(host['CapAdd'] or []), 'SYS_ADMIN']
    elif change == 'bind':
        host['Binds'] = ['/private:/private']
    elif change == 'mount':
        info['Mounts'][0]['Type'] = 'bind'
    elif change == 'user':
        info['Config']['User'] = '65534:65534'
    if change == 'none':
        assert adapter.verify_test_isolation(info, stage)['NetworkMode'] == 'none'
    else:
        with pytest.raises(ValueError, match='isolation'):
            adapter.verify_test_isolation(info, stage)


@pytest.mark.parametrize('change', ['none', 'missing', 'duplicate', 'symlink', 'oversized', 'bad-json', 'bad-png'])
def test_pwa_retention_is_bounded_and_never_copies_private_profile(tmp_path, change):
    base, evidence = tmp_path / 'tests', tmp_path / 'evidence'
    base.mkdir()
    evidence.mkdir()
    (base / 'browser-profile').mkdir()
    (base / 'browser-profile/cookies').write_text('synthetic-private-must-not-export')
    data = {key: {} for key in ('browser', 'os', 'window', 'requests', 'nativeDragging', 'physicalAndroid', 'macOS')}
    result = base / 'installed-pwa.json'
    result.write_text(json.dumps(data))
    png = base / 'installed-pwa.png'
    png.write_bytes(b'\x89PNG\r\n\x1a\nIEND\xaeB`\x82')
    if change == 'missing':
        result.unlink()
    elif change == 'duplicate':
        (base / 'other').mkdir()
        (base / 'other/installed-pwa.json').write_text(json.dumps(data))
    elif change == 'symlink':
        result.unlink()
        result.symlink_to(base / 'browser-profile/cookies')
    elif change == 'oversized':
        result.write_bytes(b'x' * (1024 * 1024 + 1))
    elif change == 'bad-json':
        result.write_text('{"cookies":"unexpected"}')
    elif change == 'bad-png':
        png.write_bytes(b'partial')
    if change in {'none', 'missing'}:
        report = adapter.retain_pwa_artifacts(base, evidence)
        assert report['missing'] == (['installed-pwa.json'] if change == 'missing' else [])
        assert {row['name'] for row in report['retained']} == (
            {'installed-pwa.png'} if change == 'missing' else {'installed-pwa.json', 'installed-pwa.png'})
    else:
        with pytest.raises(ValueError):
            adapter.retain_pwa_artifacts(base, evidence)
    assert not (evidence / 'browser-profile').exists()


@pytest.mark.parametrize('guard_rejects', [False, True])
def test_failed_fixed_stage_preserves_raw_result_and_outer_attestation(monkeypatch, tmp_path, guard_rejects):
    monkeypatch.setattr(adapter, 'GUEST', tmp_path)
    monkeypatch.setattr(adapter, 'in_container', lambda: True)
    monkeypatch.setattr(adapter.os, 'getuid', lambda: 0)
    for name in ('prepared-evidence', 'evidence', 'run', 'tmp'):
        (tmp_path / name).mkdir()
    def command(argv, **kwargs):
        if 'check' in argv:
            receipt = json.loads((tmp_path / 'evidence/fixed-stage-result.json').read_text())
            assert receipt['exitCode'] == 0 and receipt['cases'][0]['outcome'] == 'skipped'
            assert receipt['passed'] is False
            if guard_rejects:
                raise subprocess.CalledProcessError(1, argv)
    monkeypatch.setattr(adapter, 'command', command)
    monkeypatch.setattr(adapter, 'root_guest_preflight', lambda env: {'fixture': True})

    def run(argv, **kwargs):
        path = tmp_path / 'evidence/junit.xml'
        xml = fixed_junit(path, 'root-guest')
        ET.SubElement(xml[0], 'skipped')
        ET.ElementTree(xml).write(path)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(adapter.subprocess, 'run', run)
    error = subprocess.CalledProcessError if guard_rejects else ValueError
    with pytest.raises(error):
        adapter.guest_test(root_guest=True)
    assert not json.loads((tmp_path / 'evidence/result.json').read_text())['passed']
    assert json.loads((tmp_path / 'evidence/result.json').read_text())['attestation']
    fixed = json.loads((tmp_path / 'evidence/fixed-stage-result.json').read_text())
    assert fixed['passed'] is False and fixed['exitCode'] == 0
    assert len(fixed['cases']) == 8 and fixed['cases'][0]['outcome'] == 'skipped'
    assert ET.parse(tmp_path / 'evidence/junit.xml').find('.//skipped') is not None


def test_pwa_uninstall_failure_still_closes_context_without_hiding_failure():
    import ast
    import asyncio
    from unittest.mock import AsyncMock
    tree = ast.parse((ROOT / 'tests/test_browser_auth.py').read_text())
    test = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'test_installed_pwa_launch')
    cleanup = next(n.finalbody for n in ast.walk(test) if isinstance(n, ast.Try) and n.finalbody
                   and 'PWA.uninstall' in ast.unparse(n.finalbody))
    function = ast.AsyncFunctionDef(name='cleanup', args=ast.arguments(
        posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]), body=cleanup, decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    context = type('Context', (), {'close': AsyncMock()})()
    cdp = type('CDP', (), {'send': AsyncMock(side_effect=RuntimeError('synthetic uninstall failure'))})()
    namespace = {'installed': True, 'cdp': cdp, 'context': context, 'origin': 'http://127.0.0.1:12345'}
    exec(compile(module, '<actual-pwa-cleanup>', 'exec'), namespace)
    with pytest.raises(RuntimeError, match='synthetic uninstall failure'):
        asyncio.run(namespace['cleanup']())
    context.close.assert_awaited_once()


def test_fixed_stage_workflow_guards_and_preparation_are_scoped():
    workflow = yaml.load((ROOT / '.github/workflows/default-installed.yml').read_text(), Loader=yaml.BaseLoader)
    bridge = yaml.load((ROOT / '.github/workflows/integration-checks.yml').read_text(), Loader=yaml.BaseLoader)
    for name in ('root_guest', 'installed_pwa'):
        assert workflow['on']['workflow_dispatch']['inputs'][name]['default'] == 'false'
        assert workflow['on']['workflow_call']['inputs'][name]['default'] == 'false'
        assert bridge['on']['workflow_dispatch']['inputs'][name]['default'] == 'false'
        assert bridge['jobs']['default-installed']['with'][name] == '${{ inputs.' + name + ' }}'
    job = workflow['jobs']['default-installed']
    assert job['timeout-minutes'] == '30'
    steps = job['steps']
    guard = next(i for i, s in enumerate(steps) if s.get('name') == 'Reject conflicting fixed stages before preparation')
    private = next(i for i, s in enumerate(steps) if s.get('with', {}).get('repository') == 'microsoft/amplifier-module-loop-live')
    assert guard < private
    assert 'exit 1' in steps[guard]['run']
    source = (ROOT / 'scripts/qualify_default_installed.py').read_text()
    assert "client_helpers().browser_prepare_commands" in source and "'--lane', 'browser-installed'" in source
    assert "timeout=300" in source and "timeout=420" in source
    assert "Preparation must stop before tests" in source
    assert "snapshot['wheels']" in source and "snapshot['browsers']" in source and "snapshot['system']" in source


def test_pwa_sbin_path_is_preparation_only(monkeypatch):
    monkeypatch.setenv('PRIVATE_TOKEN', 'synthetic-must-not-leak')
    original = adapter.clean_env()
    prepared = adapter.pwa_preparation_environment(original)
    assert '/usr/sbin' in prepared['PATH'].split(':') and '/sbin' in prepared['PATH'].split(':')
    assert prepared['PLAYWRIGHT_BROWSERS_PATH'] == str(adapter.GUEST / 'browsers')
    assert original == adapter.clean_env()
    assert '/usr/sbin' not in original['PATH'].split(':') and 'PRIVATE_TOKEN' not in prepared


def test_root_evidence_copy_cannot_widen_receipt_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, 'GUEST', tmp_path)
    prepared, evidence = tmp_path / 'prepared-evidence', tmp_path / 'evidence'
    prepared.mkdir(mode=0o755)
    prepared.chmod(0o755)
    (prepared / 'receipt.json').write_text('{"prepared":true}')
    evidence.mkdir(mode=0o700)
    adapter.copy_prepared_evidence(evidence, 'root-guest')
    assert evidence.stat().st_mode & 0o777 == 0o700
    assert (evidence / 'receipt.json').read_bytes() == (prepared / 'receipt.json').read_bytes()


def test_pwa_preflight_records_effective_environment_and_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, 'GUEST', tmp_path)
    (tmp_path / 'candidate/frontend').mkdir(parents=True)
    (tmp_path / 'candidate/frontend/package-lock.json').write_text(json.dumps({
        'packages': {'node_modules/playwright': {'version': '1.63.0'}}}))
    python = tmp_path / '.venv/bin/python'
    original = {'actual_interpreter': str(python), 'interpreter': '/unused/env/bin/python',
                'environment': {'PLAYWRIGHT_BROWSERS_PATH': '/unused/browsers', 'HOME': '/unused/home'},
                'prerequisites': {'playwright_version': '1.63.0', 'chromium': str(tmp_path / 'browsers/chromium/chrome')}}
    env = {'PLAYWRIGHT_BROWSERS_PATH': str(tmp_path / 'browsers'), 'HOME': str(tmp_path / 'home'),
           'DISPLAY': ':99', 'XDG_RUNTIME_DIR': str(tmp_path / 'run'), 'PRIVATE_TOKEN': 'not-retained'}
    actual = adapter.pwa_preflight_receipt(original, env, python)
    assert actual['interpreter'] == str(python)
    assert actual['environment'] == {k: v for k, v in env.items() if k != 'PRIVATE_TOKEN'}
    actual['prerequisites']['playwright_version'] = '0.0.0'
    with pytest.raises(ValueError, match='lock-matched'):
        adapter.pwa_preflight_receipt(actual, env, python)
