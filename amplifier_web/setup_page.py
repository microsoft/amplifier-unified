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


_BOOTSTRAP = """
<ol>
  <li><strong>Before using this HTTPS address in a browser</strong>, on the trusted host that already owns the CA, export its existing public certificate: <code>amplifier-unified setup-tls export &gt; amplifier-unified-ca.crt</code>.</li>
  <li>Transfer that file over verified SSH or a local trusted path. For example, after verifying the SSH host, use <code>scp &lt;trusted-host&gt;:/secure/path/amplifier-unified-ca.crt .</code>. Do not obtain the first copy from this page.</li>
  <li>On the device, run <code>openssl x509 -in amplifier-unified-ca.crt -noout -fingerprint -sha256</code> and compare the result character-for-character with <code>amplifier-unified doctor</code> on the trusted host.</li>
</ol>
<p class="warning">The fingerprint displayed here is not independent proof. HSTS has no HTTP fallback: do not disable HSTS, accept a TLS warning, or use a browser workaround to bootstrap trust.</p>
<p class="note">Managed devices may require their approved administrator path. Do not bypass that policy.</p>
"""


_INSTRUCTIONS = {
    Platform.MACOS: """
<ol start="4">
  <li>Open <strong>Keychain Access</strong>, select the <strong>login</strong> keychain, import the verified certificate, then set its SSL trust to <strong>Always Trust</strong>.</li>
  <li>Optional Terminal alternative (no <code>sudo</code>): <code>security add-trusted-cert -r trustRoot -p ssl -k "$HOME/Library/Keychains/login.keychain-db" amplifier-unified-ca.crt</code>.</li>
  <li>Quit and reopen the browser, then return to the configured HTTPS address.</li>
</ol>
<p class="note">An iPad using “Request Desktop Website” can identify as macOS. If this is an iPad, use the iOS/iPadOS section below instead.</p>
""",
    Platform.WINDOWS: """
<ol start="4">
  <li>Open <strong>Manage user certificates</strong> (<code>certmgr.msc</code>) and import the verified certificate into <strong>Trusted Root Certification Authorities</strong> for the current user.</li>
  <li>Optional PowerShell alternative (no elevation): <code>certutil -user -addstore -f Root amplifier-unified-ca.crt</code>.</li>
  <li>Close and reopen the browser, then return to the configured HTTPS address.</li>
</ol>
""",
    Platform.ANDROID: """
<ol start="4">
  <li>Open Settings, search for <strong>Install a certificate</strong>, choose <strong>CA certificate</strong>, and select the verified file.</li>
  <li>Restart the browser. Some Android apps intentionally do not trust user-installed CAs; use the browser for this host.</li>
</ol>
""",
    Platform.IOS: """
<ol start="4">
  <li>Open the verified file on the device, install the downloaded profile in Settings, then enable it in Settings → General → About → Certificate Trust Settings.</li>
  <li>Quit and reopen the browser, then return to the configured HTTPS address.</li>
</ol>
""",
    Platform.OTHER: """
<ol start="4">
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

    certificate_status = (
        '<p>Already using a trusted connection? '
        '<a class="download" href="/ca.crt">Download the existing public CA</a>.</p>'
        f'<p><strong>SHA-256 fingerprint:</strong> <code>{escape(fingerprint or "", quote=True)}</code></p>'
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
<p>Use this page to read the setup instructions for the configured HTTPS host. Certificate installation is never performed here.</p>
<p>Already connected securely? <a href="/setup/terminal">Install the Terminal app on your computer</a> and connect to this service.</p>
<h2>Before browser use</h2>
{_BOOTSTRAP}
{certificate_status}
<h2>Choose your device</h2>
{sections}
</main></body></html>"""
