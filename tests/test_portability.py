"""Shared actions with real Git, Foundation native locks and persisted outputs."""
import base64
import copy
import json
from pathlib import Path

import pytest

from amplifier_foundation.session import SharedSessionStore, SessionTransferFencedError
from amplifier_web.host.storage import SessionStore
from amplifier_web.portability import Portability
from amplifier_web.portability_policy import readiness_policy
from amplifier_web.runtime import RuntimeManager
from amplifier_web.service import AppService, AppError
from amplifier_worktrees.git import atomic, digest, git
from test_worktrees import repository


def host_env(monkeypatch, root):
    monkeypatch.setenv('AMPLIFIER_HOME', str(root / 'native'))
    monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME', str(root / 'locks'))
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(root / 'app'))


async def setup_hosts(tmp_path, monkeypatch):
    source = tmp_path / 'source'; source.mkdir()
    destination = tmp_path / 'destination'; destination.mkdir()
    root = repository(source)
    destrepo = destination / 'repo'
    git(destination, 'clone', '--no-local', str(root), str(destrepo))
    host_env(monkeypatch, source)
    runtime = RuntimeManager(); runtime.retention.wake = lambda: None
    app = AppService(source / 'app', runtime, workspace=root)
    await app.dispatch('session.create', {})
    session = app._session(); sid = session['id']
    session.update(selection={'provider': 'fixture', 'model': 'test-model'},
        task={'id': 'task-original', 'revision': 4, 'objective': 'Finish the original work', 'status': 'active'},
        messages=[{'id': 'typed', 'role': 'user', 'text': 'Keep this task'},
                  {'id': 'spoken', 'role': 'user', 'text': 'Spoken history', 'via': 'call', 'voiceId': 'voice-original'}],
        draft='Unsent draft stays unsent', artifactRefs=[])
    store = SessionStore.for_app(app.data_dir, root)
    store.save(sid, [{'role': 'user', 'content': 'Keep this task'}, {'role': 'assistant', 'content': 'Completed original work'}], {'name': 'Portable task'})
    atomic(app.data_dir / 'sessions' / sid / 'control-state.json', {'task': session['task'], 'selection': session['selection'], 'budget': {'maxIterations': 12}})
    app._save()
    out1 = (await app.dispatch('outputs.write', {'sessionId': sid, 'title': 'Original', 'variant': 'document', 'content': 'First output', 'messageId': 'typed'}))['result']
    out2 = (await app.dispatch('outputs.write', {'sessionId': sid, 'title': 'Revision', 'variant': 'document', 'content': 'Second output', 'parentId': out1['id'], 'evidenceIds': [out1['id']]}))['result']
    await app.dispatch('outputs.comment', {'sessionId': sid, 'id': out2['id'], 'body': 'Retain this review'})
    (root / 'a.txt').write_text('staged\n'); git(root, 'add', 'a.txt')
    (root / 'a.txt').write_text('staged\nunstaged\n')
    (root / 'new.txt').write_text('Untracked work')
    host_env(monkeypatch, destination)
    target_runtime = RuntimeManager(); target_runtime.retention.wake = lambda: None
    target = AppService(destination / 'app', target_runtime, workspace=destrepo)
    for left, right in ((app, target), (target, app)):
        atomic(left.portability.node.directory / 'peers.json', {right.portability.node.identity['id']: right.portability.node.identity})
    # These service tests isolate account-network behavior. The separate probe
    # tests and two-process acceptance exercise the subprocess boundary.
    def policy(self, payload, workspace):
        selection = payload['intent']['selection']
        return readiness_policy('provider-fixture', selection.get('instance') or selection['provider'],
                                selection['model'], selection.get('effort') or 'high')
    monkeypatch.setattr(Portability, 'destination_policy', policy)
    async def checks(self, payload, workspace, policy):
        assert (Path(workspace) / '.git').is_file()
        return {'runtimeVerified': True, 'accountVerified': True, 'nativeFenceVerified': True,
                'credentialsOrigin': 'destination', 'readinessPolicy': policy, 'readinessPolicyHash': digest(policy)}
    monkeypatch.setattr(Portability, 'destination_checks', checks)
    return app, target, source, destination, root, destrepo, sid, out1, out2


async def export(app, target, sid, root):
    inspected = (await app.dispatch('worktree.inspect', {'sessionId': sid}))['result']
    args = {'sessionId': sid, 'destination': target.portability.node.identity['id'],
            'sourceRevision': inspected['repository']['sourceRevision'], 'expectedExecutionRevision': app._session(sid).get('executionRevision', 0),
            'mode': 'carry_dirty', 'reviewedContent': True}
    started = (await app.app_bridge('dispatch', {'action': 'portability.export', 'id': 'export-original', 'args': args}, sid))['result']
    await __import__('asyncio').gather(*list(app.portability.jobs))
    receipt = app.portability.node.get(started['id'])
    assert receipt['phase'] == 'prepared', receipt
    return receipt, args


