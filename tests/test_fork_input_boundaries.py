"""Fork only a proven canonical prefix, independent of later delivery failure."""
import copy

import pytest

from amplifier_web.automatic_history import display_message, merge_web_history
from amplifier_web.host.storage import SessionStore
from amplifier_web.session_store import fork_session


def history(tmp_path, *, status='idle'):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    rows = [{'role': 'user', 'content': 'First'},
            {'role': 'assistant', 'content': 'First answer'},
            {'role': 'user', 'content': 'Second'},
            {'role': 'assistant', 'content': 'Second answer'}]
    source = {'id': 'original', 'title': 'Original', 'workspace': str(workspace),
              'bundle': 'work', 'status': status,
              'messages': [{'id': str(i), 'role': row['role'], 'text': row['content']}
                           for i, row in enumerate(rows)]}
    store = SessionStore.for_app(tmp_path, workspace)
    store.save(source['id'], rows, {}, preserve_system=True)
    return store, rows, source


@pytest.mark.parametrize('status', ['idle', 'error', 'stopped'])
@pytest.mark.parametrize('delivery', [None, 'unknown', 'sending', 'failed'])
def test_final_unmatched_input_forks_only_proven_prior_checkpoint(tmp_path, status, delivery):
    store, rows, source = history(tmp_path, status=status)
    final = {'id': 'unmatched', 'role': 'user', 'text': 'New submission'}
    if delivery:
        final['delivery'] = {'status': delivery}
    source['messages'].append(final)
    before = copy.deepcopy(source)
    path = store.directory('original') / 'transcript.jsonl'
    original_bytes = path.read_bytes()
    result = fork_session(tmp_path, source, 'fork', turn=2)
    assert store.load('fork')[0] == rows
    assert [r['text'] for r in result['messages']] == [r['text'] for r in before['messages'][:4]]
    assert path.read_bytes() == original_bytes and source == before
    assert store.load('fork')[1]['fork']['jobs_replayed'] is False


def test_earlier_ui_only_input_does_not_poison_exact_later_cut(tmp_path):
    store, rows, source = history(tmp_path)
    source['messages'] = [display_message(row, index, source) for index, row in enumerate(rows)]
    source['messages'].insert(2, {'id': 'failed', 'role': 'user', 'text': 'Rejected input',
                                  'delivery': {'status': 'failed'}})
    original = copy.deepcopy(source)
    fork_session(tmp_path, source, 'fork', turn=2)
    saved = store.load('fork')[0]
    assert saved[:2] == rows[:2]
    assert len(saved) == 3 and saved[2]['metadata']['amplifier_visible_reference']
    assert 'Rejected input' in saved[2]['content']
    assert 'Second answer' not in str(saved)
    assert source == original and store.load('original')[0] == rows


@pytest.mark.parametrize('delivery', ['unknown', 'sending', 'failed'])
def test_unknown_tail_cannot_include_unshown_future_native_content(tmp_path, delivery):
    store, rows, source = history(tmp_path)
    source['messages'].append({'id': 'unknown', 'role': 'user', 'text': 'Missing prompt',
                               'delivery': {'status': delivery}})
    rows.extend([{'role': 'user', 'content': 'Unshown future prompt'},
                 {'role': 'assistant', 'content': 'Unshown future answer'}])
    store.save('original', rows, {}, preserve_system=True)
    with pytest.raises(ValueError, match='boundary'):
        fork_session(tmp_path, source, 'fork', turn=2)
    assert not store.directory('fork').exists()
    assert store.load('original')[0] == rows


def test_attachment_input_identity_survives_refresh_and_fork(tmp_path):
    store, rows, source = history(tmp_path)
    expanded = {'role': 'user', 'content': 'Third\n\nUser attachment: synthetic.png',
                'metadata': {'amplifier_input': {'version': 1, 'kind': 'user',
                                                'id': 'third', 'source': 'chat'}}}
    rows.append(expanded)
    source['messages'].append({'id': 'third-web', 'role': 'user', 'text': 'Third',
                               'inputId': 'third', 'attachments': [{'id': 'synthetic-image'}]})
    store.save('original', rows, {}, preserve_system=True)
    merge_web_history(source, [display_message(row, index, source) for index, row in enumerate(rows)])
    assert source['messages'][-1]['nativeIndex'] == 4
    original = copy.deepcopy(source)
    fork_session(tmp_path, source, 'fork', turn=2)
    assert store.load('fork')[0] == rows[:4]
    assert source == original and store.load('original')[0] == rows
    # Independent fork IDs must also rebind the native hash without changing
    # visible message IDs or inheriting the original session's identity scope.
    full = fork_session(tmp_path, source, 'full')
    again = {**source, **full, 'id': 'full'}
    assert again['messages'][-1]['id'] == source['messages'][-1]['id']
    assert again['messages'][-1]['nativeMessageId'] != source['messages'][-1]['nativeMessageId']
    fork_session(tmp_path, again, 'fork-again', turn=2)
    assert store.load('fork-again')[0] == rows[:4]


