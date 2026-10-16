"""Deterministic, credential-free provider for real-worker concurrency checks."""
import asyncio
import os
import json
from pathlib import Path
import time
import uuid

from amplifier_core import ModelInfo, ProviderInfo
from amplifier_core.message_models import ChatResponse, TextBlock

__amplifier_module_type__ = 'provider'


class ConcurrencyProvider:
    name = 'concurrency'

    def __init__(self, coordinator=None, config=None):
        self.coordinator = coordinator
        self.config = config or {}

    def get_info(self):
        return ProviderInfo(id=self.name, display_name='Local concurrency fixture',
                            capabilities=['streaming'], defaults={
                                'model': 'synthetic-stream', 'context_window': 200000,
                                'max_output_tokens': 4096})

    async def list_models(self):
        return [ModelInfo(id='synthetic-stream', display_name='Synthetic stream',
                          context_window=200000, max_output_tokens=4096,
                          capabilities=['streaming'])]

    def parse_tool_calls(self, response):
        return []

    async def complete(self, request, **kwargs):
        request_id = 'bench-' + uuid.uuid4().hex
        parts = []
        # Timestamps and DOM are measured on the same isolated machine. No
        # network provider or credentials participate in this qualification.
        latest_user = next((message for message in reversed(request.messages)
                            if message.role == 'user' and not (message.metadata or {}).get('ephemeral')), None)
        chunks = 4 if 'BENCH_FINISH_FAST' in str(getattr(latest_user, 'content', '')) else int(self.config.get('chunks', 480))
        with (Path(os.environ['AMPLIFIER_WEB_HOME'])/'benchmark-provider.jsonl').open('a') as stream:
            stream.write(json.dumps({'pid':os.getpid(),'requestId':request_id,'chunks':chunks,
                                     'source':__file__,'startedAt':time.time()})+'\n')
        for sequence in range(chunks):
            marker = f'[BENCH:{os.getpid()}:{sequence}:{time.time_ns() // 1000000}] '
            parts.append(marker)
            await self.coordinator.hooks.emit('llm:stream_block_delta', {
                'block_type': 'text', 'text': marker, 'request_id': request_id,
                'block_index': 0})
            await asyncio.sleep(float(self.config.get('interval', .25)))
        return ChatResponse(content=[TextBlock(text=''.join(parts))],
                            finish_reason='stop', usage={
                                'input_tokens': 1, 'output_tokens': len(parts), 'total_tokens': 1+len(parts)})


async def mount(coordinator, config=None):
    await coordinator.mount('providers', ConcurrencyProvider(coordinator, config or {}),
                            name='concurrency')
