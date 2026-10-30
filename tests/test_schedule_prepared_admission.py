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


PROVIDER = '''from pathlib import Path
import json, time, uuid
from amplifier_core import ProviderInfo
from amplifier_core.message_models import ChatResponse, TextBlock, Usage
from amplifier_web.execution_events import CALL_PURPOSE, CURRENT_CALL
__amplifier_module_type__ = 'provider'
class Provider:
    name = 'fixture'
    def __init__(self, config=None, coordinator=None): self.coordinator, self.config = coordinator, config or {}
    def get_info(self): return ProviderInfo(id='fixture', display_name='Offline fixture', defaults={'model':'fixture', 'reasoning_effort':'low'})
    async def list_models(self): return [{'id':'fixture'}, {'id':'fixture-pinned'}]
    def parse_tool_calls(self, response): return []
    async def complete(self, request, **kwargs):
        purpose = CALL_PURPOSE.get()
        runtime = self.coordinator.get_capability('live.runtime')
        generation = getattr(runtime, 'generation', None) or {}
        row = {'id':str(uuid.uuid4()), 'telemetryCallId':CURRENT_CALL.get(),
            'sessionId':self.coordinator.session_id, 'purpose':purpose,
            'lifecycle':(purpose or {}).get('lifecycle', 'turn'),
            'generationId':generation.get('id'), 'inputIds':generation.get('input_ids', []),
            'requestModel':request.model, 'requestEffort':request.reasoning_effort,
            'kwargsModel':kwargs.get('model'), 'kwargsEffort':kwargs.get('reasoning_effort'),
            'effectiveModel':kwargs.get('model') or request.model or 'fixture',
            'effectiveEffort':kwargs.get('reasoning_effort') or request.reasoning_effort or 'low'}
        def record(phase):
            with Path(self.config['calls']).open('a') as stream:
                stream.write(json.dumps({**row, 'phase':phase, 'at':time.time()})+'\\n')
        record('started')
        record('completed')
        return ChatResponse(content=[TextBlock(text='SCHEDULE_ADMISSION_CONTROL')], usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2))
async def mount(coordinator, config=None):
    await coordinator.mount('providers', Provider(config, coordinator), name='fixture')
'''


async def until(predicate):
    async with asyncio.timeout(45):
        while not predicate():
            await asyncio.sleep(.02)


def assert_execution(report):
    """Compare the complete observed populations, never a filtered happy subset."""
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
    turn_calls = []
    for row in begins:
        assert row['telemetryCallId'], row
        if row['purpose'] is None:
            assert row['lifecycle'] == 'turn', row
            assert len(row['inputIds']) == 1, row
            turn_calls.append((row['sessionId'], row['inputIds'][0], row['generationId']))
            selected = report['selection']
            assert row['effectiveModel'] == (selected['model'] if selected else 'fixture'), row
            assert row['effectiveEffort'] == (selected['effort'] if selected else 'low'), row
            if selected:
                assert row['requestModel'] == selected['model'] and row['requestEffort'] == selected['effort'], row
        else:
            # Naming is deliberately enabled. Only its explicit producer purpose
            # and exact completed input may explain an additional provider call.
            assert row['purpose']['label'] == 'Session naming' and row['lifecycle'] == 'background', row
            assert (row['sessionId'], row['purpose']['turnId']) in expected_inputs, row
    assert Counter(turn_calls) == Counter(started), 'Unexpected foreground provider execution'


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
    call['purpose'] = {'label': 'Unattributed work', 'lifecycle': 'background'}
    call['lifecycle'] = 'background'
    with pytest.raises(AssertionError):
        assert_execution(bad)
    report['negativeControls'] = ['additional_accepted_admission', 'additional_generation',
                                  'unattributed_auxiliary_call']
    if report['selection']:
        bad = copy.deepcopy(report)
        call = next(row for row in bad['calls'] if row['phase'] == 'started' and row['purpose'] is None)
        call.update(requestModel=None, kwargsModel=None, effectiveModel='fixture')
        with pytest.raises(AssertionError):
            assert_execution(bad)
        bad = copy.deepcopy(report)
        call = next(row for row in bad['calls'] if row['phase'] == 'started' and row['purpose'] is None)
        call.update(requestEffort=None, kwargsEffort=None, effectiveEffort='low')
        with pytest.raises(AssertionError):
            assert_execution(bad)
        report['negativeControls'] += ['dropped_model_pin', 'dropped_effort_pin']


@pytest.mark.parametrize('selection', [None, {'instance': 'fixture', 'model': 'fixture-pinned', 'effort': 'high'}], ids=['default', 'pinned'])
async def test_prepared_first_and_second_occurrences_execute_once(tmp_path, monkeypatch, selection):
    import amplifier_module_context_simple as context
    import amplifier_module_loop_live as loop
    import amplifier_module_hook_context_intelligence as capture
    import amplifier_web.runtime_worker as worker

    shared, workspace, home = (tmp_path / name for name in ('shared', 'workspace', 'app'))
    shared.mkdir(); workspace.mkdir()
    monkeypatch.setenv('AMPLIFIER_HOME', str(shared))
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(home))
    monkeypatch.setenv('AMPLIFIER_RUNTIME_IMMUTABLE', '1')
    monkeypatch.setenv('AMPLIFIER_UNIFIED_IMPORT_HOME', str(tmp_path / 'legacy'))
    monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME', str(tmp_path / 'ownership'))
    monkeypatch.setenv('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', str(tmp_path / 'capture'))
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
    command = [sys.executable, str(Path(worker.__file__).resolve())]
    monkeypatch.setattr(RuntimeManager, '_command', lambda self, *a, **kw: list(self.command or command))
    runtime = RuntimeManager(command=command, startup_timeout=45, retention={'prewarm_on_select': False})
    app = AppService(home, runtime=runtime, workspace=workspace)
    app.management = Management(app)
    runtime.app_bridge, runtime.home = app.app_bridge, home
    now = [1800000000.0]
    app.schedules.clock = lambda: now[0]
    observations = []
    report = {'selection': selection, 'stages': observations, 'expected': [], 'admissions': [],
        'generations': [], 'providerEvents': [], 'calls': [], 'repeatTicks': [], 'versions': {
        name: importlib.metadata.version(name) for name in ('amplifier-core', 'amplifier-foundation',
            'amplifier-module-loop-live', 'amplifier-module-context-simple')}}
    original_dispatch = app.dispatch
    original_admit = runtime.scheduled_input
    original_event = app.on_runtime_event
    source_id = None
    completed = False

    def read_calls():
        return [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []

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
        await app.management.ensure_runtime(app._session(source_id))
        await runtime.control(source_id, 'task.get', {})
        source_summary = template(app, app._session(source_id))[1]
        fields = {'sessionId': source_id, **config(now[0], 'task'), 'destination': 'new_task',
                  'newTaskMaxTurns': 1, 'prompt': 'Report SCHEDULE_ADMISSION_CONTROL, then finish.', 'notificationPolicy': 'always'}
        preview = (await app.dispatch('schedule.preview', fields))['result']
        schedule = (await app.dispatch('schedule.create', {**fields, 'expectedRevision': 0,
            'previewHash': preview['previewHash']}, command_id='synthetic-schedule'))['result']['schedule']
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
                path = Path(artifacts) / ('schedule-prepared-' + ('pinned' if selection else 'default') + '.json')
                path.write_text(json.dumps(report, indent=2))
