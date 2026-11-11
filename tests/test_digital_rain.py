"""Digital Rain is optional artwork over Reference, never a replacement layout."""
import base64
from copy import deepcopy
import re
import struct

from test_canvas_apps import app, dispatch
from test_ready import (
    appearance_source, assert_builtin_library_contract, assert_delta,
    assert_palette_contrast, declarations, frontend_source, rules,
)


def source():
    return appearance_source("digital-rain")


def test_digital_rain_retains_three_original_embedded_tiles_and_palette_contrast():
    css = source()
    assert_delta(css, animated=True)
    tiles = re.findall(r"data:image/png;base64,([A-Za-z0-9+/=]+)", css)
    assert len(tiles) == 3
    dimensions = []
    for tile in tiles:
        data = base64.b64decode(tile, validate=True)
        assert data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR"
        dimensions.append(struct.unpack(">II", data[16:24]))
    assert dimensions == [(512, 768), (704, 960), (896, 1152)]
    readable = re.sub(r"data:image/png;base64,[A-Za-z0-9+/=]+", "", css)
    assert not re.search(r"\b(matrix|tron|kitt)\b", readable, re.I)
    root = declarations(css, "#amp-one")
    assert "monospace" in root["--a-font-ui"]
    for role in ("heading", "chat", "code"):
        assert root["--a-font-" + role] == "var(--a-font-ui)"
    assert_palette_contrast(css)


def test_rain_uses_shared_root_and_panel_paint_without_landing_layout_or_busy_feedback():
    css = source()
    root = declarations(css, "#amp-one")
    assert root["--a-background-art"] == root["--a-conversation-art"] == "var(--digital-rain-art)"
    assert root["background-size"] == "512px 768px, 704px 960px, 896px 1152px"
    assert root["background-repeat"] == "repeat"
    assert root["--a-conversation-art-size"] == root["background-size"]
    assert root["--a-conversation-art-repeat"] == root["background-repeat"]
    assert root["--a-conversation-art-position"] == root["background-position"]
    panel = declarations(frontend_source("main-refresh.css"), "#amp-one.a-approachable-shell .a-conversation")
    assert panel["background-size"] == "var(--a-conversation-art-size,100% 240px)"
    assert panel["background-repeat"] == "var(--a-conversation-art-repeat,no-repeat)"
    assert panel["background-position"] == "var(--a-conversation-art-position,0 0)"
    reference = frontend_source("reference.css")
    assert declarations(reference, "#amp-one")["background-image"] == "var(--a-background-art)"
    # Background images are not inherited. The shared landing surface must let
    # root artwork show through, not cover it with an opaque surface.
    landing = declarations(frontend_source("adaptive-work.css"), "#amp-one .a-adaptive-home")
    assert landing["background"] == "transparent"
    assert not any('[data-part="work-browser"]' in selector for _, selector, _ in rules(css))
    assistant = declarations(css, "#amp-one .a-assistant .a-message-body")
    assert assistant["background-color"] == "var(--a-surface)" and assistant["color"] == "var(--a-ink)"
    assert not any(".a-message-actions" in selector for _, selector, _ in rules(css))
    assert declarations(css, '#amp-one [data-part="messages"]') == {"background-color": "transparent"}


def test_rain_motion_is_opt_in_to_media_and_decorations_and_forced_colors_hides_art():
    css = source()
    motion = ("(prefers-reduced-motion:no-preference)",)
    owners = '#amp-one:not([data-decorations="off"]), #amp-one:not([data-decorations="off"]) .a-conversation'
    animation = declarations(css, owners, motion)
    assert animation == {"animation": "digital-rain-drift 72s linear infinite"}
    frames = [values for context, _, values in rules(css)
              if context == (*motion, "keyframes digital-rain-drift")]
    assert len(frames) == 2 and all(set(values) == {"background-position"} for values in frames)
    for media in ("(prefers-reduced-motion:reduce)", "(forced-colors:active)"):
        assert declarations(css, "#amp-one, #amp-one .a-conversation", (media,)) == {"animation": "none"}
    off = declarations(css, '#amp-one[data-decorations="off"]')
    assert off == {"--a-background-art": "none", "--a-conversation-art": "none", "animation": "none"}
    assert declarations(css, '#amp-one[data-decorations="off"] .a-conversation') == {"animation": "none"}
    forced = declarations(frontend_source("reference.css"), "#amp-one", ("(forced-colors:active)",))
    assert forced["background-image"] == "none!important"
    assert forced["--a-background-art"] == forced["--a-conversation-art"] == "none!important"
    # Both painted owners stop drifting as well as losing their artwork.


def test_rain_inherits_host_transcript_edges_with_focus_and_forced_color_guards():
    assert not re.search(r"(?:-webkit-)?mask(?:-[\w-]+)?\s*:", source())
    css = frontend_source("conversation-rail.css")
    baseline = declarations(css, "#amp-one .a-messages")
    assert baseline["mask-image"] == baseline["-webkit-mask-image"] == "none!important"
    masked = [(context, selector, values) for context, selector, values in rules(css)
              if values.get("mask-image", "").startswith("var(--_a-transcript-edge-mask)")]
    assert len(masked) == 1 and masked[0][0] == ()
    selector = masked[0][1]
    for guard in ('[data-overflow-above=true]', '[data-overflow-below=true]',
                  ':not([data-transcript-iframe-focus=true])', ':not(:focus-visible)',
                  'input:focus', 'textarea:focus', 'select:focus', '[contenteditable]:focus', 'iframe:focus'):
        assert guard in selector
    forced = declarations(css, selector, ("(forced-colors:active)",))
    assert forced["mask-image"] == forced["-webkit-mask-image"] == "none!important"


async def test_digital_rain_builtin_is_passive_immutable_and_roundtrips_as_a_reference_zip(app):
    await assert_builtin_library_contract(app, "digital-rain", "Digital Rain")


async def test_rain_preview_and_selection_use_the_scoped_preference_owner(app):
    before = deepcopy(app.appearance.read("one"))
    selected = app.clients.records["one"]["selectedSessionId"]
    theme = (await dispatch(app, "theme.read", {"id": "builtin:digital-rain"}))["result"]
    preview = (await dispatch(app, "appearance.preview", {"name": theme["name"], "css": theme["css"]}))["result"]
    assert preview["status"] == "awaiting-browser" and not preview["activated"]
    current = app.appearance.read("one")
    assert current["effective"] == before["effective"] and current["shared"] == before["shared"]
    assert current["local"] == before["local"]
    assert rules(current["preview"]["theme"]["css"]) == rules(theme["css"])
    assert current["composition"]["foundation"]["id"] == "reference-v1"
    await dispatch(app, "appearance.preview.cancel", {})
    assert app.appearance.read("one")["preview"] is None
    await dispatch(app, "appearance.preferences.update", {"scope": "shared",
        "expectedRevision": before["shared"]["revision"], "patch": {"appearanceId": theme["id"]}})
    after = app.appearance.read("one")
    assert after["effective"] == {**before["effective"], "appearanceId": theme["id"]}
    assert after["local"] == before["local"]
    assert after["theme"]["id"] == theme["id"] and after["theme"]["css"] == theme["css"]
    assert app.clients.records["one"]["selectedSessionId"] == selected