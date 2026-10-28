"""Provider-free driver around real Core tools, Work checks and worker pipes.

Only the finite driver/local-command tool is synthetic. No credential discovery,
provider request, user transcript or host application is used.
"""
import asyncio
import importlib.util
import json
from pathlib import Path
import resource
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from amplifier_core import HookResult, ToolResult
from amplifier_core.message_models import ToolCall
from amplifier_foundation.bundle import Bundle, BundleModuleResolver, PreparedBundle
from amplifier_web.app_guidance import install_app_access
from amplifier_web.runtime_worker import Worker, configure_protocol, publish
from amplifier_web import performance_diagnostics as timings


class LocalCommand:
    name = 'local_command'
    description = 'Run the fixed synthetic local command.'
    input_schema = {'type': 'object', 'properties': {}, 'additionalProperties': False}

    async def execute(self, arguments):
        assert arguments == {}
        proc = await asyncio.create_subprocess_exec('/bin/sh', '-c', 'printf LATENCY_COMMAND_OK',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await proc.communicate()
        return ToolResult(success=proc.returncode == 0,
            output={'stdout': out.decode(), 'stderr': err.decode(), 'returncode': proc.returncode})


class Probe(Worker):
    async def setup(self, data):
        self.workspace = Path(data['workspace'])
        modules = ('loop-live', 'context-simple', 'tool-skills')
        paths = {name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent
                 for name in modules}
        bundle = Bundle(name='non-llm-latency', base_path=self.workspace,
            session={'orchestrator': {'module': 'loop-live'}, 'context': {'module': 'context-simple'}},
            tools=[{'module': 'tool-skills', 'config': {
                'skills_dirs': [str(self.workspace / '.amplifier/skills')],
                'visibility': {'placement': 'request'}}}])
        prepared = PreparedBundle(bundle.to_mount_plan(), BundleModuleResolver(paths), bundle)
        self.session = await prepared.create_session(session_id=data['id'], session_cwd=self.workspace)
        c = self.session.coordinator
        assert not c.get('providers')
        self.runtime = SimpleNamespace(generation={'id': 'fixture-generation', 'input_ids': []},
                                       session_id=data['id'], closed=False, stop_requested=False)
        self.activation_gate = SimpleNamespace(current_valid=True)
        self.context_inputs = []
        from amplifier_web.execution_events import ExecutionEvents
        self.telemetry = ExecutionEvents(data['id'], publish)
        self.install_activity(c)
        await c.mount('tools', LocalCommand(), name='local_command')
        await install_app_access(c, self.app_access_bridge(c))
        self.results = {}

        async def observe(event, value):
            call = value.get('tool_call_id')
            if call:
                result = value.get('result', value.get('tool_result'))
                if hasattr(result, 'model_dump'):
                    result = result.model_dump()
                self.results[call] = result
            return HookResult()
        c.hooks.register('tool:post', observe, name='fixture-result')
        # Warm the real skill loader before the measured batch. This warm-up is
        # explicit and identical in every arm; it is not a production bypass.
        await self.invoke('warm', 'load_skill', {'skill_name': 'latency-control'})
        self.results.clear()
        publish({'type': 'runtime.ready', 'report': {'session_id': data['id'], 'providers': []}})

    async def invoke(self, identity, name, arguments):
        c = self.session.coordinator
        loop = c.get('orchestrator')
        loop._tool_calls_this_turn = 0
        start = time.monotonic_ns()
        await loop._execute_tool_only(ToolCall(id=identity, name=name, arguments=arguments),
            c.get('tools'), c.hooks, None, c)
        end = time.monotonic_ns()
        value = self.results.pop(identity)
        assert value['success'], (name, value)
        return {'callId': identity, 'operation': name, 'startNs': start, 'endNs': end,
                'result': value['output']}

    async def command(self, data):
        if data.get('op') == 'start':
            try:
                await self.setup(data['session'])
            except Exception as error:
                publish({'type': 'runtime.error', 'error': repr(error)})
                self.shutdown.set()
            return
        if data.get('op') == 'probe':
            try:
                assert timings.TIMING_ENABLED is data['timing'], 'Both processes must inherit the same startup switch'
                before = time.process_time_ns()
                calls = [('command', 'local_command', {}),
                         ('skill', 'load_skill', {'skill_name': 'latency-control'}),
                         ('actions', 'app_control', {'operation': 'list_actions', 'args': {'prefix': 'theme.', 'limit': 1}}),
                         ('history', 'app_control', {'operation': 'history', 'args': {
                             'action': 'read', 'session_id': data['historyId'], 'limit': 5, 'text_limit': 100}})]
                values = await asyncio.gather(*(self.invoke(data['batch'] + '-' + label, name, args) for label, name, args in calls))
                publish({'op': 'reply', 'id': data['id'], 'result': {'calls': values,
                    'cpuNs': time.process_time_ns() - before,
                    'peakRSSKiB': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                    'clockDomain': timings.clock_domain(), 'providers': 0}})
            except Exception as error:
                publish({'op': 'reply', 'id': data['id'], 'error': repr(error)})
            return
        if data.get('op') == 'stop':
            self.shutdown.set()
            return
        await super().command(data)


if __name__ == '__main__':
    configure_protocol()
    asyncio.run(Probe().run())
