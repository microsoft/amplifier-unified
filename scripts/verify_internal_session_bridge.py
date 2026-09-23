#!/usr/bin/env python3
"""Opt-in cross-repository acceptance; install candidate CLI/memory in a private venv.

Runs the real memory launcher, CLI initializer/headless save, Foundation storage,
and Unified discovery. Only subprocess dispatch and the Core session/provider
execution boundary are replaced. No model, browser, AppService, or real history.
"""
from __future__ import annotations

import asyncio
from contextlib import redirect_stdout
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch


def main():
    cli = importlib.import_module('amplifier_app_cli.main')
    runner = importlib.import_module('amplifier_app_cli.session_runner')
    from amplifier_app_cli.session_store import SessionStore
    from amplifier_memory import suggest
    from amplifier_web.native_history import NativeHistory
    from amplifier_web.session_navigation import is_top_level
    from rich.console import Console

    ids = {'internal': 'a7fe814d-1134-4b2d-8bfc-8332bc039b6f',
           'human': 'a81e814d-1134-4b2d-8bfc-8332bc039b6f',
           'legacy': 'b81e814d-1134-4b2d-8bfc-8332bc039b6f'}
    dispatches = []
    observed_before_execute = []
    with tempfile.TemporaryDirectory(prefix='internal-session-bridge-') as tmp:
        private = Path(tmp).resolve()
        workspace = private / 'workspace'
        workspace.mkdir()
        before_cwd = Path.cwd()
        environment = {**os.environ, 'HOME': str(private / 'home'),
            'USERPROFILE': str(private / 'home'), 'AMPLIFIER_HOME': str(private / 'amplifier'),
            'AMPLIFIER_SESSION_STATE_HOME': str(private / 'ownership'),
            'AMPLIFIER_SESSION_ORIGIN': 'human', 'AMPLIFIER_SESSION_VISIBILITY': 'chat'}
        environment.pop('PYTEST_CURRENT_TEST', None)
        environment.pop('AMPLIFIER_SESSION_PURPOSE', None)
        try:
            os.chdir(workspace)
            with patch.dict(os.environ, environment, clear=True):
                native = SessionStore()

                def execute_fixture(identity, prompt, *, child_env, initial=None, fail=False):
                    messages = list(initial or [])
                    context = MagicMock()
                    context.get_messages = AsyncMock(side_effect=lambda: list(messages))
                    async def set_messages(value):
                        messages[:] = value
                    context.set_messages = AsyncMock(side_effect=set_messages)
                    session = MagicMock()
                    session.config = {}
                    session.session_id = identity
                    session.cleanup = AsyncMock()
                    session.coordinator.get.side_effect = lambda key: context if key == 'context' else ({} if key == 'providers' else None)
                    session.coordinator.get_capability.return_value = None
                    session.coordinator.cancellation.is_cancelled = False
                    async def execute(prompt):
                        metadata = native.get_metadata_if_exists(identity)
                        observed_before_execute.append({'id': identity, 'visibility': metadata.get('session_visibility')})
                        messages.extend([{'role': 'user', 'content': prompt}, {'role': 'assistant', 'content': '[]'}])
                        if fail:
                            raise RuntimeError('synthetic execution failure')
                        return '[]'
                    session.execute = AsyncMock(side_effect=execute)
                    output = io.StringIO()
                    with (patch.dict(os.environ, child_env, clear=True),
                          patch.object(runner, '_create_bundle_session', AsyncMock(return_value=session)),
                          patch('amplifier_app_cli.commands.init.check_first_run', return_value=False),
                          patch.object(cli, 'console', Console(file=io.StringIO())), redirect_stdout(output)):
                        code = 0
                        try:
                            asyncio.run(cli.execute_single(prompt, {}, [], False, session_id=identity,
                                bundle_name='synthetic', output_format='json', initial_transcript=initial))
                        except SystemExit as exc:
                            code = exc.code
                    return subprocess.CompletedProcess([], code, output.getvalue(), '')

                def dispatch(argv, **kwargs):
                    dispatches.append({'argv': argv[:-1], 'visibility': kwargs['env'].get('AMPLIFIER_SESSION_VISIBILITY'),
                                       'purpose': kwargs['env'].get('AMPLIFIER_SESSION_PURPOSE')})
                    return execute_fixture(ids['internal'], argv[-1], child_env=kwargs['env'])

                with patch.object(suggest.subprocess, 'run', side_effect=dispatch):
                    assert suggest.default_model_call('synthetic memory judge') == '[]'
                assert os.environ['AMPLIFIER_SESSION_VISIBILITY'] == 'chat'
                internal_messages, internal_metadata = native.load(ids['internal'])
                assert internal_metadata['session_visibility'] == 'internal'
                assert internal_metadata['session_purpose'] == 'memory.suggestion'
                assert observed_before_execute[0]['visibility'] == 'internal'
                # Ordinary agent-invoked JSON CLI work remains a user-owned root.
                assert execute_fixture(ids['human'], 'synthetic standalone user task',
                    child_env={**environment, 'AMPLIFIER_SESSION_ORIGIN': 'agent'}).returncode == 0
                # Re-entering an internal job as a human does not erase its origin.
                assert execute_fixture(ids['internal'], 'synthetic resume', child_env=environment,
                    initial=internal_messages, fail=True).returncode == 1
                assert native.load(ids['internal'])[1]['session_purpose'] == 'memory.suggestion'
                # New launcher labels never retroactively classify legacy history.
                native.save(ids['legacy'], [{'role':'user', 'content':'synthetic legacy'}], {'bundle':'synthetic'})
                legacy, _ = native.load(ids['legacy'])
                assert execute_fixture(ids['legacy'], 'synthetic legacy resume', initial=legacy,
                    child_env={**environment, 'AMPLIFIER_SESSION_VISIBILITY':'internal',
                               'AMPLIFIER_SESSION_PURPOSE':'memory.suggestion'}).returncode == 0
                assert 'session_visibility' not in native.load(ids['legacy'])[1]
                original = {str(path.relative_to(private)): path.read_bytes() for path in native.base_dir.rglob('*') if path.is_file()}
                snapshot = NativeHistory(private / 'amplifier').scan()
                rows = snapshot['sessions']
                classifications = {row['nativeIdentity']: row['sessionKind'] for row in rows}
                assert classifications == {ids['internal']:'internal', ids['human']:'root', ids['legacy']:'root'}
                assert len([row for row in rows if is_top_level(row)]) == 2
                assert original == {str(path.relative_to(private)): path.read_bytes() for path in native.base_dir.rglob('*') if path.is_file()}
                assert native.load(ids['internal'])[0][:2] == internal_messages
                result = {'passed':True, 'dispatches':dispatches, 'beforeExecute':observed_before_execute,
                    'classifications':classifications, 'ordinaryChatCount':2, 'nativeFilesUnchangedByDiscovery':True,
                    'syntheticOnly':True, 'realProviderCalls':0}
        finally:
            os.chdir(before_cwd)
    modules = [runner, importlib.import_module('amplifier_app_cli.incremental_save'),
               importlib.import_module('amplifier_app_cli.session_provenance'), suggest,
               importlib.import_module('amplifier_web.native_history'),
               importlib.import_module('amplifier_foundation.session.history')]
    result['sources'] = [{'module': module.__name__, 'path': module.__file__,
        'sha256':hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()} for module in modules]
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
