"""Breadbin's Reference delta and the shared source/library seams it inherits.

These checks inspect authored rules and opaque colors, not computed cascade,
geometry or pixels. The retained focused browser checks own those observations.
"""
import base64
from copy import deepcopy
import hashlib
from importlib.resources import files
import io
import json
from pathlib import Path
import re
import zipfile

import pytest
import tinycss2

from amplifier_web import theme_library
from amplifier_web.service import AppError, validate_theme
from test_canvas_apps import app, dispatch


ROOT = Path(__file__).resolve().parents[1]
PALETTE_TOKENS = (
    "bg", "surface", "soft", "ink", "muted", "line", "accent", "on-accent",
    "tint", "green", "danger",
)


def appearance_source(name):
    return files("amplifier_web").joinpath("appearances", name + ".amplifier.css").read_text(encoding="utf-8")


def source():
    return appearance_source("ready")


def frontend_source(name):
    # Inspect the selected delivery snapshot, never a stale generated dev view.
    # Canonical client authoring and snapshot refresh follow components/README.md.
    return (ROOT / "components/web-client/src" / name).read_text(encoding="utf-8")


def normalize_selector(selector):
    selector = re.sub(r"\s+", " ", selector).strip()
    return re.sub(r"\s*([,()>])\s*", r"\1", selector)


def rules(css):
    """Retain nested media/container/keyframe context, spelling and importance."""
    def walk(nodes, context=()):
        for node in nodes:
            assert node.type != "error"
            if node.type == "at-rule":
                assert node.lower_at_keyword in {"media", "container", "keyframes"}
                assert node.content is not None
                prelude = re.sub(r"\s+", "", tinycss2.serialize(node.prelude))
                if node.lower_at_keyword != "media":
                    prelude = node.lower_at_keyword + " " + prelude
                yield from walk(tinycss2.parse_rule_list(
                    node.content, skip_comments=True, skip_whitespace=True,
                ), (*context, prelude))
                continue
            assert node.type == "qualified-rule"
            values = tinycss2.parse_declaration_list(
                node.content, skip_comments=True, skip_whitespace=True,
            )
            assert all(value.type == "declaration" for value in values)
            yield context, normalize_selector(tinycss2.serialize(node.prelude)), {
                value.name: tinycss2.serialize(value.value).strip()
                + ("!important" if value.important else "") for value in values
            }
    return list(walk(tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True)))


def declarations(css, selector, context=()):
    found = [values for current, target, values in rules(css)
             if current == context and target == normalize_selector(selector)]
    assert found, (context, selector)
    # A delta may add a later root block for a large embedded asset. Preserve
    # source order for identical selectors rather than demanding one declaration site.
    return {name: value for values in found for name, value in values.items()}


def pair(value):
    match = re.fullmatch(r"light-dark\(\s*(#[0-9a-f]{6})\s*,\s*(#[0-9a-f]{6})\s*\)", value, re.I)
    assert match, value
    return match.group(1), match.group(2)


def luminance(color):
    def linear(channel):
        channel = int(channel, 16) / 255
        return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
    red, green, blue = (linear(color[index:index + 2]) for index in (1, 3, 5))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(first, second):
    low, high = sorted((luminance(first), luminance(second)))
    return (high + 0.05) / (low + 0.05)


def assert_delta(css, *, animated=False):
    assert len(css.encode("utf-8")) < 100_000
    validate_theme(css)
    readable = re.sub(r"data:image/png;base64,[A-Za-z0-9+/=]+", "", css)
    assert not re.search(r"https?://|file:|@import|@font-face|</?script", readable, re.I)
    forbidden = {
        "position", "inset", "top", "right", "bottom", "left", "z-index", "display",
        "height", "min-height", "max-height", "width", "min-width", "max-width",
        "overflow", "overflow-x", "overflow-y", "transform", "pointer-events",
        "content", "backdrop-filter", "padding", "padding-inline-start", "padding-inline-end", "margin",
    }
    for context, selector, values in rules(css):
        if any(item.startswith("keyframes ") for item in context):
            assert animated and selector in {"from", "to"}
            assert set(values) == {"background-position"}
            continue
        assert selector.startswith("#amp-one")
        assert not re.search(r"\baside\b|::before|::after|:has\(|:nth-", selector)
        assert not re.search(r"\[(?:aria-busy|data-region-pending|data-state|data-activity|data-running)\b", selector)
        assert forbidden.isdisjoint(values), selector
        for name, value in values.items():
            assert not name.startswith(("mask", "-webkit-mask", "transition"))
            assert not name.startswith("animation") or animated
            assert not value.endswith("!important"), (selector, name)
        if ".a-message" in selector:
            assert selector.endswith(".a-message-body"), selector


