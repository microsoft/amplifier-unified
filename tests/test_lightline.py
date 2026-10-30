"""Lightline's portable resource and library contract.

CSS checks describe authored rules and opaque token contrast, not browser-computed
styles, native cascade clearance, layout, or visual acceptance.
"""
import hashlib
import re
from copy import deepcopy
from importlib.resources import files

import pytest
import tinycss2

from amplifier_web import theme_library
from amplifier_web.service import AppError, validate_theme
from test_canvas_apps import app, dispatch
from test_digital_rain import luminance


CANDIDATE_SHA256 = "b5b8c804d89ca226304d9f8c47273926e0e8ba51fe6015a40be4a73a736af9a6"
PALETTE = {
    "bg": "#efefec", "surface": "#fffffc", "ink": "#242a2e",
    "accent": "#315d6b", "line": "#a0a8aa",
}


def resource():
    return files("amplifier_web").joinpath("appearances", "lightline.amplifier.css")


def source():
    return resource().read_text(encoding="utf-8")


def rules():
    """Parse authored declarations, retaining media context and !important."""
    def walk(nodes, media=""):
        for node in nodes:
            assert node.type != "error"
            if node.type == "at-rule":
                assert node.lower_at_keyword == "media" and node.content is not None
                yield from walk(tinycss2.parse_rule_list(
                    node.content, skip_comments=True, skip_whitespace=True,
                ), tinycss2.serialize(node.prelude).strip())
            else:
                assert node.type == "qualified-rule"
                values = tinycss2.parse_declaration_list(
                    node.content, skip_comments=True, skip_whitespace=True,
                )
                assert all(value.type == "declaration" for value in values)
                yield media, tinycss2.serialize(node.prelude).strip(), {
                    value.name: tinycss2.serialize(value.value).strip()
                    + ("!important" if value.important else "") for value in values
                }
    return list(walk(tinycss2.parse_stylesheet(
        source(), skip_comments=True, skip_whitespace=True,
    )))


def declarations(selector, media=""):
    matches = [values for context, target, values in rules()
               if context == media and target == selector]
    assert len(matches) == 1, (media, selector)
    return matches[0]


def test_lightline_packaged_resource_matches_the_authored_candidate():
    data = resource().read_bytes()
    assert hashlib.sha256(data).hexdigest() == CANDIDATE_SHA256
    assert len(data) < 100_000
    css = data.decode("utf-8")
    validate_theme(css)
    metadata, body = theme_library.metadata(css)
    assert metadata["name"] == "Lightline"
    assert metadata["description"] == theme_library.RESOURCE_PRESETS["lightline"]["description"]
    assert css.count(theme_library.MARKER) == 1
    assert body.startswith("/* Lightline:")


def test_lightline_is_self_contained_static_and_leaves_overflow_to_the_host():
    css = source()
    assert not re.search(r"https?://|file:|@import|@font-face|url\s*\(|</?script", css, re.I)
    # No positioned overlay, transcript mask, animation, or replacement layout.
    for _, selector, values in rules():
        assert selector.startswith("#amp-one")
        assert "::before" not in selector and "::after" not in selector
        for name in values:
            assert not name.startswith(("animation", "transition", "mask", "-webkit-mask"))
            assert name not in {
                "position", "z-index", "display", "height", "min-height", "max-height",
                "width", "min-width", "max-width", "overflow", "overflow-y",
                "overflow-x", "backdrop-filter", "transform", "pointer-events",
            }
    assert declarations('#amp-one [data-part="messages"]') == {
        "background": "transparent", "scrollbar-color": "var(--a-line) var(--a-bg)",
    }
    # Static paint has no motion to disable in reduced-motion mode.
    off = declarations('#amp-one[data-decorations="off"]')
    assert off == {"--ll-paths": "none", "--ll-glow": "transparent",
                   "--ll-sheen": "transparent", "--a-navigation-art": "none"}
    assert declarations("#amp-one", "(max-width:760px)")["--ll-paths"] == "none"


def test_lightline_authored_light_and_dark_scheme_tokens():
    root = declarations("#amp-one")
    assert root["color-scheme"] == "light dark"
    for mode in ("light", "dark"):
        assert declarations(f'#amp-one[data-theme-scheme="{mode}"]') == {"color-scheme": mode}
    for token in (*theme_library.TOKENS, "on-accent"):
        pair = re.fullmatch(r"light-dark\((#[0-9a-f]{6}),(#[0-9a-f]{6})\)", root["--a-" + token])
        assert pair, token
        if token in PALETTE:
            assert pair[1] == PALETTE[token]
    assert root["--a-navigation-art"] == root["--a-conversation-art"] == root["--a-canvas-art"] == "none"


@pytest.mark.parametrize("mode", [0, 1], ids=["light", "dark"])
def test_lightline_opaque_text_token_contrast(mode):
    root = declarations("#amp-one")

    def color(token):
        pair = re.fullmatch(r"light-dark\((#[0-9a-f]{6}),(#[0-9a-f]{6})\)", root[token])
        assert pair, token
        return pair[mode + 1]

    pairs = [("--a-ink", "--a-" + surface) for surface in ("bg", "surface", "soft", "tint")]
    pairs += [("--a-muted", "--a-" + surface) for surface in ("bg", "surface", "soft")]
    pairs += [("--a-accent", "--a-" + surface) for surface in ("bg", "surface", "tint")]
    pairs += [("--a-on-accent", "--a-accent"), ("--ll-user-ink", "--ll-user")]
    for ink, surface in pairs:
        low, high = sorted((luminance(color(ink)), luminance(color(surface))))
        assert (high + .05) / (low + .05) >= 4.5, (ink, surface)