async def test_ui_agent_transfer_preserves_canonical_history_lineage_and_dirty_work(tmp_path, monkeypatch):
    app, target, source, destination, root, destrepo, sid, out1, out2 = await setup_hosts(tmp_path, monkeypatch)
    try:
        host_env(monkeypatch, source)
        original = SessionStore.for_app(app.data_dir, root).directory(sid) / 'transcript.jsonl'
        raw = original.read_bytes()
        receipt, args = await export(app, target, sid, root)
        duplicate = (await app.app_bridge('dispatch', {'action': 'portability.export', 'id': 'export-original', 'args': args}, sid))['result']
        assert duplicate['duplicate']
        with pytest.raises(SessionTransferFencedError): SharedSessionStore(root, sid).acquire(app='independent-cli')
        with pytest.raises(ValueError, match='handoff'): await app.runtime.start(copy.deepcopy(app._session(sid)), app.on_runtime_event)
        host_env(monkeypatch, destination)
        staged = (await target.dispatch('portability.stage', {'path': receipt['package'], 'repository': str(destrepo)}))['result']
        working = Path(staged['destinationState']['workspace'])
        assert not target.state['sessions']
        with pytest.raises(SessionTransferFencedError): SharedSessionStore(working, sid).acquire(app='independent-tui')
        assert (working / 'a.txt').read_text() == 'staged\nunstaged\n'
        assert git(working, 'show', ':a.txt') == b'staged\n'
        assert (working / 'new.txt').read_text() == 'Untracked work'
        host_env(monkeypatch, source)
        released = (await app.dispatch('portability.release', {'sessionId': sid, 'id': receipt['id'], 'expectedRevision': receipt['revision'], 'path': staged['receiptPath']}))['result']
        assert not original.exists()
        assert (Path(released['archive']) / 'transcript.jsonl').read_bytes() == raw
        with pytest.raises(SessionTransferFencedError): SharedSessionStore(root, sid).acquire(app='independent-cli')
        host_env(monkeypatch, destination)
        active = (await target.dispatch('portability.activate', {'sessionId': sid, 'id': staged['id'], 'expectedRevision': staged['revision'], 'path': released['receiptPath']}))['result']
        assert active['phase'] == 'active' and not target.runtime.workers
        session = target._session(sid)
        assert session['nativeIdentity'] == sid and session['task']['id'] == 'task-original'
        assert session['draft'] == 'Unsent draft stays unsent'
        assert any(row.get('voiceId') == 'voice-original' for row in session['messages'])
        assert SessionStore.for_app(target.data_dir, working).history(sid).transcript_path.read_bytes() == raw
        assert target.outputs.store.read(out2['id'])['parentId'] == out1['id']
        assert target.outputs.store.read(out2['id'])['evidenceIds'] == [out1['id']]
        assert target.outputs.content(target.outputs.store.read(out2['id'])) == b'Second output'
        assert target.outputs.store.comments(out2['id'])[0]['body'] == 'Retain this review'
        held = SharedSessionStore(working, sid).acquire(app='independent-tui'); held.release()
        assert app.portability.fenced(sid) and not target.portability.fenced(sid)
        assert not list((destination / 'native').rglob('keys.env'))
    finally:
        host_env(monkeypatch, source); await app.close()
        host_env(monkeypatch, destination); await target.close()


