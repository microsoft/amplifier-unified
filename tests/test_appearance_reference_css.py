"""Check selected-client source and bundled deltas, not generated dev views.

Canonical authoring is in amplifier-client-web; components/README.md owns refresh.
"""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "components/web-client/src/reference.css"
APPEARANCES = ROOT / "amplifier_web/appearances"
BUILTINS = (
    "default",
    "aurora",
    "atelier",
    "graphite",
    "digital-rain",
    "lightline",
    "ready",
)
PALETTE_TOKENS = {
    "--a-bg",
    "--a-surface",
    "--a-soft",
    "--a-ink",
    "--a-muted",
    "--a-line",
    "--a-accent",
    "--a-on-accent",
    "--a-tint",
    "--a-green",
    "--a-danger",
}
FONT_TOKENS = {
    "--a-font-ui",
    "--a-font-heading",
    "--a-font-chat",
    "--a-font-code",
}


def _balanced_css(source):
    depth = 0
    quote = None
    comment = False
    index = 0
    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""
        if comment:
            if char == "*" and next_char == "/":
                comment = False
                index += 2
                continue
        elif quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
        elif char == "/" and next_char == "*":
            comment = True
            index += 2
            continue
        elif char in {"'", '"'}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                return False
        index += 1
    return not comment and quote is None and depth == 0


def _selectors(source):
    return [
        selector.strip()
        for selector in re.findall(r"(?m)^\s*([^@\s{}][^{}\r\n]*?)\s*\{", source)
    ]


def _luminance(color):
    channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return sum(weight * value for weight, value in zip((0.2126, 0.7152, 0.0722), linear))


def _contrast(first, second):
    low, high = sorted((_luminance(first), _luminance(second)))
    return (high + 0.05) / (low + 0.05)


