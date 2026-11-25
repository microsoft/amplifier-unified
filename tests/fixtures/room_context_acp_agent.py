"""Public SDK peer exercising actual HTTP MCP; synthetic instructions, no model."""
import asyncio
import json
import ssl
import sys

import httpx2
from acp import RequestError, run_agent
from acp.schema import AgentMessageChunk, McpCapabilities, PromptResponse, TextContentBlock
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from external_acp_agent import Fixture


class Reader(Fixture):
    async def initialize(self, **kwargs):
        result = await super().initialize(**kwargs)
        result.agent_capabilities.mcp_capabilities = McpCapabilities(http=True)
        return result

    def attach(self, servers):
        assert len(servers) == 1 and servers[0].type == 'http'
        self.server = servers[0]

    async def new_session(self, mcp_servers, **kwargs):
        self.attach(mcp_servers)
        return await super().new_session(**kwargs)

    async def resume_session(self, mcp_servers, **kwargs):
        if (self.home / 'reject-mount').exists():
            raise RequestError(-32603, 'Fixture MCP attachment refused')
        self.attach(mcp_servers)
        return await super().resume_session(**kwargs)

    async def prompt(self, session_id, prompt, **kwargs):
        self.record('prompt', sessionId=session_id, text=prompt[-1].text)
        headers = {row.name: row.value for row in self.server.headers}
        try:
            async with httpx2.AsyncClient(headers=headers, verify=ssl.create_default_context(cafile=sys.argv[2])) as http:
                async with streamable_http_client(self.server.url, http_client=http) as streams:
                    async with ClientSession(*streams) as client:
                        await client.initialize()
                        before, pages = None, []
                        while True:
                            result = (await client.call_tool('conversation_read', {'limit': 20, 'before': before})).model_dump(by_alias=True)
                            assert not result.get('isError')
                            page = result['structuredContent']
                            pages.append(page)
                            before = page['before']
                            if before is None:
                                break
                        self.record('read', sessionId=session_id, pages=pages)
                        text = pages[-1]['items'][0]['text']
        except Exception as error:
            def failures(exc):
                if isinstance(exc, BaseExceptionGroup):
                    return [leaf for child in exc.exceptions for leaf in failures(child)]
                # Fixture-only diagnostics: never retain the bearer header.
                return [{'kind': type(exc).__name__, 'message': str(exc).replace(headers['Authorization'], '[redacted]')}]
            self.record('read-refused', sessionId=session_id, errors=failures(error))
            text = 'Source unavailable'
        await self.client.session_update(session_id=session_id,
            update=AgentMessageChunk(content=TextContentBlock(text=text)))
        return PromptResponse(stop_reason='end_turn')


if __name__ == '__main__':
    asyncio.run(run_agent(Reader(sys.argv[1], True), use_unstable_protocol=True))
