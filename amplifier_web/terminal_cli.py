"""Install or launch a saved terminal connection without changing Unified's env."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import platform
import re
import socket
import ssl
import subprocess
import tempfile
import uuid
import webbrowser

import aiohttp

from .deployment import is_loopback, load_server_config, validate_origin


def root():
    return Path(os.environ.get('AMPLIFIER_TERMINAL_HOME', Path.home() / '.local/share/amplifier-terminal')).expanduser().resolve()


def saved():
    directory = root()
    if not (directory / 'default.json').is_file():
        return None
    try:
        identity = json.loads((directory / 'default.json').read_text())['id']
        if not re.fullmatch('[a-f0-9]{32}', identity):
            raise ValueError()
        connection = directory / 'connections' / identity
        record = json.loads((connection / 'connection.json').read_text())
        environment = Path(record['environment']).resolve()
        if not environment.is_relative_to(directory / 'versions') or not (environment / '.activated').is_file():
            raise ValueError()
        if record['tokenFile'] != str(connection / 'token') or record.get('caFile') not in {None, str(connection / 'ca.crt')}:
            raise ValueError()
        validate_origin(record['server'])
        return record
    except (KeyError, ValueError, OSError):
        raise ValueError('The saved terminal installation is incomplete. Install it again from the service setup page.') from None


def local_options(args, data_dir):
    server = args.server
    token = args.token_file
    ca = args.ca_file
    if not server:
        connection = saved()
        if connection:
            return connection['server'], token or connection['tokenFile'], ca or connection.get('caFile')
        config = load_server_config(data_dir)
        secure = config['tls']['method'] != 'none'
        server = f"{'https' if secure else 'http'}://127.0.0.1:{config['port']}"
        local_token = data_dir / 'config/auth/control-token'
        token = token or (str(local_token) if local_token.is_file() else None)
        if config['tls']['method'] == 'ca' and not ca and (data_dir / 'config/tls/ca.crt').is_file():
            ca = str(data_dir / 'config/tls/ca.crt')
    return server, token, ca


def install(args, data_dir):
    if args.setup_file:
        if args.server or args.token_file or args.ca_file:
            raise ValueError('A downloaded setup file already identifies its service. Do not combine it with server or credential options.')
        path = Path(args.setup_file).expanduser().resolve()
        if path.stat().st_size > 8_000_000 or not path.read_bytes().startswith(b'#!/bin/bash\n# Amplifier Terminal Setup v1'):
            raise ValueError('Choose a Terminal setup file downloaded from your Unified service.')
        raise SystemExit(subprocess.call(['/bin/bash', str(path)]))
    server, token_file, ca_file = local_options(args, data_dir)
    server = validate_origin(server)
    from urllib.parse import urlsplit
    if urlsplit(server).scheme != 'https' and not is_loopback(urlsplit(server).hostname):
        raise ValueError('Use verified HTTPS for a remote service.')
    token = Path(token_file).expanduser().read_text().strip() if token_file else os.environ.get('AMPLIFIER_UNIFIED_TOKEN')
    if not token:
        url = server + '/setup/terminal'
        webbrowser.open(url)
        print('Finish terminal installation in your browser: ' + url)
        return
    if platform.machine().lower() not in {'aarch64', 'arm64'} or platform.system() not in {'Darwin', 'Linux'}:
        raise ValueError('This release supports Apple silicon Macs and ARM64 Linux. See the setup page for platform requirements.')
    target = 'macos-arm64' if platform.system() == 'Darwin' else 'linux-arm64'
    context = ssl.create_default_context(cafile=str(Path(ca_file).expanduser()) if ca_file else None)

    async def download():
        headers = {'Authorization': 'Bearer ' + token}
        timeout = aiohttp.ClientTimeout(total=240)
        async with aiohttp.ClientSession(headers=headers, connector=aiohttp.TCPConnector(ssl=context), timeout=timeout) as client:
            async with client.post(server + '/api/actions', json={'action': 'terminal.prepare', 'id': uuid.uuid4().hex,
                'args': {'server': server, 'platform': target, 'name': socket.gethostname()[:80]}}, allow_redirects=False) as response:
                if response.status != 200:
                    raise ValueError('Could not prepare setup. Open ' + server + '/setup/terminal to sign in and retry.')
                result = await response.json()
            url = result['installer']['downloadUrl']
            if not re.fullmatch('/api/terminal/installers/[a-f0-9]{32}', url):
                raise ValueError('The service returned an invalid installer location.')
            async with client.get(server + url, allow_redirects=False) as response:
                if response.status != 200:
                    raise ValueError('Setup download failed. Retry from the service setup page.')
                chunks, size = [], 0
                async for chunk in response.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > 8_000_000:
                        raise ValueError('Setup download is too large.')
                    chunks.append(chunk)
                body = b''.join(chunks)
                if not body.startswith(b'#!/bin/bash\n# Amplifier Terminal Setup v1') or not body.endswith(b'\n'):
                    raise ValueError('Invalid setup download.')
                return body
    print('Preparing a terminal installation on this computer…', flush=True)
    body = asyncio.run(download())
    with tempfile.TemporaryDirectory(prefix='amplifier-terminal-') as temporary:
        path = Path(temporary) / 'setup.sh'
        path.write_bytes(body)
        path.chmod(0o600)
        raise SystemExit(subprocess.call(['/bin/bash', str(path)]))
