"""Offline real worker preparation and two fresh scheduled executions.

Only the provider is fake. The scheduler, creation/task receipts, worker processes,
Core/Foundation, loop, context and configuration persistence are production code.
No existing schedules, accounts or homes are used.
"""
import asyncio
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
import json
from amplifier_core import ProviderInfo
from amplifier_core.message_models import ChatResponse, TextBlock, Usage
__amplifier_module_type__ = 'provider'
class Provider:
    name = 'fixture'
    def __init__(self, coordinator, config): self.coordinator, self.config = coordinator, config
    def get_info(self): return ProviderInfo(id='fixture', display_name='Offline fixture', defaults={'model':'fixture'})
    async def list_models(self): return [{'id':'fixture'}]
    def parse_tool_calls(self, response): return []
    async def complete(self, request, **kwargs):
        with Path(self.config['calls']).open('a') as stream:
            stream.write(json.dumps({'sessionId':self.coordinator.session_id, 'model':request.model})+'\\n')
        return ChatResponse(content=[TextBlock(text='SCHEDULE_ADMISSION_CONTROL')], usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2))
async def mount(coordinator, config=None):
    await coordinator.mount('providers', Provider(coordinator, config or {}), name='fixture')
'''


async def until(predicate):
    async with asyncio.timeout(45):
        while not predicate():
            await asyncio.sleep(.02)


@pytest.mark.parametrize('selection', [None, {'instance': 'fixture', 'model': 'fixture', 'effort': 'high'}], ids=['default', 'pinned'])
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
    report = {'selection': selection, 'stages': observations, 'versions': {
        name: importlib.metadata.version(name) for name in ('amplifier-core', 'amplifier-foundation',
            'amplifier-module-loop-live', 'amplifier-module-context-simple')}}
    original_dispatch = app.dispatch
    source_id = None

    async def observed_dispatch(action, args=None, **kwargs):
        result = await original_dispatch(action, args, **kwargs)
        if kwargs.get('origin') == 'scheduler' and action in {'session.create', 'task.create'}:
            target = args['id'] if action == 'session.create' else args['sessionId']
            _, summary = template(app, app._session(target))
            observations.append({'stage': action, 'sessionId': target, 'summary': summary})
        return result

    app.dispatch = observed_dispatch
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
                'changed': configuration_changes(observations[-2]['summary'], observations[-1]['summary'])})
            await until(lambda: app.schedules.store.run(source_id, run['id'])['phase'] == 'completed')
            target = run['destinationSessionId']
            await until(lambda: app._session(target)['status'] == 'idle')
            assert target not in destinations and target != source_id
            destinations.append(target)
            assert run['creationConfirmed'] and run['taskId']
            task = (await runtime.control(target, 'task.get', {}))['task']
            assert task['id'] == run['taskId'] and task['maxTurns'] == 1
            actual_calls = [json.loads(line) for line in calls.read_text().splitlines()]
            assert len(actual_calls) == occurrence + 1
            assert [row['sessionId'] for row in actual_calls] == destinations
            assert any(row.get('text') == 'SCHEDULE_ADMISSION_CONTROL' for row in app._session(target)['messages'])
            await app.schedules.tick()
            assert len(app.schedules.store.runs(source_id)) == occurrence + 1
        assert template(app, app._session(source_id))[1] == source_summary
        assert app._session(source_id)['messages'] == []
        report['calls'] = actual_calls
    finally:
        if source_id:
            report['runs'] = app.schedules.store.runs(source_id)
        await app.close()
        artifacts = os.environ.get('AMPLIFIER_TEST_ARTIFACTS')
        if artifacts:
            path = Path(artifacts) / ('schedule-prepared-' + ('pinned' if selection else 'default') + '.json')
            path.write_text(json.dumps(report, indent=2))
