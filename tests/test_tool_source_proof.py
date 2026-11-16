"""Qualification receipt/package binding; fixtures do not claim real-tool proof."""
import base64
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import sys
from types import ModuleType, SimpleNamespace

import pytest

from amplifier_web.tool_approval_scope import _source
from amplifier_web.tool_source_proof import QualifiedToolSources


@pytest.fixture
def package(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    project = home / 'runtime' / ('q-' + '1' * 32)
    prefix = project / '.venv'
    site = prefix / 'lib/python3.13/site-packages'
    receipt = home / 'updates/releases' / ('2' * 32)
    source = receipt / 'foundation/cache/reference'
    name = 'amplifier-module-tool-filesystem'
    package_name = 'amplifier_module_tool_filesystem'
    installed = site / package_name
    mounted = source / package_name
    for path in (installed, mounted):
        path.mkdir(parents=True)
        (path / '__init__.py').write_text('')
        (path / 'read.py').write_text('class ReadTool:\n async def execute(self, args):\n  return args\n')
        (path / 'helper.py').write_text('meaning = 1\n')
    (source / '.amplifier_cache_meta.json').write_text(json.dumps(
        {'commit': 'a' * 40, 'git_url': 'https://example.invalid/reference'}))
    info = site / 'amplifier_module_tool_filesystem-1.0.dist-info'
    info.mkdir()
    (info / 'METADATA').write_text(f'Name: {name}\nVersion: 1.0\n')
    direct = {'url': source.as_uri(), 'dir_info': {'editable': False}}
    (info / 'direct_url.json').write_text(json.dumps(direct))
    def record():
        rows = []
        for path in sorted(installed.iterdir()):
            raw = path.read_bytes()
            digest = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip('=')
            rows.append(f'{package_name}/{path.name},sha256={digest},{len(raw)}')
        (info / 'RECORD').write_text('\n'.join(rows) + '\n')
    record()
    expected = [{'name': name, 'version': '1.0', 'directUrl': direct, 'path': str(source),
                 'cacheSource': {'url': 'https://example.invalid/reference', 'revision': 'a' * 40,
                                 'ref': 'main', 'dirty': False, 'subdirectory': '.',
                                 'trackedContentSha256': 'b' * 64}}]
    (receipt / 'runtime-project.json').write_text(json.dumps({'project': project.name}))
    (receipt / 'runtime-installed.json').write_text(json.dumps(expected))
    (receipt / 'runtime.lock').write_text('exact fixture lock\n')
    (project / 'uv.lock').write_text('exact fixture lock\n')
    monkeypatch.setattr(sys, 'prefix', str(prefix))
    distribution = importlib.metadata.PathDistribution(info)
    original = importlib.metadata.distribution
    monkeypatch.setattr(importlib.metadata, 'distribution',
                        lambda value: distribution if value == name else original(value))
    def load(origin=mounted):
        module = ModuleType(package_name + '.read')
        module.__file__ = str(origin / 'read.py')
        exec(compile((origin / 'read.py').read_text(), module.__file__, 'exec'), module.__dict__)
        monkeypatch.setitem(sys.modules, module.__name__, module)
        return module.ReadTool()
    return SimpleNamespace(home=home, project=project, receipt=receipt, prefix=prefix,
        installed=installed, mounted=mounted, source=source, info=info, direct=direct,
        expected=expected, distribution=distribution, record=record, load=load,
        capture=lambda: QualifiedToolSources.capture(project, receipt, expected))


@pytest.mark.parametrize('origin', ['installed', 'mounted'])
def test_noneditable_local_wheel_matches_exact_qualified_package(package, origin):
    proof = package.capture()
    source, kind = _source(package.load(getattr(package, origin)), proof)
    assert kind == 'read' and source['selection'] == package.direct
    assert source['qualifiedSource']['revision'] == 'a' * 40
    assert len(source['packageContent']) == len(source['implementation']) == 64
    assert proof.same(package.capture())


@pytest.mark.parametrize('subdirectory', ['.', 'modules/tool-apply-patch'])
def test_source_store_build_wheel_and_distinct_mounted_object_share_exact_payload(package, subdirectory):
    build_root = package.home / 'source-store/builds/selected'
    object_root = package.home / 'source-store/objects/selected'
    for root in (build_root, object_root):
        shutil.copytree(package.source, root / subdirectory)
        if subdirectory != '.':
            shutil.copy2(package.source / '.amplifier_cache_meta.json', root / '.amplifier_cache_meta.json')
    selected = build_root / subdirectory
    package.direct['url'] = selected.as_uri()
    package.expected[0]['path'] = str(selected)
    package.expected[0]['cacheSource']['subdirectory'] = subdirectory
    (package.info / 'direct_url.json').write_text(json.dumps(package.direct))
    (package.receipt / 'runtime-installed.json').write_text(json.dumps(package.expected))
    mounted = object_root / subdirectory / package.mounted.name
    source, kind = _source(package.load(mounted), package.capture())
    assert kind == 'read' and source['selection'] == package.direct
    assert source['qualifiedSource']['subdirectory'] == subdirectory


@pytest.mark.parametrize('fault', ['prefix', 'missing-receipt', 'different-receipt',
                                  'different-lock', 'editable', 'dirty', 'no-source', 'bad-record'])
def test_unqualified_or_changed_inputs_never_supply_source(package, monkeypatch, fault):
    if fault == 'prefix': monkeypatch.setattr(sys, 'prefix', '/different/runtime')
    elif fault == 'missing-receipt': (package.receipt / 'runtime-installed.json').unlink()
    elif fault == 'different-receipt': (package.receipt / 'runtime-installed.json').write_text('[]')
    elif fault == 'different-lock': (package.project / 'uv.lock').write_text('different')
    elif fault == 'bad-record': (package.info / 'RECORD').write_text('"unterminated')
    else:
        if fault == 'editable':
            package.direct['dir_info']['editable'] = True
            (package.info / 'direct_url.json').write_text(json.dumps(package.direct))
        elif fault == 'dirty': package.expected[0]['cacheSource']['dirty'] = True
        else: package.expected[0].pop('cacheSource')
        (package.receipt / 'runtime-installed.json').write_text(json.dumps(package.expected))
    with pytest.raises(ValueError):
        _source(package.load(), package.capture())


@pytest.mark.parametrize('fault', ['installed', 'mounted', 'extra-file', 'record',
                                  'changed-record-and-bytes', 'foreign-origin', 'changed-receipt'])
def test_package_recheck_refuses_drift_after_qualification(package, tmp_path, fault):
    proof = package.capture()
    tool = package.load()
    _source(tool, proof)
    if fault in {'installed', 'mounted'}:
        (getattr(package, fault) / 'helper.py').write_text('meaning = 2\n')
    elif fault == 'extra-file': (package.mounted / 'surprise.py').write_text('effect = True\n')
    elif fault == 'record': (package.info / 'RECORD').unlink()
    elif fault == 'changed-record-and-bytes':
        for path in (package.installed, package.mounted):
            (path / 'helper.py').write_text('meaning = 2\n')
        package.record()
    elif fault == 'foreign-origin':
        other = tmp_path / 'unselected' / package.mounted.name
        shutil.copytree(package.mounted, other)
        tool = package.load(other)
    else: (package.receipt / 'runtime-installed.json').unlink()
    with pytest.raises((OSError, ValueError)):
        _source(tool, proof)


@pytest.mark.parametrize('fault', ['no-proof', 'wrong-module', 'replaced', 'rebound'])
def test_known_loaded_implementation_is_still_required(package, monkeypatch, fault):
    proof = package.capture()
    tool = package.load()
    if fault == 'no-proof': proof = None
    elif fault == 'wrong-module':
        monkeypatch.setattr(sys.modules[type(tool).__module__], '__file__', str(package.mounted / 'helper.py'))
    elif fault == 'replaced': tool.execute = lambda args: args
    else: tool.execute = type(tool)().execute
    with pytest.raises(ValueError):
        _source(tool, proof)
