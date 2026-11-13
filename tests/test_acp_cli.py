"""Real CLI stdio subprocesses attach to one persistent AppService owner."""
import asyncio
import json
import sys

from test_acp_server import host, finish  # noqa: F401: shared real-host fixture
from test_coordination_generation import source_plan, until


class StdioPeer:
    def __init__(self, process):
        self.process = process
        self.serial = 0
        self.updates = []

    async def write(self, message):
        self.process.stdin.write((json.dumps(message) + '\n').encode())
        await self.process.stdin.drain()

    async def send(self, method, **params):
        self.serial += 1
        await self.write({'jsonrpc': '2.0', 'id': self.serial, 'method': method, 'params': params})
        return self.serial

    async def result(self, identity):
        async with asyncio.timeout(8):
            while True:
                line = await self.process.stdout.readline()
                assert line, (await self.process.stderr.read()).decode()
                row = json.loads(line)
                if row.get('id') == identity:
                    return row
                self.updates.append(row)

    async def call(self, method, **params):
        return await self.result(await self.send(method, **params))

    async def eof(self):
        self.process.stdin.close()
        await asyncio.wait_for(self.process.wait(), 5)
        assert self.process.returncode == 0, (await self.process.stderr.read()).decode()


async def test_two_stdio_launchers_share_host_and_resume_after_eof_without_replay(host, tmp_path):
    app, client, runtime, workspace = host
    service = app['service']
    credential = tmp_path / 'owner-token'
    credential.write_text(app['control_token'])
    credential.chmod(0o600)
    processes = []

    async def launch():
        process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'amplifier_web',
            '--data-dir', str(tmp_path / 'relay-does-not-own-state'), '--workspace', str(workspace),
            'acp', '--server', str(client.make_url('')).rstrip('/'), '--token-file', str(credential),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        processes.append(process)
        peer = StdioPeer(process)
        assert (await peer.call('initialize', protocolVersion=1))['result']['protocolVersion'] == 1
        return peer

    try:
        first, second = await launch(), await launch()
        a = (await first.call('session/new', cwd=str(workspace), mcpServers=[]))['result']['sessionId']
        b = (await second.call('session/new', cwd=str(workspace), mcpServers=[]))['result']['sessionId']
        for sid in (a, b):
            source_plan(service, service._session(sid))
        runtime.send_ack.clear()
        await first.send('session/prompt', sessionId=a, prompt=[{'type': 'text', 'text': 'Retain first'}])
        second_prompt = await second.send('session/prompt', sessionId=b, prompt=[{'type': 'text', 'text': 'Stop second'}])
        await until(lambda: len(runtime.sent) == 2)
        inputs = {sid: identity for sid, _, identity in runtime.sent}
        await first.eof()
        await until(lambda: len(app['acp_connections']) == 1)
        assert runtime.closed == 0 and runtime.cancelled_sends == [] and runtime.stopped == []
        runtime.send_ack.set()
        await second.write({'jsonrpc': '2.0', 'method': 'session/cancel', 'params': {'sessionId': b}})
        assert (await second.result(second_prompt))['result']['stopReason'] == 'cancelled'
        assert runtime.stopped == [b]
        await finish(service, a, inputs[a], 'Retained first result')
        third = await launch()
        assert (await third.call('session/resume', sessionId=a, cwd=str(workspace), mcpServers=[]))['result'] == {}
        assert len(runtime.sent) == 2 and third.updates == []
        assert {row['id'] for row in service.state['sessions']} == {a, b}
        follow = await third.send('session/prompt', sessionId=a,
            prompt=[{'type': 'text', 'text': 'Explicit next input'}])
        await until(lambda: len(runtime.sent) == 3)
        await finish(service, a, runtime.sent[-1][2], 'Final result')
        assert (await third.result(follow))['result']['stopReason'] == 'end_turn'
        assert any(row['params']['update'].get('content', {}).get('text') == 'Final result' for row in third.updates)
        await second.eof()
        await third.eof()
        await until(lambda: not app['acp_connections'])
        assert runtime.closed == 0 and runtime.cancelled_sends == []
        assert not (tmp_path / 'relay-does-not-own-state').exists()
    finally:
        runtime.send_ack.set()
        for process in processes:
            if process.returncode is None:
                process.kill()
        await asyncio.gather(*(process.wait() for process in processes))
