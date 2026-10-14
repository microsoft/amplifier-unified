"""Real Core/Foundation child mounts share the host's assignment admission."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip('amplifier_module_loop_live')
pytest.importorskip('amplifier_module_context_simple')
from test_collaborative_workspaces import app
from test_assignments import start, limits


@pytest.mark.parametrize('mode', ['finite', 'resumed', 'persistent', 'nested'])
async def test_native_child_admission_counts_without_topology_and_cannot_escape_limit(app, tmp_path, monkeypatch, mode):
    from amplifier_foundation.bundle import Bundle, BundleModuleResolver
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_web.runtime_worker import Worker
    from amplifier_web.host.children import install_children, _PERSISTENT
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.host.approvals import Approvals
    from amplifier_web.execution_events import ExecutionEvents
    from amplifier_web.capacity import admission

    source = app.state['sessions'][0]
    await start(app)
    await limits(app, source, maxTotalTokens=30, reserveTokens=20)
    spec = importlib.util.spec_from_file_location('assignment_child_fixture', Path(__file__).parent / 'fixtures/standalone_children_probe.py')
    fixture = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, fixture)
    spec.loader.exec_module(fixture)
    plan = {'session': {'orchestrator': {'module': 'loop-live'}, 'context': {'module': 'context-simple'}},
            'agents': {'worker': {'instruction': 'Bounded assignment admission check'}}}
    paths = {name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent for name in ('loop-live', 'context-simple')}
    prepared = fixture.ProbePrepared(plan, BundleModuleResolver(paths), Bundle(name='fixture', session=plan['session'], agents=plan['agents']))
    approvals = Approvals(None, AsyncMock(return_value='deny'))
    parent = await prepared.create_session(session_id=source['id'], approval_system=approvals)
    events = []
    worker = Worker()
    worker.session = parent
    worker.telemetry = ExecutionEvents(source['id'], events.append)
    async def guard(row):
        result = await admission(app, source['id'], {'call': row})
        if not result['allowed']:
            raise ValueError('; '.join(result['reasons']))
        row.update(budgetRevision=result['budgetRevision'], admittedAt=result['admittedAt'])
    worker.telemetry.admission_guard = guard
    parent.coordinator.register_capability('web.activity.install', worker.install_activity)
    registry = await install_children(parent, prepared, Runtime(source['id']), SessionStore(tmp_path / 'children'), approvals)
    calls, denied = [], []
    class Provider:
        def get_info(self): return SimpleNamespace(id='fixture', defaults={})
        async def complete(self, request):
            calls.append(request.model)
            return SimpleNamespace(usage={'input_tokens': 10, 'output_tokens': 10})
    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        observed = coordinator.get_capability('web.provider_observe')(Provider())
        if not calls:
            await observed.complete(SimpleNamespace(model='first'))
            for event in events:
                if event.get('type') == 'execution.event':
                    await app.on_runtime_event('execution.event', event['event'])
            events.clear()
        with pytest.raises(ValueError, match='unreserved'):
            await observed.complete(SimpleNamespace(model='must-not-call'))
        denied.append(coordinator.session_id)
        if mode == 'nested' and coordinator.session_id == 'child':
            await registry.spawn('worker', 'nested', registry.sessions['child'], sub_session_id='grandchild')
        return 'Assignment limit confirmed'
    monkeypatch.setattr(fixture.FiniteOrchestrator, 'execute', execute)
    token = _PERSISTENT.set(mode == 'persistent')
    try:
        await registry.spawn('worker', 'inspect', parent, sub_session_id='child')
        if mode == 'resumed':
            await registry.resume('child', 'continue', parent)
        assert len(denied) == (2 if mode in {'resumed', 'nested'} else 1)
        assert calls == ['first']
        assert app.assignments.usage(app.assignments.current(source))['metrics']['grossTotalTokens']['value'] == 20
    finally:
        _PERSISTENT.reset(token)
        await parent.cleanup()
