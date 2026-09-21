"""Owned context revisions. Original events and revision evidence remain intact."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
from .host.config import app_home, write_private
from .host.storage import SessionStore
from .session_store import fork_session
from .session_files import validate_id


def receipt_path(home, identity, operation_id):
    validate_id(identity)
    # Command IDs are protocol identities, not filesystem path components.
    filename = hashlib.sha256(operation_id.encode()).hexdigest() + '.json'
    return Path(home) / 'sessions' / identity / 'history-revisions' / filename


def same(left, right):
    return json.dumps(left, sort_keys=True, ensure_ascii=False) == json.dumps(right, sort_keys=True, ensure_ascii=False)


def recover_pending(home, workspace, identity):
    """Classify an interrupted atomic transcript save while holding writer ownership.

    Never overwrite a transcript that another owner may have changed. A crash
    before the save leaves the old context; a crash after it leaves the revision.
    Neither case starts another input or replays an external effect.
    """
    directory = receipt_path(home, identity, 'unused').parent
    pointer = directory.parent / 'pending-history-edit.json'
    if not pointer.exists():
        return
    operation_id = json.loads(pointer.read_text())['operationId']
    store = SessionStore.for_app(home, workspace)
    for path in [receipt_path(home, identity, operation_id)]:
        receipt = json.loads(path.read_text())
        if receipt.get('phase') != 'prepared':
            pointer.unlink(missing_ok=True)
            continue
        saved = store.load(identity)
        current = saved[0] if saved else []
        phase = 'committed' if same(current, receipt['nativeAfter']) else 'aborted' if same(current, receipt['nativeBefore']) else 'superseded'
        if phase == 'aborted':
            control = directory.parent / 'control-state.json'
            previous = receipt.get('controlBefore')
            if previous is not None and control.exists() and same(json.loads(control.read_text()), {**previous, 'goal': None}):
                write_private(control, json.dumps(previous))
        write_private(path, json.dumps({**receipt, 'phase': phase}, ensure_ascii=False))
        pointer.unlink(missing_ok=True)


async def rewind(controls, args):
    controls.require_idle()
    identity = controls.session.session_id
    path = receipt_path(app_home(), identity, args['operationId'])
    if path.exists():
        # Replaying a completed context mutation could discard new work. The
        # service owns command deduplication; worker calls are single admission.
        raise ValueError('This edit was already attempted. Reload its result before editing again.')
    pointer = path.parent.parent / 'pending-history-edit.json'
    if pointer.exists():
        raise ValueError('An interrupted edit needs the session to be reloaded before accepting more work.')
    source = copy.deepcopy(args['source'])
    source['status'] = 'idle'
    store = SessionStore.for_app(app_home(), source['workspace'])
    prepared = fork_session(app_home(), source, identity,
                            before_message_id=args['messageId'], prepare_only=True)
    context = controls.coordinator.get('context')
    before = copy.deepcopy(await context.get_messages())
    goal_before = copy.deepcopy(controls.coordinator.session_state.get('goal'))
    await controls.checkpoint()
    saved = store.load(identity)
    after = prepared['context']
    if not any(row.get('role') in {'system', 'developer'} for row in after):
        after = [row for row in before if row.get('role') in {'system', 'developer'}] + after
    keep_system = bool(saved and saved[1].get('preserve_system'))
    native_after = [row for row in after if keep_system or row.get('role') not in {'system', 'developer'}]
    result = {'messages': prepared['messages'], 'throughTurn': prepared['throughTurn'],
              'operationId': args['operationId'], 'eventsPreserved': True,
              'sharedHistoryOffset': source.get('sharedHistoryOffset', 0),
              'sharedHistoryUserTurnOffset': source.get('sharedHistoryUserTurnOffset', 0),
              'sharedHistoryTotal': source.get('sharedHistoryOffset', 0) + len(prepared['messages']) + 1}
    receipt = {'phase': 'prepared', 'messageId': args['messageId'], 'operationId': args['operationId'],
               'contextBefore': before, 'goalBefore': goal_before,
               'controlBefore': json.loads(controls.state_path().read_text()) if controls.state_path().exists() else None,
               'visibleBefore': source['messages'], 'executionBefore': source.get('execution'), 'nativeBefore': saved[0] if saved else [],
               'nativeAfter': native_after, 'result': result}
    write_private(path, json.dumps(receipt, ensure_ascii=False))
    write_private(pointer, json.dumps({'operationId': args['operationId']}))
    try:
        await context.set_messages(after)
        controls.coordinator.session_state['goal'] = None
        await controls.checkpoint()
    except BaseException:
        # Still under ownership; rollback failure leaves the prepared evidence
        # for classification on next mount, never an automatic input replay.
        await context.set_messages(before)
        controls.coordinator.session_state['goal'] = goal_before
        await controls.checkpoint()
        write_private(path, json.dumps({**receipt, 'phase': 'aborted'}, ensure_ascii=False))
        pointer.unlink(missing_ok=True)
        raise
    stamp = store.history(identity).transcript_path.stat()
    result['nativeRevision'] = [stamp.st_mtime_ns, stamp.st_size]
    write_private(path, json.dumps({**receipt, 'phase': 'committed'}, ensure_ascii=False))
    pointer.unlink(missing_ok=True)
    return result


def trim_execution(session, previous, message_id):
    from .execution import anchor_turns, rollup
    # Preserve earlier work, including turns outside the loaded message window.
    cut = next((i for i, row in enumerate(previous) if row['id'] == message_id), len(previous))
    removed = {row['id'] for row in previous[cut:]}
    discarded_inputs = {row.get('inputId') for row in previous[cut:] if row.get('inputId')}
    tree = session.get('execution')
    if tree:
        current = session['messages']
        session['messages'] = previous
        anchor_turns(session)
        session['messages'] = current
        tree['turns'] = [row for row in tree.get('turns', []) if row.get('anchorMessageId') not in removed
                         and row.get('inputId') not in discarded_inputs]
        kept = {row['id'] for row in tree['turns']}
        tree['nodes'] = [row for row in tree.get('nodes', []) if row.get('turnId') in kept]
        tree['currentTurnId'] = None
        tree['aggregateUsage'] = rollup([row for row in tree['nodes'] if row.get('kind') == 'llm'])
    session['generations'] = [row for row in session.get('generations', [])
                              if not discarded_inputs.intersection(row.get('input_ids', []))]


def apply_revision(service, session, result, *, interrupted=False):
    edit = session.get('historyEdit', {})
    if edit.get('operationId') != result.get('operationId') or edit.get('applied'):
        return False
    previous = copy.deepcopy(session.get('messages', []))
    session.update({key: copy.deepcopy(result[key]) for key in ('messages', 'nativeRevision', 'sharedHistoryOffset', 'sharedHistoryUserTurnOffset', 'sharedHistoryTotal') if key in result})
    trim_execution(session, previous, edit['messageId'])
    session.pop('streamingId', None)
    session.update(historyManaged=False, historyLoaded=True, historyLoading=False, streaming='', workers=[], approvals=[])
    for key in ('historyActivity', 'nativeBoundary', 'nativeBoundaryId', 'messageWindow', 'executionWindow', 'error', 'failure', 'health'):
        session.pop(key, None)
    message = service._message(session, 'user', edit['text'], edit['via'], inputId=edit['operationId'], attachments=edit['attachments'])
    if interrupted:
        message['delivery'] = {'status': 'unknown'}
    from .execution import ensure_turn
    ensure_turn(session, edit['operationId'], edit['text'])
    session['status'] = 'interrupted' if interrupted else 'working'
    service._activity(session, session['status'], 'Edit interrupted; work has not been replayed.' if interrupted else 'Generating from your edited message.', reset=True)
    edit.update(applied=True, phase='interrupted' if interrupted else 'ready')
    return True


def recover_views(service):
    """Reconcile durable edit evidence on restart without submitting an input."""
    for session in service.state['sessions']:
        edit = session.get('historyEdit', {})
        if edit.get('phase') != 'working':
            continue
        path = receipt_path(service.data_dir, session.get('runtimeSessionId') or session['id'], edit['operationId'])
        try:
            receipt = json.loads(path.read_text()) if path.exists() else None
            committed = receipt and receipt.get('phase') == 'committed'
            if receipt and receipt.get('phase') == 'prepared':
                store = SessionStore.for_app(service.data_dir, session['workspace'])
                saved = store.load(session.get('runtimeSessionId') or session['id'])
                committed = same(saved[0] if saved else [], receipt['nativeAfter'])
            if committed:
                apply_revision(service, session, receipt['result'], interrupted=True)
        except (ValueError, OSError, KeyError, TypeError):
            pass  # Preserve files; leave a visible interrupted edit, never guess.
        edit.update(phase='error', error='The app restarted during this edit. Saved work was not replayed; review the conversation before continuing.')
        session['configurationBusy'] = False
        session['error'] = edit['error']
