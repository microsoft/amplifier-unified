"""Native shadowing uses exact effective ownership, including incomplete legacy keys."""
import uuid

from amplifier_web.native_catalog import NativeCatalog
from amplifier_web.session_files import amplifier_home
from test_automatic_history import app_factory
from test_native_directory import seed


async def test_native_ownership_keeps_null_keys_duplicates_hidden_and_live_moves(app_factory, tmp_path):
    app = app_factory()
    catalog = NativeCatalog(tmp_path / 'native-owners.sqlite3', amplifier_home())
    for name in ('one', 'two'):
        (tmp_path / name).mkdir()
    projects = [seed(catalog, tmp_path / name, 3) for name in ('one', 'two')]
    app.directory.bind(catalog)
    first_project, first_workspace = projects[0]
    second_project, second_workspace = projects[1]
    owners = []
    for identity, project, native in (
        ('owner-one', first_project, 'saved-00000'),
        ('owner-two', first_project, 'saved-00000'),
        ('owner-no-project', None, 'saved-00001'),
        ('owner-no-identity', second_project, 'saved-00002'),
    ):
        row = app._new_session({'title': identity, 'workspace': first_workspace['path']})
        row.update(id=identity, nativeProject=project, nativeIdentity=native, runtimeSessionId=native)
        owners.append(row)
    app._state['sessions'] = []
    app.directory.sync(owners, app._state)
    # Legacy/incomplete summaries cannot contribute a complete ownership pair.
    app.db.execute('UPDATE history_local_rows SET native_project=NULL WHERE id=?', ('owner-no-project',))
    app.db.execute('UPDATE history_local_rows SET native_id=NULL WHERE id=?', ('owner-no-identity',))
    app.db.commit()
    app.db.execute('PRAGMA automatic_index=OFF')

    def native_id(project, number):
        return uuid.uuid5(uuid.NAMESPACE_URL, f'amplifier-session:{project}/saved-{number:05}').hex

    def visible(state=None):
        sql, params = app.directory.effective(state)
        return {row[0] for row in app.db.execute(sql + ' SELECT id FROM directory_rows', params)}

    native_ids = {native_id(project, number) for project, _ in projects for number in range(3)}
    expected = native_ids - {native_id(first_project, 0)} | {row['id'] for row in owners}
    assert visible() == expected

    # Hiding a shadowing owner does not resurrect its canonical native alias.
    hidden = app.db.execute('SELECT hidden_id FROM history_local_rows WHERE id=?', ('owner-one',)).fetchone()[0]
    assert visible({**app._state, 'hiddenNativeSessions': [hidden]}) == expected - {'owner-one', 'owner-two'}

    # The admitted owner wins over its saved native key; the other duplicate
    # still owns the original pair. Project identity remains part of the key.
    moved = {**owners[0], 'nativeProject': second_project, 'nativeIdentity': 'saved-00002',
             'runtimeSessionId': 'saved-00002', 'workspace': second_workspace['path']}
    app._state['sessions'] = [moved]
    assert visible() == expected - {native_id(second_project, 2)}
    assert [row['id'] for row in app.directory.summary_ids(['owner-one'])] == ['owner-one']
    assert app._state['sessions'] == [moved]
    assert not app.runtime.started and not app.runtime.sent
