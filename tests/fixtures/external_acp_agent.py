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
                        ToolCallUpdate, PermissionOption)


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
        return InitializeResponse(protocol_version=1, agent_capabilities=AgentCapabilities(
            session_capabilities=SessionCapabilities(
                resume=SessionResumeCapabilities() if self.resume else None)))

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
        text = ''.join(block.text for block in prompt)
        self.record('prompt', sessionId=session_id, text=text)
        self.cancelled.clear()
        if text == 'drop-after-effect':
            os._exit(7)
        if text == 'permission':
            result = await self.client.request_permission(session_id=session_id,
                tool_call=ToolCallUpdate(tool_call_id='synthetic', title='Synthetic permission'),
                options=[PermissionOption(option_id='allow-this', name='Allow', kind='allow_once')])
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
