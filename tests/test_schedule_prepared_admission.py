"""Offline real worker preparation and two fresh scheduled executions.

Only the provider is fake. The scheduler, creation/task receipts, worker processes,
Core/Foundation, loop, context and configuration persistence are production code.
No existing schedules, accounts or homes are used.
"""
import asyncio
import copy
from collections import Counter
import importlib.metadata
import json
import os
from pathlib import Path
import sys

import pytest

from amplifier_web.builtin_behaviors import DEFAULT_BEHAVIORS
from amplifier_web.management import Management
from amplifier_web.runtime import RuntimeManager
from amplifier_web.service import AppService
from amplifier_web.session_creation import configuration_changes, template
from test_schedules import config


OBSERVER = '''import contextvars, functools, hashlib, inspect, json, os, time, uuid
from pathlib import Path
from amplifier_core import HookResult

INVOCATION = contextvars.ContextVar('schedule_fixture_producer', default=None)

def record(kind, **values):
    row = {'kind':kind, 'at':time.time(), **values}
    with Path(os.environ['SCHEDULE_FIXTURE_OBSERVATIONS']).open('a') as stream:
        stream.write(json.dumps(row)+'\\n')
    return row

def binding(coordinator):
    runtime = coordinator.get_capability('live.runtime')
    generation = getattr(runtime, 'generation', None) or {}
    return {'sessionId':coordinator.session_id, 'generationId':generation.get('id'),
            'inputIds':list(generation.get('input_ids', []))}

def wrap(cls, method):
    original = getattr(cls, method)
    signature = inspect.signature(original)
    source_hash = hashlib.sha256(inspect.getsource(original).encode()).hexdigest()
    @functools.wraps(original)
    async def observed(self, *args, **kwargs):
        coordinator = signature.bind(self, *args, **kwargs).arguments['coordinator']
        invocation = {'id':str(uuid.uuid4()), 'producer':method,
                      'sourceSha256':source_hash, **binding(coordinator)}
        active = {**invocation, 'resolutions':[]}
        token = INVOCATION.set(active)
        record('producer', **invocation, phase='started')
        try:
            result = await original(self, *args, **kwargs)
            evidence = {}
            if method == '_evaluate_goal':
                evidence['satisfied'] = result[0]
            if method == '_execute_one_turn':
                evidence['returnedMarker'] = result == 'SCHEDULE_ADMISSION_CONTROL'
            record('producer', **invocation, phase='completed', **evidence)
            return result
        except BaseException as exc:
            record('producer', **invocation, phase='failed', errorType=type(exc).__name__)
            raise
        finally:
            INVOCATION.reset(token)
    setattr(cls, method, observed)

def install():
    from amplifier_module_loop_streaming import StreamingOrchestrator
    for method in ('_execute_one_turn', '_evaluate_goal', '_summarize_goal_run', '_judge_stall'):
        wrap(StreamingOrchestrator, method)

def attach(coordinator):
    async def resolved(event, data):
        active = INVOCATION.get()
        row = {'id':str(uuid.uuid4()), 'invocationId':active['id'] if active else None,
               **binding(coordinator), **{key:data.get(key) for key in ('scope','basis','provider','model')}}
        row = record('resolution', **row)
        if active is not None:
            active['resolutions'].append(row)
        return HookResult()
    async def progress(event, data):
        record('goal_progress', **binding(coordinator),
               **{key:data.get(key) for key in ('state','turn','continuations','cap','schema_version')})
        return HookResult()
    coordinator.hooks.register('provider:resolve', resolved, name='fixture-producer-resolution')
    coordinator.hooks.register('orchestrator:goal_progress', progress, name='fixture-goal-progress')
'''


