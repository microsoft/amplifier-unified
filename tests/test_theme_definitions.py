"""Retired complete-theme APIs cannot mutate new Reference profiles."""
from copy import deepcopy

import pytest

from amplifier_web.service import ACTION_DEFINITIONS, AppError, validate_theme
from test_canvas_apps import app, dispatch


@pytest.mark.parametrize('action', ['theme.apply', 'theme.preview', 'theme.revert', 'theme.reset'])
async def test_complete_definition_and_token_actions_are_retired(app, action):
    before = deepcopy(app.appearance.shared())
    assert action not in ACTION_DEFINITIONS
    with pytest.raises(AppError):
        await dispatch(app, action, {'name': 'Old definition', 'definition': {'version': 1},
                                     'tokens': {'accent': '#123456'}})
    assert app.appearance.shared() == before


@pytest.mark.parametrize('css', [
    '#amp-one{background:url(https://example.com/art.png)}',
    '#amp-one{background:url(data:image/svg+xml;base64,PHN2Zz4=)}',
    '#amp-one{background:none}</style><script>alert(1)</script>',
    '@import "https://example.com/style.css";',
])
def test_delta_validator_keeps_external_artwork_and_style_injection_boundary(css):
    with pytest.raises(AppError):
        validate_theme(css)


async def test_library_does_not_accept_retired_structured_definition(app):
    before = deepcopy(app.appearance.shared())
    for action in ('theme.save', 'appearance.preview'):
        with pytest.raises(AppError):
            await dispatch(app, action, {'name': 'Old definition', 'definition': {'version': 1}})
    assert app.appearance.shared() == before
