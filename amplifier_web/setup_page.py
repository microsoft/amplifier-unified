"""Static, anonymous onboarding page for trusting the app-owned CA."""
from __future__ import annotations

from enum import Enum
from html import escape


class Platform(str, Enum):
    MACOS = "macos"
    WINDOWS = "windows"
    ANDROID = "android"
    IOS = "ios"
    OTHER = "other"


def detect_platform(user_agent: str | None) -> Platform:
    """Classify a user agent without retaining or rendering its contents."""
    value = (user_agent or "").lower()
    if any(token in value for token in ("iphone", "ipad", "ipod")):
        return Platform.IOS
    if "android" in value:
        return Platform.ANDROID
    if "windows" in value:
        return Platform.WINDOWS
    if "macintosh" in value or "mac os x" in value:
        # iPad desktop mode identifies itself as Macintosh. Do not overclaim.
        return Platform.MACOS
    return Platform.OTHER


_INSTRUCTIONS = {
    Platform.MACOS: """
<ol>
  <li>On a trusted host shell, run <code>amplifier-unified setup-tls export &gt; amplifier-unified-ca.crt</code>.</li>
  <li>Transfer it over verified SSH or from local removable storage. Before trusting it, compare its SHA-256 fingerprint with the value printed by <code>amplifier-unified doctor</code> on the trusted host.</li>
  <li>On the Mac, run <code>sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain amplifier-unified-ca.crt</code>.</li>
  <li>Quit and reopen the browser, then return to the configured HTTPS address.</li>
</ol>
<p class="note">An iPad using “Request Desktop Website” can identify as macOS. If this is an iPad, use the iOS/iPadOS section below instead.</p>
""",
    Platform.WINDOWS: """
<ol>
  <li>On a trusted host shell, run <code>amplifier-unified setup-tls export &gt; amplifier-unified-ca.crt</code>.</li>
  <li>Transfer it over verified SSH or local removable storage. Compare its SHA-256 fingerprint with <code>amplifier-unified doctor</code> on the trusted host before trusting it.</li>
  <li>In an elevated PowerShell, run <code>certutil -addstore -f Root amplifier-unified-ca.crt</code>.</li>
  <li>Close and reopen the browser, then return to the configured HTTPS address.</li>
</ol>
""",
    Platform.ANDROID: """
<ol>
  <li>On a trusted host shell, run <code>amplifier-unified setup-tls export &gt; amplifier-unified-ca.crt</code>.</li>
  <li>Transfer it over verified SSH or local removable storage. Compare its SHA-256 fingerprint with <code>amplifier-unified doctor</code> on the trusted host before trusting it.</li>
  <li>Open Settings, search for <strong>Install a certificate</strong>, choose <strong>CA certificate</strong>, and select the verified file.</li>
  <li>Restart the browser. Some Android apps intentionally do not trust user-installed CAs; use the browser for this host.</li>
</ol>
""",
    Platform.IOS: """
<ol>
  <li>On a trusted host shell, run <code>amplifier-unified setup-tls export &gt; amplifier-unified-ca.crt</code>.</li>
  <li>Transfer it over verified SSH or local removable storage. Compare its SHA-256 fingerprint with <code>amplifier-unified doctor</code> on the trusted host before trusting it.</li>
  <li>Open the file on the device, install the downloaded profile in Settings, then enable it in Settings → General → About → Certificate Trust Settings.</li>
  <li>Quit and reopen the browser, then return to the configured HTTPS address.</li>
</ol>
""",
    Platform.OTHER: """
<ol>
  <li>On a trusted host shell, run <code>amplifier-unified setup-tls export &gt; amplifier-unified-ca.crt</code>.</li>
  <li>Transfer it over verified SSH or local removable storage. Compare its SHA-256 fingerprint with <code>amplifier-unified doctor</code> on the trusted host before trusting it.</li>
  <li>Install the verified CA using this operating system’s native trust-store tool. On Debian/Ubuntu, copy it to <code>/usr/local/share/ca-certificates/</code> and run <code>sudo update-ca-certificates</code>.</li>
  <li>Restart the browser. Firefox may use a separate certificate store.</li>
</ol>
""",
}


def render_setup_page(platform: Platform, certificate_available: bool, fingerprint: str | None) -> str:
    """Return the complete setup document from controlled values only."""
    if type(platform) is not Platform:
        raise TypeError("platform must be a Platform value")
    selected = platform.value
    if certificate_available and not fingerprint:
        raise ValueError("A configured CA must have a SHA-256 fingerprint")

    download = (
        '<p><a class="download" href="/ca.crt">Download the app-owned CA certificate</a></p>'
        f'<p><strong>SHA-256 fingerprint:</strong> <code>{escape(fingerprint or "", quote=True)}</code></p>'
        '<p class="warning">The fingerprint shown on this page is not independent proof. '
        'Compare it with <code>amplifier-unified doctor</code> on a trusted local or SSH host before trusting this certificate.</p>'
        if certificate_available
        else '<p class="warning">A local CA is not configured yet. On the trusted host, run '
        '<code>amplifier-unified setup-tls</code>; this page does not create or modify TLS files.</p>'
    )
    sections = "".join(
        f'<details data-platform="{item.value}"{" open" if item is platform else ""}>'
        f"<summary>{label}</summary>{_INSTRUCTIONS[item]}</details>"
        for item, label in (
            (Platform.MACOS, "macOS"),
            (Platform.WINDOWS, "Windows"),
            (Platform.ANDROID, "Android"),
            (Platform.IOS, "iOS and iPadOS"),
            (Platform.OTHER, "Other platforms"),
        )
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Amplifier Unified secure setup</title>
<style>
body {{ color: #17212b; font: 16px/1.5 system-ui, sans-serif; margin: 0; background: #f4f7f9; }}
main {{ background: white; box-sizing: border-box; margin: 2rem auto; max-width: 52rem; padding: clamp(1rem, 4vw, 3rem); }}
h1 {{ line-height: 1.2; }} code {{ overflow-wrap: anywhere; }} details {{ border-top: 1px solid #ccd5dd; padding: .75rem 0; }}
summary {{ cursor: pointer; font-weight: 700; }} .download {{ font-size: 1.1rem; font-weight: 700; }}
.warning {{ border-left: .3rem solid #a95700; padding-left: 1rem; }} .note {{ color: #4a5560; }}
@media (max-width: 35rem) {{ body {{ background: white; }} main {{ margin: 0; }} ol {{ padding-left: 1.4rem; }} }}
</style></head><body><main>
<h1>Trust this host’s local certificate authority</h1>
<p>Use this page only to obtain and verify the app-owned CA for the configured HTTPS host. Do not disable HSTS or browser certificate checks.</p>
{download}
<h2>Choose your device</h2>
{sections}
</main></body></html>"""