PROVIDER = '''from pathlib import Path
import json, time, uuid
from amplifier_core import ProviderInfo
from amplifier_core.message_models import ChatResponse, TextBlock, Usage
from amplifier_web.execution_events import CALL_PURPOSE, CURRENT_CALL
from schedule_observer import INVOCATION, attach
__amplifier_module_type__ = 'provider'
class Provider:
    name = 'fixture'
    def __init__(self, config=None, coordinator=None): self.coordinator, self.config = coordinator, config or {}
    def get_info(self): return ProviderInfo(id='fixture', display_name='Offline fixture', defaults={'model':'fixture', 'reasoning_effort':'low'})
    async def list_models(self): return [{'id':'fixture'}, {'id':'fixture-pinned'}]
    def parse_tool_calls(self, response): return []
    async def complete(self, request, **kwargs):
        purpose = CALL_PURPOSE.get()
        invocation = INVOCATION.get()
        runtime = self.coordinator.get_capability('live.runtime')
        generation = getattr(runtime, 'generation', None) or {}
        row = {'id':str(uuid.uuid4()), 'telemetryCallId':CURRENT_CALL.get(),
            'sessionId':self.coordinator.session_id, 'purpose':purpose,
            'lifecycle':(purpose or {}).get('lifecycle', 'turn'),
            'generationId':generation.get('id'), 'inputIds':generation.get('input_ids', []),
            'requestModel':request.model, 'requestEffort':request.reasoning_effort,
            'kwargsModel':kwargs.get('model'), 'kwargsEffort':kwargs.get('reasoning_effort'),
            'effectiveModel':kwargs.get('model') or request.model or 'fixture',
            'effectiveEffort':kwargs.get('reasoning_effort') or request.reasoning_effort or 'low',
            'extendedThinking':kwargs.get('extended_thinking'),
            'invocationId':invocation['id'] if invocation else None,
            'producer':invocation['producer'] if invocation else None,
            'resolutions':list(invocation['resolutions']) if invocation else []}
        def record(phase, **evidence):
            with Path(self.config['calls']).open('a') as stream:
                stream.write(json.dumps({**row, 'phase':phase, 'at':time.time(), **evidence})+'\\n')
        record('started')
        try:
            producer = row['producer']
            if producer == '_execute_one_turn':
                text = 'SCHEDULE_ADMISSION_CONTROL'
                evidence = {'responseKind':'conversation_marker'}
            elif producer == '_evaluate_goal':
                messages = await self.coordinator.get('context').get_messages()
                def has_marker(message):
                    if message.get('role') != 'assistant': return False
                    content = message.get('content')
                    if isinstance(content, str): return content == 'SCHEDULE_ADMISSION_CONTROL'
                    return isinstance(content, list) and any(
                        (block.get('text') if isinstance(block, dict) else getattr(block, 'text', None))
                        == 'SCHEDULE_ADMISSION_CONTROL' for block in content)
                satisfied = any(has_marker(message) for message in messages)
                text = ('YES' if satisfied else 'NO') + '\\n' + (
                    'The synthetic assistant result contains the exact marker.' if satisfied
                    else 'The synthetic assistant result has not produced the marker.')
                evidence = {'responseKind':'goal_verdict', 'markerInAssistantContext':satisfied}
            elif producer == '_summarize_goal_run':
                text = 'The synthetic run ended; inspect its recorded goal outcome.'
                evidence = {'responseKind':'goal_summary'}
            elif producer == '_judge_stall':
                text = 'RESOLVABLE\\nNOT_DEMONSTRATED\\nThe synthetic marker has not been verified.'
                evidence = {'responseKind':'stall_verdict'}
            else:
                raise AssertionError('Unattributed provider call')
            response = ChatResponse(content=[TextBlock(text=text)], usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2))
            record('completed', **evidence)
            return response
        except BaseException as exc:
            record('failed', errorType=type(exc).__name__)
            raise
async def mount(coordinator, config=None):
    attach(coordinator)
    await coordinator.mount('providers', Provider(config, coordinator), name='fixture')
'''


async def until(predicate):
    async with asyncio.timeout(45):
        while not predicate():
            await asyncio.sleep(.02)


