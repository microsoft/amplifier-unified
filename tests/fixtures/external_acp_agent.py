"""Real upstream ACP SDK peer with synthetic durable sessions; no model/tools."""
import asyncio
import json
import os
from pathlib import Path
import sys
import uuid

from acp import run_agent
from acp.schema import (AgentCapabilities, AgentMessageChunk, InitializeResponse,
                        NewSessionResponse, PromptResponse, ResumeSessionResponse,
                        SessionCapabilities, SessionResumeCapabilities, TextContentBlock,
                        ToolCallUpdate, ToolCallProgress, PermissionOption)


class Fixture:
    def __init__(self, home, resume):
        self.home, self.resume = Path(home), resume
        self.home.mkdir(parents=True, exist_ok=True)
        self.cancelled = asyncio.Event()

    def on_connect(self, connection):
        self.client = connection

    def record(self, kind, **values):
        with (self.home / 'operations.jsonl').open('a') as stream:
            stream.write(json.dumps({'kind': kind, **values}) + '\n')

    async def initialize(self, protocol_version, client_capabilities=None, **kwargs):
        assert protocol_version == 1
        assert not client_capabilities.terminal
        assert not client_capabilities.fs.read_text_file and not client_capabilities.fs.write_text_file
        result = InitializeResponse(protocol_version=1, agent_capabilities=AgentCapabilities(
            session_capabilities=SessionCapabilities(
                resume=SessionResumeCapabilities() if self.resume else None)),
            field_meta={'com.microsoft.amplifier.approval-policy': {
                'version': 1, 'owner': 'instance', 'producer': 'fixture/v1', 'reusableRequests': True}})
        if (self.home / 'advertise-instance').exists():
            namespace = 'com.microsoft.amplifier.agent-instance'
            self.record('instance-offer', offered=kwargs.get(namespace))
            result.field_meta[namespace] = {'version': 1, 'agentId': 'public:upgraded-peer'}
        return result

    async def new_session(self, cwd, mcp_servers=None, **kwargs):
        identity = str(uuid.uuid4())
        (self.home / identity).write_text(cwd)
        self.record('new', sessionId=identity, cwd=cwd)
        return NewSessionResponse(session_id=identity)

    async def resume_session(self, session_id, cwd, mcp_servers=None, **kwargs):
        assert self.resume and (self.home / session_id).read_text() == cwd
        self.record('resume', sessionId=session_id)
        return ResumeSessionResponse()

    async def prompt(self, session_id, prompt, **kwargs):
        text = prompt[-1].text
        self.record('prompt', sessionId=session_id, text=text,
                    **({'context': [block.text for block in prompt[:-1]]} if len(prompt) > 1 else {}))
        self.cancelled.clear()
        if text == 'drop-after-effect':
            os._exit(7)
        if text.startswith('permission'):
            options = [PermissionOption(option_id='allow-this', name='Allow once', kind='allow_once'),
                       PermissionOption(option_id='deny-this', name='Deny', kind='reject_once')]
            if text != 'permission':
                options.insert(0, PermissionOption(option_id='remember-this', name='Always allow matching request', kind='allow_always'))
            if text == 'permission-always-only':
                options = options[:1]
            request = asyncio.create_task(self.client.request_permission(session_id=session_id,
                tool_call=ToolCallUpdate(tool_call_id='synthetic', title='Synthetic permission',
                    raw_input={'path': str(self.home / 'reviewed.txt')},
                    field_meta={'com.microsoft.amplifier.approval-policy': {
                        'version': 1, 'producer': 'fixture/v1', 'key': 'a' * 64,
                        'allowOptionId': 'allow-this', 'description': 'Write the same reviewed file.',
                        'authorityLifetime': 'invalid' if text == 'permission-invalid-lifetime' else 'request'}}), options=options))
            cancelled = asyncio.create_task(self.cancelled.wait())
            try:
                if text in {'permission-withdraw', 'permission-changed', 'permission-cancel-request'}:
                    while not (self.home / 'withdraw').exists() and not self.cancelled.is_set():
                        await asyncio.sleep(.01)
                    if text == 'permission-cancel-request':
                        # The official SDK emits $/cancel_request for this exact
                        # pending RPC. Keep the prompt open to prove withdrawal
                        # does not depend on a tool update or end of turn.
                        request.cancel()
                        await asyncio.gather(request, return_exceptions=True)
                        self.record('permission-cancelled')
                        while not (self.home / 'release').exists() and not self.cancelled.is_set():
                            await asyncio.sleep(.01)
                        return PromptResponse(stop_reason='end_turn')
                    progress = ({'raw_input': {'path': str(self.home / 'different.txt')}}
                                if text == 'permission-changed' else {'status': 'failed'})
                    await self.client.session_update(session_id=session_id,
                        update=ToolCallProgress(tool_call_id='synthetic', **progress))
                done, _ = await asyncio.wait([request, cancelled], return_when=asyncio.FIRST_COMPLETED)
                if request not in done:
                    if text == 'permission-stop-leaves-request':
                        # A peer may settle its prompt without explicitly
                        # cancelling an earlier permission RPC. Keep it alive
                        # until the host retires the now-terminal request.
                        self.left_open_permission = request
                        def retired(task):
                            error = None if task.cancelled() else task.exception()
                            self.record('permission-left-open-retired',
                                cancelled=task.cancelled(), error=type(error).__name__ if error else None)
                        request.add_done_callback(retired)
                        return PromptResponse(stop_reason='cancelled')
                    request.cancel()
                    await asyncio.gather(request, return_exceptions=True)
                    return PromptResponse(stop_reason='cancelled')
                result = request.result()
            finally:
                cancelled.cancel()
                await asyncio.gather(cancelled, return_exceptions=True)
            self.record('permission', outcome=result.outcome.model_dump(mode='json', by_alias=True, exclude_none=True))
        await self.client.session_update(session_id=session_id,
            update=AgentMessageChunk(content=TextContentBlock(text='Reply: ')))
        if text == 'wait-for-stop':
            await self.cancelled.wait()
            return PromptResponse(stop_reason='cancelled')
        await self.client.session_update(session_id=session_id,
            update=AgentMessageChunk(content=TextContentBlock(text=text)))
        return PromptResponse(stop_reason='end_turn')

    async def cancel(self, session_id, **kwargs):
        self.record('cancel', sessionId=session_id)
        self.cancelled.set()


if __name__ == '__main__':
    asyncio.run(run_agent(Fixture(sys.argv[1], '--no-resume' not in sys.argv), use_unstable_protocol=True))
