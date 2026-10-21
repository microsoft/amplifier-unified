"""The composer qualification is a fixed selection, not an arbitrary runner."""
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import compact_composer_checks as checks


def test_fixed_source_and_installed_selections(tmp_path):
    source = checks.plan(tmp_path)
    assert [r['id'] for r in source['scenarios']] == [
        'composer-units', 'compact-composer', 'composer-options', 'composer-primary-action',
        'composer-readiness', 'message-delivery', 'draft-switch']
    installed = checks.plan(tmp_path, installed=True)
    assert [r['id'] for r in installed['scenarios']] == [
        'compact-composer', 'composer-options', 'composer-primary-action', 'message-delivery']
    assert all(r['command'][0] == 'node' for r in source['scenarios'])
    assert all('AMPLIFIER_TEST_ARTIFACTS' in r['env'] for r in installed['scenarios'])


def test_manifest_binds_every_static_byte(tmp_path):
    (tmp_path / 'assets').mkdir()
    (tmp_path / 'build.json').write_text('{"id":"synthetic"}')
    (tmp_path / 'assets/app.js').write_text('original')
    first = checks.manifest(tmp_path)
    (tmp_path / 'assets/app.js').write_text('changed')
    assert first != checks.manifest(tmp_path)
    assert set(first) == {'build.json', 'assets/app.js'}