def assert_execution(report):
    """Compare the complete observed populations, never a filtered happy subset."""
    assert not report.get('observationErrors'), report.get('observationErrors')
    expected = report['expected']
    assert len(expected) == len({row['inputId'] for row in expected})
    assert len(expected) == len({row['target'] for row in expected})
    keys = ('target', 'inputId', 'taskId', 'taskRevision')
    assert Counter(tuple(row[key] for key in keys) for row in report['admissions']) == Counter(
        tuple(row[key] for key in keys) for row in expected), 'Unexpected admission'
    for row in report['admissions']:
        assert 'error' not in row and row['result'] == {'accepted': True, 'inputId': row['inputId']}, row
    expected_inputs = Counter((row['target'], row['inputId']) for row in expected)
    started, finished = [], []
    for row in report['generations']:
        assert row['sessionId'] == row['rootSessionId'], row
        ids = row.get('input_ids') or [row.get('initial_input_id')]
        assert len(ids) == 1, row
        bound = (row['sessionId'], ids[0], row['generation_id'])
        assert bound[2], row
        if row['event'] == 'generation.started':
            started.append(bound)
        else:
            assert row['event'] == 'generation.finished', row
            finished.append(bound)
    assert Counter((sid, identity) for sid, identity, _ in started) == expected_inputs, 'Unexpected generation'
    assert Counter(started) == Counter(finished), 'Unsettled or duplicate generation'
    assert len({row[2] for row in started}) == len(expected), 'Generation reused'
    calls = report['calls']
    begins = [row for row in calls if row['phase'] == 'started']
    ends = [row for row in calls if row['phase'] == 'completed']
    assert Counter(row['id'] for row in begins) == Counter(row['id'] for row in ends), 'Unsettled provider call'
    assert len({row['id'] for row in begins}) == len(begins), 'Duplicate provider observation'
    assert len(begins) + len(ends) == len(calls), 'Unexplained provider outcome'
    end_by_id = {row['id']: row for row in ends}
    for row in begins:
        assert all(end_by_id[row['id']].get(key) == value
                   for key, value in row.items() if key not in {'phase', 'at'}), 'Changed provider completion'
    producers = report['producerEvents']
    assert all(row.get('kind') in {'producer', 'resolution', 'goal_progress'} for row in producers), 'Unknown observation kind'
    assert all(row.get('phase') in {'started', 'completed'} for row in producers
               if row['kind'] == 'producer'), 'Unsettled producer phase'
    entries = [row for row in producers if row['kind'] == 'producer' and row['phase'] == 'started']
    terminals = [row for row in producers if row['kind'] == 'producer' and row['phase'] == 'completed']
    assert len({row['id'] for row in entries}) == len(entries), 'Duplicate producer invocation'
    assert Counter(row['id'] for row in entries) == Counter(row['id'] for row in terminals), 'Unsettled producer'
    assert not any(row.get('phase') == 'failed' for row in producers), 'Failed producer'
    invocation_by_id = {row['id']: row for row in entries}
    terminal_by_id = {row['id']: row for row in terminals}
    for row in entries:
        assert all(terminal_by_id[row['id']].get(key) == value
                   for key, value in row.items() if key not in {'phase', 'at'}), 'Changed producer completion'
    expected_producers = Counter((*bound, method) for bound in started
                                 for method in ('_execute_one_turn', '_evaluate_goal'))
    assert all(len(row['inputIds']) == 1 for row in entries), entries
    assert Counter((row['sessionId'], row['inputIds'][0], row['generationId'], row['producer'])
                   for row in entries) == expected_producers, 'Unexpected producer population'
    resolutions = [row for row in producers if row['kind'] == 'resolution']
    resolution_by_id = {row['id']: row for row in resolutions}
    assert len(resolution_by_id) == len(resolutions), 'Duplicate resolution'
    call_invocations, call_resolutions, turn_calls, evaluators = [], [], [], []
    for row in begins:
        assert row['telemetryCallId'], row
        assert row['invocationId'] in invocation_by_id, 'Unattributed provider call'
        invocation = invocation_by_id[row['invocationId']]
        assert row['producer'] == invocation['producer'], row
        for key in ('sessionId', 'generationId', 'inputIds'):
            assert row[key] == invocation[key], row
        assert len(row['inputIds']) == 1, row
        bound = (row['sessionId'], row['inputIds'][0], row['generationId'])
        assert bound in started, row
        # Exactly one native producer resolution, captured inside this invocation.
        # Never match a session-global "last scope" or correlate by arrival order.
        assert len(row['resolutions']) == 1, row
        resolution, = row['resolutions']
        assert resolution_by_id.get(resolution['id']) == resolution, row
        assert resolution['invocationId'] == row['invocationId'], row
        assert all(resolution[key] == row[key] for key in ('sessionId', 'generationId', 'inputIds')), row
        assert resolution['basis'] and resolution['provider'] == 'fixture', row
        assert resolution['model'] == row['effectiveModel'], row
        call_invocations.append(row['invocationId'])
        call_resolutions.append(resolution['id'])
        if row['producer'] == '_execute_one_turn':
            assert resolution['scope'] == 'conversation', row
            turn_calls.append(bound)
            selected = report['selection']
            assert row['effectiveModel'] == (selected['model'] if selected else 'fixture'), row
            assert row['effectiveEffort'] == (selected['effort'] if selected else 'low'), row
            if selected:
                assert row['requestModel'] == selected['model'] and row['requestEffort'] == selected['effort'], row
            assert terminal_by_id[row['invocationId']]['returnedMarker'], row
        else:
            assert row['producer'] in {'_evaluate_goal', '_summarize_goal_run', '_judge_stall'}, row
            assert resolution['scope'] == 'goal_utility', row
            assert row['extendedThinking'] is False, row
            if row['producer'] == '_evaluate_goal':
                evaluators.append(bound)
                assert terminal_by_id[row['invocationId']]['satisfied'] is True, row
                end = next(item for item in ends if item['id'] == row['id'])
                assert end['responseKind'] == 'goal_verdict' and end['markerInAssistantContext'], end
    assert Counter(turn_calls) == Counter(started), 'Unexpected foreground provider execution'
    assert Counter(evaluators) == Counter(started), 'Missing or duplicate real evaluation'
    assert Counter(call_invocations) == Counter(invocation_by_id.keys()), 'Unexpected producer call population'
    assert Counter(call_resolutions) == Counter(resolution_by_id.keys()), 'Unaccounted resolution'
    progress = [row for row in producers if row['kind'] == 'goal_progress']
    assert progress, 'No real goal outcome'
    assert all(row['state'] == 'achieved' and row['turn'] == 1 and row['continuations'] == 0
               and row['cap'] == 1 for row in progress), progress
    assert Counter((row['sessionId'], row['inputIds'][0], row['generationId'])
                   for row in progress if len(row['inputIds']) == 1) == Counter(started), 'Unexpected goal outcome'
    assert len(progress) == len(started)


