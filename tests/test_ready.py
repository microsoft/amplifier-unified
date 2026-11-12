"""Breadbin Reference delta: source paint/contrast, not browser visual acceptance."""
from copy import deepcopy
from importlib.resources import files
import base64
import re

import pytest
import tinycss2

from amplifier_web import theme_library, appearance_packages
from amplifier_web.service import AppError, validate_theme
from test_appearance_preferences import app, call


TOKENS = ('bg', 'surface', 'soft', 'ink', 'muted', 'line', 'accent', 'on-accent', 'tint', 'green', 'danger')


def source(name='ready'):
    return files('amplifier_web').joinpath('appearances', name + '.amplifier.css').read_text(encoding='utf-8')


def rules(name='ready'):
    def walk(nodes, context=''):
        for node in nodes:
            assert node.type != 'error'
            if node.type == 'at-rule':
                assert node.lower_at_keyword == 'media' and node.content is not None
                yield from walk(tinycss2.parse_rule_list(node.content, skip_comments=True, skip_whitespace=True),
                                tinycss2.serialize(node.prelude).strip())
            else:
                assert node.type == 'qualified-rule'
                values = tinycss2.parse_declaration_list(node.content, skip_comments=True, skip_whitespace=True)
                assert all(v.type == 'declaration' for v in values)
                assert not any(v.important for v in values)
                yield context, re.sub(r'\s+', ' ', tinycss2.serialize(node.prelude).strip()), {
                    v.name: tinycss2.serialize(v.value).strip() for v in values}
    return list(walk(tinycss2.parse_stylesheet(source(name), skip_comments=True, skip_whitespace=True)))


def declarations(selector, name='ready'):
    found = [v for _, s, v in rules(name) if s == selector]
    assert len(found) == 1, selector
    return found[0]


def luminance(color):
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    channels = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels]
    return sum(v * w for v, w in zip(channels, (.2126, .7152, .0722)))


def contrast(a, b):
    low, high = sorted((luminance(a), luminance(b)))
    return (high + .05) / (low + .05)


def assert_delta_contract(name):
    css = source(name)
    validate_theme(css)
    assert len(css.encode()) < 100_000
    assert not re.search(r'https?://|file:|@import|@font-face|url\s*\(|</?script', css, re.I)
    forbidden = {'position', 'z-index', 'display', 'height', 'min-height', 'max-height',
                 'width', 'min-width', 'max-width', 'overflow', 'overflow-x', 'overflow-y',
                 'backdrop-filter', 'transform', 'pointer-events', 'content'}
    for _, selector, values in rules(name):
        assert selector.startswith('#amp-one')
        assert '::before' not in selector and '::after' not in selector
        assert not any(k.startswith(('animation', 'transition', 'mask', '-webkit-mask')) for k in values)
        assert forbidden.isdisjoint(values)
    root = declarations('#amp-one', name)
    for token in TOKENS:
        assert re.fullmatch(r'light-dark\(#[0-9a-f]{6}, #[0-9a-f]{6}\)', root['--a-' + token]), token
    assert 'sans-serif' in root['--a-font-ui'] and 'monospace' in root['--a-font-chat']
    assert 'monospace' in root['--a-font-code']
    # Shared Reference owns scheme, forced colors, responsive scrolling and focus.
    assert 'color-scheme' not in root
    for mode in (0, 1):
        def color(token):
            return re.fullmatch(r'light-dark\((#[0-9a-f]{6}), (#[0-9a-f]{6})\)', root['--a-' + token])[mode + 1]
        for ink in ('ink', 'muted'):
            for surface in ('bg', 'surface', 'soft', 'tint'):
                assert contrast(color(ink), color(surface)) >= 4.5, (mode, ink, surface)
        assert contrast(color('on-accent'), color('accent')) >= 4.5


async def assert_passive_library_contract(app, name, label):
    before = deepcopy(app.appearance.shared())
    selected = app.clients.records['one']['selectedSessionId']
    listing = await call(app, 'theme.list')
    row = next(r for r in listing['items'] if r['id'] == 'builtin:' + name)
    assert row['name'] == label and row['source'] == 'built-in' and row['base'] == 'reference-v1'
    assert 'css' not in row and not theme_library.folder(app).exists()
    value = await call(app, 'theme.read', {'id': row['id']})
    assert value['css'] == source(name)
    assert value['fingerprint'] == row['fingerprint'] == theme_library.fingerprint(source(name))
    for action in ('theme.update', 'theme.delete'):
        args = {'id': row['id'], 'expectedFingerprint': row['fingerprint']}
        if action == 'theme.update':
            args.update(name='Changed', css='#amp-one{color:red}')
        with pytest.raises(AppError):
            await call(app, action, args)
    exported = await call(app, 'theme.export', {'id': row['id']})
    package = appearance_packages.load(base64.b64decode(exported['base64']))
    assert package.manifest['name'] == label and package.manifest['formatVersion'] == 2
    assert package.manifest['base'] == 'reference-v1' and package.css == source(name)
    saved = await call(app, 'theme.import', {'packageBase64': exported['base64']})
    assert (await call(app, 'theme.import', {'packageBase64': exported['base64']}))['id'] == saved['id']
    assert (await call(app, 'theme.read', {'id': saved['id']}))['css'] == source(name)
    assert app.appearance.shared() == before and app.clients.records['one']['selectedSessionId'] == selected


def test_breadbin_static_delta_and_authored_contrast():
    assert_delta_contract('ready')


def test_breadbin_continuous_violet_screen_and_control_paint_not_layout():
    surface = declarations('#amp-one [data-part="conversation"], #amp-one [data-part="messages"]')
    assert surface == {'background-color': 'var(--a-surface)'}
    assistant = declarations('#amp-one .a-assistant .a-message-body')
    assert assistant['background-color'] == 'transparent'
    assert declarations('#amp-one .a-markdown pre')['background-color'] == 'light-dark(#ede6fb, #3e317e)'
    assert declarations('#amp-one [data-part="composer-surface"]')['background-color'] == 'var(--a-soft)'
    assert declarations('#amp-one [data-part="header"]')['background-size'] == '100% 3px'
    assert declarations('#amp-one[data-decorations="off"]') == {'--ready-key-bevel': 'none', '--ready-key-pressed': 'none'}
    user = declarations('#amp-one .a-user .a-message-body')
    for mode in (0, 1):
        fg = re.findall(r'#[0-9a-f]{6}', user['color'])[mode]
        bg = re.findall(r'#[0-9a-f]{6}', user['background-color'])[mode]
        assert contrast(fg, bg) >= 4.5


async def test_breadbin_passive_builtin_immutable_zip_roundtrip(app):
    await assert_passive_library_contract(app, 'ready', 'Breadbin')