async def _portable_artifact(app, sid, root, output, kind, monkeypatch):
    """Use existing producers; the adapter may add a reference, never body bytes."""
    from amplifier_web import resource_files
    mid = 'typed'
    if kind == 'writing':
        saved = output
    elif kind in {'dataset', 'image'}:
        from test_output_images import png
        name = 'synthetic-data.json' if kind == 'dataset' else 'synthetic-image.png'
        (root / name).write_bytes(b'{"synthetic": true}' if kind == 'dataset' else png())
        saved = (await app.dispatch('outputs.attach', {
            'sessionId': sid, 'kind': 'dataset' if kind == 'dataset' else 'file',
            'path': name, 'title': 'Synthetic portable artifact', 'messageId': mid}))['result']
        (root / name).unlink()
    elif kind == 'canvas':
        await app.dispatch('canvas.show', {'kind': 'markdown', 'content': 'Synthetic Canvas source',
                                          'title': 'Synthetic source definition'})
        saved = (await app.dispatch('outputs.attach', {
            'sessionId': sid, 'kind': 'canvas', 'canvasId': app.state['canvas']['id'],
            'title': 'Synthetic Canvas snapshot', 'messageId': mid}))['result']
    elif kind == 'media':
        from test_portability_outputs import attach_saved_media
        saved, mid, _ = await attach_saved_media(app, sid, root)
    else:
        from test_generated_images import receipt
        receipt(root, 'synthetic-portable-generation')
        saved = (await app.dispatch('outputs.attachImage', {
            'sessionId': sid, 'title': 'Synthetic generated image', 'messageId': mid,
            'receiptPath': 'synthetic-portable-generation.json'}))['result']
        (root / 'synthetic-portable-generation.json').unlink()
        (root / 'synthetic-portable-generation.png').unlink()
    original = app.outputs.record(sid, saved['id'])
    body = copy.deepcopy(original['body'])
    content = app.outputs.content(original)
    owned_resource = copy.deepcopy(app.state_resource(body['$resource']))
    puts = []
    real_put = resource_files.put

    def put(db, value):
        puts.append(copy.deepcopy(value))
        return real_put(db, value)

    with monkeypatch.context() as patch:
        patch.setattr(resource_files, 'put', put)
        presented = (await app.dispatch('canvas.present', {
            'sessionId': sid, 'outputId': original['id'], 'version': original['version'],
            'messageId': mid, 'presentation': {'chat': 'compact', 'canvas': 'unchanged'}},
            command_id='synthetic-portable-present'))['result']
    record = app.outputs.record(sid, original['id'])
    row = next(row for row in app.state['canvasArtifacts'] if row['id'] == presented['id'])
    entry = next(entry for entry in row['versions'] if entry['version'] == presented['version'])
    assert record['body'] == body and app.outputs.content(record) == content
    assert owned_resource not in puts
    assert entry['outputBinding'] == {
        'outputId': record['id'], 'outputVersion': record['version'],
        'sessionId': sid, 'sha256': record['sha256']}
    if kind == 'canvas':
        assert entry['body'] == record['canvasSource']
    elif kind == 'generated':
        assert row['id'] == record['canvasId'] and entry['body'] == body
    elif kind == 'media':
        assert set(app.state_resource(entry['body']['$resource'])) == {'mediaReference'}
    else:
        assert app.state_resource(entry['body']['$resource']) == {
            'outputReference': entry['outputBinding']}
    assert next(link for link in row['publications']
                if link['version'] == presented['version'] and link['messageId'] == mid)['outputId'] == record['id']
    return record, row, presented, mid


async def _ordinary_portable_revision(app, sid, row):
    """Synthetic later definition through the existing version/body owners."""
    from amplifier_web.canvas_versions import save
    from amplifier_web.resource_files import put
    from test_output_images import png
    content = ('data:image/png;base64,' + base64.b64encode(png(4, 2)).decode()
               if row['kind'] == 'image' else 'Ordinary later content without output binding')
    previous = copy.deepcopy(row)
    current = copy.deepcopy(row)
    current.update(body=put(app.db, {'content': content}), title='Ordinary later definition')
    current.pop('outputBinding', None)
    if 'contentResource' in current:
        current['contentResource'] = copy.deepcopy(current['body'])
    # Keep the canonical generator identity on the row for the old definition,
    # but do not attach its binding/marker to ordinary later content.
    save(current, previous, app.state, force=True, message='typed')
    row.clear()
    row.update(current)
    app._save_changes(globals={'canvasArtifacts'})
    assert row['sessionId'] == sid and row['revision'] == 2
    assert 'outputBinding' not in row['versions'][1]
    assert all('imageGeneration' not in link and 'outputId' not in link
               for link in row['publications'] if link['version'] == 2)


def _transfer_resource(payload, value):
    from amplifier_web.resource_files import prepare
    prepared = prepare(value)
    payload['resources'][prepared.identity] = copy.deepcopy(value)
    return {'$resource': prepared.identity, 'bytes': prepared.size}


async def _reject_signed_artifact(app, target, body, tmp_path, destination, destrepo, monkeypatch):
    """A verified source signature must not bypass binding or admit dependents."""
    from amplifier_portability.capsule import write_capsule
    from amplifier_web import portability as transfer, portability_data
    signed = app.portability.node.sign(body)
    verified = target.portability.node.verify(
        signed, kind='capsule', signer=app.portability.node.identity['id'])
    with pytest.raises(ValueError):
        portability_data.validate(verified['payload'])
    package = tmp_path / 'synthetic-invalid-artifact.json'
    write_capsule(package, signed)
    host_env(monkeypatch, destination)
    tables = ('output_records', 'output_comments', 'output_receipts', 'state_resources', 'questions')
    before = {table: target.db.execute('SELECT * FROM ' + table + ' ORDER BY rowid').fetchall()
              for table in tables}
    native = {str(path.relative_to(destination)): path.read_bytes()
              for path in (destination / 'native').rglob('*') if path.is_file()}
    sessions = copy.deepcopy(target.state['sessions'])
    canvases = copy.deepcopy(target.state['canvasArtifacts'])
    records = target.portability.node.records()

    def dependent_admission(*args, **kwargs):
        pytest.fail('Invalid artifact binding reached dependent destination admission')

    with monkeypatch.context() as patch:
        patch.setattr(target.portability.node, 'receive', dependent_admission)
        patch.setattr(target.portability, 'destination_policy', dependent_admission)
        patch.setattr(target.portability, 'destination_checks', dependent_admission)
        patch.setattr(transfer, 'restore_workspace', dependent_admission)
        patch.setattr(transfer, 'native_store', dependent_admission)
        patch.setattr(portability_data, 'preflight_install', dependent_admission)
        patch.setattr(portability_data, 'install_outputs', dependent_admission)
        patch.setattr(portability_data, 'install_receipts', dependent_admission)
        with pytest.raises(AppError):
            await target.dispatch('portability.stage', {'path': str(package), 'repository': str(destrepo)})
    assert {table: target.db.execute('SELECT * FROM ' + table + ' ORDER BY rowid').fetchall()
            for table in tables} == before
    assert {str(path.relative_to(destination)): path.read_bytes()
            for path in (destination / 'native').rglob('*') if path.is_file()} == native
    assert target.state['sessions'] == sessions and target.state['canvasArtifacts'] == canvases
    assert target.portability.node.records() == records == []
    assert not target.runtime.workers and not app.runtime.workers


