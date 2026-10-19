"""Imported names are generated while idle without loading or resuming chats."""
import asyncio
import json

import pytest

from test_automatic_history import app_factory, native_session
from amplifier_web.automatic_history import revision
from amplifier_web.automatic_naming import AutomaticNaming
from amplifier_web.naming_backfill import NamingBackfill
from amplifier_web.naming import read, set_automatic, directory_for


async def finish_actions(app):
    for _ in range(100):
        await asyncio.sleep(0)  # Let task-removal callbacks run, including already-done tasks.
        pending = [task for task in app.tasks if not task.done()]
        if not pending:
            return
        await asyncio.wait_for(asyncio.gather(*pending), timeout=5)
    raise AssertionError('Naming tasks did not settle')


async def imported(app_factory, tmp_path, count=1, **metadata):
    for index in range(count):
        native_session(tmp_path / 'saved-project', f'unnamed-{index}', messages=[
            {'role': 'user', 'content': '<context_file paths="@notes.md">Internal instructions</context_file>\nSummarize the quarterly report'},
            {'role': 'assistant', 'content': 'A saved answer'}],
            metadata={'name': '', 'name_source': 'fallback', **metadata})
    app = app_factory()
    for _ in range(100):
        await app.history.refresh(force=False)
        if app.state['sharedHistory'].get('reconciliation', {}).get('phase') == 'complete':
            break
    assert len(app.directory.select('history_managed AND native_project IS NOT NULL', limit=count+1)) == count
    calls = []

    async def complete(session, prompt):
        calls.append((session['id'], prompt))
        return json.dumps({'action': 'set', 'name': 'Quarterly report summary'})

    async def ready(session):
        return True

    app.naming_backfill = NamingBackfill(app, complete=complete)
    clock = [100]
    naming = AutomaticNaming(app, provider_ready=ready, clock=lambda: clock[0])
    return app, naming, clock, calls


async def tick(app, naming, clock):
    clock[0] += 15
    await naming.schedule()
    await finish_actions(app)


async def test_idle_imports_get_names_without_loading_history_or_running_tools(app_factory, tmp_path, monkeypatch):
    app, naming, clock, calls = await imported(app_factory, tmp_path)
    session = app.directory.select('history_managed AND native_project IS NOT NULL', limit=1)[0]
    assert session['id'] not in {row['id'] for row in app.state['sessions']}
    directory = directory_for(app.data_dir, session)
    transcript = (directory / 'transcript.jsonl').read_bytes()
    original_session = app._session

    def no_hydration(*args, **kwargs):
        assert kwargs.get('hydrate') is False
        return original_session(*args, **kwargs)

    monkeypatch.setattr(app, '_session', no_hydration)
    await tick(app, naming, clock)
    session = app._session(session['id'], hydrate=False)
    assert session['title'] == 'Quarterly report summary'
    assert session['historyLoaded'] is False and session['messages'] == []
    assert len(calls) == 1 and 'Summarize the quarterly report' in calls[0][1]
    assert 'Internal instructions' not in calls[0][1] and '<context_file' not in calls[0][1]
    assert (directory / 'transcript.jsonl').read_bytes() == transcript
    assert read(directory)['name_source'] == 'generated'
    assert not app.runtime.started and not app.runtime.sent
    await tick(app, naming, clock)
    assert len(calls) == 1


async def test_discovery_and_preview_are_read_only_but_background_loop_names(app_factory, tmp_path):
    app, naming, clock, calls = await imported(app_factory, tmp_path)
    session = app.directory.select('history_managed AND native_project IS NOT NULL', limit=1)[0]
    from amplifier_web.chat_title_preview import read as preview
    assert preview(app.data_dir, session)['title'] == 'Summarize the quarterly report'
    await app.history.refresh()
    assert not calls
    app.history.naming = naming
    app.history.start()
    for _ in range(100):
        if calls:
            break
        await asyncio.sleep(.01)
    assert len(calls) == 1
    await finish_actions(app)


