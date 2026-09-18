from html.parser import HTMLParser

import pytest

from amplifier_web.setup_page import Platform, detect_platform, render_setup_page


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.attrs = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs.extend(attrs)


@pytest.mark.parametrize(
    ("user_agent", "expected"),
    [
        ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)", Platform.MACOS),
        ("Mozilla/5.0 (Windows NT 10.0; Win64; x64)", Platform.WINDOWS),
        ("Mozilla/5.0 (Linux; Android 14)", Platform.ANDROID),
        ("Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X)", Platform.IOS),
        ("Mozilla/5.0 (X11; Linux x86_64)", Platform.OTHER),
    ],
)
def test_detect_platform_uses_only_the_closed_platform_enum(user_agent, expected):
    assert detect_platform(user_agent) is expected


def test_macos_desktop_user_agent_keeps_ipad_ambiguity_honest():
    page = render_setup_page(detect_platform("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"), True, "AA:BB")
    assert 'data-platform="macos" open' in page
    assert "iPad using “Request Desktop Website” can identify as macOS" in page


@pytest.mark.parametrize("platform", list(Platform))
def test_renderer_opens_detected_platform_and_keeps_every_platform_available(platform):
    page = render_setup_page(platform, True, "AA:BB")
    assert page.count("<details ") == len(Platform)
    assert page.count(" open") == 1
    assert f'data-platform="{platform.value}" open' in page


def test_renderer_rejects_unclosed_platform_values_and_does_not_echo_untrusted_values():
    with pytest.raises(TypeError):
        render_setup_page("attacker.example", True, "AA:BB")  # type: ignore[arg-type]

    page = render_setup_page(Platform.OTHER, True, "AA:BB\"><img src=x onerror=alert(1)>")
    assert "attacker.example" not in page
    assert "Mozilla/" not in page
    assert '<img src=x onerror=alert(1)>' not in page
    assert "&lt;img src=x onerror=alert(1)&gt;" in page


def test_renderer_has_fixed_fingerprint_warning_and_no_active_or_login_content():
    page = render_setup_page(Platform.WINDOWS, True, "AA:BB")
    parsed = Tags()
    parsed.feed(page)

    assert "SHA-256 fingerprint:" in page
    assert "not independent proof" in page
    assert not {"script", "iframe", "frame", "form"} & set(parsed.tags)
    assert "password" not in page.lower()
    assert "HSTS" in page
    assert "Certificate installation is never performed here." in page
    assert "/ca.crt" not in page


def test_renderer_instructions_use_trusted_transfer_then_minimal_user_trust():
    page = render_setup_page(Platform.OTHER, True, "AA:BB")

    assert page.index("Before browser use") < page.index("Choose your device")
    assert page.count("setup-tls export") == 1
    assert page.count("verified SSH") == 1
    assert "&lt;trusted-host&gt;" in page
    assert "openssl x509 -in amplifier-unified-ca.crt -noout -fingerprint -sha256" in page
    assert "security add-trusted-cert -r trustRoot -p ssl -k \"$HOME/Library/Keychains/login.keychain-db\"" in page
    assert "Keychain Access" in page
    assert "System.keychain" not in page
    assert "sudo security" not in page
    assert "certutil -user -addstore -f Root amplifier-unified-ca.crt" in page
    assert "elevated PowerShell" not in page
    assert "Managed devices may require their approved administrator path" in page
    assert "Install a certificate" in page
    assert "Certificate Trust Settings" in page
    assert "update-ca-certificates" in page
    assert page.count("reopen the browser") == 3
    assert "Restart the browser" in page
    assert "disable HSTS" in page
    assert "http://" not in page


def test_renderer_ca_absent_state_stays_useful():
    page = render_setup_page(Platform.OTHER, False, None)
    assert "not configured yet" in page
    assert "setup-tls" in page
    assert "/ca.crt" not in page


async def test_renderer_fits_narrow_and_wide_chromium_viewports():
    playwright = pytest.importorskip("playwright.async_api")
    async with playwright.async_playwright() as driver:
        try:
            browser = await driver.chromium.launch(headless=True)
        except playwright.Error as exc:
            pytest.skip(f"Chromium is unavailable: {exc}")
        try:
            for width in (320, 1280):
                page = await browser.new_page(viewport={"width": width, "height": 800})
                await page.set_content(render_setup_page(Platform.IOS, True, "AA:BB"))
                assert await page.locator('details[data-platform="ios"][open]').count() == 1
                assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                await page.screenshot()
                await page.close()
        finally:
            await browser.close()