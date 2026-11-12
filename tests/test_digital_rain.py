"""Digital Rain stays self-contained; common layout/focus belong to Reference."""
import base64
import re
import struct
from importlib.resources import files

import pytest
import tinycss2

from amplifier_web.service import validate_theme
from test_appearance_preferences import app, call, update


def source():
    return files('amplifier_web').joinpath('appearances/digital-rain.amplifier.css').read_text()


def test_digital_rain_is_portable_and_has_three_original_tiles():
    css = source()
    validate_theme(css)
    assert len(css.encode()) < 100_000
    tiles = re.findall(r'data:image/png;base64,([A-Za-z0-9+/=]+)', css)
    assert len(tiles) == 3
    dimensions = []
    for tile in tiles:
        data = base64.b64decode(tile, validate=True)
        assert data.startswith(b'\x89PNG\r\n\x1a\n')
        dimensions.append(struct.unpack('>II', data[16:24]))
    assert dimensions == [(512, 768), (704, 960), (896, 1152)]
    readable = re.sub(r'data:image/png;base64,[A-Za-z0-9+/=]+', '', css)
    assert not re.search(r'\b(matrix|tron|kitt)\b|https?://|@import|@font-face', readable, re.I)
    assert 'digital-rain-drift 72s linear infinite' in css
    assert not re.search(r'conversation["\']?\]?\s*>\s*\*', css)


def test_motion_only_runs_with_no_preference_and_decorations_enabled():
    css = source()
    assert '@media (prefers-reduced-motion: no-preference)' in css
    assert '#amp-one:not([data-decorations="off"]) .a-conversation' in css
    for media in ('prefers-reduced-motion: reduce', 'forced-colors: active'):
        assert '@media (' + media + ') {\n  #amp-one, #amp-one .a-conversation { animation: none; }' in css
    assert '#amp-one[data-decorations="off"] .a-conversation { animation: none; }' in css
    off = css.split('#amp-one[data-decorations="off"] {', 1)[1].split('}', 1)[0]
    assert '--a-background-art: none' in off and '--a-conversation-art: none' in off
    assert 'animation: none' in off


def test_delta_has_no_layout_masks_overlay_or_unrelated_aside_targets():
    def walk(nodes, keyframes=False):
        for node in nodes:
            assert node.type != 'error'
            if node.type == 'at-rule':
                assert node.lower_at_keyword in {'media', 'keyframes'} and node.content is not None
                walk(tinycss2.parse_rule_list(node.content, skip_comments=True, skip_whitespace=True),
                     node.lower_at_keyword == 'keyframes')
            else:
                assert node.type == 'qualified-rule'
                selector = tinycss2.serialize(node.prelude)
                if not keyframes:
                    assert selector.strip().startswith('#amp-one')
                    assert not re.search(r'\baside\b|aria-busy|data-region-pending|::before|::after', selector)
                values = tinycss2.parse_declaration_list(node.content, skip_comments=True, skip_whitespace=True)
                for v in values:
                    assert v.type == 'declaration' and not v.important
                    assert not v.name.startswith(('mask', '-webkit-mask'))
                    assert v.name not in {'position', 'display', 'height', 'min-height', 'max-height',
                        'width', 'min-width', 'max-width', 'overflow', 'overflow-x', 'overflow-y',
                        'padding', 'margin', 'transform', 'z-index', 'pointer-events'}
    walk(tinycss2.parse_stylesheet(source(), skip_comments=True, skip_whitespace=True))
    assert '#amp-one .a-assistant .a-message-body' in source()
    assert 'border-inline-start: 3px solid var(--a-green)' in source()
    # No per-theme sidebar/assistant padding workaround: Reference owns it once.
    assert 'padding-inline' not in source()


def luminance(color):
    channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels]
    return sum(w * v for w, v in zip((.2126, .7152, .0722), linear))


@pytest.mark.parametrize('ink,surface', [
    ('#143522', '#f8fcf9'), ('#486652', '#e8f3ec'), ('#ffffff', '#176c3b'),
    ('#143522', '#d6ebdd'), ('#e0f4e5', '#06100b'), ('#a0bdab', '#0b1a12'), ('#03200c', '#85eea2'),
])
def test_digital_rain_text_palette_contrast(ink, surface):
    assert ink in source() and surface in source()
    low, high = sorted((luminance(ink), luminance(surface)))
    assert (high + .05) / (low + .05) >= 4.5


async def test_digital_rain_passive_copy_preview_and_profile_selection(app):
    before = app.appearance.shared()
    listing = await call(app, 'theme.list')
    row = next(r for r in listing['items'] if r['id'] == 'builtin:digital-rain')
    assert row['name'] == 'Digital Rain' and row['source'] == 'built-in' and 'css' not in row
    theme = await call(app, 'theme.read', {'id': row['id']})
    assert theme['css'] == source()
    saved = await call(app, 'theme.save', {'name': 'My rain', 'css': theme['css']})
    assert saved['id'].startswith('saved:') and saved['id'].endswith('.zip')
    await call(app, 'appearance.preview', {'name': theme['name'], 'css': theme['css']})
    assert app.appearance.shared() == before
    await call(app, 'appearance.preview.cancel')
    await update(app, {'appearanceId': row['id']})
    assert app.appearance.read('one')['effective']['appearanceId'] == row['id']
    assert app.appearance.read('one')['theme']['css'] == theme['css']
