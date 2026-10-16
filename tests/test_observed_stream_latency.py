"""Observed text has a bounded batch window; hidden progress stays amortized."""
import asyncio

import pytest

from test_automatic_history import app_factory


async def chats(app_factory):
    app = app_factory()
    await app.dispatch('session.create', {'title': 'Viewed'})
    viewed = app.state['selectedSessionId']
    await app.dispatch('session.create', {'title': 'Background'})
    background = app.state['selectedSessionId']
    for queue in list(app.queues):
        app.unsubscribe(queue)
    return app, viewed, background


@pytest.mark.parametrize('observer', ['selected-client', 'second-client', 'session-stream', 'child-view'])
async def test_observed_stream_reaches_queue_before_background_window(app_factory, observer):
    app, viewed, background = await chats(app_factory)
    if observer == 'session-stream':
        queue = app.subscribe(viewed)
    else:
        app.clients.attach('first')
        app.clients.attach('second')
        app.clients.records['first']['selectedSessionId'] = background
        app.clients.records['second']['selectedSessionId'] = background
        owner = 'second' if observer == 'second-client' else 'first'
        if observer == 'child-view':
            app.clients.records[owner]['view']['subagentHistory'] = {'sessionId': viewed}
        else:
            app.clients.records[owner]['selectedSessionId'] = viewed
        with app.clients.bind(owner):
            queue = app.subscribe()
    await app.on_runtime_event('assistant.delta', {'sessionId': viewed, 'text': 'Visible now'})
    # The ordinary background publication window is 250ms. This contract leaves
    # generous scheduling allowance around the shorter observed-stream window.
    snapshot = await asyncio.wait_for(queue.get(), .18)
    if observer == 'child-view':
        # Child readers use their detail endpoint; the shell carries a new
        # revision, rather than embedding every child transcript.
        assert snapshot['revision'] == app.state['revision']
        assert app._session(viewed)['streaming'] == 'Visible now'
    else:
        assert 'Visible now' in str(snapshot)
    assert not app._progress_dirty


async def test_unobserved_stream_retains_background_batch_window(app_factory):
    app, viewed, background = await chats(app_factory)
    app.clients.attach('viewer')
    app.clients.records['viewer']['selectedSessionId'] = viewed
    with app.clients.bind('viewer'):
        queue = app.subscribe()
    await app.on_runtime_event('assistant.delta', {'sessionId': background, 'text': 'Hidden'})
    revision = app.state['revision']
    await asyncio.sleep(.10)
    assert app.state['revision'] == revision
    assert app._progress_dirty and queue.empty()
    await asyncio.wait_for(asyncio.shield(app._progress_publish_task), .3)
    assert not app._progress_dirty
    assert queue.empty(), 'Unobserved detail is not broadcast to another selected chat'


async def test_visible_delta_shortens_existing_background_wait_without_starvation(app_factory):
    app, viewed, background = await chats(app_factory)
    queue = app.subscribe(viewed)
    await app.on_runtime_event('assistant.delta', {'sessionId': background, 'text': 'Background'})
    task = app._progress_publish_task
    await asyncio.sleep(.02)
    await app.on_runtime_event('assistant.delta', {'sessionId': viewed, 'text': 'First'})
    for _ in range(6):
        await asyncio.sleep(.005)
        await app.on_runtime_event('assistant.delta', {'sessionId': viewed, 'text': ' more'})
    assert app._progress_publish_task is task, 'Wake the existing owner; do not churn publication tasks'
    snapshot = await asyncio.wait_for(queue.get(), .12)
    assert 'First more' in str(snapshot)
    assert app._session(background)['streaming'] == 'Background'


async def test_disconnected_client_does_not_accelerate_hidden_progress(app_factory):
    app, viewed, _ = await chats(app_factory)
    app.clients.attach('disconnected')
    app.clients.records['disconnected']['selectedSessionId'] = viewed
    await app.on_runtime_event('assistant.delta', {'sessionId': viewed, 'text': 'Retained client'})
    revision = app.state['revision']
    await asyncio.sleep(.10)
    assert app.state['revision'] == revision and app._progress_dirty
