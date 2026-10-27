"""Static, anonymous onboarding page for trusting the app-owned CA."""
from __future__ import annotations

from enum import Enum
from html import escape


class Platform(str, Enum):
    MACOS = "macos"
    WINDOWS = "windows"
    ANDROID = "android"
    IOS = "ios"
    LINUX = "linux"
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
    if "linux" in value:
        return Platform.LINUX
    return Platform.OTHER


_BOOTSTRAP = """
<ol>
  <li><strong>Before using this HTTPS address in a browser</strong>, on the trusted host that already owns the CA, export its existing public certificate: <code>amplifier-unified setup-tls export &gt; amplifier-unified-ca.crt</code>.</li>
  <li>Transfer that file over verified SSH or a local trusted path. For example, after verifying the SSH host, use <code>scp &lt;trusted-host&gt;:/secure/path/amplifier-unified-ca.crt .</code>. Do not obtain the first copy from this page.</li>
  <li>On the device, run <code>openssl x509 -in amplifier-unified-ca.crt -noout -fingerprint -sha256</code> and compare the result character-for-character with <code>amplifier-unified setup-tls status</code> on the trusted host.</li>
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
  <li>Install the verified CA using this operating system’s approved trust-store tool. For Ubuntu or Debian, use the Linux section.</li>
  <li>Check your browser’s certificate settings; browser trust can differ from operating-system trust.</li>
</ol>
""",
    Platform.LINUX: """
<p>These commands are for <strong>Ubuntu and Debian</strong>, on the computer running your browser, not the Unified server. Other Linux distributions need their own approved trust-store procedure. WSL trust does not configure a Windows browser.</p>
<p>Use a normal-user terminal with administrator access through <code>sudo</code>. The tools <code>openssl</code>, <code>install</code> and <code>update-ca-certificates</code> must be available (Ubuntu/Debian packages <code>openssl</code>, <code>coreutils</code> and <code>ca-certificates</code>). Ask your administrator if they are missing or policy blocks installation.</p>
<ol start="4">
  <li>Set the exact path of the public CA obtained through the trusted transfer above. For a browser download on an already trusted connection:
<pre><code id="linux-ca-file">ca_file="$HOME/Downloads/amplifier-unified-ca.crt"</code></pre>
If the browser saved a duplicate, use this <strong>instead</strong>:
<pre><code id="linux-ca-duplicate">ca_file="$HOME/Downloads/amplifier-unified-ca (1).crt"</code></pre>
Keep the quotes around spaces and parentheses. Adjust the directory if needed; do not use a wildcard or select an arbitrary downloaded certificate.
<pre><code id="linux-ca-fingerprint">openssl x509 -in "$ca_file" -noout -subject -issuer -fingerprint -sha256</code></pre></li>
  <li><strong>Stop and compare the SHA-256 fingerprint</strong> with <code>amplifier-unified setup-tls status</code> run on the intended Unified host through a trusted console or verified SSH connection. Use that host’s actual data directory if non-default. This page and the downloaded file are not independent proof. Do not install if the fingerprints differ or the CA’s origin is uncertain. A trusted root can authenticate other sites, not just Unified.</li>
  <li>Only after that comparison, install this one verified PEM certificate into the <strong>system</strong> trust store. The destination starts with <code>/usr</code>, <strong>not <code>~/usr</code></strong> (which means a folder under your home). Use the same terminal:
<pre><code id="linux-ca-install">ca_dest=/usr/local/share/ca-certificates/amplifier-unified-ca.crt
if [ -e "$ca_dest" ] || [ -L "$ca_dest" ]; then
  printf '%s\\n' "Stop: $ca_dest already exists; ask your administrator to check it." &gt;&amp;2
  false
elif [ "$(grep -c -- '-----BEGIN ' "$ca_file")" != 1 ] ||
     [ "$(grep -c -- '-----END ' "$ca_file")" != 1 ]; then
  printf '%s\\n' "Stop: expected exactly one PEM certificate, not a bundle." &gt;&amp;2
  false
else
  sudo install -d -m 0755 /usr/local/share/ca-certificates &amp;&amp;
  sudo install -m 0644 -- "$ca_file" "$ca_dest" &amp;&amp;
  sudo update-ca-certificates
fi</code></pre>
This uses a stable <code>.crt</code> filename and readable public-certificate permissions. An existing destination is not overwritten; another host’s CA needs a distinct name. Stop on any command error.</li>
  <li>Check OS trust for this CA, using the generated certificate bundle:
<pre><code id="linux-ca-verify">openssl verify -CAfile /etc/ssl/certs/ca-certificates.crt "$ca_file"</code></pre>
Expect the selected filename followed by <code>OK</code>. OpenSSL can also consult its configured default trust locations. This checks OS trust for the CA, not the remote server’s hostname, connection, or browser trust.</li>
  <li>Save your browser work, then fully quit and reopen it yourself. Open the intended HTTPS address and confirm there is no certificate warning before signing in. <strong>Firefox, Chromium, Chrome and Edge on Linux can use different certificate stores</strong>, depending on version, packaging, profile and policy (including confined Snap packages). OS success is not proof of browser success.</li>
</ol>
<p><strong>Still “Not secure”?</strong> Open the browser’s connection/certificate details and read the exact error before importing anything again. If the browser says security warnings were turned off for this site, save any unsent work, turn warnings back on, and reload without proceeding past a warning. Check that the served certificate belongs to the intended host and chains to the verified CA. Match the URL hostname against the certificate’s Subject Alternative Name (SAN), not just its Common Name (CN). A hostname mismatch needs the correct URL or a server-certificate repair; a date error needs a clock/validity check. Neither is fixed by another CA import.</p>
<p>If the error is an untrusted issuer and the host/CA identity checks match, inspect the browser’s certificate manager for that verified CA. Firefox has a separate Authorities store; Chromium/Chrome and Edge versions can expose different local-certificate controls. Use the installed browser’s certificate settings or your administrator’s approved procedure to import the same verified CA for website trust. Do not disable verification, bypass the warning, or run NSS/certutil commands against a guessed profile/database.</p>
<p class="note">A rehash warning about <code>ca-certificates.crt</code> containing more than one certificate normally refers to the generated multi-certificate bundle. It does not prove your CA or browser is correct. A warning naming your individual CA file needs investigation: each local <code>.crt</code> file must contain exactly one PEM certificate.</p>
<p><strong>Undo only this installation:</strong> if you installed the new file at the exact destination above, remove only that file and rebuild the bundle. Do not remove a pre-existing CA or use a wildcard.</p>
<pre><code id="linux-ca-remove">sudo rm -- /usr/local/share/ca-certificates/amplifier-unified-ca.crt &amp;&amp;
sudo update-ca-certificates --fresh</code></pre>
<p>Any separate browser import must also be removed from that browser’s certificate settings. Save work before restarting it.</p>
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
            (Platform.LINUX, "Linux — Ubuntu and Debian"),
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
pre {{ white-space: pre-wrap; overflow-wrap: anywhere; padding: .75rem; background: #f4f7f9; }}
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