def assert_palette_contrast(css):
    # Omission inherits the foundation; it is not a demand to duplicate shared CSS.
    root = declarations(frontend_source("reference.css"), "#amp-one")
    root.update(declarations(css, "#amp-one"))
    colors = {token: pair(root["--a-" + token]) for token in PALETTE_TOKENS}
    text_pairs = [("ink", surface) for surface in ("bg", "surface", "soft", "tint")]
    text_pairs += [("muted", surface) for surface in ("bg", "surface", "soft")]
    text_pairs += [(color, surface) for color in ("accent", "green", "danger")
                   for surface in ("bg", "surface")]
    text_pairs += [("on-accent", "accent"), ("accent", "tint")]
    for mode in (0, 1):
        for ink, surface in text_pairs:
            assert contrast(colors[ink][mode], colors[surface][mode]) >= 4.5, (mode, ink, surface)
        for surface in ("bg", "surface", "soft", "tint"):
            assert contrast(colors["accent"][mode], colors[surface][mode]) >= 3, (mode, "focus", surface)


def test_ready_is_a_small_static_palette_type_and_material_delta():
    css = source()
    assert_delta(css)
    assert not re.search(r"url\s*\(", css, re.I)
    root = declarations(css, "#amp-one")
    assert "system-ui" in root["--a-font-ui"]
    for role in ("heading", "chat", "code"):
        assert "monospace" in root["--a-font-" + role]
    assert root["--a-font"] == "var(--a-font-ui)"
    assert_palette_contrast(css)
    user = declarations(css, "#amp-one .a-user .a-message-body")
    for mode in (0, 1):
        background = pair(user["background-color"])[mode]
        assert contrast(pair(user["color"])[mode], background) >= 4.5
        assert contrast(pair(root["--a-accent"])[mode], background) >= 3


def test_ready_paints_header_keys_and_bodies_without_owning_layout():
    css = source()
    header = declarations(css, '#amp-one [data-part="header"]')
    assert header["background-size"] == "100% 3px"
    spectrum = [selector for _, selector, values in rules(css)
                if any("var(--ready-spectrum)" in value for value in values.values())]
    assert spectrum == ['#amp-one [data-part="header"]']
    buttons = "#amp-one :where(button.a-soft, button.a-primary, button.a-send)"
    assert declarations(css, buttons) == {"box-shadow": "var(--ready-key-bevel)"}
    assert declarations(css, buttons + ":active") == {"box-shadow": "var(--ready-key-pressed)"}
    assistant = declarations(css, "#amp-one .a-assistant .a-message-body")
    assert assistant["background-color"] == "var(--a-surface)"
    assert assistant["border"] == "1px solid var(--a-line)"
    assert assistant["border-radius"] == "var(--a-message-radius)"
    assert not any(".a-message-actions" in selector for _, selector, _ in rules(css))


def test_ready_decorations_off_removes_keycap_art_not_functional_shapes():
    # A surviving bevel is a product defect, not a reason to drop the old off contract.
    # Search both owners: a common fix belongs in shared presentation where possible.
    off_values = {}
    off_button_rules = []
    for css in (source(), frontend_source("theme-presentation.css")):
        for context, selector, values in rules(css):
            if not context and '[data-decorations="off"]' in selector:
                off_values.update({name: value for name, value in values.items() if name.startswith("--ready-")})
                if "button" in selector and values.get("box-shadow") in {"none", "none!important"}:
                    off_button_rules.append(selector)
    # An override of the common button selector covers its :active state too;
    # otherwise both authored decorative variables must be switched off.
    common_buttons = all(any(part in selector for selector in off_button_rules)
                         for part in ("button.a-soft", "button.a-primary", "button.a-send"))
    assert common_buttons or all(off_values.get(name) in {"none", "none!important"}
                                 for name in ("--ready-key-bevel", "--ready-key-pressed"))


