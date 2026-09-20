import json

from amplifier_web.bundles import BundleManager, document_metadata
from amplifier_web.bundle_selection import defaults
from amplifier_web.host.config import load_config
from amplifier_web.service import AppService


async def test_picker_uses_manifest_labels_and_sorts_without_changing_aliases(tmp_path):
    manager = BundleManager(tmp_path / 'app')
    first = tmp_path / 'first.yaml'
    first.write_text('bundle:\n  name: declared-id\n  display_name: Zebra Research\n  description: Research tools\n')
    second = tmp_path / 'second.md'
    second.write_text('---\nbundle:\n  name: other-id\n  display_name: Aardvark Research\n---\nInstructions')
    manager.store.update(tmp_path, 'global', lambda s: s.update(bundle={'added': {
        'aaa': str(first), 'zzz': str(second)}}))
    result = await manager.perform('bundles.list', {'workspace': str(tmp_path)})
    rows = result['registeredBundles']
    assert rows[0]['name'] == 'zzz'
    assert next(row for row in rows if row['name'] == 'aaa') == {
        'name': 'aaa', 'value': 'aaa', 'label': 'Zebra Research', 'description': 'Research tools'}
    assert [row['label'].casefold() for row in rows] == sorted(row['label'].casefold() for row in rows)


async def test_cached_labels_follow_selected_uri_and_legacy_registry_still_works(tmp_path):
    manager = BundleManager(tmp_path / 'app')
    uri = 'git+https://github.com/example/research@main'
    old = tmp_path / 'cached.yaml'
    old.write_text('bundle:\n  name: research\n  display_name: Old cached label\n')
    manager.store.update(tmp_path, 'global', lambda s: s.update(bundle={'added': {
        'research': uri, 'legacy': uri + '#subdirectory=other'}}))
    config = load_config(tmp_path, home=manager.home)
    config.registry_home.mkdir(parents=True)
    registry = {'bundles': {
        'research': {'uri': uri, 'display_name': 'Research lab'},
        'legacy': {'uri': uri + '#subdirectory=other'},
    }}
    path = config.registry_home / 'registry.json'
    path.write_text(json.dumps(registry))
    async def labels():
        result = await manager.perform('bundles.list', {'workspace': str(tmp_path)})
        return {row['name']: row['label'] for row in result['registeredBundles']}
    assert (await labels())['research'] == 'Research lab'
    assert (await labels())['legacy'] == 'legacy'
    registry['bundles']['research'].update(uri=uri + '-old', local_path=str(old))
    path.write_text(json.dumps(registry))
    assert (await labels())['research'] == 'research'


def test_discovery_reads_display_metadata_but_keeps_identity():
    result = document_metadata('bundle:\n  name: research\n  display_name: Research lab\n', 'bundle.yaml')
    assert result['name'] == 'research' and result['display_name'] == 'Research lab'


async def test_clean_install_defaults_to_work_and_keeps_saved_choices(tmp_path):
    home = tmp_path / 'app'
    assert defaults(home, tmp_path)['effective'] == 'work'
    assert load_config(tmp_path, home=home).active_bundle == 'work'
    app = AppService(home, workspace=tmp_path)
    try:
        assert app._new_session({})['bundle'] == 'work'
        await app.dispatch('bundle.default', {'scope': 'app', 'bundle': 'anchors'})
        await app.dispatch('session.create', {})
        assert app._session()['bundle'] == 'anchors'
    finally:
        await app.close()
    reopened = AppService(home, workspace=tmp_path)
    try:
        assert reopened._new_session({})['bundle'] == 'anchors'
        assert reopened._session()['bundle'] == 'anchors'
    finally:
        await reopened.close()
