"""Qualification receipt/package binding; fixtures do not claim real-tool proof."""
import base64
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import sys
from types import ModuleType, SimpleNamespace

import pytest

from amplifier_web import session_files, tool_source_proof
from amplifier_web.tool_approval_scope import _source
from amplifier_web.tool_source_proof import QualifiedToolSources


@pytest.fixture
def package(tmp_path, monkeypatch, request):
    home = tmp_path / 'home'
    project = home / 'runtime' / ('q-' + '1' * 32)
    prefix = project / '.venv'
    site = prefix / 'lib/python3.13/site-packages'
    receipt = home / 'updates/releases' / ('2' * 32)
    source = receipt / 'foundation/cache/reference'
    role = getattr(request, 'param', 'filesystem')
    name = 'amplifier-module-tool-' + role
    package_name = name.replace('-', '_')
    class_name = 'ReadTool' if role == 'filesystem' else 'SelectedTool'
    installed = site / package_name
    mounted = source / package_name
    for path in (installed, mounted):
        path.mkdir(parents=True)
        (path / '__init__.py').write_text('')
        (path / 'read.py').write_text(f'class {class_name}:\n async def execute(self, args):\n'
                                     '  def nested(value):\n   return value\n  return nested(args)\n')
        (path / 'helper.py').write_text('meaning = 1\n')
    (source / '.amplifier_cache_meta.json').write_text(json.dumps(
        {'commit': 'a' * 40, 'git_url': 'https://example.invalid/reference'}))
    info = site / (package_name + '-1.0.dist-info')
    info.mkdir()
    (info / 'METADATA').write_text(f'Name: {name}\nVersion: 1.0\n')
    (info / 'entry_points.txt').write_text(f'[amplifier.modules]\ntool-{role} = {package_name}:mount\n')
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
        return getattr(module, class_name)()
    return SimpleNamespace(home=home, project=project, receipt=receipt, prefix=prefix,
        installed=installed, mounted=mounted, source=source, info=info, direct=direct,
        expected=expected, distribution=distribution, record=record, load=load,
        capture=lambda: QualifiedToolSources.capture(project, receipt, expected))


@pytest.mark.parametrize('origin', ['installed', 'mounted'])
def test_noneditable_local_wheel_matches_exact_qualified_package(package, origin):
    proof = package.capture()
    source, kind = _source(package.load(getattr(package, origin)), proof)
    assert kind == 'read' and 'selection' not in source and 'origin' not in source
    assert source['modules'] == {'tool-filesystem': 'amplifier_module_tool_filesystem:mount'}
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
    assert kind == 'read' and 'selection' not in source
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


@pytest.mark.parametrize('package', ['bash', 'catalog'], indirect=True)
def test_other_selected_module_owns_its_python_tool(package):
    source, kind = _source(package.load(), package.capture())
    assert kind == 'ordinary'  # Fixture class, not the canonical BashTool.
    assert source['distribution'] == package.expected[0]['name']
    assert set(source['modules']) == {package.expected[0]['name'].replace('amplifier-module-', '')}


def test_same_selected_package_under_another_cache_path_has_same_semantics(package):
    before, _ = _source(package.load(), package.capture())
    relocated = package.source.parent / 'relocated'
    shutil.copytree(package.source, relocated)
    package.direct['url'] = relocated.as_uri()
    package.expected[0]['path'] = str(relocated)
    (package.info / 'direct_url.json').write_text(json.dumps(package.direct))
    (package.receipt / 'runtime-installed.json').write_text(json.dumps(package.expected))
    after, _ = _source(package.load(relocated / package.mounted.name), package.capture())
    assert before == after


def test_module_entrypoint_change_invalidates_private_source_proof(package):
    proof = package.capture()
    (package.info / 'entry_points.txt').write_text('[amplifier.modules]\nother = elsewhere:mount\n')
    with pytest.raises(ValueError):
        _source(package.load(), proof)


@pytest.mark.parametrize('form', ['registry', 'archive'])
def test_qualified_locked_wheel_does_not_require_a_source_cache(package, form):
    row = package.expected[0]
    row.pop('path'); row.pop('cacheSource')
    digest = 'c' * 64
    url = 'https://packages.invalid/tool.whl'
    direct = {} if form == 'registry' else {'url': url, 'archive_info': {'hashes': {'sha256': digest}}}
    row['directUrl'] = direct
    (package.info / 'direct_url.json').write_text(json.dumps(direct))
    (package.receipt / 'runtime-installed.json').write_text(json.dumps(package.expected))
    source = '{registry = "https://packages.invalid/simple"}' if form == 'registry' else '{url = "' + url + '"}'
    lock = ('[[package]]\nname = "' + row['name'] + '"\nversion = "1.0"\nsource = ' + source
            + '\nwheels = [{url = "' + url + '", hash = "sha256:' + digest + '"}]\n')
    (package.receipt / 'runtime.lock').write_text(lock)
    (package.project / 'uv.lock').write_text(lock)
    proof = package.capture()
    described, _ = _source(package.load(package.installed), proof)
    assert described['qualifiedSource']['lockedArtifactHashes'] == ['sha256:' + digest]
    with pytest.raises(ValueError):
        _source(package.load(), proof)  # Unselected source import remains unsupported.
    (package.installed / 'helper.py').write_text('changed = True\n')
    package.record()  # A rewritten RECORD cannot change the captured baseline.
    with pytest.raises(ValueError):
        _source(package.load(package.installed), proof)


