"""Exact installed-backend smoke; run with empty PYTHONPATH outside the checkout."""
import asyncio
import hashlib
import importlib.metadata
import json
from pathlib import Path
import tempfile

import amplifier_web
from amplifier_web.service import AppService, ACTION_DEFINITIONS


async def main():
    installed = Path(amplifier_web.__file__).resolve().parent
    source = Path('/workspace/amplifier_web')
    assert not installed.is_relative_to(source.parent), installed
    hashes = {}
    for name in ('canvas_views.py', 'canvas_visibility.py', 'client_views.py', 'service.py', 'app_guidance.py', 'canvas_files.py'):
        actual = hashlib.sha256((installed / name).read_bytes()).hexdigest()
        assert actual == hashlib.sha256((source / name).read_bytes()).hexdigest(), name
        hashes[name] = actual
    assert ACTION_DEFINITIONS['canvas.show'][1]['properties']['presentation']['default'] == 'canvas'
    # This is an installed-distribution receipt, not a claim to retain the
    # original wheel archive. Verify RECORD hashes and every shipped static byte.
    distribution = importlib.metadata.distribution('amplifier-unified')
    installed_files = {}
    for item in distribution.files or []:
        if item.hash and item.hash.mode == 'sha256':
            import base64
            digest = hashlib.sha256(Path(distribution.locate_file(item)).read_bytes()).digest()
            assert base64.urlsafe_b64encode(digest).decode().rstrip('=') == item.hash.value, str(item)
            installed_files[str(item)] = digest.hex()
    def static_files(root):
        return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(root.rglob('*')) if path.is_file()}
    installed_static = static_files(installed / 'static')
    assert installed_static == static_files(source / 'static'), 'Installed static bytes differ from candidate'
    Path('/artifacts/installed-distribution.json').write_text(json.dumps({
        'version': distribution.version, 'files': installed_files,
        'installedStatic': installed_static, 'sourceHashes': hashes,
        'scope': 'RECORD-verified installed distribution; not original wheel archive'
    }, indent=2) + '\n')
    with tempfile.TemporaryDirectory(prefix='artifact-installed-', dir='/artifacts') as folder:
        root = Path(folder)
        service = AppService(root / 'app', workspace=root)
        try:
            await service.dispatch('session.create', {})
            service.clients.attach('probe')
            with service.clients.bind('probe'):
                sid = service.state['selectedSessionId']
                service._session(sid)['messages'].append(
                    {'id': 'probe-turn', 'role': 'user', 'text': 'Synthetic saved review'})
                first = await service.dispatch('canvas.show', {'kind': 'text', 'content': 'Primary'})
                second = await service.dispatch('canvas.show', {'kind': 'markdown', 'content': '# Inline',
                                                                 'presentation': 'inline'})
                assert service.state['canvas']['id'] == first['result']['id']
                artifact = second['result']['id']
                assert service.canvas_views.presentation(artifact, 1)['presentation'] == 'inline'
                await service.dispatch('canvas.select', {'id': artifact, 'version': 1})
                assert service.state['canvas']['id'] == artifact
                assert service.canvas_views.presentation(artifact, 1)['presentation'] == 'canvas'
        finally:
            await service.close()
    evidence = {'installedRoot': str(installed), 'sourceHashes': hashes,
                'result': 'pass', 'scope': 'installed backend; production frontend qualified separately'}
    Path('/artifacts/installed-probe.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print('PASS installed backend import, exact source hashes and presentation actions', installed)


if __name__ == '__main__':
    asyncio.run(main())