def test_lightline_authored_bubbles_exclude_footers_and_keep_narrow_padding():
    outer = declarations("#amp-one .a-message.a-assistant,#amp-one .a-message.a-user")
    assert outer == {"background": "transparent!important", "border": "0!important",
                     "box-shadow": "none", "padding": "0!important"}
    assistant = "#amp-one .a-message.a-assistant>.a-message-body"
    bubble = declarations(assistant)
    assert bubble["background"] == "var(--a-surface)" and bubble["color"] == "var(--a-ink)"
    assert bubble["border"] == "1px solid var(--a-line)" and bubble["padding"] == "16px 20px"
    assert declarations(assistant, "(max-width:760px)") == {"padding": "12px"}
    user = declarations("#amp-one .a-message.a-user>.a-message-body")
    assert user["background"] == "var(--ll-user)" and user["color"] == "var(--ll-user-ink)"
    actions = declarations("#amp-one .a-message>.a-message-actions")
    assert actions == {"background": "transparent", "border": "0", "box-shadow": "none",
                       "color": "var(--a-muted)"}


def test_lightline_forced_colors_authored_logo_reset_covers_dark_specificity():
    forced = "(forced-colors:active)"
    root = declarations("#amp-one", forced)
    assert root["--a-bg"] == root["--a-surface"] == root["--ll-user"] == "Canvas"
    assert root["--a-ink"] == root["--ll-user-ink"] == "CanvasText"
    assert root["--a-accent"] == "LinkText" and root["--ll-paths"] == "none"
    # The forced-colors rule follows the tint rules, with matching specificity
    # for explicit dark scheme and OS dark preference. This is source evidence.
    filters = [(media, selector, values["filter"]) for media, selector, values in rules() if "filter" in values]
    assert filters[-1] == (forced,
        "#amp-one .a-brand img,#amp-one[data-theme-scheme] .a-brand img,"
        "#amp-one:not([data-theme-scheme]) .a-brand img", "none")
    assert any(media == "(prefers-color-scheme:dark)" and value != "none"
               for media, _, value in filters[:-1])
    buttons = declarations("#amp-one :is(.a-primary,.a-send)", forced)
    assert buttons == {"background": "ButtonFace", "color": "ButtonText", "border": "1px solid ButtonBorder"}


async def test_included_lightline_can_be_listed_read_and_saved_without_selecting_it(app):
    before = deepcopy(app.state["theme"])
    selected = app.state["selectedSessionId"]
    listing = (await dispatch(app, "theme.list", {}))["result"]
    row = next(item for item in listing["items"] if item["id"] == "builtin:lightline")
    assert row["name"] == "Lightline" and row["source"] == "built-in"
    assert row["palette"] == PALETTE and row["tag"] == "Architectural"
    assert row["radius"] == "8px" and row["heading"].startswith("ui-sans-serif")
    assert row["background"] == "none" and "css" not in row
    assert not theme_library.folder(app).exists(), "Browsing must not allocate user storage"
    theme = (await dispatch(app, "theme.read", {"id": row["id"]}))["result"]
    assert theme["css"] == source()
    assert theme["fingerprint"] == row["fingerprint"] == CANDIDATE_SHA256
    saved = (await dispatch(app, "theme.save", {"name": "My Lightline", "css": theme["css"]}))["result"]
    copy = (await dispatch(app, "theme.read", {"id": saved["id"]}))["result"]
    assert saved["id"].startswith("saved:") and copy["name"] == "My Lightline"
    assert theme_library.metadata(copy["css"])[1] == theme_library.metadata(theme["css"])[1]
    assert (await dispatch(app, "theme.read", {"id": row["id"]}))["result"] == theme
    assert app.state["theme"] == before and app.state["selectedSessionId"] == selected


async def test_included_lightline_is_immutable(app):
    before = deepcopy(app.state["theme"])
    theme = (await dispatch(app, "theme.read", {"id": "builtin:lightline"}))["result"]
    for action in ("theme.update", "theme.delete"):
        args = {"id": theme["id"], "expectedFingerprint": theme["fingerprint"]}
        if action == "theme.update":
            args.update(name="Changed", css="#amp-one{color:red}")
        with pytest.raises(AppError):
            await dispatch(app, action, args)
    assert (await dispatch(app, "theme.read", {"id": theme["id"]}))["result"] == theme
    assert app.state["theme"] == before and not theme_library.folder(app).exists()


async def test_lightline_export_import_roundtrip_retains_body_without_changing_selection(app):
    before = deepcopy(app.state["theme"])
    theme = (await dispatch(app, "theme.read", {"id": "builtin:lightline"}))["result"]
    exported = await dispatch(app, "theme.export", {"id": theme["id"]})
    css = exported["result"]["css"]
    assert exported["effects"][0]["content"] == css
    assert exported["effects"][0]["filename"] == "lightline.amplifier.css"
    assert exported["result"]["fingerprint"] == theme_library.fingerprint(css)
    assert css.count(theme_library.MARKER) == 1
    # Portable export normalizes header JSON spacing, not the authored CSS body.
    assert theme_library.metadata(css) == theme_library.metadata(source())
    validate_theme(css)
    saved = (await dispatch(app, "theme.import", {"css": css}))["result"]
    assert saved["name"] == theme["name"] and saved["description"] == theme["description"]
    repeated = (await dispatch(app, "theme.import", {"css": css}))["result"]
    assert repeated["id"] == saved["id"]
    assert (await dispatch(app, "theme.read", {"id": saved["id"]}))["result"]["css"] == css
    current = await dispatch(app, "theme.export", {})
    assert current["effects"][0]["content"] == before["css"]
    assert app.state["theme"] == before
