"""Opt-in published Instance forwarding, with synthetic state and no prompts."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import socket
import ssl
import sys

import aiohttp
import pytest

from amplifier_web.acp_participant import ACPParticipant
from amplifier_web.runtime import RuntimeManager
from amplifier_web.server import create_app


async def test_published_instance_forwards_retirement_and_outside_prompt_advisory(tmp_path, aiohttp_client):
    binary = os.environ.get('ACP_INSTANCE_BINARY')
    if not binary:
        pytest.skip('Select an independently verified published Instance binary for this optional integration check.')
    assert hashlib.sha256(Path(binary).read_bytes()).hexdigest() == '3306716c95ac5162fabc0990e6738c5486a23e9eef85ae636ad378cc0b3250f0'
    runtime = RuntimeManager(command=[sys.executable, '-c', 'raise AssertionError("No native input authorized")'])
    app = await create_app(tmp_path / 'unified', workspace=tmp_path, runtime=runtime,
        preload_providers=False, voice=False, background_updates=False, background_work=False)
    http = await aiohttp_client(app)
    credential = tmp_path / 'owner-token'
    credential.write_text(app['control_token'])
    credential.chmod(0o600)
    home = tmp_path / 'instance'
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    initialized = await asyncio.create_subprocess_exec(binary, 'init', '--home', str(home),
        '--name', 'Conditional retirement fixture', '--bind', '127.0.0.1', '--port', str(port),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    assert await initialized.wait() == 0
    configured = json.loads((home / 'config.json').read_text())
    configured['config']['server']['welcome_port'] = None
    (home / 'config.json').write_text(json.dumps(configured))
    process = await asyncio.create_subprocess_exec(binary, 'serve', '--home', str(home),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    trust = ssl.create_default_context(cafile=str(home / 'tls/root-ca.crt'))
    control = (home / 'secrets/control-token').read_text().strip()
    origin = f'https://127.0.0.1:{port}/api/v1'
    peers = []
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=trust),
                headers={'Authorization': 'Bearer ' + control}) as api:
            async def request(method, path, body=None):
                async with api.request(method, origin + path, json=body) as response:
                    assert response.status == 200, (method, path, response.status)
                    return await response.json()
            async with asyncio.timeout(15):
                while True:
                    assert process.returncode is None
                    try:
                        config = await request('GET', '/config')
                        break
                    except aiohttp.ClientConnectorError:
                        await asyncio.sleep(.02)
            value = config['config']
            value.update(enabled=True, setup_complete=True, purpose='Synthetic conditional retirement check')
            value['server']['public_url'] = f'https://127.0.0.1:{port}'
            value['backend'] = {'command': sys.executable, 'args': ['-m', 'amplifier_web',
                '--data-dir', str(tmp_path / 'relay'), '--workspace', str(tmp_path), 'acp',
                '--server', str(http.make_url('')).rstrip('/'), '--token-file', str(credential)],
                'env': {'PYTHONPATH': os.environ['PYTHONPATH'], 'PYTHONDONTWRITEBYTECODE': '1'},
                'secret_env': {}, 'working_directory': str(tmp_path), 'allowed_working_roots': [str(tmp_path)]}
            await request('PUT', '/config', {'expected_revision': config['revision'], 'config': value})
            grant = await request('POST', '/tokens', {'name': 'retirement-fixture', 'scopes': ['acp'],
                                                       'expires_in_seconds': 600})
            attach = tmp_path / 'instance-token'
            attach.write_text(grant['token'])
            attach.chmod(0o600)
            endpoint = {'id': 'instance', 'label': 'Published Instance', 'command': binary,
                'args': ['acp', '--home', str(home), '--token-file', str(attach)]}
            binding, hints = {}, asyncio.Event()
            async def save(value):
                binding.update(value)
            async def update(_):
                raise AssertionError('No prompt or public update was authorized')
            async def permission(_):
                raise AssertionError('No permission decision was authorized')
            first = ACPParticipant(endpoint, str(tmp_path), update=update, permission=permission)
            peers.append(first)
            await first.connect(binding, save)
            identity = binding['sessionId']
            assert first.supports_retirement
            assert first.initialization_metadata['io.github.bkrabach.agent-instance']['instanceId']
            owner = next(iter(app['service']._execution_observers.attachments))
            assert owner.attached == {identity} and not runtime.workers
            async def hinted(peer):
                assert peer is first and peer.session_id == identity
                hints.set()
            first.retirement_available = hinted
            await app['service'].on_runtime_event('runtime.retirement_requested', {'sessionId': identity})
            await asyncio.wait_for(hints.wait(), 5)
            assert await first.retire() == 'retired'
            assert identity not in runtime._ephemeral_mcp
            await first.close()
            second = ACPParticipant(endpoint, str(tmp_path), update=update, permission=permission)
            peers.append(second)
            await second.connect(binding, save)
            assert second.session_id == identity and len(app['service'].state['sessions']) == 1
            assert await second.retire() == 'retired'
            assert not runtime.workers and not runtime._ephemeral_mcp
            if os.environ.get('ACP_INSTANCE_EVIDENCE'):
                Path(os.environ['ACP_INSTANCE_EVIDENCE']).write_text(json.dumps({
                    'version': '0.22.4', 'binarySha256': hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
                    'sessionId': identity, 'newSessions': 1, 'resumes': 1, 'retiredReceipts': 2,
                    'outsidePromptAdvisory': True, 'prompts': 0, 'nativeWorkers': 0,
                    'privateCaVerified': True, 'port': port, 'instancePid': process.pid}, indent=2) + '\n')
    finally:
        await asyncio.gather(*(peer.close() for peer in peers), return_exceptions=True)
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                process.kill()
                await process.wait()
