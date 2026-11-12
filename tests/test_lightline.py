"""Lightline delta source rules/opaque contrast, not rendered acceptance."""
import re

from test_ready import assert_delta_contract, assert_passive_library_contract, declarations, contrast
from test_appearance_preferences import app


def test_lightline_static_delta_and_authored_contrast():
    assert_delta_contract('lightline')


def test_lightline_crisp_outlines_and_warm_user_paint_leave_geometry_to_reference():
    assistant = declarations('#amp-one .a-assistant .a-message-body', 'lightline')
    assert assistant == {'border': '1px solid var(--a-line)', 'border-inline-start': '1px solid var(--a-line)',
                         'border-radius': 'var(--a-message-radius)', 'background-color': 'var(--a-surface)'}
    user = declarations('#amp-one .a-user .a-message-body', 'lightline')
    assert user['border-inline-start'].startswith('3px solid ')
    for mode in (0, 1):
        foreground = re.findall(r'#[0-9a-f]{6}', user['color'])[mode]
        background = re.findall(r'#[0-9a-f]{6}', user['background-color'])[mode]
        assert contrast(foreground, background) >= 4.5
    root = declarations('#amp-one', 'lightline')
    assert root['--a-surface'] == 'light-dark(#ffffff, #182329)'
    assert root['--a-surface-shadow'] == root['--a-panel-shadow'] == 'none'
    assert root['--a-panel-border-width'] == '1px'


async def test_lightline_passive_builtin_immutable_zip_roundtrip(app):
    await assert_passive_library_contract(app, 'lightline', 'Lightline')
