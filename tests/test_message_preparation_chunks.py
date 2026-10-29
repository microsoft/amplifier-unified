"""Long saved conversations retain bounded, content-checked preparation."""
import pytest

from amplifier_web import resource_files
from amplifier_web.message_preparation import MessagePreparation


def owner(identity='owner', count=3512):
    return {'id': identity, 'messages': [
        {'id': f'm-{i}', 'role': 'assistant', 'text': 'Synthetic é ' + 'x' * 2200,
         'via': 'text', 'createdAt': float(i), 'nested': {'value': i}}
        for i in range(count)]}


def save(cache, row):
    pending = {}
    result = cache.prepare(row, row['messages'], pending)
    cache.committed(pending, {row['id']: row})
    return result


def test_long_conversation_reuses_preparation_after_checking_every_chunk(monkeypatch):
    cache, row = MessagePreparation(), owner()
    original = resource_files.prepare
    encoded = []
    def prepare(value):
        encoded.append(len(value))
        return original(value)
    monkeypatch.setattr(resource_files, 'prepare', prepare)
    first = save(cache, row)
    assert save(cache, row) is first
    assert encoded == [3512]
    # A borrowed alias in the final chunk must invalidate the saved snapshot.
    row['messages'][-1]['nested']['value'] = -1
    updated = save(cache, row)
    assert updated == original(row['messages']) and updated != first
    assert encoded == [3512, 3512]
    assert save(cache, row) is updated


@pytest.mark.parametrize('index', [0, 127, 128, 3511])
def test_chunk_edges_preserve_typed_values_and_order(index):
    cache, row = MessagePreparation(), owner()
    first = save(cache, row)
    row['messages'][index]['nested'] = {'number': -0.0, 'flag': True}
    after = save(cache, row)
    assert after == resource_files.prepare(row['messages']) and after != first
    previous = cache.entries[row['id']]
    row['messages'][index]['nested'] = {'flag': 1, 'number': 0.0}
    assert save(cache, row) == resource_files.prepare(row['messages'])
    assert cache.entries[row['id']] is not previous


def test_pathological_single_message_depth_and_cycles_keep_fallback():
    cache = MessagePreparation()
    row = {'id': 'large-one', 'messages': [{'nested': [None] * 21000}]}
    assert save(cache, row) == resource_files.prepare(row['messages'])
    assert not cache.entries
    nested = 'leaf'
    for _ in range(33):
        nested = [nested]
    row['messages'] = [{'nested': nested}]
    assert save(cache, row) == resource_files.prepare(row['messages'])
    assert not cache.entries
    row['messages'] = [{} for _ in range(130)]
    row['messages'][-1]['cycle'] = row['messages']
    with pytest.raises(ValueError, match='Circular'):
        save(cache, row)
    assert not cache.entries


def test_long_owners_and_provisional_replacements_keep_shared_memory_bound():
    cache, rows = MessagePreparation(), {}
    for identity in ('one', 'two'):
        rows[identity] = owner(identity)
        pending = {}
        cache.prepare(rows[identity], rows[identity]['messages'], pending)
        cache.committed(pending, rows)
    assert set(cache.entries) == set(rows)
    committed = dict(cache.entries)
    pending = {}
    for identity in ('one', 'three', 'four'):
        rows[identity] = owner(identity)
        rows[identity]['messages'][-1]['text'] = 'Changed before commit'
        cache.prepare(rows[identity], rows[identity]['messages'], pending)
        retained = {id(entry): entry for entry in cache.entries.values()}
        retained.update((id(entry), entry) for entry in pending.values() if entry is not None)
        assert sum(entry.charge for entry in retained.values()) <= cache.MAX_BYTES
    assert cache.entries == committed
    cache.committed(pending, rows)
    assert sum(entry.charge for entry in cache.entries.values()) <= cache.MAX_BYTES


async def test_long_preparation_failed_save_does_not_acknowledge_or_hide_retry(
        tmp_path, monkeypatch):
    from amplifier_web.service import AppService
    from amplifier_web import session_projection
    from amplifier_web.cold_display import load, MARKER
    from amplifier_web.state_records import load as load_state
    from amplifier_web.storage_migration import close_maintenance
    from test_automatic_history import ObservedRuntime
    app = AppService(tmp_path / 'app', ObservedRuntime(), workspace=tmp_path)
    try:
        for background in (app.history, app.event_log_view, app.diagnostics,
                           app.schedules, app.observations):
            await background.close()
        row = app._new_session({'workspace': str(tmp_path)})
        row['messages'] = owner()['messages']
        app._state['sessions'].append(row)
        app._publish_changes(sessions={row['id']})
        await close_maintenance(app)
        previous = app._message_preparation.entries[row['id']]
        alias = row['messages'][-1]['nested']
        alias['value'] = 'first attempted change'
        persist = session_projection.persist
        def fail(*args, **kwargs):
            persist(*args, **kwargs)
            raise RuntimeError('Synthetic save rejection')
        with monkeypatch.context() as patch:
            patch.setattr(session_projection, 'persist', fail)
            with pytest.raises(RuntimeError, match='save rejection'):
                app._publish_changes(sessions={row['id']})
        assert app._message_preparation.entries[row['id']] is previous
        alias['value'] = 'later retry change'
        app._publish_changes(sessions={row['id']})
        reference = next(saved for saved in load_state(app.db)['sessions'] if saved['id'] == row['id'])
        value = load(app.db, reference['$viewPayload'])
        assert load(app.db, value[MARKER]['messages'])[-1]['nested']['value'] == 'later retry change'
        assert app._message_preparation.entries[row['id']] is not previous
    finally:
        await close_maintenance(app)
        await app.close()