@pytest.mark.parametrize('kind', ['writing', 'dataset', 'image', 'canvas', 'media', 'generated'])
async def test_exact_artifact_transfer_roundtrip_preserves_existing_refs_without_body_copy(
        tmp_path, monkeypatch, kind):
    from amplifier_web import portability_data, resource_files
    from test_portability_outputs import release_to, activate
    app, target, source, destination, root, destrepo, sid, _, output = await setup_hosts(tmp_path, monkeypatch)
    try:
        host_env(monkeypatch, source)
        record, row, presented, mid = await _portable_artifact(app, sid, root, output, kind, monkeypatch)
        if kind in {'writing', 'generated'}:
            await _ordinary_portable_revision(app, sid, row)
        if kind == 'writing':
            assert record['version'] == 2 and presented['version'] == 1
            assert record['presentationArtifact'] == {
                'id': row['id'], 'version': 1, 'outputVersion': 2}
        # Retained transfer admission must not re-prove a live history origin.
        app._session(sid)['messages'] = [message for message in app._session(sid)['messages']
                                        if message['id'] != mid]
        store = SessionStore.for_app(app.data_dir, root)
        store.save(sid, [{'role': 'user', 'content': 'Revised synthetic history; artifact origin removed'}],
                   {}, preserve_system=True)
        raw = (store.directory(sid) / 'transcript.jsonl').read_bytes()
        app._save()
        captured = portability_data.capture(app, app._session(sid))
        original_record = next(item for item in captured['outputs'] if item['id'] == record['id'])
        original_canvas = next(item for item in captured['canvas'] if item['id'] == row['id'])
        original_resources = copy.deepcopy(captured['resources'])
        expected_content = app.outputs.content(record)
        # Both directions use the existing signed export/stage/release/apply path.
        for left, right, left_home, right_home, repository in (
                (app, target, source, destination, destrepo),
                (target, app, destination, source, root)):
            staged, released = await release_to(left, right, sid, left_home, right_home,
                                                repository, monkeypatch)
            host_env(monkeypatch, right_home)
            assert (await activate(right, sid, staged, released))['phase'] == 'active'
            imported = right.outputs.record(sid, record['id'])
            saved = next(item for item in right.state['canvasArtifacts'] if item['id'] == row['id'])
            assert imported == original_record
            assert saved['versions'] == original_canvas['versions']
            assert saved['publications'] == original_canvas['publications']
            assert right.outputs.content(imported) == expected_content
            native = SessionStore.for_app(right.data_dir, right._session(sid)['workspace'])
            assert (native.directory(sid) / 'transcript.jsonl').read_bytes() == raw
            puts = []
            real_put = resource_files.put

            def put(db, value):
                puts.append(copy.deepcopy(value))
                return real_put(db, value)

            with monkeypatch.context() as patch:
                patch.setattr(resource_files, 'put', put)
                retained = (await right.dispatch('canvas.present', {
                    'sessionId': sid, 'outputId': imported['id'], 'version': imported['version'],
                    'presentation': {'chat': 'compact', 'canvas': 'unchanged'}},
                    command_id='synthetic-retained-represent'))['result']
            assert retained['id'] == presented['id'] and retained['version'] == presented['version']
            assert retained['reference'] == presented['reference']
            assert retained['publication']['status'] == 'unchanged'
            assert puts == []
            assert portability_data.capture(right, right._session(sid))['resources'] == original_resources
            if kind == 'generated':
                link = next(link for link in saved['publications'] if link.get('outputId') == imported['id'])
                assert link['imageGeneration'] == {
                    'verified': True, 'requestId': 'synthetic-portable-generation',
                    'operation': 'generate', 'parentId': None}
                assert all('imageGeneration' not in link for link in saved['publications']
                           if link['version'] == 2)
        assert not app.runtime.workers and not target.runtime.workers
    finally:
        host_env(monkeypatch, source); await app.close()
        host_env(monkeypatch, destination); await target.close()