@pytest.mark.parametrize('restriction', ['busy', 'update', 'voice', 'no-view', 'loading'])
async def test_background_naming_waits_for_an_idle_open_view(app_factory, tmp_path, restriction):
    app, naming, clock, calls = await imported(app_factory, tmp_path)
    if restriction == 'busy':
        active = app._new_session({'title': 'Unrelated active work'})
        active['status'] = 'working'
        app.state['sessions'].append(active)
    elif restriction == 'update':
        app.state.setdefault('updates', {})['pendingRestart'] = True
    elif restriction == 'voice':
        app.state['voice']['status'] = 'connected'
    elif restriction == 'no-view':
        for queue in list(app.queue_clients):
            app.unsubscribe(queue)
    else:
        app.state['sharedHistory']['loading'] = True
    await tick(app, naming, clock)
    assert not calls and not naming.attempts


@pytest.mark.parametrize('metadata', [
    {'name': 'Chosen title', 'name_source': 'manual'},
    {'name': 'Existing generated title', 'name_source': 'generated'},
    {'name_auto': False},
    {'parent_id': 'parent-chat'},
])
async def test_existing_names_auto_off_and_workers_are_excluded(app_factory, tmp_path, metadata):
    app, naming, clock, calls = await imported(app_factory, tmp_path, **metadata)
    await tick(app, naming, clock)
    assert not calls


async def test_two_per_pass_with_cadence_and_persistent_failure_suppression(app_factory, tmp_path):
    app, naming, clock, calls = await imported(app_factory, tmp_path, 3)

    async def failing(session, prompt):
        calls.append(session['id'])
        raise ValueError('Unavailable naming provider')

    app.naming_backfill.complete = failing
    await tick(app, naming, clock)
    assert len(calls) == 2
    await naming.schedule()
    await finish_actions(app)
    assert len(calls) == 2  # Fast catalog recovery ticks cannot spend more.
    await tick(app, naming, clock)
    assert len(calls) == 3
    home = app.data_dir
    await app.close()
    restarted = app_factory(home=home)
    await restarted.history.refresh()
    restarted.naming_backfill = NamingBackfill(restarted, complete=failing)
    restored = AutomaticNaming(restarted, provider_ready=naming.provider_ready)
    await restored.schedule()
    await finish_actions(restarted)
    assert len(calls) == 3
    assert len(restored.attempts) == 3
    # Explicitly changing the Auto preference permits a new attempt.
    session = restarted.directory.select('history_managed AND native_project IS NOT NULL', limit=1)[0]
    set_automatic(restarted.data_dir, session, False)
    set_automatic(restarted.data_dir, session, True)
    restored.next_scan = 0
    await restored.schedule()
    await finish_actions(restarted)
    assert len(calls) == 4


async def test_unconfigured_provider_is_eligible_after_setup(app_factory, tmp_path):
    app, naming, clock, calls = await imported(app_factory, tmp_path)
    configured = [False]

    async def ready(session):
        return configured[0]

    naming.provider_ready = ready
    await tick(app, naming, clock)
    assert not calls and not naming.attempts
    configured[0] = True
    await tick(app, naming, clock)
    assert len(calls) == 1


async def test_manual_choice_during_automatic_probe_wins(app_factory, tmp_path):
    app, naming, clock, calls = await imported(app_factory, tmp_path)

    async def rename(session, prompt):
        await app.dispatch('session.rename', {'id': session['id'], 'title': 'My report'})
        return json.dumps({'action': 'set', 'name': 'Late generated title'})

    app.naming_backfill.complete = rename
    await tick(app, naming, clock)
    session = next(row for row in app.state['sessions'] if row.get('nativeProject'))
    assert session['title'] == 'My report'
    assert not app.state['namingBackfill']['named']


async def test_pause_between_queued_calls_defers_without_consuming_attempt(app_factory, tmp_path):
    app, naming, clock, calls = await imported(app_factory, tmp_path, 2)
    app.naming_backfill.concurrency = 1

    async def pause(session, prompt):
        calls.append(session['id'])
        app.state.setdefault('updates', {})['pendingRestart'] = True
        return json.dumps({'action': 'set', 'name': 'First report'})

    app.naming_backfill.complete = pause
    await tick(app, naming, clock)
    assert len(calls) == 1 and len(naming.attempts) == 1
    assert len(app.state['namingBackfill']['skipped']) == 1
    app.state.setdefault('updates', {})['pendingRestart'] = False
    await tick(app, naming, clock)
    assert len(calls) == 2


