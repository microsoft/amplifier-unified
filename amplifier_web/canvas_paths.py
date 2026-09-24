"""Copy saved file locations on the app host, without opening or reading files."""
from pathlib import Path

from .shell_modules import fail


def paths(state, canvas):
    path = Path(canvas.get('path') or '')
    if not path.is_absolute():
        return {}
    result = {'absolute': str(path)}
    root = canvas.get('workspacePath')
    if not root:
        owner = next((row for row in state.get('sessions', []) if row['id'] == canvas.get('sessionId')), {})
        root = owner.get('workspace') or next((row['path'] for row in state.get('workspaces', []) if row['id'] == canvas.get('workspaceId')), None)
    if root and Path(root).is_absolute():
        try:
            result['relative'] = str(path.relative_to(root))
        except ValueError:
            pass
    return result


def copy_path(state, args):
    canvas = state.get('canvas', {})
    if canvas.get('id') != args['id']:
        fail('This canvas has been replaced.', 409)
    value = paths(state, canvas).get(args['format'])
    if value is None:
        fail('This artifact has no saved ' + args['format'] + ' file path.')
    return {'path': value, 'host': 'server'}, [{'type': 'clipboard.write', 'content': value, 'canvasId': canvas['id']}]
