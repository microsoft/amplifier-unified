"""Read-only installation and service checks shared by CLI and reset."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from . import __version__


def doctor(home):
    from .deployment import load_server_config
    import yaml
    from .tls import ssl_context
    checks = []
    try:
        config = load_server_config(home)
        checks.append({"ok": True, "message": "Settings are valid"})
    except (OSError, ValueError, yaml.YAMLError) as error:
        return {"ok": False, "checks": [{"ok": False, "message": "Settings need attention. Check config/server.yaml.", "detail": str(error)}]}
    try:
        import pam
        if not callable(pam.authenticate):
            raise ImportError("Authentication support is unavailable")
        checks.append({"ok": True, "message": "Sign-in support is available"})
    except (ImportError, OSError, AttributeError) as error:
        checks.append({"ok": False, "message": "Sign-in support needs repair. Try amplifier-unified reset.", "detail": str(error)})
    try:
        ssl_context(home, config)
        checks.append({"ok": True, "message": "Secure connection is configured" if config['tls']['method'] != 'none' else "Local connection is configured"})
    except (OSError, ValueError) as error:
        checks.append({"ok": False, "message": "The connection certificate needs attention. Check your TLS settings.", "detail": str(error)})
    return {"ok": all(row['ok'] for row in checks), "version": __version__, "checks": checks}


def service_state(home):
    from . import deployment_service as service
    if sys.platform == 'darwin':
        if not service.managed_launchd(home):
            return 'not configured'
        import os
        result = subprocess.run(['launchctl', 'print', f'gui/{os.getuid()}/{service.LAUNCHD_LABEL}'], capture_output=True, text=True, timeout=10)
        if result.returncode:
            return 'stopped'
        return 'running' if any(line.strip() == 'state = running' for line in result.stdout.splitlines()) else 'starting'
    if not service.managed_unit(home):
        return 'not configured'
    from .reset import user_service_environment
    result = subprocess.run(['systemctl', '--user', 'show', service.UNIT_NAME, '--property=ActiveState', '--value'], capture_output=True, text=True, timeout=10, env=user_service_environment())
    if result.returncode:
        raise RuntimeError('Unable to contact the background service manager.')
    return {'active': 'running', 'activating': 'starting', 'inactive': 'stopped', 'failed': 'failed',
            'deactivating': 'stopping'}.get(result.stdout.strip(), 'unknown')


def matches_health(value, home, expected):
    from .auth import data_identity
    return (isinstance(value, dict) and value.get('ok') is True and value.get('app') == 'amplifier-unified'
            and value.get('dataIdentity') == data_identity(home)
            and value.get('version') == expected['version']
            and (not expected['revision'] or value.get('revision') == expected['revision']))


async def status(home, wait=0):
    import aiohttp
    from .deployment import load_server_config
    from .update_readiness import probe_targets, running_identity
    import yaml
    expected = running_identity()
    try:
        initial_state = await asyncio.to_thread(service_state, home)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        initial_state = 'unavailable'
    if initial_state == 'unavailable':
        from .reset import service_manager_help
        return {'ok': False, 'service': initial_state, 'message': "The background service manager could not be reached.",
                'nextStep': service_manager_help() if sys.platform == 'linux' else 'Sign in to your desktop account and retry service status.'}
    try:
        config = load_server_config(home)
        manager = SimpleNamespace(home=home, service=SimpleNamespace(server_config=config, port=config['port']))
        urls, pin = probe_targets(manager)
        token = (Path(home) / 'config/auth/control-token').read_text().strip()
    except (OSError, ValueError, yaml.YAMLError) as error:
        if initial_state == 'not configured':
            return {"ok": False, "service": initial_state, "message": "Unified's background service is not set up.",
                    "nextStep": "Run amplifier-unified service install to set it up."}
        return {"ok": False, "service": initial_state, "message": "Unified's connection settings need attention. Run amplifier-unified doctor.", "detail": str(error)}
    deadline = asyncio.get_running_loop().time() + wait
    state = 'unknown'
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2), trust_env=False) as client:
        while True:
            try:
                state = await asyncio.to_thread(service_state, home)
            except (OSError, RuntimeError, subprocess.SubprocessError):
                state = 'unavailable'
            for url in urls:
                try:
                    async with client.get(url, ssl=pin, allow_redirects=False,
                                          headers={'Authorization': 'Bearer ' + token, 'Host': f"localhost:{config['port']}"}) as response:
                        value = await response.json() if response.status == 200 else None
                        if matches_health(value, home, expected):
                            # A reachable manual host is useful information, but
                            # cannot stand in for a healthy managed service.
                            ok = state == 'running'
                            if not ok and asyncio.get_running_loop().time() < deadline:
                                continue
                            return {"ok": ok, "responding": True, "service": state,
                                    "message": "Unified is running and responding." if ok else "Unified responds, but its background service is " + state + ".",
                                    "url": (config['public_origins'] or [url.removesuffix('/api/health')])[0]}
                except (aiohttp.ClientError, OSError, TimeoutError, ValueError):
                    pass
            if asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(.5)
    return {"ok": False, "responding": False, "service": state,
            "message": f"Unified is not ready yet (background service: {state}).",
            "nextStep": "Run amplifier-unified service logs to see why startup has not completed."}


def display(result, *, as_json=False, verbose=False):
    if as_json:
        print(json.dumps(result))
        return
    if 'checks' in result:
        print('Amplifier Unified · Installation check')
        for row in result['checks']:
            print(('✓ ' if row['ok'] else '• ') + row['message'])
            if verbose and row.get('detail'):
                print('  ' + row['detail'])
    else:
        print(result['message'])
        if result.get('ok') and result.get('url'):
            print('Open: ' + result['url'])
        if result.get('nextStep'):
            print(result['nextStep'])
        if verbose and result.get('detail'):
            print(result['detail'])