def test_reference_supplies_scheme_rows_focus_and_native_scroll_ownership():
    css = frontend_source("reference.css")
    assert "import './reference.css';" in frontend_source("main.jsx")
    assert declarations(css, "#amp-one")["color-scheme"] == "light dark"
    for mode in ("light", "dark"):
        assert declarations(css, f'#amp-one[data-theme-scheme="{mode}"]') == {"color-scheme": mode}
    rows = declarations(css, "#amp-one :where(.a-nav-chat:not(.a-nav-chat-rename), .a-workspace-row)")
    assert rows["padding-inline-start"] == "8px" and rows["padding-inline-end"] == "4px"
    selected = declarations(css, '#amp-one :where(.a-nav-chat.is-selected, .a-nav-chat[aria-current="page"], .a-workspace-row.is-selected)')
    assert selected["box-shadow"] == "inset 3px 0 var(--a-accent)"
    focus = [(selector, values) for context, selector, values in rules(css)
             if not context and selector.endswith(":focus-visible")]
    assert len(focus) == 1 and '[tabindex]:not([tabindex="-1"])' in focus[0][0]
    assert focus[0][1]["outline"] == "2px solid var(--a-accent)"
    transcript = declarations(css, "#amp-one.a-chat-shell .a-messages")
    assert transcript["overflow"] == "auto" and transcript["min-height"] == "0"
    assert transcript["scrollbar-gutter"] == "stable" and transcript["overscroll-behavior"] == "contain"
    narrow = declarations(frontend_source("main-refresh.css"), "#amp-one.a-approachable-shell .a-messages",
                          ("container conversation(max-width:520px)",))
    assert narrow["padding"] == "22px 18px"
    assert "clamp(" in narrow["gap"]


def test_shared_message_and_composer_owners_keep_body_footer_and_focus_separate():
    reference = frontend_source("reference.css")
    chat = frontend_source("chat-presentation.css")
    assert declarations(reference, "#amp-one .a-message-body")["font-family"] == "var(--a-font-chat)"
    assert declarations(reference, "#amp-one .a-markdown")["line-height"] == "1.7"
    footer = declarations(reference, "#amp-one :where(.a-message-actions, .a-message-attribution, .a-message-channel, .a-message-date)")
    assert footer == {"color": "var(--a-muted)", "font-family": "var(--a-font-ui)"}
    actions = declarations(chat, "#amp-one .a-message-actions")
    assert actions["display"] == "flex" and actions["align-items"] == "center"
    assert not {"background", "background-color", "border", "box-shadow", "padding"} & actions.keys()
    assert declarations(chat, "#amp-one .a-message-body")["padding"] == "8px 14px"
    outer = declarations(chat, "#amp-one .a-message.a-user,#amp-one.a-chat-shell .a-message.a-user")
    assert outer["background"] == "none" and outer["border"] == "0" and outer["padding"] == "0"
    for _, selector, values in rules(reference + "\n" + chat):
        if selector.endswith(".a-message.a-assistant"):
            assert values.get("background", "transparent") in {"none", "transparent"}
            assert values.get("border", "0") == "0"
    # The shared footer's muted treatment excludes primary confirmation actions.
    refresh = frontend_source("main-refresh.css")
    assert 'button:not(.a-primary):not(.a-send){color:var(--a-muted)}' in refresh
    primary = declarations(reference, "#amp-one :where(.a-primary, .a-send)")
    assert primary == {"color": "var(--a-on-accent)", "background-color": "var(--a-accent)"}
    composer = frontend_source("compact-composer.css")
    boundary = "#amp-one .a-compact-composer .a-compose-surface:focus-within"
    assert declarations(composer, boundary)["outline"] == "2px solid var(--a-accent)"
    assert declarations(composer, boundary, ("(forced-colors:active)",))["outline-color"] == "Highlight"