def test_execution_oracle_rejects_additional_accepted_execution():
    """A duplicate accepted input is an error even if IDs/results look valid."""
    expected = {'target': 'destination', 'inputId': 'schedule:one', 'taskId': 'task', 'taskRevision': 1}
    admission = {**expected, 'result': {'accepted': True, 'inputId': expected['inputId']}}
    report = {'expected': [expected], 'admissions': [admission, copy.deepcopy(admission)]}
    with pytest.raises(AssertionError, match='Unexpected admission'):
        assert_execution(report)


def assert_fault_controls(report):
    """Adversarial copies of real observations; never submit another input."""
    bad = copy.deepcopy(report)
    bad['admissions'].append(copy.deepcopy(bad['admissions'][0]))
    with pytest.raises(AssertionError, match='Unexpected admission'):
        assert_execution(bad)
    bad = copy.deepcopy(report)
    for event in report['generations'][:2]:
        bad['generations'].append({**event, 'generation_id': 'unaccounted-generation'})
    with pytest.raises(AssertionError, match='Unexpected generation'):
        assert_execution(bad)
    bad = copy.deepcopy(report)
    # Even a superficially plausible auxiliary label must not hide execution.
    call = next(row for row in bad['calls'] if row['phase'] == 'started')
    call['invocationId'] = 'unobserved-producer'
    with pytest.raises(AssertionError):
        assert_execution(bad)
    report['negativeControls'] = ['additional_accepted_admission', 'additional_generation',
                                  'unattributed_auxiliary_call']
    bad = copy.deepcopy(report)
    progress = next(row for row in bad['producerEvents'] if row['kind'] == 'goal_progress')
    progress['state'] = 'error'
    with pytest.raises(AssertionError):
        assert_execution(bad)
    bad = copy.deepcopy(report)
    call = next(row for row in bad['calls'] if row['phase'] == 'started')
    call['resolutions'][0]['invocationId'] = 'different-producer'
    with pytest.raises(AssertionError):
        assert_execution(bad)
    report['negativeControls'] += ['failed_goal_outcome', 'misbound_resolution']
    bad = copy.deepcopy(report)
    original_call = next(row for row in report['calls'] if row['phase'] == 'started')
    extra_calls = [copy.deepcopy(row) for row in report['calls'] if row['id'] == original_call['id']]
    for row in extra_calls:
        row['id'] = 'extra-call'
        row['telemetryCallId'] = 'extra-telemetry-call'
    bad['calls'].extend(extra_calls)
    with pytest.raises(AssertionError):
        assert_execution(bad)
    bad = copy.deepcopy(report)
    original_call = next(row for row in report['calls']
                         if row['phase'] == 'started' and row['producer'] == '_evaluate_goal')
    extra_id = 'extra-summary-invocation'
    for row in report['producerEvents']:
        if row.get('id') == original_call['invocationId']:
            bad['producerEvents'].append({**copy.deepcopy(row), 'id': extra_id, 'producer': '_summarize_goal_run'})
    resolution = {**copy.deepcopy(original_call['resolutions'][0]),
                  'id': 'extra-resolution', 'invocationId': extra_id}
    bad['producerEvents'].append(resolution)
    for row in report['calls']:
        if row['id'] == original_call['id']:
            bad['calls'].append({**copy.deepcopy(row), 'id': 'extra-summary-call',
                'telemetryCallId': 'extra-summary-telemetry', 'producer': '_summarize_goal_run',
                'invocationId': extra_id, 'resolutions': [resolution]})
    with pytest.raises(AssertionError, match='Unexpected producer population'):
        assert_execution(bad)
    for mutation in ('phase', 'identity', 'provider_identity', 'malformed'):
        bad = copy.deepcopy(report)
        if mutation == 'phase':
            row = next(row for row in bad['producerEvents'] if row['kind'] == 'producer')
            bad['producerEvents'].append({**row, 'phase': 'cancelled'})
        elif mutation == 'identity':
            row = next(row for row in bad['producerEvents'] if row.get('phase') == 'completed')
            row['sessionId'] = 'wrong-destination'
        elif mutation == 'provider_identity':
            row = next(row for row in bad['calls'] if row['phase'] == 'completed')
            row['sessionId'] = 'wrong-destination'
        else:
            bad['observationErrors'] = [{'file': 'calls', 'line': 1, 'error': 'truncated'}]
        with pytest.raises(AssertionError):
            assert_execution(bad)
    report['negativeControls'] += ['extra_provider_call', 'fully_attributed_extra_summary',
        'unknown_producer_phase', 'changed_producer_completion', 'changed_provider_completion',
        'malformed_observation']
    if report['selection']:
        bad = copy.deepcopy(report)
        call = next(row for row in bad['calls'] if row['phase'] == 'started' and row['producer'] == '_execute_one_turn')
        call.update(requestModel=None, kwargsModel=None, effectiveModel='fixture')
        with pytest.raises(AssertionError):
            assert_execution(bad)
        bad = copy.deepcopy(report)
        call = next(row for row in bad['calls'] if row['phase'] == 'started' and row['producer'] == '_execute_one_turn')
        call.update(requestEffort=None, kwargsEffort=None, effectiveEffort='low')
        with pytest.raises(AssertionError):
            assert_execution(bad)
        report['negativeControls'] += ['dropped_model_pin', 'dropped_effort_pin']


