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