@pytest.mark.parametrize('location', ['current', 'historical'])
@pytest.mark.parametrize('defect', [
    'binding-owner', 'binding-output', 'binding-version', 'binding-bool', 'binding-string',
    'typed-owner', 'typed-output', 'typed-version', 'typed-bool', 'typed-content', 'definition-kind',
])
async def test_signed_artifact_transfer_rejects_typed_binding_corruption_in_every_saved_version(
        tmp_path, monkeypatch, location, defect):
    from amplifier_portability.capsule import read_capsule
    from amplifier_web import portability_data
    app, target, source, destination, root, destrepo, sid, output, other = await setup_hosts(tmp_path, monkeypatch)
    try:
        host_env(monkeypatch, source)
        record, row, _, _ = await _portable_artifact(app, sid, root, output, 'writing', monkeypatch)
        if defect in {'binding-output', 'typed-output'}:
            other = (await app.dispatch('outputs.write', {
                'sessionId': sid, 'title': 'Equal bytes, distinct synthetic output',
                'variant': 'document', 'content': app.outputs.content(record).decode()}))['result']
            assert other['id'] != record['id']
            assert other['version'] == record['version'] and other['sha256'] == record['sha256']
            assert other['body'] == record['body']
        if location == 'historical':
            await _ordinary_portable_revision(app, sid, row)
        receipt, _ = await export(app, target, sid, root)
        body = read_capsule(Path(receipt['package']))['body']
        portability_data.validate(body['payload'])
        changed = copy.deepcopy(body)
        payload = changed['payload']
        canvas = next(item for item in payload['canvas'] if item['id'] == row['id'])
        selected = canvas['versions'][0]
        latest = copy.deepcopy(canvas['versions'][-1])
        entries = [selected, canvas] if location == 'current' else [selected]
        wrapper = copy.deepcopy(payload['resources'][selected['body']['$resource']])
        if defect.startswith('binding-'):
            field, replacement = {
                'binding-owner': ('sessionId', 'synthetic-other-task'),
                'binding-output': ('outputId', other['id']),
                'binding-version': ('outputVersion', record['version'] + 1),
                'binding-bool': ('outputVersion', True),
                'binding-string': ('outputVersion', str(record['version'])),
            }[defect]
            for entry in entries:
                entry['outputBinding'][field] = replacement
        elif defect == 'definition-kind':
            for entry in entries:
                entry['kind'] = 'html'  # A saved inert reference never grants execution.
        else:
            if defect == 'typed-content':
                wrapper['content'] = 'Competing inline content is not the owning body'
            else:
                field, replacement = {
                    'typed-owner': ('sessionId', 'synthetic-other-task'),
                    'typed-output': ('outputId', other['id']),
                    'typed-version': ('outputVersion', record['version'] + 1),
                    'typed-bool': ('outputVersion', True),
                }[defect]
                wrapper['outputReference'][field] = replacement
            reference = _transfer_resource(payload, wrapper)
            for entry in entries:
                entry['body'] = copy.deepcopy(reference)
        if location == 'historical':
            assert canvas['versions'][-1] == latest and canvas['revision'] == 2
            assert canvas['body'] == latest['body']  # Latest is valid; old saved entry alone is bad.
        await _reject_signed_artifact(app, target, changed, tmp_path, destination, destrepo, monkeypatch)
    finally:
        host_env(monkeypatch, source); await app.close()
        host_env(monkeypatch, destination); await target.close()


@pytest.mark.parametrize('defect', [
    'mapping-output-version', 'mapping-output-bool', 'mapping-output-string',
    'mapping-canvas-version', 'mapping-canvas-bool', 'mapping-canvas-string', 'mapping-canvas-id',
    'publication-output', 'publication-missing-output', 'publication-version',
    'publication-bool', 'publication-string', 'saved-version-bool', 'saved-version-string',
    'duplicate-saved-version',
])
async def test_signed_artifact_transfer_rejects_mapping_and_publication_namespace_corruption(
        tmp_path, monkeypatch, defect):
    from amplifier_portability.capsule import read_capsule
    from amplifier_web import portability_data
    app, target, source, destination, root, destrepo, sid, output, other = await setup_hosts(tmp_path, monkeypatch)
    try:
        host_env(monkeypatch, source)
        record, row, _, _ = await _portable_artifact(app, sid, root, output, 'writing', monkeypatch)
        receipt, _ = await export(app, target, sid, root)
        body = read_capsule(Path(receipt['package']))['body']
        portability_data.validate(body['payload'])
        changed = copy.deepcopy(body)
        payload = changed['payload']
        canvas = next(item for item in payload['canvas'] if item['id'] == row['id'])
        saved = next(item for item in payload['outputs'] if item['id'] == record['id'])
        if defect.startswith('mapping-'):
            field, replacement = {
                'mapping-output-version': ('outputVersion', 99),
                'mapping-output-bool': ('outputVersion', True),
                'mapping-output-string': ('outputVersion', '1'),
                'mapping-canvas-version': ('version', 99),
                'mapping-canvas-bool': ('version', True),
                'mapping-canvas-string': ('version', '1'),
                'mapping-canvas-id': ('id', 'synthetic-missing-canvas'),
            }[defect]
            saved['presentationArtifact'][field] = replacement
        elif defect == 'publication-missing-output':
            canvas['publications'][0].pop('outputId')
        elif defect.startswith('publication-'):
            field, replacement = {
                'publication-output': ('outputId', other['id']),
                'publication-version': ('version', 99),
                'publication-bool': ('version', True),
                'publication-string': ('version', '1'),
            }[defect]
            canvas['publications'][0][field] = replacement
        elif defect == 'duplicate-saved-version':
            canvas['versions'].append(copy.deepcopy(canvas['versions'][0]))
        else:
            canvas['versions'][0]['version'] = True if defect == 'saved-version-bool' else '1'
        await _reject_signed_artifact(app, target, changed, tmp_path, destination, destrepo, monkeypatch)
    finally:
        host_env(monkeypatch, source); await app.close()
        host_env(monkeypatch, destination); await target.close()


