"""Receiving -> bounded browser projection; no SDK messages or private extras."""
import copy
import json

import pytest

from amplifier_web.browser_detail import page, project, read_text
from amplifier_web.execution import ingest
from amplifier_web.session_health import failure_details, generation_failure


PRIVATE = 'PRIVATE-PROJECTION-PAYLOAD'
LIMITS = dict(mode='none', elapsed_seconds=None, connect_seconds=5,
              pool_seconds=5, read_seconds=None, write_seconds=None)


def wait(**patch):
    return dict(version=1, attempt=1, limits=dict(LIMITS),
                observedAt=10, lastResponseActivityAt=11, **patch)


def unknown_failure(timeout=False):
    value = failure_details(dict(retryable=False, request_outcome='unknown',
                                 effects='may_have_occurred'))
    if timeout:
        value.update(category='provider_timeout', errorType='LLMTimeoutError')
    return value


def session(nodes):
    return dict(id='root', messages=[], execution=dict(nodes=nodes, turns=[
        dict(id='turn', phase='completed', endedAt=20)]))


@pytest.mark.parametrize('mode,elapsed,read', [('none', None, None),
                                            ('elapsed', 2, 2), ('phase', None, 3)])
@pytest.mark.parametrize('phase', ['completed', 'error', 'cancelled'])
def test_received_wait_and_failure_survive_project_page_and_persisted_reload(mode, elapsed, read, phase):
    source = session([])
    metadata = wait()
    metadata['limits'].update(mode=mode, elapsed_seconds=elapsed, read_seconds=read)
    node = dict(id='call', kind='llm', turnId='turn', sessionId='child',
                rootSessionId='root', parentId='worker', provider='fixture',
                model='public-model', phase=phase, startedAt=1, endedAt=20,
                lifecycle='turn', requestCapture={'truncated': False},
                providerWait=metadata, failure=unknown_failure(True))
    ingest(source, node)
    # The real receiving seam does not ingest requestCapture; persisted request
    # details are attached separately by the existing event-log loader.
    source['execution']['nodes'][0]['requestCapture'] = node['requestCapture']
    original = copy.deepcopy(source)
    for restored in (source, json.loads(json.dumps(source))):
        for public in (project(restored)['execution']['nodes'], page(restored, 'nodes')['items']):
            row = public[0]
            assert row['providerWait'] == metadata
            assert row['failure'] == node['failure']
            for field in ('id', 'kind', 'turnId', 'sessionId', 'rootSessionId',
                          'parentId', 'provider', 'model', 'phase', 'startedAt',
                          'endedAt', 'lifecycle', 'requestCapture'):
                assert row[field] == node[field]
    assert source == original


def test_root_two_children_and_paged_terminal_rows_keep_their_own_latest_observations():
    source = session([])
    for i in range(105):
        metadata = wait()
        metadata['attempt'] = i + 1
        metadata['observedAt'] = 100 + i
        identity = ['root', 'child-a', 'child-b'][i % 3]
        event = dict(id=f'call-{i}', kind='llm', turnId='turn',
                     sessionId=identity, rootSessionId='root',
                     parentId=None if identity == 'root' else identity + '-worker',
                     revision=1, phase='running', providerWait=metadata)
        ingest(source, event)
        ingest(source, {**event, 'revision': 2, 'phase': 'error', 'endedAt': 300,
                        'failure': unknown_failure()})
        # A delayed progress observation cannot reopen the settled call.
        ingest(source, {**event, 'revision': 9, 'providerWait': wait()})
    restored = json.loads(json.dumps(source))
    latest = project(restored)
    earlier = page(restored, 'nodes', latest['executionWindow']['before'])
    rows = earlier['items'] + latest['execution']['nodes']
    assert len(rows) == 105
    for i, row in enumerate(rows):
        assert row['providerWait']['attempt'] == i + 1
        assert row['providerWait']['observedAt'] == 100 + i
        assert row['sessionId'] == ['root', 'child-a', 'child-b'][i % 3]
        assert row['phase'] == 'error' and row['endedAt'] == 300
        assert row['failure'] == unknown_failure()
    assert latest['execution']['aggregateUsage'] == source['execution']['aggregateUsage']