class AppearanceReferenceCssTests(unittest.TestCase):
    def test_reference_is_neutral_and_owns_shared_accessibility_defaults(self):
        css = REFERENCE.read_text(encoding="utf-8")
        tokens = set(re.findall(r"(?m)^\s*(--[a-z0-9-]+)\s*:", css))
        self.assertTrue(PALETTE_TOKENS <= tokens)
        self.assertTrue(FONT_TOKENS <= tokens)
        self.assertIn("--a-font: var(--a-font-ui);", css)
        self.assertIn("--a-background-art: none;", css)
        self.assertIn("color: var(--a-on-accent);", css)
        self.assertIn("background-color: var(--a-accent);", css)
        self.assertIn("decoration strip to 24px", css)
        self.assertIn("color: var(--a-muted);", css)
        self.assertIn("padding-inline-start: 8px;", css)
        self.assertIn("box-shadow: inset 3px 0 var(--a-accent);", css)
        self.assertIn(":focus-visible", css)
        self.assertIn('[tabindex]:not([tabindex="-1"])', css)
        self.assertIn("#amp-one ::selection", css)
        self.assertIn("@media (forced-colors: active)", css)
        self.assertIn("scrollbar-color:", css)
        self.assertNotIn("amplifier-appearance", css)
        self.assertTrue(_balanced_css(css))

    def test_reference_restores_shell_dialog_and_composer_primitives(self):
        css = REFERENCE.read_text(encoding="utf-8")
        for selector in (
            "html,",
            "#amp-one.a-chat-shell .a-layout {",
            "#amp-one.a-chat-shell .a-conversation {",
            "#amp-one.a-chat-shell .a-messages {",
            "#amp-one .a-overlay {",
            "#amp-one .a-dialog {",
            "#amp-one .a-dialog-head {",
            "#amp-one .a-form-grid {",
            "#amp-one .a-sr-only {",
            "#amp-one.a-chat-shell .a-composer .a-file-input {",
            "#amp-one.a-chat-shell .a-attachments {",
            "#amp-one.a-chat-shell .a-model-popover {",
            "#amp-one :where(svg)",
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, css)
        self.assertIn("scrollbar-gutter: stable;", css)
        self.assertIn("overscroll-behavior: contain;", css)
        self.assertIn("display: none;", css)
        self.assertIn("clip-path: inset(50%);", css)
        layout = re.search(r"\.a-layout\s*\{([^}]+)\}", css).group(1)
        messages = re.search(r"\.a-messages\s*\{([^}]+)\}", css).group(1)
        inline_menu = re.search(r"\.a-model-popover\s*\{([^}]+)\}", css).group(1)
        measured_menu = re.search(r"\.a-model-popover\.a-compact-popover\s*\{([^}]+)\}", css).group(1)
        self.assertIn("position: relative;", layout)
        self.assertIn("display: flex;", messages)
        self.assertIn("flex-direction: column;", messages)
        self.assertIn("position: absolute;", inline_menu)
        self.assertIn("bottom: calc(100% + 12px);", inline_menu)
        self.assertIn("position: fixed;", measured_menu)
        self.assertIn("bottom: auto;", measured_menu)
        self.assertTrue(_balanced_css(css))

    def test_shared_panel_geometry_and_message_footer_do_not_depend_on_a_delta(self):
        css = REFERENCE.read_text(encoding="utf-8")
        presentation = (ROOT / "components/web-client/src/chat-presentation.css").read_text(encoding="utf-8")
        main_refresh = (ROOT / "components/web-client/src/main-refresh.css").read_text(encoding="utf-8")
        self.assertIn('flex: 0 0 var(--nav-width);', css)
        self.assertIn('flex: 0 0 var(--canvas-width);', css)
        self.assertIn('container-name: conversation;', css)
        rail = re.search(r"\.a-nav-rail\s*\{([^}]+)\}", css).group(1)
        self.assertIn('position: absolute;', rail)
        self.assertIn('inset: 0 auto 0 0;', rail)
        self.assertIn('flex-direction: column;', rail)
        footer = re.search(r"\.a-message-actions\{([^}]+)\}", presentation).group(1)
        self.assertIn('display:flex', footer)
        self.assertIn('align-items:center', footer)
        self.assertIn('margin-inline:auto', re.search(r"\.a-composer\{([^}]+)\}", main_refresh).group(1))
        # Paint defaults precede appearance treatment; structure must not repaint
        # a deliberate material such as Lightline's warm user bubble.
        user_body = re.search(r"\.a-user>\.a-message-body\{([^}]+)\}", presentation).group(1)
        self.assertNotIn('background', user_body)
        self.assertIn('#amp-one :where(.a-user > .a-message-body)', css)
        canvas = (ROOT / "components/web-client/src/canvas-workspace.css").read_text(encoding="utf-8")
        self.assertIn('.a-canvas-preview{padding:16px}', canvas)
        self.assertIn('.a-canvas-preview:has(>.a-canvas-html){padding:0}', canvas)
        self.assertRegex(css, r'\.task-list-item\s*\{\s*list-style:\s*none;')
        self.assertRegex(css, r'\.task-list-item > input\[type="checkbox"\]\s*\{\s*margin-inline-end:\s*0\.5em;')

    def test_shared_navigation_and_landing_keep_content_bounded_and_readable(self):
        css = REFERENCE.read_text(encoding="utf-8")
        row = re.search(r'\.a-nav-chat:not\(\.a-nav-chat-rename\), \.a-workspace-row\)\s*\{([^}]+)\}', css).group(1)
        select = re.search(r':where\(\.a-nav-chat-select, \.a-workspace-select\)\s*\{([^}]+)\}', css).group(1)
        self.assertIn('display: flex;', row)
        self.assertIn('min-width: 0;', row)
        self.assertIn('display: flex;', select)
        self.assertIn('flex: 1;', select)
        self.assertIn('min-width: 0;', select)
        navigation = (ROOT / "components/web-client/src/navigation-details.css").read_text(encoding="utf-8")
        rename = re.search(r'\.a-navigation-flyout \.a-nav-chat-rename\{([^}]+)\}', navigation).group(1)
        for declaration in ('display:flex', 'flex-wrap:wrap', 'padding:3px'):
            self.assertIn(declaration, rename)
        landing = (ROOT / "components/web-client/src/adaptive-work.css").read_text(encoding="utf-8")
        self.assertIn('background:transparent', re.search(r'\.a-adaptive-home\{([^}]+)\}', landing).group(1))
        self.assertIn('background:var(--a-surface)', re.search(r'\.a-home-content\{([^}]+)\}', landing).group(1))

    def test_presentation_suppresses_header_art_and_preserves_footer_primaries(self):
        main_refresh = (ROOT / "components/web-client/src/main-refresh.css").read_text(encoding="utf-8")
        presentation = (ROOT / "components/web-client/src/theme-presentation.css").read_text(encoding="utf-8")
        self.assertIn(
            "button:not(.a-primary):not(.a-send){color:var(--a-muted)}",
            main_refresh,
        )
        self.assertIn(
            ':is([data-part="header"],.a-conversation,.a-nav-rail,.a-canvas-chrome)',
            presentation,
        )

    def test_each_builtin_is_a_scoped_palette_and_type_delta(self):
        for name in BUILTINS:
            with self.subTest(appearance=name):
                path = APPEARANCES / f"{name}.amplifier.css"
                self.assertTrue(path.is_file(), path)
                css = path.read_text(encoding="utf-8")
                tokens = set(re.findall(r"(?m)^\s*(--[a-z0-9-]+)\s*:", css))
                self.assertTrue(PALETTE_TOKENS <= tokens)
                self.assertTrue(FONT_TOKENS <= tokens)
                self.assertRegex(css, r"--a-font\s*:\s*var\(--a-font-ui\)")
                self.assertTrue(_balanced_css(css))
                self.assertNotRegex(css, r":nth-(?:child|of-type)|:has\s*\(")
                self.assertNotRegex(css, r"!\s*important\b")
                self.assertNotRegex(css, r"\blabel\b")
                self.assertNotRegex(
                    css,
                    r"\[(?:aria-busy|aria-pressed|data-state|data-running|data-activity)\b",
                )

                for selector in _selectors(css):
                    if selector in {"from", "to"}:
                        self.assertEqual(name, "digital-rain")
                    else:
                        self.assertTrue(selector.startswith("#amp-one"), (name, selector))
                    if ".a-message" in selector:
                        self.assertIn(".a-message-body", selector)
                        self.assertNotIn(".a-message-actions", selector)

                for destination in re.findall(r"\burl\(\s*([^)]*)\s*\)", css, re.I | re.S):
                    value = destination.strip().strip("'\"")
                    self.assertTrue(value.lower().startswith("data:"), value[:100])

    def test_builtin_palette_text_contrast_in_both_schemes(self):
        checks = (
            ("--a-ink", "--a-bg"),
            ("--a-ink", "--a-surface"),
            ("--a-ink", "--a-soft"),
            ("--a-ink", "--a-tint"),
            ("--a-muted", "--a-bg"),
            ("--a-muted", "--a-surface"),
            ("--a-muted", "--a-soft"),
            ("--a-accent", "--a-bg"),
            ("--a-accent", "--a-surface"),
            ("--a-accent", "--a-tint"),
            ("--a-on-accent", "--a-accent"),
            ("--a-green", "--a-bg"),
            ("--a-green", "--a-surface"),
            ("--a-green", "--a-soft"),
            ("--a-danger", "--a-bg"),
            ("--a-danger", "--a-surface"),
            ("--a-danger", "--a-soft"),
        )
        pair_pattern = re.compile(
            r"(--a-[a-z-]+)\s*:\s*light-dark\(\s*(#[0-9a-f]{6})\s*,\s*(#[0-9a-f]{6})\s*\)",
            re.I,
        )
        for name in BUILTINS:
            css = (APPEARANCES / f"{name}.amplifier.css").read_text(encoding="utf-8")
            pairs = {
                match.group(1): (match.group(2), match.group(3))
                for match in pair_pattern.finditer(css)
            }
            with self.subTest(appearance=name):
                self.assertTrue(PALETTE_TOKENS <= pairs.keys())
                for scheme in (0, 1):
                    for foreground, background in checks:
                        self.assertGreaterEqual(
                            _contrast(pairs[foreground][scheme], pairs[background][scheme]),
                            4.5,
                            (name, scheme, foreground, background),
                        )

        for name in ("lightline", "ready"):
            css = (APPEARANCES / f"{name}.amplifier.css").read_text(encoding="utf-8")
            body = re.search(
                r"#amp-one\s+\.a-user\s+\.a-message-body\s*\{([^}]+)\}", css
            ).group(1)
            foreground = re.search(
                r"color\s*:\s*light-dark\(\s*(#[0-9a-f]{6})\s*,\s*(#[0-9a-f]{6})",
                body,
                re.I,
            ).groups()
            background = re.search(
                r"background-color\s*:\s*light-dark\(\s*(#[0-9a-f]{6})\s*,\s*(#[0-9a-f]{6})",
                body,
                re.I,
            ).groups()
            for scheme in (0, 1):
                self.assertGreaterEqual(
                    _contrast(foreground[scheme], background[scheme]),
                    4.5,
                    (name, scheme, "user bubble"),
                )

    def test_digital_rain_art_is_local_and_motion_is_optional(self):
        css = (APPEARANCES / "digital-rain.amplifier.css").read_text(encoding="utf-8")
        self.assertEqual(css.count("data:image/png;base64,"), 3)
        self.assertIn("digital-rain-drift 72s linear infinite", css)
        self.assertIn("@media (prefers-reduced-motion: no-preference)", css)
        self.assertIn("@media (prefers-reduced-motion: reduce)", css)
        self.assertIn('[data-decorations="off"]', css)

    def test_transcript_and_primary_controls_use_separate_shared_roles(self):
        presentation = (ROOT / "components/web-client/src/chat-presentation.css").read_text(encoding="utf-8")
        interactions = (ROOT / "components/web-client/src/message-interactions.css").read_text(encoding="utf-8")
        reference = REFERENCE.read_text(encoding="utf-8")
        self.assertIn("color:var(--a-on-accent)", presentation)
        self.assertIn("color:var(--a-on-accent)", interactions)
        self.assertIn(".a-message-body", reference)
        self.assertIn(".a-message-actions", reference)
        self.assertNotIn(".a-message-actions{", reference)

    def test_superseded_fullskin_source_is_removed(self):
        self.assertFalse((ROOT / "components/web-client/src/unified.css").exists())


if __name__ == "__main__":
    unittest.main()