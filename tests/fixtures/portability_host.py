"""Private JSON pipe host for separate-process portability acceptance."""
import asyncio
import base64
import contextlib
import copy
import json
import os
from pathlib import Path
import sys

from amplifier_foundation.session import SharedSessionStore, SessionTransferFencedError
from amplifier_web.host.storage import SessionStore
from amplifier_web.runtime import RuntimeManager
from amplifier_web.service import AppService
from amplifier_worktrees.git import atomic, git


ROOT = Path(__file__).resolve().parents[2]


async def seed(app):
    await app.dispatch('session.create', {})
    session = app._session()
    identity = session['id']
    session.update(selection={'provider': 'portability-fixture', 'model': 'fixture-model', 'effort': 'high'},
        task={'id': 'task-original', 'revision': 4, 'objective': 'Finish the saved work', 'status': 'active'},
        messages=[{'id': 'typed-original', 'role': 'user', 'text': 'Keep this task'},
                  {'id': 'spoken-original', 'role': 'user', 'text': 'Spoken history', 'via': 'call',
                   'voiceId': 'voice-original'}],
        draft='Unsent fixture draft', artifactRefs=[], deferRuntimeUntilInteraction=True)
    store = SessionStore.for_app(app.data_dir, session['workspace'])
    store.save(identity, [{'role': 'user', 'content': 'Keep this task'},
                          {'role': 'assistant', 'content': 'Completed original work'}], {'name': 'Portable task'})
    unknown_job = {'id': 'unknown-original', 'status': 'outcome_unknown',
                   'inputIds': ['uncertain-original-input'], 'inputsReplayed': False}
    atomic(store.directory(identity) / 'live-jobs' / 'job-unknown-original.json', unknown_job)
    unknown_args = {'sessionId': identity, 'requestId': 'operation-request-original',
                    'command': 'printf portability-fixture-no-replay'}
    receipt, fresh = app.operations.requests.begin(identity, 'operations.submit', unknown_args, 'ui')
    assert fresh
    unknown_request = app.operations.requests.finish(receipt, 'outcome_unknown', inputsReplayed=False)
    atomic(app.data_dir / 'sessions' / identity / 'control-state.json', {
        'task': session['task'], 'selection': session['selection'], 'budget': {'maxIterations': 12},
        'taskReceipts': {'original-task-command': {'id': 'original-task-receipt', 'status': 'active'}},
    })
    app._save()
    first = (await app.dispatch('outputs.write', {'sessionId': identity, 'title': 'Original',
        'variant': 'document', 'content': 'First output', 'messageId': 'typed-original'}))['result']
    second = (await app.dispatch('outputs.write', {'sessionId': identity, 'title': 'Revision',
        'variant': 'document', 'content': 'Second output', 'parentId': first['id'], 'evidenceIds': [first['id']]}))['result']
    comment = (await app.dispatch('outputs.comment', {'sessionId': identity, 'id': second['id'],
        'body': 'Retain this review'}))['result']
    root = Path(session['workspace'])
    (root / 'a.txt').write_text('staged\n')
    git(root, 'add', 'a.txt')
    (root / 'a.txt').write_text('staged\nunstaged\n')
    (root / 'new.txt').write_text('Untracked original work')
    return {'sessionId': identity, 'firstOutput': first, 'secondOutput': second, 'comment': comment, 'unknownJob': unknown_job,
            'unknownRequest': unknown_request, 'unknownRequestArgs': unknown_args,
            'transcript': base64.b64encode(store.history(identity).transcript_path.read_bytes()).decode()}