@pytest.mark.parametrize('change', ['hash', 'input-id', 'duplicate-input-id', 'service', 'version'])
def test_attachment_identity_conflict_never_falls_back_to_text(tmp_path, change):
    store, rows, source = history(tmp_path)
    expanded = {'role': 'user', 'content': [
        {'type': 'text', 'text': 'Third'}, {'type': 'text', 'text': 'Attachment context'}],
        'metadata': {'amplifier_input': {'version': 1, 'kind': 'user', 'id': 'third'}}}
    rows.append(expanded)
    source['messages'].append({'id': 'third-web', 'role': 'user', 'text': 'Third',
                               'inputId': 'third', 'attachments': [{'id': 'synthetic-image'}]})
    merge_web_history(source, [display_message(row, index, source) for index, row in enumerate(rows)])
    if change == 'hash':
        expanded['content'][1]['text'] = 'Rewritten attachment context'
    elif change == 'input-id':
        expanded['metadata']['amplifier_input']['id'] = 'different'
    elif change == 'service':
        expanded['metadata']['amplifier_input']['kind'] = 'service'
    elif change == 'version':
        expanded['metadata']['amplifier_input']['version'] = 99
    else:
        rows.append(copy.deepcopy(expanded))
    store.save('original', rows, {}, preserve_system=True)
    original = copy.deepcopy(source)
    with pytest.raises(ValueError):
        fork_session(tmp_path, source, 'fork', turn=2)
    assert not store.directory('fork').exists()
    assert source == original and store.load('original')[0] == rows
    # Full copies do not need to infer a cut. Preserve every canonical row and
    # retain the normal escape hatch for ambiguous older histories.
    fork_session(tmp_path, source, 'full')
    assert store.load('full')[0] == rows


def test_inferred_repeated_answer_is_not_a_checkpoint_end_proof(tmp_path):
    store, rows, source = history(tmp_path)
    rows[1]['content'] = rows[3]['content'] = 'Same answer'
    source['messages'][1]['text'] = source['messages'][3]['text'] = 'Same answer'
    source['messages'].append({'id': 'missing', 'role': 'user', 'text': 'Not checkpointed'})
    store.save('original', rows, {}, preserve_system=True)
    with pytest.raises(ValueError, match='boundary'):
        fork_session(tmp_path, source, 'ambiguous', turn=2)
    # Exact imported native indices disambiguate the otherwise identical text.
    source['messages'][:4] = [display_message(row, i, source) for i, row in enumerate(rows)]
    fork_session(tmp_path, source, 'anchored', turn=2)
    assert store.load('anchored')[0] == rows


def test_exact_cut_cannot_retain_a_future_native_anchor(tmp_path):
    store, rows, source = history(tmp_path)
    source['messages'] = [display_message(rows[i], i, source) for i in (0, 3, 2)]
    with pytest.raises(ValueError, match='changed'):
        fork_session(tmp_path, source, 'future', turn=1)
    assert not store.directory('future').exists()


def test_future_hidden_tool_rows_are_not_checkpoint_end(tmp_path):
    store, rows, source = history(tmp_path)
    source['messages'].append({'id': 'missing', 'role': 'user', 'text': 'Not checkpointed'})
    rows.extend([{'role': 'assistant', 'tool_calls': [{'id': 'later', 'name': 'bash'}]},
                 {'role': 'tool', 'tool_call_id': 'later', 'name': 'bash', 'content': 'Future tool result'}])
    store.save('original', rows, {}, preserve_system=True)
    with pytest.raises(ValueError, match='boundary'):
        fork_session(tmp_path, source, 'future', turn=2)
    assert not store.directory('future').exists()
    assert store.load('original')[0] == rows


def test_checkpoint_end_does_not_mask_conflicting_prior_order(tmp_path):
    store, rows, source = history(tmp_path)
    source['messages'] = [display_message(rows[i], i, source) for i in (2, 0, 3)]
    source['messages'].append({'id': 'missing', 'role': 'user', 'text': 'Unconfirmed'})
    with pytest.raises(ValueError, match='changed'):
        fork_session(tmp_path, source, 'fork', turn=2)
    assert not store.directory('fork').exists()


async def test_rejected_service_fork_preserves_selection_and_original(tmp_path):
    from amplifier_web.service import AppError, AppService
    app = AppService(tmp_path, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        source = app._session()
        source.update(status='error', messages=[
            {'id': 'u', 'role': 'user', 'text': 'Missing user context'},
            {'id': 'a', 'role': 'assistant', 'text': 'Missing answer'},
            {'id': 'later', 'role': 'user', 'text': 'Unconfirmed', 'delivery': {'status': 'unknown'}}])
        store = SessionStore.for_app(tmp_path, tmp_path)
        rows = [{'role': 'user', 'content': 'Only a legacy summary remains'}]
        store.save(source['id'], rows, {}, preserve_system=True)
        original = copy.deepcopy(source)
        selected = app.state['selectedSessionId']
        with pytest.raises(AppError, match='boundary'):
            await app.dispatch('session.fork', {'id': source['id'], 'turn': 1})
        assert app.state['selectedSessionId'] == selected
        assert app.state['sessions'] == [original]
        assert store.load(source['id'])[0] == rows
    finally:
        await app.close()