def test_shared_forced_colors_and_decorations_override_delta_art_and_controls():
    css = frontend_source("reference.css")
    forced = ("(forced-colors:active)",)
    root = declarations(css, "#amp-one", forced)
    expected = {"bg": "Canvas", "surface": "Canvas", "soft": "Canvas", "tint": "Canvas",
                "ink": "CanvasText", "muted": "CanvasText", "green": "CanvasText", "danger": "CanvasText",
                "line": "ButtonBorder", "accent": "Highlight", "on-accent": "HighlightText"}
    for token, color in expected.items():
        assert root["--a-" + token] == color + "!important"
    for part in ("background", "navigation", "conversation", "canvas"):
        assert root[f"--a-{part}-art"] == "none!important"
    assert root["background-image"] == "none!important"
    for selector in ("#amp-one :where(.a-primary, .a-send)", "#amp-one button.a-soft"):
        controls = declarations(css, selector, forced)
        assert controls["color"] == "ButtonText!important"
        assert controls["background-color"] == "ButtonFace!important"
        assert controls["box-shadow"] == "none!important"
    assert declarations(css, "#amp-one ::selection", forced) == {
        "color": "HighlightText", "background-color": "Highlight",
    }
    off = frontend_source("theme-presentation.css")
    assert declarations(off, '#amp-one[data-decorations="off"][data-part="app"]')["background-image"] == "none!important"
    surfaces = '#amp-one[data-decorations="off"][data-part="app"] :is([data-part="header"],.a-conversation,.a-nav-rail,.a-canvas-chrome)'
    assert declarations(off, surfaces) == {"background-image": "none!important"}


def preference_snapshot(app):
    return deepcopy((app.appearance.shared(), app.appearance.read("one"), app.appearance.read("two"),
                     {client: app.clients.records[client]["selectedSessionId"] for client in ("one", "two")}))


def exported_package(receipt, *, identity, name, css, description):
    result = receipt["result"]
    assert result["id"] == identity and result["name"] == name
    assert result["format"] == "zip" and "css" not in result
    assert result["mimeType"] == "application/zip"
    assert result["filename"] == theme_library.slug(name) + ".zip"
    assert len(receipt["effects"]) == 1
    effect = receipt["effects"][0]
    expected = {"type": "download", "filename": result["filename"],
                "mime": "application/zip", "mimeType": "application/zip",
                "content": {"base64": result["base64"]}}
    assert {key: effect[key] for key in expected} == expected
    # service._dispatch decorates every effect with delivery metadata.
    assert effect["clientId"] == "one" and effect["origin"] == "ui"
    assert isinstance(effect["id"], str) and effect["id"]
    assert isinstance(effect["createdAt"], (int, float)) and effect["createdAt"] > 0
    raw = base64.b64decode(result["base64"], validate=True)
    assert result["fingerprint"] == hashlib.sha256(raw).hexdigest()
    # Inspect the actual wire envelope independently of the package loader.
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        manifest = json.loads(archive.read("appearance.json"))
        assert manifest["type"] == "appearance" and manifest["formatVersion"] == 2
        assert manifest["base"] == "reference-v1" and manifest["name"] == name
        assert manifest["description"] == description
        assert archive.read(manifest["stylesheet"]).decode("utf-8") == css
        assert set(archive.namelist()) == {"appearance.json", manifest["stylesheet"]}
    return raw