@pytest.mark.parametrize('selection', [None, {'instance': 'fixture', 'model': 'fixture-pinned', 'effort': 'high'}], ids=['default', 'pinned'])
@pytest.mark.parametrize('authorization', ['ui', 'full-system'])
async def test_prepared_first_and_second_occurrences_execute_once(tmp_path, monkeypatch, selection, authorization):
    import amplifier_module_context_simple as context
    import amplifier_module_loop_live as loop
    import amplifier_module_hook_context_intelligence as capture

    shared, workspace, home = (tmp_path / name for name in ('shared', 'workspace', 'app'))
    shared.mkdir(); workspace.mkdir()
    monkeypatch.setenv('AMPLIFIER_HOME', str(shared))
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(home))
    monkeypatch.setenv('AMPLIFIER_RUNTIME_IMMUTABLE', '1')
    monkeypatch.setenv('AMPLIFIER_UNIFIED_IMPORT_HOME', str(tmp_path / 'legacy'))
    monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME', str(tmp_path / 'ownership'))
    monkeypatch.setenv('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', str(tmp_path / 'capture'))
    producer_events = tmp_path / 'producer-observations.jsonl'
    monkeypatch.setenv('SCHEDULE_FIXTURE_OBSERVATIONS', str(producer_events))
    (tmp_path / 'schedule_observer.py').write_text(OBSERVER)
    launcher = tmp_path / 'observed_worker.py'
    launcher.write_text("import asyncio\nfrom schedule_observer import install\n"
                       "install()\nfrom amplifier_web.runtime_worker import Worker, configure_protocol\n"
                       "configure_protocol()\nasyncio.run(Worker().run())\n")
    modules = {name: str(Path(module.__file__).parent) for name, module in (
        ('loop-live', loop), ('context-simple', context), ('hook-context-intelligence', capture))}
    (shared / 'settings.yaml').write_text(json.dumps({'bundle': {'app': []},
        'web_bundles': {'excluded': list(DEFAULT_BEHAVIORS)}, 'sources': {'modules': modules}}))
    provider = tmp_path / 'provider' / 'amplifier_module_provider_fixture'
    provider.mkdir(parents=True)
    (provider / '__init__.py').write_text(PROVIDER)
    calls = tmp_path / 'calls.jsonl'
    # Compose an ordinary root with a local behavior, not an exported snapshot.
    awareness = tmp_path / 'awareness.md'; awareness.write_text('Synthetic schedule awareness.\n')
    behavior = tmp_path / 'behavior.yaml'
    behavior.write_text(json.dumps({'bundle': {'name': 'fixture-behavior'}, 'context': {'awareness': str(awareness)}}))
    bundle = tmp_path / 'bundle.yaml'
    bundle.write_text(json.dumps({'bundle': {'name': 'schedule-fixture'},
        'includes': [{'bundle': str(behavior)}],
        'session': {'orchestrator': {'module': 'loop-live', 'config': {'use_streaming': False, 'max_iterations': 1}},
                    'context': {'module': 'context-simple', 'source': modules['context-simple']}},
        'providers': [{'module': 'provider-fixture', 'source': str(provider.parent), 'config': {'calls': str(calls)}}]}))
    command = [sys.executable, str(launcher)]
    monkeypatch.setattr(RuntimeManager, '_command', lambda self, *a, **kw: list(self.command or command))
    runtime = RuntimeManager(command=command, startup_timeout=45, retention={'prewarm_on_select': False})
    app = AppService(home, runtime=runtime, workspace=workspace)
    app.management = Management(app)
    runtime.app_bridge, runtime.home = app.app_bridge, home
    now = [1800000000.0]
    app.schedules.clock = lambda: now[0]
    observations = []
    report = {'selection': selection, 'stages': observations, 'expected': [], 'admissions': [],
        'generations': [], 'providerEvents': [], 'calls': [], 'producerEvents': [], 'repeatTicks': [], 'versions': {
        name: importlib.metadata.version(name) for name in ('amplifier-core', 'amplifier-foundation',
            'amplifier-module-loop-live', 'amplifier-module-context-simple')}}
    original_dispatch = app.dispatch
    original_admit = runtime.scheduled_input
    original_event = app.on_runtime_event
    source_id = None
    completed = False

    def read_calls():
        errors, decoded = [], {}
        for name, path in (('producerEvents', producer_events), ('calls', calls)):
            try:
                raw = path.read_text() if path.exists() else ''
            except (OSError, UnicodeError) as exc:
                errors.append({'file': path.name, 'error': type(exc).__name__})
                raw = ''
            # Retain original lines even when a partial write cannot be decoded.
            report.setdefault('rawObservations', {})[name] = raw
            decoded[name] = []
            for number, line in enumerate(raw.splitlines(), 1):
                try:
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError('Observation is not an object')
                    decoded[name].append(value)
                except ValueError as exc:
                    errors.append({'file': path.name, 'line': number, 'error': str(exc)})
        report['observationErrors'] = errors
        report['producerEvents'] = decoded['producerEvents']
        return decoded['calls']

    async def observed_admit(target, args, guard):
        row = {'target': target, **copy.deepcopy(args)}
        report['admissions'].append(row)
        def observed_guard():
            reason = guard()
            summary = template(app, app._session(target))[1]
            directory = home / 'sessions' / target
            observations.append({'stage': 'input_admission', 'sessionId': target,
                'summary': summary, 'guardReason': reason,
                'planSource': 'effective' if (directory / 'effective-configuration.json').exists() else 'override'})
            return reason
        try:
            result = await original_admit(target, args, observed_guard)
            row['result'] = copy.deepcopy(result)
            return result
        except BaseException as exc:
            row['error'] = {'type': type(exc).__name__, 'detail': str(exc)}
            raise

    async def observed_event(kind, data):
        if kind == 'runtime.generation':
            report['generations'].append(copy.deepcopy(data))
        elif kind == 'execution.event' and data.get('kind') == 'llm':
            report['providerEvents'].append(copy.deepcopy(data))
        await original_event(kind, data)

    async def observed_dispatch(action, args=None, **kwargs):
        result = await original_dispatch(action, args, **kwargs)
        if kwargs.get('origin') == 'scheduler' and action in {'session.create', 'task.create'}:
            target = args['id'] if action == 'session.create' else args['sessionId']
            configuration, summary = template(app, app._session(target))
            # This fixture owns every value and has no credentials. Exact plans
            # are diagnostic evidence here only; production receipts keep hashes.
            observations.append({'stage': action, 'sessionId': target,
                                 'summary': summary, 'configuration': configuration,
                                 'planSource': 'effective' if (home / 'sessions' / target / 'effective-configuration.json').exists() else 'override',
                                 'receipt': copy.deepcopy(result['result'])})
        return result

    app.dispatch = observed_dispatch
    runtime.scheduled_input = observed_admit
    app.on_runtime_event = observed_event
    try:
        await app.dispatch('session.create', {'bundle': str(bundle), 'workspace': str(workspace),
                                             **({'selection': selection} if selection else {})})
        source_id = app._session()['id']
        if authorization == 'full-system':
            from test_full_feedback import mode
            await mode(app, app._session(source_id))
        await app.management.ensure_runtime(app._session(source_id))
        await runtime.control(source_id, 'task.get', {})
        source_summary = template(app, app._session(source_id))[1]
        fields = {'sessionId': source_id, **config(now[0], 'task'), 'destination': 'new_task',
                  'newTaskMaxTurns': 1, 'prompt': 'Report SCHEDULE_ADMISSION_CONTROL, then finish.', 'notificationPolicy': 'always'}
        preview = (await app.dispatch('schedule.preview', fields))['result']
        schedule = (await app.dispatch('schedule.create', {**fields, 'expectedRevision': 0,
            'previewHash': preview['previewHash']}, command_id='synthetic-schedule',
            **({'origin': 'system', 'caller_session_id': source_id} if authorization == 'full-system' else {})))['result']['schedule']
        report['authorization'] = authorization
        if authorization == 'full-system':
            assert schedule['authorization']['origin'] == 'system'
            assert schedule['authorization']['authority']['sessionId'] == source_id
            assert 'messageId' not in schedule['authorization']
        report['sourceConfiguration'] = source_summary
        destinations = []
        for occurrence in range(2):
            now[0] = app.schedules.store.get(source_id, schedule['id'])['nextDue']
            await app.schedules.tick(); await app.schedules.tick()
            run = app.schedules.store.runs(source_id)[0]
            report['runs'] = app.schedules.store.runs(source_id)
            assert run['phase'] in {'accepted', 'running', 'completed'}, json.dumps({
                'phase': run['phase'], 'detail': run.get('detail'),
                'changed': configuration_changes(observations[-2]['summary'], observations[-1]['summary'])
                           if len(observations) >= 2 else None})
            await until(lambda: app.schedules.store.run(source_id, run['id'])['phase'] == 'completed')
            target = run['destinationSessionId']
            await until(lambda: app._session(target)['status'] == 'idle')
            assert target not in destinations and target != source_id
            destinations.append(target)
            report['expected'].append({'target': target, 'inputId': run['inputId'],
                'taskId': run['taskId'], 'taskRevision': run['taskRevision']})
            assert run['creationConfirmed'] and run['taskId']
            task = (await runtime.control(target, 'task.get', {}))['task']
            assert task['id'] == run['taskId'] and task['maxTurns'] == 1
            report['calls'] = read_calls()
            assert_execution(report)
            assert any(row.get('text') == 'SCHEDULE_ADMISSION_CONTROL' for row in app._session(target)['messages'])
            before = (copy.deepcopy(report['admissions']), copy.deepcopy(report['generations']))
            await app.schedules.tick(); await app.schedules.tick()
            assert (report['admissions'], report['generations']) == before
            report['repeatTicks'].append({'occurrence': occurrence + 1, 'additionalAdmissions': 0,
                                         'additionalGenerations': 0})
            assert len(app.schedules.store.runs(source_id)) == occurrence + 1
        assert template(app, app._session(source_id))[1] == source_summary
        assert app._session(source_id)['messages'] == []
        completed = True
    except BaseException as exc:
        report['failure'] = {'type': type(exc).__name__, 'detail': str(exc)}
        raise
    finally:
        if source_id:
            report['runs'] = app.schedules.store.runs(source_id)
        try:
            await app.close()
            report['shutdown'] = 'completed'
            # Naming cleanup drains background calls. Check the final complete
            # observations too, including anything arriving during shutdown.
            report['calls'] = read_calls()
            if completed:
                assert_execution(report)
                assert len(report['expected']) == 2
                assert_fault_controls(report)
                report['verified'] = True
        except BaseException as exc:
            report['shutdownOrFinalCheckFailure'] = {'type': type(exc).__name__, 'detail': str(exc)}
            raise
        finally:
            report['calls'] = read_calls()
            artifacts = os.environ.get('AMPLIFIER_TEST_ARTIFACTS')
            if artifacts:
                path = Path(artifacts) / ('schedule-prepared-' + ('pinned' if selection else 'default')
                    + ('-full-system' if authorization == 'full-system' else '') + '.json')
                path.write_text(json.dumps(report, indent=2))