@pytest.mark.parametrize('defect', [
    'request', 'operation', 'parent', 'verified-bool', 'verified-integer', 'origin',
    'canvas-request', 'output-canvas', 'old-body', 'ordinary-version', 'ordinary-output',
])
async def test_signed_artifact_transfer_rejects_corrupt_or_inherited_generation_markers(
        tmp_path, monkeypatch, defect):
    from amplifier_portability.capsule import read_capsule
    from amplifier_web import portability_data
    app, target, source, destination, root, destrepo, sid, output, _ = await setup_hosts(tmp_path, monkeypatch)
    try:
        host_env(monkeypatch, source)
        record, row, _, _ = await _portable_artifact(app, sid, root, output, 'generated', monkeypatch)
        await _ordinary_portable_revision(app, sid, row)
        receipt, _ = await export(app, target, sid, root)
        body = read_capsule(Path(receipt['package']))['body']
        portability_data.validate(body['payload'])
        changed = copy.deepcopy(body)
        payload = changed['payload']
        canvas = next(item for item in payload['canvas'] if item['id'] == row['id'])
        saved = next(item for item in payload['outputs'] if item['id'] == record['id'])
        link = next(item for item in canvas['publications'] if item.get('outputId') == record['id'])
        marker = copy.deepcopy(link['imageGeneration'])
        if defect in {'request', 'operation', 'parent', 'verified-bool', 'verified-integer'}:
            field, replacement = {
                'request': ('requestId', 'synthetic-different-request'),
                'operation': ('operation', 'edit'),
                'parent': ('parentId', output['id']),
                'verified-bool': ('verified', False),
                'verified-integer': ('verified', 1),
            }[defect]
            link['imageGeneration'][field] = replacement
        elif defect == 'origin':
            link['messageId'] = 'spoken'
        elif defect == 'canvas-request':
            canvas['imageRequestId'] = 'synthetic-different-request'
        elif defect == 'output-canvas':
            saved['canvasId'] = 'synthetic-different-canvas'
        elif defect == 'old-body':
            canvas['versions'][0]['body'] = copy.deepcopy(canvas['versions'][1]['body'])
        elif defect == 'ordinary-version':
            ordinary = next(item for item in canvas['publications'] if item['version'] == 2)
            ordinary.update(outputId=record['id'], imageGeneration=marker)
        else:
            ordinary_record = next(item for item in payload['outputs'] if item['id'] == output['id'])
            binding = {'outputId': output['id'], 'outputVersion': output['version'],
                       'sessionId': sid, 'sha256': output['sha256']}
            ordinary_record['presentationArtifact'] = {'id': canvas['id'], 'version': 2,
                                                       'outputVersion': output['version']}
            reference = _transfer_resource(payload, {'outputReference': binding})
            canvas.update(kind='markdown', body=reference, outputBinding=binding)
            canvas.pop('contentResource', None)
            canvas['versions'][1].update(kind='markdown', body=copy.deepcopy(reference), outputBinding=binding)
            canvas['versions'][1].pop('contentResource', None)
            ordinary = next(item for item in canvas['publications'] if item['version'] == 2)
            ordinary.update(outputId=output['id'], imageGeneration=marker)
        await _reject_signed_artifact(app, target, changed, tmp_path, destination, destrepo, monkeypatch)
    finally:
        host_env(monkeypatch, source); await app.close()
        host_env(monkeypatch, destination); await target.close()


