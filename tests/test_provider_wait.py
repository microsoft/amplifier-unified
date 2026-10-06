"""Contract and adversarial receiving tests, with no provider payload capture."""
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from amplifier_web.execution import ingest
from amplifier_web.execution_events import CALL_PURPOSE, CURRENT_CALL, ExecutionEvents
from amplifier_web.provider_wait import observation, public_wait
from amplifier_web.runtime import normalize_event
from amplifier_web.session_health import exception_details


LIMITS = dict(mode='none', elapsed_seconds=None, connect_seconds=5,
              pool_seconds=5, read_seconds=None, write_seconds=None)
SECRET = 'PRIVATE-PAYLOAD-SENTINEL'


def progress(kind='attempt_started', attempt=1, **extra):
    return dict(version=1, observation=kind, attempt=attempt, limits=dict(LIMITS), **extra)


@pytest.mark.parametrize('patch', [
    {'version': True}, {'version': 2}, {'attempt': True}, {'attempt': 0},
    {'attempt': 1.5}, {'attempt': float('inf')}, {'attempt': 10**400}, {'observation': 'heartbeat'},
    {'limits': []}, {'limits': {}}, {'limits': {**LIMITS, 'read_seconds': SECRET}},
    {'limits': {**LIMITS, 'pool_seconds': -1}},
    {'limits': {**LIMITS, 'write_seconds': float('nan')}},
    {'limits': {**LIMITS, 'write_seconds': 10**400}},
    {'limits': {**LIMITS, 'read_seconds': False}},
    {'limits': {**LIMITS, 'elapsed_seconds': 0}},
    {'limits': {**LIMITS, 'mode': 'elapsed'}},
    {'limits': {**LIMITS, 'elapsed_seconds': 10}},
])
def test_malformed_observation_is_ignored(patch):
    assert observation({**progress(), **patch}) is None


def test_nested_metadata_is_validated_twice():
    wait = dict(version=1, attempt=1, limits={**LIMITS, 'payload': SECRET},
                observedAt=100, lastResponseActivityAt=99, raw=SECRET)
    event = dict(id='call', kind='llm', phase='running', sessionId='root',
                 rootSessionId='root', providerWait=wait, payload=SECRET)
    normalized = normalize_event(dict(type='execution.event', event=event), 'root')[1]
    assert normalized['providerWait'] == dict(version=1, attempt=1, limits=LIMITS,
                                              observedAt=100, lastResponseActivityAt=99)
    session = {}
    ingest(session, event)  # Bypass normalization deliberately.
    assert SECRET not in json.dumps(session) + json.dumps(normalized)
    for bad in (True, float('nan'), -1, SECRET):
        invalid = {**event, 'providerWait': {**wait, 'observedAt': bad}}
        assert 'providerWait' not in normalize_event(dict(type='execution.event', event=invalid), 'root')[1]
        fresh = {}
        ingest(fresh, invalid)
        assert 'providerWait' not in fresh['execution']['nodes'][0]
    assert public_wait(None) is None