async def test_scans_bound_metadata_reads_and_rotate_past_unconfigured_folders(app_factory, tmp_path, monkeypatch):
    app, naming, clock, calls = await imported(app_factory, tmp_path, 40)
    from amplifier_web import automatic_naming
    seen = []
    original = automatic_naming.read

    def tracked(directory):
        seen.append(directory)
        return original(directory)

    async def not_ready(session):
        return False

    naming.provider_ready = not_ready
    residents = {row['id'] for row in app.state['sessions']}
    monkeypatch.setattr(automatic_naming, 'read', tracked)
    await tick(app, naming, clock)
    assert len(seen) == 32
    await tick(app, naming, clock)
    assert len(seen) == 64 and len(set(seen)) == 40
    assert not calls
    assert {row['id'] for row in app.state['sessions']} == residents


async def test_deferred_name_waits_for_new_content_and_oversized_input_is_bounded(app_factory, tmp_path, monkeypatch):
    app, naming, clock, calls = await imported(app_factory, tmp_path)

    async def defer(session, prompt):
        calls.append(session['id'])
        return json.dumps({'action': 'defer'})

    app.naming_backfill.complete = defer
    await tick(app, naming, clock)
    await tick(app, naming, clock)
    assert len(calls) == 1
    session = next(row for row in app.state['sessions'] if row.get('nativeProject'))
    directory = directory_for(app.data_dir, session)

    async def refresh_current_revision():
        expected_revision = revision(session)
        assert expected_revision is not None
        for _ in range(100):
            await app.history.refresh(force=True)
            current = app.directory.resolve(session['id'])
            if current is not None and current.get('nativeRevision') == expected_revision:
                break
        assert current is not None
        assert current.get('nativeRevision') == expected_revision, (
            'History catalog did not observe the updated transcript revision')

    with (directory / 'transcript.jsonl').open('a') as stream:
        stream.write(json.dumps({'role': 'user', 'content': 'Include the revenue breakdown'}) + '\n')
    await refresh_current_revision()
    await tick(app, naming, clock)
    assert len(calls) == 2
    # Oversized first records never cause a full transcript read or model call.
    (directory / 'transcript.jsonl').write_text(json.dumps({'role': 'user', 'content': 'x' * (300 * 1024)}) + '\n')
    await refresh_current_revision()
    await tick(app, naming, clock)
    assert len(calls) == 2
    assert 'no user message' in app.state['namingBackfill']['skipped'][0]['reason'].lower()


async def test_attempt_receipts_stay_in_sql_and_only_requested_owner_changes(app_factory):
    app = app_factory()
    AutomaticNaming(app)
    app.db.executemany('INSERT INTO automatic_naming_attempts VALUES (?,?)',
        ((f'old-{index}', json.dumps([index, 0])) for index in range(27000)))
    app.db.commit()
    statements = []
    app.db.set_trace_callback(statements.append)
    naming = AutomaticNaming(app)
    assert not any(statement.startswith('SELECT') for statement in statements)
    assert naming.attempts.get('old-23') == '[23, 0]'
    assert naming.attempts.get('unseen') is None
    naming.attempts['old-23'] = 'changed source'
    naming.attempts['new-owner'] = 'new source'
    assert naming.attempts.pop('old-24') == '[24, 0]'
    app.db.commit()
    assert len(naming.attempts) == 27000
    reads = [statement for statement in statements if statement.startswith('SELECT source')]
    assert len(reads) == 3 and all(' WHERE id=' in statement for statement in reads)
    app.db.set_trace_callback(None)
    restored = AutomaticNaming(app)
    assert restored.attempts.get('old-23') == 'changed source'
    assert restored.attempts.get('new-owner') == 'new source'
    assert restored.attempts.get('old-24') is None
    assert restored.attempts.get('old-25') == '[25, 0]'