async def assert_builtin_library_contract(app, identity, name):
    # Independent non-default profiles make accidental selection/adoption visible.
    await dispatch(app, "appearance.preferences.update", {"scope": "shared",
        "expectedRevision": app.appearance.shared()["revision"],
        "patch": {"appearanceId": "builtin:graphite", "scheme": "dark", "decorations": False}})
    initial = app.appearance.read("one")
    await dispatch(app, "appearance.client.report", {"generation": initial["generation"],
        "revision": 0, "storageVersion": "test-local-preferences", "status": "ready",
        "activeScope": "client",
        "values": {**initial["shared"]["values"], "appearanceId": "builtin:atelier", "scheme": "light"}}, origin="ui")
    before = preference_snapshot(app)
    css = appearance_source(identity)
    library_id = "builtin:" + identity
    listing = (await dispatch(app, "theme.list", {}))["result"]
    assert listing["errors"] == []
    assert {item["id"] for item in listing["items"] if item["source"] == "built-in"} == {
        "builtin:" + key for key in ("default", "aurora", "atelier", "graphite", "digital-rain", "lightline", "ready")}
    assert not any(item["id"] in {"reference", "builtin:reference"} for item in listing["items"])
    row = next(item for item in listing["items"] if item["id"] == library_id)
    assert row["name"] == name and row["source"] == "built-in" and row["readOnly"] is True
    assert row["base"] == "reference-v1" and "css" not in row
    theme = (await dispatch(app, "theme.read", {"id": library_id}))["result"]
    assert theme == {**row, "css": css}
    assert theme["fingerprint"] == hashlib.sha256(css.encode("utf-8")).hexdigest()
    assert not theme_library.folder(app).exists(), "Listing and reading built-ins must not allocate storage"
    for action in ("theme.update", "theme.delete"):
        args = {"id": library_id, "expectedFingerprint": theme["fingerprint"]}
        if action == "theme.update":
            args.update(name="Changed built-in", css="#amp-one{--a-accent:#123456}")
        with pytest.raises(AppError):
            await dispatch(app, action, args)
    exported = await dispatch(app, "theme.export", {"id": library_id})
    raw = exported_package(exported, identity=library_id, name=name, css=css, description=theme["description"])
    assert not theme_library.folder(app).exists(), "Exporting/denied mutation of a built-in is passive"
    assert preference_snapshot(app) == before

    copy_name = "My " + name
    saved = (await dispatch(app, "theme.save", {"name": copy_name, "css": css,
                                              "description": theme["description"]}))["result"]
    assert saved["id"].startswith("saved:") and saved["id"].endswith(".zip")
    assert saved["base"] == "reference-v1" and saved["format"] == "zip" and saved["packageVersion"] == 2
    copy = (await dispatch(app, "theme.read", {"id": saved["id"]}))["result"]
    assert copy["name"] == copy_name and copy["source"] == "saved"
    validate_theme(copy["css"])
    copy_export = await dispatch(app, "theme.export", {"id": saved["id"]})
    copy_raw = exported_package(copy_export, identity=saved["id"], name=copy_name, css=css,
                                description=theme["description"])
    assert saved["fingerprint"] == copy["fingerprint"] == hashlib.sha256(copy_raw).hexdigest()
    imported = (await dispatch(app, "theme.import", {"packageBase64": exported["result"]["base64"]}))["result"]
    repeated = (await dispatch(app, "theme.import", {"packageBase64": exported["result"]["base64"]}))["result"]
    assert imported["source"] == "saved" and imported["id"].endswith(".zip")
    assert repeated["id"] == imported["id"] and imported["id"] != saved["id"]
    roundtrip = (await dispatch(app, "theme.read", {"id": imported["id"]}))["result"]
    assert roundtrip["name"] == name and roundtrip["description"] == theme["description"]
    # Compiled CSS may normalize URL syntax; the archive retains exact authored bytes.
    assert rules(roundtrip["css"]) == rules(css) and rules(copy["css"]) == rules(css)
    assert roundtrip["fingerprint"] == imported["fingerprint"] == hashlib.sha256(raw).hexdigest()
    reexport = await dispatch(app, "theme.export", {"id": imported["id"]})
    assert exported_package(reexport, identity=imported["id"], name=name, css=css,
                            description=theme["description"]) == raw
    copy_import = (await dispatch(app, "theme.import", {"packageBase64": copy_export["result"]["base64"]}))["result"]
    assert copy_import["id"] == saved["id"], "Import of an existing saved copy deduplicates"
    assert (await dispatch(app, "theme.read", {"id": library_id}))["result"] == theme
    assert preference_snapshot(app) == before


async def test_ready_builtin_is_passive_immutable_and_roundtrips_as_a_reference_zip(app):
    await assert_builtin_library_contract(app, "ready", "Breadbin")