async def test_rejected_account_stage_remains_fenced_and_never_replayed(tmp_path, monkeypatch):
    app, target, source, destination, root, destrepo, sid, *_ = await setup_hosts(tmp_path, monkeypatch)
    try:
        host_env(monkeypatch, source)
        receipt, _ = await export(app, target, sid, root)
        async def reject(*_): raise ValueError('Destination account is unavailable')
        monkeypatch.setattr(Portability, 'destination_checks', reject)
        host_env(monkeypatch, destination)
        with pytest.raises(AppError, match='account is unavailable'):
            await target.dispatch('portability.stage', {'path': receipt['package'], 'repository': str(destrepo)})
        assert target.portability.node.get(receipt['id'])['phase'] == 'unknown'
        duplicate = (await target.dispatch('portability.stage', {'path': receipt['package'], 'repository': str(destrepo)}))['result']
        assert duplicate['duplicate'] and duplicate['phase'] == 'unknown'
        assert not target.runtime.workers
        assert app.portability.fenced(sid)
        host_env(monkeypatch, source)
        cancelled = (await app.dispatch('portability.cancel', {'sessionId': sid, 'id': receipt['id'], 'expectedRevision': receipt['revision'], 'evidence': 'Destination credentials unavailable; keep this task here.'}))['result']
        assert not app.portability.fenced(sid)
        held = SharedSessionStore(root, sid).acquire(app='source-restored'); held.release()
        host_env(monkeypatch, destination)
        unknown = target.portability.node.get(receipt['id'])
        discarded = (await target.dispatch('portability.discard', {'sessionId': sid, 'id': receipt['id'], 'expectedRevision': unknown['revision'], 'path': cancelled['receiptPath']}))['result']
        assert discarded['phase'] == 'discarded' and not target.state['sessions']
    finally:
        host_env(monkeypatch, source); await app.close()
        host_env(monkeypatch, destination); await target.close()


async def test_interrupted_release_can_finish_exact_archival_without_rollback(tmp_path, monkeypatch):
    app, target, source, destination, root, destrepo, sid, *_ = await setup_hosts(tmp_path, monkeypatch)
    try:
        host_env(monkeypatch, source)
        receipt, _ = await export(app, target, sid, root)
        host_env(monkeypatch, destination)
        staged = (await target.dispatch('portability.stage', {'path': receipt['package'], 'repository': str(destrepo)}))['result']
        host_env(monkeypatch, source)
        import os
        original_replace = os.replace
        def fail_archive(src, dst):
            if '/archives/' in str(dst) and str(dst).endswith('transcript.jsonl'):
                raise OSError('Injected archive interruption')
            return original_replace(src, dst)
        with monkeypatch.context() as patch:
            patch.setattr(os, 'replace', fail_archive)
            with pytest.raises(AppError, match='archive interruption'):
                await app.dispatch('portability.release', {'sessionId': sid, 'id': receipt['id'], 'expectedRevision': receipt['revision'], 'path': staged['receiptPath']})
        interrupted = app.portability.node.get(receipt['id'])
        assert interrupted['phase'] == 'unknown' and interrupted['previousPhase'] == 'releasing'
        assert SharedSessionStore(root, sid).transfer_fence()['phase'] == 'committed'
        released = (await app.dispatch('portability.release', {'sessionId': sid, 'id': receipt['id'], 'expectedRevision': interrupted['revision'], 'path': staged['receiptPath']}))['result']
        assert released['phase'] == 'released' and app.portability.fenced(sid)
        assert (Path(released['archive']) / 'transcript.jsonl').exists()
    finally:
        host_env(monkeypatch, source); await app.close()
        host_env(monkeypatch, destination); await target.close()


async def test_activation_commits_before_unfencing_and_retry_does_not_reinstall(tmp_path, monkeypatch):
    app, target, source, destination, root, destrepo, sid, *_ = await setup_hosts(tmp_path, monkeypatch)
    try:
        host_env(monkeypatch, source)
        receipt, _ = await export(app, target, sid, root)
        with pytest.raises(AppError, match='fenced'):
            await app.dispatch('outputs.write', {'sessionId': sid, 'title': 'Late', 'content': 'Cannot disappear', 'variant': 'document'})
        host_env(monkeypatch, destination)
        staged = (await target.dispatch('portability.stage', {'path': receipt['package'], 'repository': str(destrepo)}))['result']
        assert not (SessionStore.for_app(target.data_dir, staged['destinationState']['workspace']).directory(sid) / 'transcript.jsonl').exists()
        host_env(monkeypatch, source)
        released = (await app.dispatch('portability.release', {'sessionId': sid, 'id': receipt['id'], 'expectedRevision': receipt['revision'], 'path': staged['receiptPath']}))['result']
        host_env(monkeypatch, destination)
        arguments = {'sessionId': sid, 'id': staged['id'], 'expectedRevision': staged['revision'], 'path': released['receiptPath']}
        with monkeypatch.context() as patch:
            def fail_clear(*_): raise OSError('Injected native clear interruption')
            patch.setattr(Portability, 'finish_native_activation', fail_clear)
            with pytest.raises(AppError, match='native clear interruption'):
                await target.dispatch('portability.activate', arguments)
        active = target.portability.node.get(staged['id'])
        assert active['phase'] == 'active' and target.portability.fenced(sid)
        native = SessionStore.for_app(target.data_dir, staged['destinationState']['workspace']).directory(sid) / 'transcript.jsonl'
        before = native.stat().st_mtime_ns
        duplicate = (await target.dispatch('portability.activate', arguments))['result']
        assert duplicate['duplicate'] and not target.portability.fenced(sid)
        assert native.stat().st_mtime_ns == before and not target.runtime.workers
    finally:
        host_env(monkeypatch, source); await app.close()
        host_env(monkeypatch, destination); await target.close()


