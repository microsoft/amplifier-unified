"""Portable CSS appearances, with bundled examples and a persistent user library."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
import re

LIMIT = 1_000_000
MARKER = '/* amplifier-appearance: '
TOKENS = ('bg', 'surface', 'soft', 'ink', 'muted', 'line', 'accent', 'tint', 'green', 'danger')
PRESETS = {
    'aurora': {
        'name': 'Aurora', 'description': 'Luminous color, rounded panels and a flowing aurora background.',
        'light': ['#e8eef7','#ffffff','#f2f6fc','#172945','#526888','#cbd8e8','#5144c8','#e9e5ff','#176951','#ad3454'],
        'dark': ['#11172a','#1c253a','#25314a','#eff4ff','#acbbd4','#3b4b67','#b8adff','#39315c','#7fdbc0','#ffadc0'],
        'background': ['radial-gradient(ellipse at 5% 85%, #a5e5da 0%, transparent 55%), radial-gradient(ellipse at 90% 8%, #c9b2f3 0%, transparent 60%), linear-gradient(130deg, #e8f1fc, #dbe2f7)', 'radial-gradient(ellipse at 5% 85%, #153f4e 0%, transparent 55%), radial-gradient(ellipse at 90% 8%, #463168 0%, transparent 60%), linear-gradient(130deg, #11172a, #182237)'],
        'radius': '22px', 'font': 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
        'heading': 'inherit', 'tag': 'Atmospheric',
    },
    'atelier': {
        'name': 'Atelier', 'description': 'Warm paper, editorial headings and quiet terracotta accents.',
        'light': ['#eee8dc','#fffcf5','#f5efe4','#302b25','#716557','#d9cebc','#954c35','#f4e1d5','#316653','#ac3d3a'],
        'dark': ['#24211d','#302b25','#3a332c','#f5ecdf','#c6b7a4','#51473b','#efaf8e','#523b2d','#94c4a9','#f3a09c'],
        'background': ['none', 'none'], 'radius': '9px',
        'font': '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
        'heading': 'Georgia, "Times New Roman", serif', 'tag': 'Editorial',
    },
    'graphite': {
        'name': 'Graphite', 'description': 'Precise edges, technical typography and a fine architectural grid.',
        'light': ['#e9ecee','#fbfcfc','#f0f3f4','#202b31','#596a74','#ccd5da','#176179','#deedf2','#286446','#a23845'],
        'dark': ['#14191c','#1e252a','#283238','#e4edf1','#a4b7c1','#40515a','#89d3e8','#223e48','#8bd3a3','#ffa8b2'],
        'background': ['linear-gradient(#788e9912 1px, transparent 1px), linear-gradient(90deg, #788e9912 1px, transparent 1px)', 'linear-gradient(#a4b7c110 1px, transparent 1px), linear-gradient(90deg, #a4b7c110 1px, transparent 1px)'],
        'radius': '5px', 'font': '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
        'heading': '"SFMono-Regular", Consolas, "Liberation Mono", monospace', 'tag': 'Precise',
    },
}


def definitions(schema, string):
    return {
        'theme.list': ('List bundled and saved appearances without applying them.', schema()),
        'theme.read': ('Read a saved appearance for preview, editing or application.', schema({'id': string(240)}, ['id'])),
        'theme.save': ('Validate and save a portable CSS appearance in the shared library without applying it.',
                       schema({'name': string(100), 'css': string(LIMIT), 'description': string(240)}, ['name', 'css'])),
    }


def preset(service, identity):
    if identity == 'default':
        return {'id': 'builtin:default', 'name': 'Amplifier', 'description': 'The original blue and lilac appearance.',
                'css': service.default_theme(), 'source': 'built-in', 'tag': 'Original',
                'palette': {'bg': '#edf1ff', 'surface': '#ffffff', 'ink': '#202e50', 'accent': '#5842df', 'line': '#e4e8f5'},
                'background': 'linear-gradient(135deg, #edf1ff, #dbd2ff)', 'radius': '16px', 'heading': 'inherit'}
    item = PRESETS[identity]
    rules = []
    for index, mode in enumerate(('light', 'dark')):
        palette = dict(zip(TOKENS, item[mode]))
        declarations = ';'.join('--a-' + key + ':' + value for key, value in palette.items())
        rules.append(f'#amp-one[data-theme-scheme="{mode}"]{{{declarations};background-color:var(--a-bg);background-image:{item["background"][index]};background-size:{"28px 28px" if identity == "graphite" else "cover"};font-family:{item["font"]}}}')
    rules.append(f'#amp-one .a-conversation,#amp-one .a-nav-rail,#amp-one .a-canvas-panel,#amp-one .a-dialog{{border-radius:{item["radius"]}}}')
    rules.append(f'#amp-one h1,#amp-one h2,#amp-one h3,#amp-one .a-brand{{font-family:{item["heading"]};letter-spacing:{"-.03em" if identity == "atelier" else "-.015em"}}}')
    if identity == 'aurora':
        rules.append('#amp-one .a-conversation,#amp-one .a-nav-rail,#amp-one .a-canvas-panel{box-shadow:0 12px 45px #16244712;border:1px solid color-mix(in srgb,var(--a-line),transparent 15%)}')
    if identity == 'graphite':
        rules.append('#amp-one .a-composer,#amp-one .a-bubble,#amp-one .a-soft,#amp-one .a-primary{border-radius:6px}#amp-one .a-brand{font-size:20px}')
    return {'id': 'builtin:' + identity, 'name': item['name'], 'description': item['description'],
            'source': 'built-in', 'css': service.default_theme() + '\n' + '\n'.join(rules),
            'palette': dict(zip(TOKENS, item['light'])), 'background': item['background'][0],
            'radius': item['radius'], 'heading': item['heading'], 'tag': item['tag']}


def folder(service):
    return service.data_dir / 'themes'


def read_file(path):
    from .service import AppError, validate_theme
    if path.is_symlink() or not path.is_file() or path.stat().st_size > LIMIT:
        raise AppError('This appearance is unavailable or exceeds 1 MB.')
    with path.open(encoding='utf-8') as stream:
        css = stream.read(LIMIT + 1)
    if len(css.encode('utf-8')) > LIMIT:
        raise AppError('Choose an appearance smaller than 1 MB.')
    validate_theme(css)
    meta = {}
    if css.startswith(MARKER):
        try:
            meta = json.loads(css[len(MARKER):css.index(' */\n')])
        except (ValueError, TypeError):
            pass
    name = meta.get('name') if isinstance(meta, dict) else None
    description = meta.get('description') if isinstance(meta, dict) else None
    return {'id': 'saved:' + path.name, 'name': str(name or path.stem.removesuffix('.amplifier'))[:100],
            'description': str(description or 'A saved appearance from your library.')[:240],
            'css': css, 'source': 'saved', 'tag': 'Your library'}


def read(service, identity):
    from .service import AppError
    if identity.startswith('builtin:') and identity[8:] in ('default', *PRESETS):
        return preset(service, identity[8:])
    name = identity.removeprefix('saved:')
    if not identity.startswith('saved:') or Path(name).name != name or not name.endswith('.css'):
        raise AppError('Appearance not found.', 404)
    try:
        return read_file(folder(service) / name)
    except (OSError, UnicodeError) as exc:
        raise AppError('Appearance not found.', 404) from exc


def listing(service):
    from .service import AppError
    rows = [preset(service, key) for key in ('default', *PRESETS)]
    errors = []
    root = folder(service)
    for path in sorted(root.glob('*.css'), key=lambda path: path.name.casefold()):
        try:
            rows.append(read_file(path))
        except (OSError, UnicodeError, AppError) as exc:
            errors.append({'name': path.name, 'message': str(exc)})
    for row in rows:
        row['fingerprint'] = hashlib.sha256(row.pop('css').encode()).hexdigest()
    return {'items': rows, 'errors': errors, 'directory': str(root)}


def save(service, args):
    from .service import AppError, validate_theme
    css = args['css']
    validate_theme(css)
    name = args['name'].strip()
    if not name:
        raise AppError('Give this appearance a name.')
    meta = {'name': name, 'description': args.get('description', '')}
    # JSON stays inside a CSS comment, including unusual imported names.
    header = json.dumps(meta, ensure_ascii=True).replace('*/', '*\\/')
    content = MARKER + header + ' */\n' + css
    if len(content.encode()) > LIMIT:
        raise AppError('Choose an appearance smaller than 1 MB.')
    digest = hashlib.sha256(content.encode()).hexdigest()[:16]
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:60] or 'appearance'
    root = folder(service)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = root / f'{slug}-{digest}.amplifier.css'
    # Publish only complete files. Polling readers never observe a partial import;
    # exclusive linking preserves earlier imports and makes retries harmless.
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=root, suffix='.tmp', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.is_symlink() or path.read_text() != content:
                    raise AppError('A different appearance already uses this file name.') from None
        finally:
            temporary.unlink(missing_ok=True)
    return {'id': 'saved:' + path.name, 'name': name, 'directory': str(root)}