async def perform(app, request):
    action = request['action']
    args = request.get('args', {})
    if action == 'identity':
        return {'host': app.portability.node.identity, 'pid': os.getpid(),
                'nativeHome': os.environ['AMPLIFIER_HOME'], 'stateHome': os.environ['AMPLIFIER_SESSION_STATE_HOME'],
                'appHome': str(app.data_dir)}
    if action == 'pair':
        atomic(app.portability.node.directory / 'peers.json', {args['id']: args})
        return {'paired': True}
    if action == 'seed':
        return await seed(app)
    if action == 'export':
        sid = args['sessionId']
        inspected = (await app.dispatch('worktree.inspect', {'sessionId': sid}))['result']
        arguments = {'sessionId': sid, 'destination': args['destination'],
            'sourceRevision': inspected['repository']['sourceRevision'],
            'expectedExecutionRevision': app._session(sid).get('executionRevision', 0),
            'mode': 'carry_dirty', 'reviewedContent': True}
        result = (await app.app_bridge('dispatch', {'action': 'portability.export',
            'id': args['commandId'], 'args': arguments}, sid))['result']
        await asyncio.gather(*list(app.portability.jobs))
        return app.portability.node.get(result['id'])
    if action == 'inspect':
        sid = args['sessionId']
        session = copy.deepcopy(app._session(sid))
        directory = SessionStore.for_app(app.data_dir, session['workspace']).directory(sid)
        transcript = directory / 'transcript.jsonl'
        controls = app.data_dir / 'sessions' / sid / 'control-state.json'
        outputs = [json.loads(row[0]) for row in app.db.execute(
            'SELECT value FROM output_records WHERE session_id=? ORDER BY created', (sid,))]
        observations = session.get('portabilityObservations', {})
        if session.get('portabilityEvidence'):
            row = app.portability.node.get(session['portabilityEvidence']['transferId'])
            package = json.loads(Path(row['destinationState']['package']).read_text())
            observations = package['body']['payload']['observations']
        return {'session': session, 'workers': list(app.runtime.workers),
            'runtimeControls': list(app.runtime.fixture_control_calls), 'observations': observations,
            'fenced': app.portability.fenced(sid), 'receipts': app.portability.node.records(sid),
            'controls': json.loads(controls.read_text()) if controls.exists() else {},
            'transcript': base64.b64encode(transcript.read_bytes()).decode() if transcript.exists() else None,
            'outputs': outputs, 'comments': [r for output in outputs for r in app.outputs.store.comments(output['id'])],
            'contents': {output['id']: base64.b64encode(app.outputs.content(output)).decode() for output in outputs}}
    if action == 'native-admission':
        store = SharedSessionStore(args['workspace'], args['sessionId'])
        try:
            held = store.acquire(app='independent-process-consumer')
        except SessionTransferFencedError:
            return {'fenced': True}
        held.release()
        return {'fenced': False}
    result = await app.dispatch(action, args, include_state=False)
    return result['result']


async def main():
    # Only executable location changes. Runtime fencing and the isolated provider
    # inference probe remain the production implementations.
    RuntimeManager._command = lambda self, *args, **kwargs: [sys.executable, '-u', str(ROOT / 'amplifier_web' / 'runtime_worker.py')]
    runtime = RuntimeManager()
    runtime.retention.wake = lambda: None
    runtime.fixture_control_calls = []
    original_control = runtime.control
    async def observed_control(session_id, operation, arguments=None):
        runtime.fixture_control_calls.append(operation)
        return await original_control(session_id, operation, arguments)
    runtime.control = observed_control
    protocol = sys.stdout
    with contextlib.redirect_stdout(sys.stderr):
        app = AppService(Path(os.environ['AMPLIFIER_WEB_HOME']), runtime, workspace=sys.argv[1])
    try:
        while line := await asyncio.to_thread(sys.stdin.readline):
            request = json.loads(line)
            if request['action'] == 'close':
                protocol.write(json.dumps({'ok': True, 'result': {'closed': True}}) + '\n')
                protocol.flush()
                break
            try:
                with contextlib.redirect_stdout(sys.stderr):
                    result = await perform(app, request)
                response = {'ok': True, 'result': result}
            except Exception as exc:
                response = {'ok': False, 'error': type(exc).__name__, 'detail': str(exc)}
            protocol.write(json.dumps(response) + '\n')
            protocol.flush()
    finally:
        with contextlib.redirect_stdout(sys.stderr):
            await app.close()


if __name__ == '__main__':
    asyncio.run(main())
