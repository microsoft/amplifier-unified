"""Exact file retention through real dispatch/bridge/HTTP routes; scripted runtime."""
import asyncio
import copy
import hashlib
import shutil
import threading
from pathlib import Path

import pytest

from amplifier_web.service import AppError, AppService
from amplifier_web.server import create_app
from test_collaborative_workspaces import app, agent_action, generation, send, settled, finish, Runtime


async def setup(app, tmp_path, content=b'Original exact bytes'):
    source, peer = app.state['sessions']
    root = tmp_path / 'owned-copy'
    root.mkdir()
    peer['workingDirectory'] = str(root)
    peer['executionRevision'] = 3
    file = root / 'report.md'
    file.write_bytes(content)
    await send(app, 'artifact-request')
    await settled(app, 'artifact-request', 'accepted')
    await generation(app, peer, 'recipient-artifact-request', ['artifact-request'])
    args = {'requestId': 'artifact-request', 'kind': 'result', 'outcome': 'success',
            'text': 'Reported result', 'references': ['report.md@sha256:' + hashlib.sha256(content).hexdigest()]}
    return source, peer, file, args


async def declare(app, peer, args):
    return await agent_action(app, peer, 'coordination.reply', args, 'artifact-declaration')


async def read(app, source, args, **patch):
    return (await agent_action(app, source, 'coordination.artifact', {
        'sessionId': source['id'], 'requestId': args['requestId'], 'reference': args['references'][0], **patch}, 'read-artifact'))['result']


async def test_report_captures_before_first_open_and_survives_source_delete_and_restart(app, tmp_path):
    source, peer, file, args = await setup(app, tmp_path)
    before = copy.deepcopy(app.state['view'])
    declared = (await declare(app, peer, args))['result']
    assert declared['artifactVerification'] == 'verified'
    assert declared['status'] == 'staged' and not declared['qualified']
    output_id = declared['artifacts'][0]['outputId']
    assert (await read(app, source, args, limit=8))['text'] == 'Original'
    await finish(app, peer, args['requestId'])
    shutil.rmtree(file.parent)
    value = await read(app, source, args)
    assert value['text'] == 'Original exact bytes'
    assert value['qualified'] and value['provenance']['executionRevision'] == 3
    assert value['provenance']['generationId'] == declared['generationId']
    assert app.state['view'] == before
    from amplifier_web.resource_files import collect
    record = app.outputs.store.read(output_id)
    assert record['body']['$resource'] not in collect(app.db, app.state)
    app._save_changes()
    await app.close()
    runtime = Runtime()
    restarted = runtime.app = AppService(app.data_dir, runtime, workspace=tmp_path)
    try:
        result = await restarted.dispatch('coordination.artifact', {
            'sessionId': source['id'], 'requestId': args['requestId'], 'reference': args['references'][0]})
        assert result['result']['text'] == 'Original exact bytes'
        # The exact reply identity wins before checking a now-terminal generation.
        duplicate = await declare(restarted, restarted._session(peer['id']), args)
        assert duplicate['duplicate'] and duplicate['result']['artifacts'][0]['outputId'] == output_id
        assert restarted.db.execute('SELECT count(*) FROM output_records').fetchone()[0] == 1
    finally:
        await restarted.close()


@pytest.mark.parametrize('failure', ['changed', 'missing', 'outside', 'symlink', 'oversize'])
async def test_invalid_exact_source_retains_report_without_accepting_dependency(app, tmp_path, failure):
    source, peer, file, args = await setup(app, tmp_path)
    if failure == 'changed':
        file.write_text('Edited before reporting')
    elif failure == 'missing':
        file.unlink()
    elif failure in {'outside', 'symlink'}:
        outside = tmp_path / 'secret.txt'
        outside.write_text('Private unrelated bytes')
        if failure == 'outside':
            args['references'] = [str(outside) + '@sha256:' + hashlib.sha256(outside.read_bytes()).hexdigest()]
        else:
            file.unlink(); file.symlink_to(outside)
    else:
        file.write_bytes(b'x' * (8 * 1024 * 1024 + 1))
    declaration = (await declare(app, peer, args))['result']
    assert declaration['artifactVerification'] == 'incomplete'
    assert declaration['artifacts'][0]['status'] == ('changed' if failure == 'changed' else 'unavailable')
    await finish(app, peer, args['requestId'])
    value = await read(app, source, args)
    assert not value['qualified'] and 'text' not in value
    assert 'Private unrelated bytes' not in str(value)
    assert app.db.execute('SELECT count(*) FROM output_records').fetchone()[0] == 0


