"""Attach standard ACP stdio to an existing Unified host; never start a writer."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import ssl
from types import SimpleNamespace
from urllib.parse import urlsplit

import aiohttp

from .deployment import is_loopback, load_server_config, validate_origin
from .runtime_protocol import MAX_MESSAGE_BYTES


def connection_options(args, data_dir):
    """Use an explicit remote connection or this installation's private settings."""
    server, token_file, ca_file = args.server, args.token_file, args.ca_file
    pin, host = None, None
    if not server:
        config = load_server_config(data_dir)
        from .browser_entry import is_windows, _owner_probe_targets
        from .update_readiness import probe_targets
        if is_windows():
            urls, pin = _owner_probe_targets(data_dir, config)
        else:
            manager = SimpleNamespace(home=data_dir, service=SimpleNamespace(
                server_config=config, port=config['port']))
            urls, pin = probe_targets(manager)
        if not urls:
            raise ValueError('No configured local listener is available. Specify --server explicitly.')
        server = urls[0].removesuffix('/api/health')
        host = f"localhost:{config['port']}"
        token_file = token_file or str(data_dir / 'config/auth/control-token')
    server = validate_origin(server)
    if urlsplit(server).scheme != 'https' and not is_loopback(urlsplit(server).hostname):
        raise ValueError('Use verified HTTPS for a remote Unified host.')
    if not token_file:
        raise ValueError('Specify --token-file for the existing Unified owner credential.')
    from .browser_entry import is_windows, current_owner_sid, read_owner_control_token
    if is_windows() and Path(token_file) == data_dir / 'config/auth/control-token':
        token = read_owner_control_token(data_dir, current_owner_sid())
    else:
        token = Path(token_file).expanduser().read_text().strip()
    if not token:
        raise ValueError('The existing owner credential is empty.')
    context = pin if pin is not None else ssl.create_default_context(
        cafile=str(Path(ca_file).expanduser()) if ca_file else None)
    return server, token, context, host


async def relay(ws, reader, writer):
    """Forward each frame once. EOF detaches; it does not send cancellation."""
    async def outgoing():
        while line := await reader.readline():
            if len(line) > MAX_MESSAGE_BYTES:
                raise ValueError('ACP input exceeds the host message limit.')
            text = line.decode('utf-8')
            if not isinstance(json.loads(text), dict):
                raise ValueError('ACP input must be a JSON-RPC object.')
            await ws.send_str(text.rstrip('\r\n'))

    async def incoming():
        async for message in ws:
            if message.type != aiohttp.WSMsgType.TEXT:
                raise ValueError('The Unified ACP connection ended unexpectedly; inspect retained work before retrying.')
            writer.write((message.data + '\n').encode('utf-8'))
            await writer.drain()
        raise ConnectionError('Unified disconnected; work may continue. No input was replayed.')

    sending, receiving = asyncio.create_task(outgoing()), asyncio.create_task(incoming())
    try:
        done, _ = await asyncio.wait({sending, receiving}, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        sending.cancel()
        receiving.cancel()
        await asyncio.gather(sending, receiving, return_exceptions=True)
        await ws.close()


async def run(args, data_dir):
    from acp.stdio import stdio_streams

    server, token, context, host = connection_options(args, data_dir)
    params = {'workspace': args.workspace}
    if args.bundle:
        params['bundle'] = args.bundle
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=15)
    async def refuse_redirect(*_):
        raise ValueError('The selected host redirected the ACP connection. No input was sent.')
    trace = aiohttp.TraceConfig()
    trace.on_request_redirect.append(refuse_redirect)
    try:
        async with aiohttp.ClientSession(timeout=timeout, trace_configs=[trace]) as http:
            async with http.ws_connect(server + '/api/acp', params=params, ssl=context,
                    headers={'Authorization': 'Bearer ' + token, **({'Host': host} if host else {})},
                    max_msg_size=MAX_MESSAGE_BYTES) as ws:
                reader, writer = await stdio_streams(limit=MAX_MESSAGE_BYTES)
                await relay(ws, reader, writer)
    except aiohttp.ClientError:
        raise ValueError('Cannot attach to the selected Unified host. Check its address, trust and owner credential; no automatic retry or host start was attempted.') from None