@pytest.mark.asyncio
async def test_concurrent_root_two_children_and_naming_keep_host_identity():
    emitted, gates = [], [asyncio.Event() for _ in range(4)]
    events = ExecutionEvents('root', emitted.append)
    events.turn_id = 'turn'
    for sid in ('child-a', 'child-b'):
        events.lifecycle(dict(type='child.updated', sessionId=sid, parentSessionId='root',
                              agent='worker', status='running', callId=sid))

    async def call(sid, index, naming=False):
        purpose = CALL_PURPOSE.set(dict(label='Naming', purpose='naming', lifecycle='background')) if naming else None
        try:
            class Provider:
                def get_info(self):
                    return SimpleNamespace(id='fixture', defaults={'model': 'model'})

                async def complete(self, request):
                    data = progress(raw=SECRET, request_id='foreign-id', model=SECRET, observedAt=-1)
                    events.hook(sid, 'llm:progress', data)
                    before = copy.deepcopy(events.nodes[CURRENT_CALL.get()])
                    events.hook('foreign', 'llm:progress', progress('response_activity'))
                    events.hook(sid, 'llm:progress', {**data, 'session_id': 'foreign'})
                    events.hook(sid, 'llm:progress', {**data, 'version': 9})
                    events.hook(sid, 'llm:progress', data)  # Duplicate admission.
                    assert events.nodes[CURRENT_CALL.get()] == before
                    gates[index].set()
                    await asyncio.gather(*(gate.wait() for gate in gates))
                    await asyncio.create_task(asyncio.to_thread(lambda: None))
                    events.hook(sid, 'llm:progress', progress('response_activity'))
                    events.hook(sid, 'llm:progress', progress(attempt=2))
                    newer = copy.deepcopy(events.nodes[CURRENT_CALL.get()])
                    events.hook(sid, 'llm:progress', progress('response_activity', attempt=1))
                    assert events.nodes[CURRENT_CALL.get()] == newer
                    return SimpleNamespace(usage=None)

            await events.instrument_provider(sid, Provider()).complete(SimpleNamespace(model=None))
        finally:
            if purpose is not None:
                CALL_PURPOSE.reset(purpose)

    await asyncio.gather(call('root', 0), call('child-a', 1), call('child-b', 2), call('root', 3, True))
    calls = [row for row in events.nodes.values() if row['kind'] == 'llm']
    assert len(calls) == 4
    assert all(row['phase'] == 'completed' and row['providerWait']['attempt'] == 2
               and 'lastResponseActivityAt' in row['providerWait'] for row in calls)
    assert {row['parentId'] for row in calls} == {None, 'worker:child-a', 'worker:child-b'}
    assert len([row for row in calls if row.get('lifecycle') == 'background']) == 1
    assert SECRET not in json.dumps(emitted)
    for row in calls:
        before = copy.deepcopy(events.nodes[row['id']])
        token = CURRENT_CALL.set(row['id'])
        try:
            events.hook(row['sessionId'], 'llm:progress', progress('response_activity', attempt=3))
            events.hook(row['sessionId'], 'provider:retry', {})
        finally:
            CURRENT_CALL.reset(token)
        assert events.nodes[row['id']] == before
    events.hook('root', 'llm:progress', progress())  # No CURRENT_CALL.
    assert len([row for row in events.nodes.values() if row['kind'] == 'llm']) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize('ending', ['error', 'cancelled'])
async def test_settlement_retains_latest_metadata_and_typed_failure(ending):
    from amplifier_core.llm_errors import LLMTimeoutError
    events = ExecutionEvents('root', lambda row: None)
    class Provider:
        def get_info(self):
            return SimpleNamespace(id='fixture', defaults={})
        async def complete(self, request):
            events.hook('root', 'llm:progress', progress())
            events.hook('root', 'llm:progress', progress('response_activity'))
            if ending == 'cancelled':
                raise asyncio.CancelledError()
            error = LLMTimeoutError(SECRET, retryable=False)
            error.request_outcome, error.effects = 'unknown', 'may_have_occurred'
            raise error
    with pytest.raises(BaseException):
        await events.instrument_provider('root', Provider()).complete(SimpleNamespace(model=None))
    row = next(iter(events.nodes.values()))
    assert row['phase'] == ending and row['endedAt']
    assert row['providerWait']['lastResponseActivityAt']
    if ending == 'error':
        assert row['failure']['effects'] == 'may_have_occurred'
        assert row['failure']['requestOutcome'] == 'unknown'
    else:
        assert 'failure' not in row
    assert SECRET not in json.dumps(row)


@pytest.mark.parametrize('ended', [101, None])
def test_ingestion_rejects_late_reopening_even_with_new_revision(ended):
    event = dict(id='call', kind='llm', phase='running', revision=1,
                 providerWait=dict(version=1, attempt=1, limits=LIMITS, observedAt=100))
    session = {}
    ingest(session, event)
    ingest(session, {**event, 'revision': 2, 'phase': 'cancelled', 'endedAt': ended})
    before = copy.deepcopy(session)
    ingest(session, {**event, 'revision': 9, 'providerWait': {**event['providerWait'], 'attempt': 9}})
    assert session == before


def test_uncertainty_requires_typed_contract_not_sdk_text():
    class UnknownOutcome(Exception):
        retryable = False
        request_outcome = 'unknown'
        effects = 'may_have_occurred'
    detail = exception_details(UnknownOutcome(SECRET))
    assert detail['category'] == 'provider_outcome_unknown'
    assert detail['effects'] == 'may_have_occurred' and detail['retryable'] is False
    assert SECRET not in json.dumps(detail)
    assert exception_details(RuntimeError("request_outcome='unknown' effects='may_have_occurred' timeout"))['category'] == 'unknown'