async def test_foreign_reader_and_forged_reference_do_not_grant_peer_library_access(app, tmp_path):
    source, peer, _, args = await setup(app, tmp_path)
    await declare(app, peer, args)
    unrelated = app._new_session({'title': 'Independent'})
    app.state['sessions'].append(unrelated)
    await generation(app, unrelated, 'unrelated', ['unrelated-input'])
    with pytest.raises(AppError, match='request and workspace'):
        await read(app, unrelated, args)
    with pytest.raises(AppError, match='exact artifact'):
        await read(app, source, {**args, 'references': ['private.md@sha256:' + 'a'*64]})
    source['workspace'] += '/moved'
    with pytest.raises(AppError, match='workspace'):
        await read(app, source, args)


@pytest.mark.parametrize('change', ['placement', 'stop', 'generation', 'response'])
async def test_async_capture_rechecks_authority_without_holding_app_lock(app, tmp_path, monkeypatch, change):
    from amplifier_web import result_artifacts
    _, peer, _, args = await setup(app, tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = result_artifacts.prepare_files
    def blocked(*values):
        entered.set()
        assert release.wait(5)
        return original(*values)
    monkeypatch.setattr(result_artifacts, 'prepare_files', blocked)
    task = asyncio.create_task(declare(app, peer, args))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        async with asyncio.timeout(2):
            async with app.lock:
                if change == 'placement': peer['executionRevision'] += 1
                elif change == 'stop': peer['interruptionRevision'] = 1
                elif change == 'generation': peer['collaborationGeneration']['id'] = 'new-generation'
                else:
                    request = app.collaboration.receipt(args['requestId'])
                    request['responseRevision'] = 1
                    app.collaboration.save(args['requestId'], request)
        release.set()
        with pytest.raises(AppError, match='changed|generation'):
            await task
        assert not app.collaboration.receipt(args['requestId']).get('response')
        assert app.db.execute('SELECT count(*) FROM output_records').fetchone()[0] == 0
    finally:
        release.set()


async def test_reply_failure_rolls_back_output_and_retry_captures_once(app, tmp_path, monkeypatch):
    _, peer, _, args = await setup(app, tmp_path)
    original = app.collaboration.insert
    def fail(*values):
        if values[0] == 'artifact-declaration': raise OSError('injected commit failure')
        return original(*values)
    monkeypatch.setattr(app.collaboration, 'insert', fail)
    with pytest.raises(OSError, match='injected'):
        await declare(app, peer, args)
    assert not app.collaboration.receipt(args['requestId']).get('response')
    assert app.db.execute('SELECT count(*) FROM output_records').fetchone()[0] == 0
    monkeypatch.setattr(app.collaboration, 'insert', original)
    await declare(app, peer, args)
    assert app.db.execute('SELECT count(*) FROM output_records').fetchone()[0] == 1


async def test_lost_ack_retains_exact_bytes_and_retry_never_reads_replacement(app, tmp_path, monkeypatch):
    source, peer, file, args = await setup(app, tmp_path)
    original = app.collaboration.publish_receipt
    def lost(value):
        original(value)
        if value.get('commandAction') == 'coordination.reply': raise OSError('lost ack after commit')
    monkeypatch.setattr(app.collaboration, 'publish_receipt', lost)
    with pytest.raises(OSError, match='lost ack'):
        await declare(app, peer, args)
    file.write_text('Replacement that must never be returned')
    retry = await declare(app, peer, args)
    assert retry['duplicate'] and (await read(app, source, args))['text'] == 'Original exact bytes'
    assert app.db.execute('SELECT count(*) FROM output_records').fetchone()[0] == 1


async def test_http_session_api_and_canvas_use_same_retained_bytes_after_deletion(app, tmp_path, authenticated_client):
    source, peer, file, args = await setup(app, tmp_path)
    await declare(app, peer, args)
    await finish(app, peer, args['requestId'])
    file.unlink()
    app._save_changes()
    await app.close()
    runtime = Runtime()
    server = await create_app(app.data_dir, workspace=tmp_path, runtime=runtime,
                              voice=False, background_updates=False, preload_providers=False)
    app = runtime.app = server['service']
    app.clients.attach('retained-reader')
    from amplifier_web.agent_canvas import scope
    client_record = app.clients.records['retained-reader']
    client_record['selectedSessionId'], client_record['selectedWorkspaceId'] = scope(app, source['id'])
    client = await authenticated_client(server)
    if client:
        headers = {'X-Amplifier-Client': 'retained-reader'}
        request = {'id': 'http-artifact', 'action': 'coordination.artifact',
                   'args': {'requestId': args['requestId'], 'reference': args['references'][0]}}
        response = await client.post('/api/sessions/' + source['id'] + '/commands', json=request, headers=headers)
        assert response.status == 200, await response.text()
        value = (await response.json())['result']
        download = await client.get(value['downloadUrl'], headers=headers)
        assert await download.read() == b'Original exact bytes'
        opened = await client.post('/api/actions', headers=headers, json={'action': 'canvas.openFile', 'args': {
            'sessionId': source['id'], 'workspace': source['workspace'], 'path': 'report.md',
            'expectedSha256': args['references'][0].split('@sha256:')[1], 'referenceRequestId': args['requestId']}})
        assert opened.status == 200, await opened.text()
        assert (await opened.json())['result']['status'] == 'opened'
        with app.clients.bind('retained-reader'):
            assert app.state['canvas']['content'] == 'Original exact bytes'
            assert 'path' not in app.state['canvas']


async def test_retained_resource_corruption_never_falls_back_to_source(app, tmp_path):
    source, peer, file, args = await setup(app, tmp_path)
    value = (await declare(app, peer, args))['result']
    record = app.outputs.store.read(value['artifacts'][0]['outputId'])
    app.db.execute('DELETE FROM state_resources WHERE id=?', (record['body']['$resource'],))
    result = await read(app, source, args)
    assert result['status'] == 'unavailable' and 'text' not in result
    assert file.read_bytes() == b'Original exact bytes'


async def test_one_declaration_bounds_total_capture_and_deduplicates_references(app, tmp_path):
    source, peer, file, args = await setup(app, tmp_path, b'x' * (6 * 1024 * 1024))
    references = args['references'][:]
    for name in ('second', 'third'):
        other = file.with_name(name + '.txt')
        other.write_bytes(file.read_bytes())
        references.append(name + '.txt@sha256:' + args['references'][0].split('@sha256:')[1])
    args['references'] = [*references, references[0]]
    value = (await declare(app, peer, args))['result']
    assert [item['status'] for item in value['artifacts']] == ['retained', 'retained', 'unavailable']
    assert value['artifactVerification'] == 'incomplete'
    assert app.db.execute('SELECT count(*) FROM output_records').fetchone()[0] == 2
    assert (await read(app, source, args, limit=4))['text'] == 'xxxx'


async def test_prior_response_revision_uses_exact_reply_receipt_not_latest_artifacts(app, tmp_path):
    source, peer, file, args = await setup(app, tmp_path)
    value = (await declare(app, peer, args))['result']
    # Emulate the retained revision owner, whose integration has its own actual
    # multi-generation tests. Retrieval must select the exact old receipt.
    reply = app.collaboration.receipt('artifact-declaration')
    reply['result']['responseRevision'] = 1
    app.collaboration.save('artifact-declaration', reply)
    request = app.collaboration.receipt(args['requestId'])
    request.update(responseRevision=2, response={'responseRevision': 2, 'references': [], 'artifacts': []})
    app.collaboration.save(args['requestId'], request)
    file.unlink()
    result = await read(app, source, args, responseRevision=1)
    assert result['text'] == 'Original exact bytes' and result['outputId'] == value['artifacts'][0]['outputId']
    with pytest.raises(AppError, match='exact response revision'):
        await read(app, source, args, responseRevision=3)


@pytest.mark.parametrize('surface', ['agent', 'canvas'])
async def test_retained_reads_release_lock_and_recheck_response_or_navigation(app, tmp_path, monkeypatch, surface):
    from amplifier_web import resource_files
    source, peer, _, args = await setup(app, tmp_path)
    await declare(app, peer, args)
    entered, release = threading.Event(), threading.Event()
    original = resource_files.read_detached
    def blocked(*values, **options):
        entered.set()
        assert release.wait(5)
        return original(*values, **options)
    monkeypatch.setattr(resource_files, 'read_detached', blocked)
    if surface == 'agent':
        task = asyncio.create_task(read(app, source, args))
    else:
        task = asyncio.create_task(app.dispatch('canvas.openFile', {'sessionId': source['id'],
            'workspace': source['workspace'], 'path': 'report.md', 'referenceRequestId': args['requestId'],
            'expectedSha256': args['references'][0].split('@sha256:')[1]}))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        async with asyncio.timeout(2):
            async with app.lock:
                if surface == 'agent':
                    request = app.collaboration.receipt(args['requestId'])
                    request['response']['text'] = 'Updated report'
                    app.collaboration.save(args['requestId'], request)
                else:
                    app.state['selectedSessionId'] = peer['id']
                    app.state['view']['draft'] = 'New private draft'
        release.set()
        if surface == 'agent':
            with pytest.raises(AppError, match='changed while reading'):
                await task
        else:
            assert (await task)['result']['status'] == 'unavailable'
            assert app.state['selectedSessionId'] == peer['id']
            assert app.state['view']['draft'] == 'New private draft'
    finally:
        release.set()


async def test_cancelled_retention_waits_for_owned_file_write_then_rolls_back(app, tmp_path, monkeypatch):
    from amplifier_web import resource_files
    _, peer, _, args = await setup(app, tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = resource_files.write_prepared
    def blocked(*values):
        entered.set()
        assert release.wait(5)
        return original(*values)
    monkeypatch.setattr(resource_files, 'write_prepared', blocked)
    task = asyncio.create_task(declare(app, peer, args))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        assert app.lock.locked()  # Writer ownership spans the detached write.
        task.cancel()
        await asyncio.sleep(.02)  # Event loop remains live while disk is blocked.
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not app.collaboration.receipt(args['requestId']).get('response')
        assert app.db.execute('SELECT count(*) FROM output_records').fetchone()[0] == 0
    finally:
        release.set()


@pytest.mark.parametrize('name,content,kind', [
    ('report.pdf', b'%PDF-1.4\n%%EOF', 'pdf'),
    ('report.html', b'<h1>Saved HTML</h1>', 'html'),
    ('report.png', b'\x89PNG\r\n\x1a\nretained-image-fixture', 'image'),
])
async def test_preview_format_survives_source_removal_without_file_editor(app, tmp_path, name, content, kind):
    source, peer, file, args = await setup(app, tmp_path, content)
    renamed = file.with_name(name)
    file.rename(renamed)
    args['references'] = [name + '@sha256:' + hashlib.sha256(content).hexdigest()]
    await declare(app, peer, args)
    renamed.unlink()
    # Inspectability does not require success or root termination.
    app.state['selectedWorkspaceId'] = app.state['workspaces'][0]['id']
    result = await app.dispatch('canvas.openFile', {'sessionId': source['id'], 'workspace': source['workspace'],
        'path': name, 'expectedSha256': hashlib.sha256(content).hexdigest(), 'referenceRequestId': args['requestId']})
    assert result['result']['status'] == 'opened'
    assert app.state['canvas']['kind'] == kind and 'path' not in app.state['canvas']
    assert not (await read(app, source, args))['qualified']


async def test_later_reply_keeps_each_exact_file_version_after_source_deletion(app, tmp_path):
    """Exercise the real revision/retention seam, not synthetic historical rows."""
    from amplifier_web.automatic_history import display_identity
    from amplifier_operations.coordination import fingerprint
    source, peer, file, args = await setup(app, tmp_path, b'First retained version')
    first = (await declare(app, peer, args))['result']
    await finish(app, peer, args['requestId'])
    await generation(app, peer, 'follow-up', ['new-human-input'])
    file.write_bytes(b'Second retained version')
    second_args = {**args, 'expectedResponseRevision': 1,
                   'references': ['report.md@sha256:' + hashlib.sha256(file.read_bytes()).hexdigest()]}
    second = (await agent_action(app, peer, 'coordination.reply', second_args, 'second-declaration'))['result']
    anchor = {'messageId': display_identity(peer, 9, 'assistant', 'Updated result'),
              'nativeIndex': 9, 'nativeText': 'Updated result', 'textDigest': fingerprint('Updated result'),
              'rootSessionId': peer['id'], 'generationId': 'follow-up'}
    await finish(app, peer, args['requestId'], generation_id='follow-up', input_ids=['new-human-input'],
                 text='Updated result', nativeTerminal=anchor)
    file.unlink()
    assert (first['responseRevision'], second['responseRevision']) == (1, 2)
    assert first['artifacts'][0]['outputId'] != second['artifacts'][0]['outputId']
    old = await read(app, source, args, responseRevision=1)
    new = await read(app, source, second_args, responseRevision=2)
    assert old['qualified'] and old['text'] == 'First retained version'
    assert new['qualified'] and new['text'] == 'Second retained version'
    duplicate = await declare(app, peer, args)
    assert duplicate['duplicate'] and duplicate['result']['responseRevision'] == 1
    assert duplicate['result']['status'] == 'sealed'
    assert app.db.execute('SELECT count(*) FROM output_records').fetchone()[0] == 2


async def test_reply_publishes_both_participants_and_no_unrelated_session(app, tmp_path, monkeypatch):
    source, peer, _, args = await setup(app, tmp_path)
    calls = []
    publish = app._publish_changes
    def observe(**kwargs):
        calls.append(set(kwargs.get('sessions', ())))
        return publish(**kwargs)
    monkeypatch.setattr(app, '_publish_changes', observe)
    await declare(app, peer, args)
    assert {source['id'], peer['id']} in calls
    assert all(scope <= {source['id'], peer['id']} for scope in calls)
