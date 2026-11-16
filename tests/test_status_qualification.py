"""Installed status qualification must fail closed rather than reuse weak proof."""
import io
import json
from pathlib import Path
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import qualify_status_emitter as owner
sys.path.pop(0)


def test_candidate_wheel_must_match_reviewed_source(tmp_path):
    source = tmp_path/'source'; source.mkdir()
    (source/'__init__.py').write_text('original')
    wheel = tmp_path/'candidate.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('amplifier_status/__init__.py', 'original')
    assert owner.matching_source(wheel, 'amplifier_status', source) == 1
    (source/'__init__.py').write_text('changed after wheel build')
    with pytest.raises(ValueError, match='differs'):
        owner.matching_source(wheel, 'amplifier_status', source)
    (source/'__init__.py').write_text('original')
    (source/'new_module.py').write_text('new module missing in stale wheel')
    with pytest.raises(ValueError, match='omits'):
        owner.matching_source(wheel, 'amplifier_status', source)
    with pytest.raises(ValueError, match='no expected package files'):
        owner.matching_source(wheel, 'different', source)


def test_standalone_tests_are_source_only_but_runtime_completeness_remains(tmp_path):
    prefix, test_root = 'amplifier_status', 'tests'
    source = tmp_path/'components/status'; source.mkdir(parents=True)
    (source/'__init__.py').write_text('runtime')
    tests = source/test_root; tests.mkdir(parents=True)
    (tests/'test_standalone.py').write_text('installed-only test')
    wheel = tmp_path/'candidate.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr(prefix+'/__init__.py', 'runtime')
    assert owner.matching_source(wheel, prefix, source) == 1
    missing = source/'new_runtime.py'; missing.write_text('must still ship')
    with pytest.raises(ValueError, match='omits.*new_runtime.py'):
        owner.matching_source(wheel, prefix, source)
    missing.unlink()
    with zipfile.ZipFile(wheel, 'a') as archive:
        archive.writestr(prefix+'/'+test_root+'/test_standalone.py', 'installed-only test')
    with pytest.raises(ValueError, match='includes standalone tests'):
        owner.matching_source(wheel, prefix, source)


def test_private_status_runtime_has_no_author_test_exemption(tmp_path):
    source = tmp_path/'amplifier_web'
    private = source/'status_protocol'; private.mkdir(parents=True)
    (private/'__init__.py').write_text('selected runtime')
    extra = private/'tests/test_unselected.py'; extra.parent.mkdir()
    extra.write_text('unselected source')
    wheel = tmp_path/'candidate.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('amplifier_web/status_protocol/__init__.py', 'selected runtime')
    with pytest.raises(ValueError, match='omits.*test_unselected.py'):
        owner.matching_source(wheel, 'amplifier_web', source)


def test_core_native_wheel_must_have_exact_publication_digest(monkeypatch):
    identity = {'version':'2.0.2', 'path':'/owned/core.whl', 'sha256':'expected'}
    def registry(digest):
        return io.BytesIO(json.dumps({'urls':[{'filename':'core.whl',
            'digests':{'sha256':digest},'packagetype':'bdist_wheel'}]}).encode())
    monkeypatch.setattr(owner.urllib.request, 'urlopen', lambda *a, **kw: registry('different'))
    with pytest.raises(ValueError, match='published registry wheel'):
        owner.published_core(identity)
    monkeypatch.setattr(owner.urllib.request, 'urlopen', lambda *a, **kw: registry('expected'))
    assert owner.published_core(identity)['wheel'] == identity


def test_candidate_force_included_bundle_files_bind_original_source(tmp_path):
    source = tmp_path/'package'; source.mkdir()
    (source/'__init__.py').write_text('original')
    external = tmp_path/'bundle.yaml'; external.write_text('exact reviewed bundle')
    wheel = tmp_path/'candidate.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('package/__init__.py', 'original')
        archive.writestr('package/bundle_data/bundle.yaml', external.read_text())
    mapping = {'package/bundle_data/bundle.yaml': external}
    assert owner.matching_source(wheel, 'package', source, mapping) == 2
    external.write_text('changed bundle')
    with pytest.raises(ValueError, match='differs'):
        owner.matching_source(wheel, 'package', source, mapping)


def test_wrong_host_and_reused_work_directory_refuse_before_effects(tmp_path, monkeypatch):
    work = tmp_path/'owned'
    argv = ['--owned-dtu','owned-dtu','--python',sys.executable,'--cli-python',sys.executable,
            '--unified-wheel','missing','--status-wheel','missing','--core-wheel','missing',
            '--work',str(work)]
    monkeypatch.setattr(owner.socket, 'gethostname', lambda:'wrong-host')
    with pytest.raises(ValueError, match='owned DTU'):
        owner.main(argv)
    assert not work.exists()
    monkeypatch.setattr(owner.socket, 'gethostname', lambda:'owned-dtu')
    work.mkdir(); receipt = work/'result.json'; receipt.write_text('old successful receipt')
    with pytest.raises(FileExistsError):
        owner.main(argv)
    assert receipt.read_text() == 'old successful receipt'
