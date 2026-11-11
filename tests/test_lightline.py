"""Lightline's palette/type/material delta over the always-loaded Reference.

Source assertions do not establish computed cascade, geometry or pixel acceptance.
"""
import re

from test_canvas_apps import app
from test_ready import (
    appearance_source, assert_builtin_library_contract, assert_delta,
    assert_palette_contrast, contrast, declarations, frontend_source, pair, rules,
)


def source():
    return appearance_source("lightline")


def test_lightline_is_a_small_static_delta_not_a_pinned_full_skin():
    css = source()
    assert_delta(css)
    assert not re.search(r"url\s*\(", css, re.I)
    root = declarations(css, "#amp-one")
    assert "system-ui" in root["--a-font-ui"]
    assert root["--a-font-heading"] == "var(--a-font-ui)"
    assert "monospace" in root["--a-font-chat"] and "monospace" in root["--a-font-code"]
    assert root["--a-font"] == "var(--a-font-ui)"
    assert pair(root["--a-ink"])[0] == "#242a2e"
    assert root["--a-panel-radius"] == "8px"
    assert root["--a-background-art"].count("linear-gradient(") == 2
    # The former logo tint/filter implementation is gone; no package-specific
    # forced-colors reset or OS-scheme duplicate is needed for a nonexistent filter.
    assert all("filter" not in values for _, _, values in rules(css))
    assert_palette_contrast(css)


def test_lightline_bubble_material_targets_bodies_not_the_shared_footer():
    css = source()
    assistant = declarations(css, "#amp-one .a-assistant .a-message-body")
    assert assistant["background-color"] == "var(--a-surface)"
    assert assistant["border"] == "1px solid var(--a-line)"
    assert assistant["border-inline-start"] == "2px solid var(--a-accent)"
    assert assistant["border-radius"] == "var(--a-message-radius)"
    user = declarations(css, "#amp-one .a-user .a-message-body")
    assert user["border-radius"] == "var(--a-message-radius)"
    for mode in (0, 1):
        assert contrast(pair(user["color"])[mode], pair(user["background-color"])[mode]) >= 4.5
    assert not any(".a-message-actions" in selector for _, selector, _ in rules(css))
    # Scroll/mask/focus and narrow geometry are tested at their shared owners in
    # test_ready, rather than requiring each appearance to repeat functional CSS.
    assert declarations(frontend_source("reference.css"), "#amp-one.a-chat-shell .a-messages")["overflow"] == "auto"


async def test_lightline_builtin_is_passive_immutable_and_roundtrips_as_a_reference_zip(app):
    await assert_builtin_library_contract(app, "lightline", "Lightline")