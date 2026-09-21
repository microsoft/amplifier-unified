"""Portable theme definitions compiled through the existing skin action path."""
from __future__ import annotations

import copy
import re

import tinycss2

TOKENS = ('bg', 'surface', 'soft', 'ink', 'muted', 'line', 'accent', 'tint', 'green', 'danger')
COLOR = {'type': 'string', 'pattern': '^#[0-9a-fA-F]{6}$'}
PALETTE = {'type': 'object', 'additionalProperties': False, 'required': list(TOKENS),
           'properties': {key: COLOR for key in TOKENS}}
DEFINITION = {'type': 'object', 'additionalProperties': False,
    'required': ['version', 'palette'], 'properties': {
        'version': {'const': 1},
        'palette': {'type': 'object', 'additionalProperties': False, 'required': ['light', 'dark'],
                    'properties': {'light': PALETTE, 'dark': PALETTE}},
        'background': {'type': 'object', 'additionalProperties': False, 'required': ['light', 'dark'],
            'properties': {'light': {'type': 'string', 'maxLength': 100000},
                           'dark': {'type': 'string', 'maxLength': 100000},
                           **{key: {'type': 'string', 'maxLength': 100} for key in ('size', 'position', 'repeat')}}},
    }}
PATCH_START = '/* amplifier:palette-patch:start */'
PATCH_END = '/* amplifier:palette-patch:end */'


def css_value(property, value):
    """Accept exactly one declaration value, never another rule or property."""
    from .service import AppError, validate_theme
    declarations = tinycss2.parse_declaration_list(property + ':' + value, skip_comments=True, skip_whitespace=True)
    if len(declarations) != 1 or declarations[0].type != 'declaration' or declarations[0].important:
        raise AppError('Use a CSS value, not additional background declarations.')
    if any(token.type in {'error', '{} block', 'at-keyword'} for token in declarations[0].value):
        raise AppError('Invalid theme background value.')
    # The complete parser rejects external URLs, imports, malformed syntax and
    # style termination. Only the existing embedded raster image formats work.
    validate_theme('#amp-one{' + property + ':' + value + '}')
    return value


def compile_definition(service, definition):
    from .canvas_apps import validate
    validate(definition, DEFINITION)
    rules = []
    background = definition.get('background', {'light': 'none', 'dark': 'none'})
    for mode in ('light', 'dark'):
        values = ';'.join('--a-' + key + ':' + definition['palette'][mode][key] for key in TOKENS)
        values += ';background-color:var(--a-bg);background-image:' + css_value('background-image', background[mode])
        for prop, fallback in [('size', 'cover'), ('position', 'center'), ('repeat', 'no-repeat')]:
            values += ';background-' + prop + ':' + css_value('background-' + prop, background.get(prop, fallback))
        rules.append('#amp-one[data-theme-scheme="' + mode + '"]{' + values + '}')
    # Always start from the current host's built-in skin, never the previously
    # selected theme. Omitted decoration intentionally means a flat background.
    return service.default_theme() + '\n' + '\n'.join(rules)


def theme_input(service, args):
    if 'definition' in args:
        # Approved surface requests carry their already compiled CSS so their
        # reviewed payload remains fixed. Public action schemas forbid supplying
        # definition and css together.
        return {**args, 'css': args.get('css') or compile_definition(service, args['definition'])}
    if 'tokens' not in args:
        return args
    current = service.state['theme']
    if current.get('definition'):
        definition = copy.deepcopy(current['definition'])
        for mode in ('light', 'dark'):
            definition['palette'][mode].update(args['tokens'])
        return {**args, 'definition': definition, 'css': compile_definition(service, definition)}
    css = re.sub(re.escape(PATCH_START) + '.*?' + re.escape(PATCH_END), '', current['css'], flags=re.S)
    # Preserve earlier partial edits, without growing the stylesheet each time.
    tokens = {**current.get('palettePatch', {}), **args['tokens']}
    declarations = ';'.join('--a-' + key + ':' + value for key, value in tokens.items())
    return {**args, 'palettePatch': tokens, 'css': css.rstrip() + '\n' + PATCH_START + '\n#amp-one{' + declarations + '}\n' + PATCH_END}