@pytest.mark.parametrize('patch', [
    {'version': True}, {'version': 2}, {'attempt': 0}, {'attempt': -1},
    {'attempt': float('nan')}, {'observedAt': float('nan')}, {'observedAt': -1},
    {'lastResponseActivityAt': float('inf')}, {'limits': {}},
    {'limits': {**LIMITS, 'read_seconds': -1}},
    {'limits': {**LIMITS, 'read_seconds': False}},
    {'limits': {**LIMITS, 'elapsed_seconds': 0}},
])
def test_malformed_persisted_wait_is_absent_not_guessed_unlimited(patch):
    metadata = {**wait(), **patch}
    source = session([dict(id='call', kind='llm', turnId='turn', providerWait=metadata)])
    for row in (project(source)['execution']['nodes'][0], page(source, 'nodes')['items'][0]):
        assert 'providerWait' not in row
    assert 'providerWait' not in project(session([dict(id='call', kind='llm')]))['execution']['nodes'][0]


def test_private_nested_fields_never_serialize_and_existing_tool_detail_stays_readable():
    metadata = wait()
    metadata.update(raw=PRIVATE, headers={'Authorization': PRIVATE}, request_id=PRIVATE)
    metadata['limits']['payload'] = PRIVATE
    failure = {**unknown_failure(True), 'cause': PRIVATE, 'body': PRIVATE, 'url': PRIVATE}
    source = session([
        dict(id='call', kind='llm', turnId='turn', providerWait=metadata,
             failure=failure, plugin=PRIVATE),
        dict(id='tool', kind='tool', turnId='turn', providerWait=metadata,
             input='i' * 8000, output='o' * 9000, error='e' * 7000)])
    for public in (project(source)['execution']['nodes'], page(source, 'nodes')['items']):
        assert PRIVATE not in json.dumps(public)
        assert public[0]['providerWait'] == wait()
        assert public[0]['failure'] == unknown_failure(True)
        assert 'providerWait' not in public[1]
        for field in ('input', 'output', 'error'):
            assert len(public[1][field]) == 512
            assert read_text(source, public[1][field + 'Detail'])['value'] == source['execution']['nodes'][1][field]


@pytest.mark.parametrize('patch', [
    {'category': 'unrecognized'}, {'category': []}, {'summary': PRIVATE},
    {'guidance': PRIVATE}, {'errorType': PRIVATE}, {'effects': 'rolled_back'},
    {'requestOutcome': 'not_sent'}, {'retryable': True}, {'replayed': True},
])
def test_forged_failure_contract_is_dropped_not_sanitized_by_message_guess(patch):
    failure = {**unknown_failure(True), **patch}
    source = session([dict(id='call', kind='llm', turnId='turn', failure=failure)])
    assert 'failure' not in project(source)['execution']['nodes'][0]
    assert 'failure' not in page(source, 'nodes')['items'][0]


@pytest.mark.parametrize('failure', [
    unknown_failure(), unknown_failure(True),
    failure_details(RuntimeError('private arbitrary SDK text')),
    failure_details('authentication', 'AuthenticationError'),
    failure_details('', 'RuntimeStartupError'),
    generation_failure(dict(error_category='provider_timeout', error_type='LLMTimeoutError',
                            error_stage='provider_request', retryable=False)),
    generation_failure(dict(error_category='context_limit', error_type='ContextLengthError',
                            error_stage='context_preparation', retryable=False)),
])
def test_existing_fixed_failure_vocabulary_is_preserved(failure):
    assert project(session([dict(id='call', kind='llm', turnId='turn',
                                failure=failure)]))['execution']['nodes'][0]['failure'] == failure