async def test_transfer_reviews_schedules_and_advances_execution_revision_on_roundtrip(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    app, target, source, destination, root, destrepo, sid, *_ = await setup_hosts(tmp_path, monkeypatch)

    def save_active_schedule(host, identity, execution_revision):
        host.schedules.store.put({'id': identity, 'sessionId': sid, 'revision': 1,
            'status': 'active', 'nextDue': 1, 'executionRevision': execution_revision,
            'taskId': 'task-original', 'taskRevision': 4, 'interruptionRevision': 0,
            'prompt': 'Review retained work', 'kind': 'monitor', 'destination': 'same_task',
            'missedRunPolicy': 'latest', 'notificationPolicy': 'changes',
            'spec': {'kind': 'interval', 'seconds': 3600}, 'createdAt': 0, 'updatedAt': 0})

    reviewed_before_quiesce = []
    for host in (app, target):
        monkeypatch.setattr(host.runtime, 'control', AsyncMock(side_effect=AssertionError('Unexpected task control')))
        monkeypatch.setattr(host.runtime, 'scheduled_input', AsyncMock(side_effect=AssertionError('Unexpected scheduled input')))
        monkeypatch.setattr(host.schedules, 'task', AsyncMock(side_effect=AssertionError('Unexpected schedule preparation')))
        quiesce = host.runtime.quiesce_for_handoff

        async def checked_quiesce(*args, _host=host, _quiesce=quiesce, **kwargs):
            assert all(row['status'] == 'needs_review' for row in _host.schedules.store.list(sid))
            reviewed_before_quiesce.append(_host.data_dir)
            return await _quiesce(*args, **kwargs)

        monkeypatch.setattr(host.runtime, 'quiesce_for_handoff', checked_quiesce)

    activation_revisions = []
    finish_native = Portability.finish_native_activation

    def checked_finish(self, row, session):
        assert all(schedule['status'] == 'needs_review' for schedule in self.app.schedules.store.list(sid))
        assert self.app.portability.fenced(sid)
        activation_revisions.append(session['executionRevision'])
        return finish_native(self, row, session)

    monkeypatch.setattr(Portability, 'finish_native_activation', checked_finish)

    async def transfer(left, right, left_home, right_home, repository):
        host_env(monkeypatch, left_home)
        receipt, _ = await export(left, right, sid, Path(left._session(sid)['workspace']))
        host_env(monkeypatch, right_home)
        staged = (await right.dispatch('portability.stage', {'path': receipt['package'], 'repository': str(repository)}))['result']
        host_env(monkeypatch, left_home)
        released = (await left.dispatch('portability.release', {'sessionId': sid, 'id': receipt['id'],
            'expectedRevision': receipt['revision'], 'path': staged['receiptPath']}))['result']
        host_env(monkeypatch, right_home)
        return (await right.dispatch('portability.activate', {'sessionId': sid, 'id': staged['id'],
            'expectedRevision': staged['revision'], 'path': released['receiptPath']}))['result']

    try:
        host_env(monkeypatch, source)
        app._session(sid)['executionRevision'] = 2
        save_active_schedule(app, 'original-schedule', 2)
        app._save()

        active = await transfer(app, target, source, destination, destrepo)
        assert active['generation'] == 1
        assert target._session(sid)['executionRevision'] == 3
        assert app.schedules.store.get(sid, 'original-schedule')['status'] == 'needs_review'
        assert not target.schedules.store.list(sid)  # Imported schedules remain historical evidence.

        # The returning host can retain a newer local execution association;
        # neither transfer generation nor the incoming revision may replace it.
        host_env(monkeypatch, source)
        app._session(sid)['executionRevision'] = 5
        save_active_schedule(app, 'retained-local-schedule', 5)
        app._save()
        host_env(monkeypatch, destination)
        save_active_schedule(target, 'destination-schedule', 3)
        target._save()

        returned = await transfer(target, app, destination, source, root)
        assert returned['generation'] == 2
        assert app._session(sid)['executionRevision'] == 6
        assert activation_revisions == [3, 6]
        assert reviewed_before_quiesce == [app.data_dir, target.data_dir]
        assert app.schedules.store.get(sid, 'retained-local-schedule')['revision'] == 2

        for host, home in ((app, source), (target, destination)):
            host_env(monkeypatch, home)
            assert all(row['status'] == 'needs_review' for row in host.schedules.store.list(sid))
            await host.schedules.tick()
            assert not host.schedules.store.runs(sid)
            host.schedules.task.assert_not_awaited()
            host.runtime.control.assert_not_awaited()
            host.runtime.scheduled_input.assert_not_awaited()
            assert not host.runtime.workers
    finally:
        host_env(monkeypatch, source); await app.close()
        host_env(monkeypatch, destination); await target.close()