def test_unlocked_noneditable_local_package_is_not_silently_trusted(package):
    package.expected[0].pop('cacheSource')
    (package.receipt / 'runtime-installed.json').write_text(json.dumps(package.expected))
    with pytest.raises(ValueError):
        _source(package.load(package.installed), package.capture())


def _windows_stat_clocks(monkeypatch, *, changed=None, missing=None):
    """Retain real file identity/bytes while modeling CPython's two clocks."""
    original_stat, original_fstat = Path.stat, os.fstat
    state = {'read': False}
    def modeled(info, family):
        values = {key: getattr(info, key) for key in dir(info) if key.startswith('st_')}
        values.update(st_birthtime_ns=100, st_ctime_ns=200 if family == 'fd' else 100)
        if state['read'] and changed and changed[0] == family:
            values[changed[1]] += 1
        if missing == family:
            values.pop('st_birthtime_ns')
        return SimpleNamespace(**values)
    monkeypatch.setattr(session_files, '_WINDOWS', True)
    monkeypatch.setattr(Path, 'stat', lambda self, **kwargs: modeled(original_stat(self, **kwargs), 'path'))
    monkeypatch.setattr(os, 'fstat', lambda fd: modeled(original_fstat(fd), 'fd'))
    return state


def _observe_file_read(monkeypatch, path, *, after_read=lambda: None, after_close=lambda: None):
    original_open = Path.open
    class Observed:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): return self
        def __exit__(self, *args):
            result = self.stream.__exit__(*args)
            after_close()
            return result
        def fileno(self): return self.stream.fileno()
        def read(self, limit):
            value = self.stream.read(limit)
            after_read()
            return value
    def opened(self, mode='r', *args, **kwargs):
        stream = original_open(self, mode, *args, **kwargs)
        return Observed(stream) if self == path and mode == 'rb' else stream
    monkeypatch.setattr(Path, 'open', opened)


@pytest.mark.parametrize('windows', [False, True])
def test_distinct_windows_stat_clocks_preserve_qualified_package(package, monkeypatch, windows):
    _windows_stat_clocks(monkeypatch)
    monkeypatch.setattr(session_files, '_WINDOWS', windows)
    tool = package.load()
    if windows:
        proof = package.capture()
        assert _source(tool, proof)[1] == 'read'
        assert proof.same(package.capture())
    else:
        # POSIX ctime must remain equal across named and descriptor metadata.
        assert package.capture() is None


@pytest.mark.parametrize('changed', [('fd', 'st_ctime_ns'), ('path', 'st_ctime_ns'),
                                    ('fd', 'st_birthtime_ns'), ('path', 'st_birthtime_ns')])
def test_windows_same_family_clock_drift_is_refused(tmp_path, monkeypatch, changed):
    path = tmp_path / 'receipt'
    path.write_bytes(b'original')
    state = _windows_stat_clocks(monkeypatch, changed=changed)
    _observe_file_read(monkeypatch, path, after_read=lambda: state.update(read=True))
    with pytest.raises(ValueError):
        tool_source_proof._read(path, tmp_path)


@pytest.mark.parametrize('missing', ['fd', 'path'])
def test_missing_windows_birthtime_never_supplies_optional_proof(package, monkeypatch, missing):
    _windows_stat_clocks(monkeypatch, missing=missing)
    assert package.capture() is None


@pytest.mark.parametrize('change', ['edit', 'replace', 'remove'])
def test_windows_named_file_mutation_after_read_is_refused(tmp_path, monkeypatch, change):
    path = tmp_path / 'receipt'
    path.write_bytes(b'original')
    before = path.stat()
    replacement = tmp_path / 'replacement'
    replacement.write_bytes(b'original')
    os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
    _windows_stat_clocks(monkeypatch)
    def mutate():
        if change == 'edit':
            path.write_bytes(b'changed!')  # Same length; mtime still fences it.
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))
        elif change == 'replace': replacement.replace(path)  # Same bytes/mtime, new object.
        else: path.unlink()
    _observe_file_read(monkeypatch, path, after_close=mutate)
    with pytest.raises((OSError, ValueError)):
        tool_source_proof._read(path, tmp_path)


@pytest.mark.parametrize('fault', ['symlink', 'oversize'])
def test_read_retains_symlink_and_size_refusal(tmp_path, fault):
    path = tmp_path / 'receipt'
    path.write_bytes(b'original')
    if fault == 'symlink':
        link = tmp_path / 'link'
        try:
            link.symlink_to(path)
        except OSError:
            pytest.skip('This account cannot create symlinks')
        path = link
    with pytest.raises(ValueError):
        tool_source_proof._read(path, tmp_path, 3 if fault == 'oversize' else 8)
