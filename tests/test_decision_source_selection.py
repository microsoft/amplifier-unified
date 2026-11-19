"""Finite authored source-map provenance, not live/installed image qualification.

The namespace and shared reader are explicit semantic owners, not inferred
transitive globals of a reviewed callback. The selector's optional minimum-name
sets are not a completeness proof; omission is caught by this authored catalog
check, separately from existing required-name/stale/callback runtime refusals.
"""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from amplifier_web.host import media_assembly as media
import amplifier_web


ROOT = Path(amplifier_web.__file__).resolve().parent.parent
MANIFEST = ROOT / 'amplifier_web/host/media_sources.json'
REPLACED = {
    'amplifier_web/host/config.py',
    'amplifier_web/host/session.py',
    'amplifier_web/management.py',
}
ADDED = {
    'amplifier_web/decision_namespace.py',
    'amplifier_web/shared_settings.py',
    'amplifier_web/provider_catalog.py',
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def authored_catalog(value):
    sources = value['sources']
    assert len(sources) == 312
    assert REPLACED | ADDED <= sources.keys()
    unchanged = {key: row for key, row in sources.items() if key not in REPLACED | ADDED}
    # The combined catalog also retains the accepted Context transition
    # implementation and four explicitly selected transition semantic owners.
    assert len(unchanged) == 306
    assert digest(unchanged) == '925e6704c45f8498acd5aa6a73374ae0604fc36725eb7d9ce24fa08641a7e292'
    assert digest({key: row for key, row in value.items() if key != 'sources'}) == (
        'f83fb6147e8ffec31841b4cdb94fe4d621441a6db0edd6a2568bdcf27d45c6bb')
    for key in REPLACED | ADDED:
        assert hashlib.sha256((ROOT / key).read_bytes()).hexdigest() == sources[key]


def test_reviewed_decision_source_catalog_retains_exact_complement_and_owner_bytes():
    authored_catalog(json.loads(MANIFEST.read_text()))


@pytest.mark.parametrize('owner', sorted(ADDED))
def test_omitted_semantic_owner_fails_authored_catalog_not_an_invented_runtime_minimum(owner):
    value = json.loads(MANIFEST.read_text())
    del value['sources'][owner]
    with pytest.raises(AssertionError):
        authored_catalog(value)


@pytest.mark.parametrize('fault', ['pending', 'missing-required', 'stale-declared'])
def test_shipped_selector_refuses_unreviewed_missing_or_stale_sources(tmp_path, monkeypatch, fault):
    # Test the real selector refusal, not a synthetic positive receipt. Installed
    # qualification separately supplies its actual supported Work plan/files.
    monkeypatch.setattr(media, 'supported_plan', lambda plan: True)
    monkeypatch.setattr(media, 'ordinary_work_plan', lambda plan: True)
    value = json.loads(MANIFEST.read_text())
    if fault == 'pending':
        value['status'] = 'pending'
    elif fault == 'missing-required':
        del value['sources'][next(iter(media._REQUIRED_SOURCES))]
    else:
        package = tmp_path / 'fixture'
        package.mkdir()
        source = package / '__init__.py'
        source.write_text('value = "actual-unreviewed-bytes"\n')
        monkeypatch.setattr(importlib.util, 'find_spec', lambda name: SimpleNamespace(origin=str(source)))
    original = Path.read_text
    def read(path, *args, **kwargs):
        return json.dumps(value) if path.name == 'media_sources.json' else original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read)
    assert media.select_root({'providers': [{'module': 'provider-openai'}]}) is None