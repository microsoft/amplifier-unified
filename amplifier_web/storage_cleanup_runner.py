"""Additional read-only proof for automatic runner-code reclamation."""
import asyncio
from pathlib import Path


async def inspect(app, item, records):
    from . import managed_runner_updates as runner
    from .staged_sources import owned_git_checkout
    from .update_retention import _probe, inspect_references
    from .updates import process
    folder = Path(app.data_dir) / 'smart-tools' / 'installs' / item['id']
    source = folder / 'source'
    if any(path.name not in {'source', 'venv'} for path in folder.iterdir()):
        raise ValueError('Additional files in the managed installation are retained.')
    await owned_git_checkout(source, folder)
    if await process('git', 'rev-parse', 'HEAD', cwd=source, timeout=5) != item.get('commit'):
        raise ValueError('The installed source revision changed.')
    if (await process('git', '--no-optional-locks', 'status', '--porcelain', '--untracked-files=no', cwd=source, timeout=5)
            or await process('git', 'ls-files', '--others', cwd=source, timeout=5)):
        raise ValueError('Local or untracked source work is retained.')
    if await process('git', 'rev-list', '--max-count=1', '--all', '--not', 'HEAD', '--remotes', cwd=source, timeout=5):
        raise ValueError('Additional local commits or saved Git work are retained.')
    exact = [row['runtime'] for row in records
             if (row.get('candidate') or {}).get('installationId') == item['id'] and row.get('runtime')]
    if not exact or any(value != exact[0] for value in exact):
        raise ValueError('The original runner runtime proof is unavailable.')
    await _probe('runner', {'installation': item, 'runtime': exact[0]}, timeout=5)
    references = await inspect_references(app.update_manager, timeout=2)
    if str(folder) in references or runner.referenced(app.data_dir, item['id']):
        raise ValueError('This runner remains in use.')
    for server in app.smart_tools.state.get('servers', []):
        if server.get('installationId') == item['id'] or any(str(folder) in str(server.get(key, '')) for key in ('command', 'cwd', 'args')):
            raise ValueError('A registered connection still uses this runner.')
    measured = await _probe('measure', {'paths': [str(folder)]}, timeout=5)
    return {'kind': 'runner', 'id': item['id'], 'label': 'Unused test runner version',
            'paths': [str(folder)], **measured}
