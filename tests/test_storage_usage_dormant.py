"""Explicit workspace cleanup preserves nonresident ownership and history."""
from pathlib import Path

import pytest

from amplifier_web.state_records import load_owner
from amplifier_web.service import AppError
from test_storage_usage import app, folder


def retire_fixture_owner(app, row, tmp_path):
    workspace = tmp_path / 'active-owner'; workspace.mkdir(exist_ok=True)
    active = app._new_session({'title': 'Unrelated active chat', 'workspace': str(workspace)})
    app._state['sessions'].extend((row, active))
    app._state['selectedSessionId'] = active['id']
    app._save()
    app._state['sessions'].remove(row)
    app._session_projection_refs.pop(row['id'], None)
    app.projections.invalidate(state=app._state)
    assert load_owner(app.db, row['id'])[0] is not None


@pytest.mark.parametrize('placement', ['workspace', 'workingDirectory'])
@pytest.mark.parametrize('alias', [False, True])
async def test_dormant_unregistered_overlap_blocks_workspace_file_cleanup(app, tmp_path, placement, alias):
    path, wid = await folder(app, tmp_path)
    nested = path / 'nested-owned'; nested.mkdir()
    owner = app._new_session({'title': 'Dormant overlapping chat'})
    owner['workspace'] = str(tmp_path / 'other-history-home')
    Path(owner['workspace']).mkdir()
    if alias:
        link = tmp_path / 'execution-alias'; link.symlink_to(nested, target_is_directory=True)
        owner[placement] = str(link)
    else:
        owner[placement] = str(nested)
    retire_fixture_owner(app, owner, tmp_path)
    before = load_owner(app.db, owner['id'])
    with pytest.raises(AppError, match='overlapping folder'):
        await app.dispatch('workspace.deleteFilesPreview', {'id': wid}, include_state=False)
    assert (path / 'data.txt').is_file()
    assert owner['id'] not in {row['id'] for row in app._state['sessions']}
    assert load_owner(app.db, owner['id']) == before


async def test_dormant_target_with_external_execution_retains_idle_guard(app, tmp_path):
    path, wid = await folder(app, tmp_path)
    owner = app._new_session({'title': 'Dormant separate execution'})
    owner.update(workspace=str(path), workingDirectory=str(tmp_path / 'external-execution'))
    Path(owner['workingDirectory']).mkdir()
    retire_fixture_owner(app, owner, tmp_path)
    with pytest.raises(AppError, match='separate worktree'):
        await app.dispatch('workspace.deleteFilesPreview', {'id': wid}, include_state=False)
    assert (path / 'data.txt').is_file()
    assert owner['id'] not in {row['id'] for row in app._state['sessions']}


async def test_dormant_hidden_native_requires_retained_transcript(app, tmp_path):
    from amplifier_web.native_history import NativeHistory
    from amplifier_web.session_files import amplifier_home, project_slug
    from amplifier_web.automatic_history import identity
    from test_automatic_history import native_session
    path, wid = await folder(app, tmp_path)
    native = native_session(path, 'dormant-native')
    index = NativeHistory(amplifier_home(), cache_path=app.data_dir / 'native-catalog.sqlite3')
    index.scan(); index.close()
    app._state['hiddenNativeSessions'] = [identity(project_slug(path), 'dormant-native')]
    assert app.directory.resolve('dormant-native') is None
    (native / 'transcript.jsonl').unlink()
    with pytest.raises(AppError, match='retained history could not be verified'):
        await app.dispatch('workspace.deleteFilesPreview', {'id': wid}, include_state=False)
    assert (path / 'data.txt').is_file()
    assert not app._state['sessions']


async def test_cleanup_preserves_and_archives_dormant_target_without_admission(app, tmp_path):
    path, wid = await folder(app, tmp_path)
    owner = app._new_session({'title': 'Dormant retained history'})
    owner.update(workspace=str(path), messages=[{'id': 'saved', 'role': 'assistant', 'text': 'Keep exact dormant content'}])
    retire_fixture_owner(app, owner, tmp_path)
    before = load_owner(app.db, owner['id'])
    preview = (await app.dispatch('workspace.deleteFilesPreview', {'id': wid}, include_state=False))['result']
    assert preview['chatCount'] == 1
    result = await app.dispatch('workspace.deleteFiles', {'id': wid,
        'confirmationToken': preview['token'], 'confirmationPath': str(path)}, include_state=False)
    assert result['result']['status'] == 'completed' and not path.exists()
    assert owner['id'] in app._state['conversationOrganization']['archived']
    assert owner['id'] not in {row['id'] for row in app._state['sessions']}
    assert load_owner(app.db, owner['id']) == before
    assert app._session(owner['id'])['messages'][0]['text'] == 'Keep exact dormant content'


async def test_missing_dormant_pointer_does_not_erase_execution_overlap_proof(app, tmp_path):
    path, wid = await folder(app, tmp_path)
    owner = app._new_session({'title': 'Unknown saved placement'})
    owner.update(workspace=str(tmp_path / 'outside-history'), workingDirectory=str(path / 'inside'))
    Path(owner['workspace']).mkdir()
    Path(owner['workingDirectory']).mkdir()
    retire_fixture_owner(app, owner, tmp_path)
    app.db.execute("UPDATE state_records SET value='null' WHERE kind='session' AND id=?", (owner['id'],))
    app.db.commit()
    with pytest.raises(AppError, match='saved chat record is unavailable'):
        await app.dispatch('workspace.deleteFilesPreview', {'id': wid}, include_state=False)
    assert (path / 'data.txt').is_file()
    assert owner['id'] not in {row['id'] for row in app._state['sessions']}


@pytest.mark.parametrize('progressive', [False, True])
async def test_explicit_storage_inspect_includes_dormant_managed_chat_without_admission(app, tmp_path, monkeypatch, progressive):
    import asyncio
    initial = tmp_path / 'initial'; initial.mkdir(exist_ok=True)
    owner = app._new_session({'title': 'Named dormant managed chat', 'workspace': str(initial), 'location': {'kind': 'managed'}})
    workspace = app.data_dir / 'chats' / owner['id'] / 'files'
    workspace.mkdir(parents=True)
    (workspace / 'saved-file').write_text('Retained managed content')
    owner['workspace'] = str(workspace)
    retire_fixture_owner(app, owner, tmp_path)
    decoded = []
    decode = app.directory.decode
    monkeypatch.setattr(app.directory, 'decode', lambda row: (decoded.append(1), decode(row))[1])
    monkeypatch.setattr(app, '_admit_session', lambda row: pytest.fail('Storage inspection admitted a saved body'))
    args = {'category': 'sessions', 'progressive': progressive}
    result = (await app.dispatch('storage.inspect', args, include_state=False))['result']
    if progressive:
        async with asyncio.timeout(10):
            while result.get('scanning'):
                await asyncio.sleep(.01)
                result = (await app.dispatch('storage.inspect', {**args, 'scanId': result['scanId']}, include_state=False))['result']
    matching = [row for row in result['rows'] if row.get('sessionId') == owner['id']]
    assert len(matching) == 1
    assert matching[0]['label'] == 'Named dormant managed chat'
    assert matching[0]['destination']['sessionId'] == owner['id']
    assert matching[0]['bytes'] >= len('Retained managed content')
    assert len(decoded) == 1, 'Only requested managed metadata is decoded; scan continuation reuses its snapshot.'
    assert owner['id'] not in {row['id'] for row in app._state['sessions']}
