# Installation and deployment

This guide covers manual installation and making Unified available on a LAN or Tailscale network, including service hosting, TLS trust, and Linux service management. The recommended installation path is in the [README](../README.md).

## Manual installation

The [README installer](../README.md#install) is the recommended path and selects
the current stable release. Use the following instructions when you want to
manage prerequisites and package installation yourself. These versioned examples
pin `v0.20.101`; choose another published release tag deliberately when needed.

Install [uv](https://docs.astral.sh/uv/) and Git, then select Python **3.13**
explicitly, as the installer does. A newer system Python may lack prebuilt
dependency wheels. uv can manage the selected Python without replacing your
system Python. Run installation and launch as your normal user.

### Linux

```sh
uv tool install --python 3.13 git+https://github.com/microsoft/amplifier-unified@v0.20.101
amplifier-unified
```

### Windows through WSL

Use a Linux distribution in WSL with systemd enabled. Add this to `/etc/wsl.conf`
in that distribution:

```ini
[boot]
systemd=true
```

Restart WSL with `wsl --shutdown` from Windows PowerShell, reopen the distribution,
then run:

```sh
uv tool install --python 3.13 git+https://github.com/microsoft/amplifier-unified@v0.20.101
amplifier-unified
```

This is WSL installation, not native Windows support. Open the app address from
your Windows browser and sign in with the account running the app in WSL.

### macOS

```sh
uv tool install --python 3.13 git+https://github.com/microsoft/amplifier-unified@v0.20.101
amplifier-unified
```

The first launch sets up the per-user background service and opens the browser;
later launches reuse it. Use `amplifier-unified serve` to run in the current
terminal instead. Package installation alone does not register a service.

### Advanced repair

For a broken installation that needs the latest recovery command, the existing
source recovery path is:

```sh
uv tool install --force --python 3.13 git+https://github.com/microsoft/amplifier-unified@main
amplifier-unified reset --dry-run
```

Inspect the preview before running `amplifier-unified reset`. This source-branch
repair path is separate from normal stable-release installation and updates.
Read [the reset guide](RESET.md) for what is preserved and other recovery options.

## Before you start

Unified defaults to `127.0.0.1:8941`. Remote access requires:

- an exact HTTPS origin for every address people will use;
- a certificate trusted on every client device (trust Unified's local CA when using `setup-tls`); and
- a non-loopback bind address.

Use an already trusted local console or SSH connection to configure the host. Do not disable certificate checks or enter credentials through an untrusted HTTPS connection.

## Configure the host

Replace the examples with the hostname and IP address clients will use:

```sh
amplifier-unified config set public_origins '["https://your-host.tailnet.ts.net:8941"]'
amplifier-unified setup-tls
amplifier-unified config set bind '["100.64.0.10", "127.0.0.1"]'
amplifier-unified doctor
```

The private server configuration is stored at `config/server.yaml` under Unified's data directory, normally `~/.amplifier-unified`. Non-loopback binds do not start until TLS and at least one exact HTTPS public origin are configured.

Additional top-level settings from local tooling or other versions do not prevent startup. Unified preserves them when saving ordinary configuration changes and does not activate features it does not implement. Known settings, including TLS, public origins and worker retention, retain their validation. Loading an existing configuration does not rewrite its file; an explicit `config reset all` resets the complete configuration.

`setup-tls` creates Unified's local CA and a leaf certificate. Run `setup-tls force`
after changing an address that must appear in the certificate or to renew an
expiring leaf; it keeps the CA and replaces the leaf certificate. Restart the
service afterward. An expired/expiring CA needs a deliberate CA rotation and
redistribution of client trust, not `setup-tls force`, which preserves that CA.

`doctor` inspects the configured certificate's validity (including a 30-day
expiry warning), SAN coverage of every explicit bind and public-origin host,
and chain against the app CA or this computer's system roots for supplied
certificates. IP addresses require IP SANs; DNS wildcards cover one label only.
Wildcard binds are not certificate identities. Include each intended LAN and
Tailscale name/IP in `public_origins` before renewing an app-owned leaf.
These are offline checks of configured files, without DNS queries or credentials:
they do not prove which certificate the running process serves, network access,
or trust on another device. Supplied certificates should be renewed with their
issuer, not replaced with an app-owned CA. Native Windows certificate diagnostics
and authenticated service/process qualification remain pending.

To use an existing trusted certificate and its private key, keep the bind on
loopback while configuring the pair, then enable the remote bind:

```sh
amplifier-unified config set tls.method ca
amplifier-unified config set public_origins '["https://your-host.example:8941"]'
amplifier-unified config set tls.cert '/absolute/path/server.crt'
amplifier-unified config set tls.key '/absolute/path/server.key'
amplifier-unified config set bind '["192.0.2.10", "127.0.0.1"]'
```

The certificate and key commands may be entered in either order. Until both are
configured, HTTPS cannot start and a non-loopback bind is refused. Invalid
configuration changes leave the previous saved configuration intact. To replace
an existing pair together, use one atomic setting:
`amplifier-unified config set tls '{method: ca, cert: /absolute/path/server.crt, key: /absolute/path/server.key}'`.
Starting the server also verifies that the certificate and key files can be loaded
as a matching pair; saving their paths alone is not a successful TLS check.

`amplifier-unified setup-tls status` reports the certificate/key readiness
separately from the availability of Unified's local CA. A supplied certificate
can be ready even when no app-owned CA exists to export. This check does not
establish that a client trusts the certificate, or that it covers the public
hostname.

Headless commands such as `access` use Unified's local CA for the app-owned
certificate pair, and normal system/environment trust for supplied certificates.
A supplied private issuer must already be trusted by the account running the
command. Configured HTTPS origins retain certificate and hostname verification.
If verification fails and no other configured origin reaches the matching host,
the command reports that failure instead of starting a temporary local host.
An unreachable host without a certificate failure keeps the ordinary local fallback.

If Unified uses a non-default data directory, pass the same `--data-dir` value to `setup-tls` and `doctor`.

## Sign in with the installing host owner's account

On Unix, the default login checks PAM for the account running Unified. An
isolated service can instead use the installing human's existing host account
through the [Constellation owner-login bridge](https://github.com/microsoft/amplifier-constellation-instance/blob/main/ops/container/README.md).
This keeps the service's OS identity, data directory and conversation ownership
separate from the account checking the password.

Run the canonical bridge as the intended host owner, with a private installation
configuration and socket directory. Expose **only its `owner-login.sock`** through
the container runtime's private Unix-socket forwarding. The guest socket must be
owned by root or the Unified service account, with no group/other permissions,
inside a private directory. Do not mount the host password database, copy a
password, expose this socket on TCP, or share another installation's bridge.
No optional bridge TCP routes are needed for login.

Configure the existing Unified installation, then restart it at a safe boundary:

```sh
amplifier-unified --data-dir /path/to/instance config set host_pam '{owner: your-host-account, socket: /run/your-installation/owner-login.sock}'
```

The normal HTTPS login page accepts that one host username and its normal PAM
password. The canonical bridge applies host authentication, account policy and
attempt limits. A missing, unavailable or rejecting bridge denies the login;
there is no fallback to the guest password. The app never saves or logs the
submitted password. TLS, Origin, CSRF, signed-session cookies and control-token
rules remain unchanged. This grants access to the same single-owner installation;
it does not create a multi-user workspace, impersonate the user for tools, or
change filesystem/agent permissions.

Set `host_pam` to `null` and restart to restore the default process-owner PAM
login. Windows keeps its existing native sign-in and does not accept this option.
Existing browser sessions retain their normal lifetime through this setting
change; changing the login backend is not a session-revocation operation.

## Trust the CA on each client

On the Unified host, export the public CA and record the fingerprint:

```sh
amplifier-unified setup-tls export > amplifier-unified-ca.crt
amplifier-unified setup-tls status
```

Transfer the CA over an already verified connection, then verify it before trusting it:

```sh
scp '<owner>@<host>:/absolute/path/amplifier-unified-ca.crt' .
openssl x509 -in amplifier-unified-ca.crt -noout -fingerprint -sha256
```

Compare that fingerprint with the trusted host's `amplifier-unified setup-tls status` output. Use the same data directory as the running host if non-default. On macOS, use Keychain Access's **login** keychain and trust it for SSL. Windows can use the current user's Trusted Root Certification Authorities store. Follow your organization's approved process for mobile-device certificate trust.

The `/setup` page can provide platform-specific follow-up instructions after TLS is trusted. Its displayed fingerprint or an anonymous certificate download is not independent proof of authenticity.

### Ubuntu and Debian clients

Run these steps on the computer running the browser, not the Unified server.
WSL trust does not configure a Windows browser. Other Linux distributions need
their own approved procedure. Use a normal-user terminal with `sudo` access and
the `openssl`, `coreutils` and `ca-certificates` packages. If tools are missing or
policy blocks installation, ask your administrator; the page installs nothing.

1. Set the exact public CA path from the trusted transfer. For a download from an
   **already trusted** connection:

```sh
ca_file="$HOME/Downloads/amplifier-unified-ca.crt"
```

If the browser saved a duplicate, use this **instead**:

```sh
ca_file="$HOME/Downloads/amplifier-unified-ca (1).crt"
```

Keep the quotes around spaces and parentheses. Adjust the directory if needed;
do not use a wildcard or select an arbitrary downloaded certificate.

```sh
openssl x509 -in "$ca_file" -noout -subject -issuer -fingerprint -sha256
```

2. **Stop and compare the SHA-256 fingerprint** with `amplifier-unified setup-tls
   status` on the intended host, through a trusted console or verified SSH. Do
   not install if the fingerprints differ or origin is uncertain. A root CA can
   authenticate other sites, not just Unified.
3. After verification, install the single PEM certificate using the same terminal.
   The destination is absolute `/usr/...`, **not `~/usr/...`** under your home:

```sh
ca_dest=/usr/local/share/ca-certificates/amplifier-unified-ca.crt
if [ -e "$ca_dest" ] || [ -L "$ca_dest" ]; then
  printf '%s\n' "Stop: $ca_dest already exists; ask your administrator to check it." >&2
  false
elif [ "$(grep -c -- '-----BEGIN ' "$ca_file")" != 1 ] ||
     [ "$(grep -c -- '-----END ' "$ca_file")" != 1 ]; then
  printf '%s\n' "Stop: expected exactly one PEM certificate, not a bundle." >&2
  false
else
  sudo install -d -m 0755 /usr/local/share/ca-certificates &&
  sudo install -m 0644 -- "$ca_file" "$ca_dest" &&
  sudo update-ca-certificates
fi
```

The stable `.crt` destination holds exactly one PEM certificate, readable by
applications. Do not overwrite an existing CA; another host needs a distinct
destination. Stop on any error. A rehash warning about the generated
`ca-certificates.crt` bundle containing multiple certificates is normally benign,
but a warning naming your individual CA file needs investigation.

4. Check OS trust:

```sh
openssl verify -CAfile /etc/ssl/certs/ca-certificates.crt "$ca_file"
```

Expect the selected filename followed by `OK`. OpenSSL can also consult its
configured default trust locations. This checks OS trust for the CA, not the
server's hostname/connection or the browser's trust.

5. Save browser work, fully quit and reopen the browser yourself, and open the
   intended HTTPS URL. Confirm there is no certificate warning before signing in.
   Firefox, Chromium/Chrome and Edge can use different stores depending on version,
   packaging, profile and policy. Confined Snap packages may not inherit OS trust.
   OS success is **not** proof of browser success.

If the browser still says **Not secure**, read its connection/certificate details
before importing anything again. If warnings were turned off for this site,
save unsent work, turn warnings back on and reload without proceeding past a
warning. Check the served host and chain against the verified CA. Match the
URL hostname against the Subject Alternative Name (SAN), not just the Common
Name (CN). A hostname mismatch needs the correct URL or server-certificate
repair; a date error needs a clock/validity check, not another CA import. For an
untrusted issuer with matching host/CA identities, inspect the installed browser's
certificate manager and follow its local-CA/Authorities import procedure or your
administrator's policy. Firefox has a separate Authorities store. Do not disable
verification, bypass warnings or run NSS/certutil against a guessed database.
Chromium's documented Linux NSS location has changed across versions; it is not
evidence of the location used by a particular Edge installation.

**Back out only your new installation**, after checking that this exact path is
the file you installed (not a pre-existing CA):

```sh
sudo rm -- /usr/local/share/ca-certificates/amplifier-unified-ca.crt &&
sudo update-ca-certificates --fresh
```

Remove any separately imported browser copy in that browser's certificate
settings too. OS removal alone does not prove browser removal.

References: [Ubuntu trust-store guide](https://ubuntu.com/server/docs/how-to/security/install-a-root-ca-certificate-in-the-trust-store/),
[Debian update-ca-certificates](https://manpages.debian.org/testing/ca-certificates/update-ca-certificates.8.en.html),
[Chromium Linux certificate management](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/docs/linux/cert_management.md),
[Firefox certificate policies](https://firefox-admin-docs.mozilla.org/reference/policies/certificates/),
and [Edge root-store policy history](https://learn.microsoft.com/en-us/deployedge/microsoft-edge-policies/microsoftrootstoreenabled).
The obsolete Edge root-store policy is not a workaround.

## Windows host: reach Unified from another device

A Windows host does not sign you in with the password of your Windows account.
On the PC, the launcher opens the browser. A browser on another device signs
in with a password that you set in Amplifier on the PC. Remote access is off
until you do the steps below, and you can turn it off again. Local use on the
PC needs none of this.

The steps use Tailscale. Amplifier listens only on this PC's own address,
`127.0.0.1`, and a Tailscale forwarding rule sends connections from your other
device to it. For a LAN address without Tailscale, see
[LAN without Tailscale](#lan-without-tailscale).

### Before you start on a Windows host

- Use **Command Prompt** (`cmd.exe`) for every command in this section. The
  commands are written for it, and it saves the certificate file as plain
  text. Windows PowerShell 5.1 saves that file as UTF-16, and another device
  may not accept it.
- If Amplifier was set up with the Windows setup file, `amplifier-unified` is
  not on the PATH. Use this full command in place of `amplifier-unified` in
  every Amplifier command of this section, also in the two checks below:

  ```
  "%LOCALAPPDATA%\Amplifier\tools\amplifier-unified\Scripts\amplifier-unified.exe" --data-dir "%LOCALAPPDATA%\Amplifier\app" <command>
  ```

  Stop and start Amplifier there with **Stop Amplifier** and **Amplifier
  Unified** in the Start menu.
- With another kind of installation, pass the same `--data-dir` value to every
  Amplifier command if Amplifier uses a non-default data directory.
- Sign-in with a password that you set in the app needs version 0.130.0 or a
  later one. Sign-in with a saved key needs version 0.128.0 or a later one.
  Version 0.131.0 removed the one-use code of earlier versions
  (`amplifier-unified browsers code`).
  `amplifier-unified --version` shows the version.
- `<port>` is the port that Amplifier uses. `amplifier-unified config get port`
  shows it. Type the number in place of `<port>`, without the angle brackets.
  Do the same for every other `<...>` in the commands.
- Type each list in square brackets exactly as shown, with no space inside it.
- Tailscale is installed on the PC and on the other device, and both are
  connected to the same private Tailscale network (tailnet).
- `<pc>.<tailnet>.ts.net` is the Tailscale name of the PC.
  `tailscale status --json --peers=false` shows it as `DNSName`, with a dot at
  the end that you leave out. The other device finds the PC by this name
  through MagicDNS. If the name does not open on the other device, check that
  MagicDNS is on for the tailnet
  ([Tailscale: MagicDNS](https://tailscale.com/docs/features/magicdns)).

### Turn remote access on

Do steps 1 to 4 one after the other. After step 1, Amplifier cannot start
again until step 3 is done: its certificate does not name the new address yet.
If Amplifier does not start in between, do step 3 and start it again. To go
back instead, set the lists back and then start Amplifier. With Tailscale, run
the command of step 2 of [Turn remote access off](#turn-remote-access-off).
With a LAN address, run the command of step 3 of
[LAN without Tailscale](#lan-without-tailscale): it sets both lists back.

1. Add the Tailscale address **after** the local address. The first address in
   the list is the main one: when a connected service sends you back to
   Amplifier after its sign-in, Amplifier uses the first address by default.

   ```
   amplifier-unified config set public_origins "[https://127.0.0.1:<port>,https://<pc>.<tailnet>.ts.net:<port>]"
   ```

2. Stop Amplifier.
3. Make a certificate that includes the new address.

   ```
   amplifier-unified setup-tls force
   ```

4. Start Amplifier and open it on the PC. If Amplifier was set up with the
   Windows setup file, Amplifier shows a message about the new certificate.
   Windows then asks whether to install it. Select **Yes**. Windows then asks
   whether to delete the earlier certificate, which Amplifier no longer uses.
   Select **Yes** there too. If Windows does not ask the second question, see
   [Certificates that `setup-tls force` replaced](#certificates-that-setup-tls-force-replaced).
   With another kind of installation no question shows: the certificate
   authority stays the same. Do not continue past a certificate warning in
   the browser.
5. Add the Tailscale forwarding rule.

   ```
   tailscale serve --bg --tcp=<port> tcp://127.0.0.1:<port>
   ```

6. Save the certificate in a file. Command Prompt saves the file in the folder
   that its prompt shows, usually `C:\Users\<your name>`.

   ```
   amplifier-unified setup-tls export > amplifier-unified-ca.crt
   ```

7. Move the file to the other device and trust it there (see
   [Trust the CA on each client](#trust-the-ca-on-each-client)).

### Sign in with a password

You set the password once in Amplifier on the PC. Then you type it on any
device, also when you are not at the PC.

1. On the PC, open Amplifier.
2. Open Settings, then **Advanced**, then **Performance & runtime**, then
   **Install app**.
3. Select the link **Devices & apps**. It opens a new page.
4. Select **Set a password**.
5. Type a password of at least 12 characters, two times. Select
   **Set password**.
6. On the other device, open `https://<pc>.<tailnet>.ts.net:<port>`. Type the
   password in the field **Password**. Select **Sign in**.

A shorter way in place of steps 2 to 4: in the browser on the PC that shows
Amplifier, open the address of that window with `/connect/sign-in` after the
port, for example `https://127.0.0.1:<port>/connect/sign-in`.

Until a password is set, the sign-in page on another device has no field. It
says that no password is set.

The password works at every address for other devices, as often as you need
it. Amplifier must be running. It uses a new address only after the steps of
[Turn remote access on](#turn-remote-access-on): the certificate must name the
address, and Amplifier must start again. Everyone who has the password and can
reach one of those addresses can sign in. Do not use a password that you use
for something else.

- To change or remove the password, open the same page on the PC. A change
  counts at once. Browsers that are signed in stay signed in.
- The page works only in a browser on the PC that signed in there. A browser
  that signed in with the password or a key cannot set the password.
- Wrong passwords are slowed down. After five wrong passwords in a row,
  Amplifier checks no password for a time: 2 seconds at first, and two times
  as long after each further wrong password, to 5 minutes at most. During
  that time it does not check the right password either. One hour with no
  wrong password, the right password, or a change of the password on the PC
  starts the count again. The wait is the same for every device. So a person
  on your network who sends wrong passwords can keep you from signing in
  from another device. That person gets in no faster. Amplifier on the PC
  itself still works then. A key from a command has no such wait (see the
  next section).
- [Sign out all browsers from the PC](#sign-out-all-browsers-from-the-pc) also
  removes the password.
- If another person may have the password, sign out all browsers first. Then
  set a new password. A new password alone does not sign out a browser that
  signed in with the earlier one.

### Sign in with a key from a command

In place of a password of your own, a command can make a random key. Amplifier
keeps one of the two: a new key replaces the password, and a new password
replaces the key. A key has no wait after wrong keys, because nobody can guess
it.

1. On the PC, make a key.

   ```
   amplifier-unified browsers key
   ```

   The command shows a key such as `ABCDE-FGHJK-LMNPQ-RSTUV-WXYZ2`, once.
   Amplifier saves only a hash of the key and cannot show the key again.
2. Save the key, for example in a password manager.
3. On the other device, open the address of the PC. Type or paste the key in
   the field **Password**. Select **Sign in**.

The key works as a password does: at every address for other devices, as often
as you need it, while Amplifier runs. Everyone who has the key and can reach
one of those addresses can sign in.

- To replace the key, run the command again. The earlier key stops working.
  Browsers that are signed in stay signed in.
- To remove the key without a new one, run
  `amplifier-unified browsers key --remove`.
- [Sign out all browsers from the PC](#sign-out-all-browsers-from-the-pc) also
  ends the key.
- If another person may have the key, sign out all browsers first. Then make a
  new key. A new key alone does not sign out a browser that signed in with the
  earlier key.

### Sign out all browsers from the PC

1. On the PC, sign out every browser, on the PC and on other devices.

   ```
   amplifier-unified browsers sign-out
   ```

2. Wait a few seconds. Until then, a new sign-in on the PC, a new password
   or a new key opens nothing.
3. To use Amplifier again, open it on the PC. For another device, set a new
   password there.

If the command says that it could not confirm the sign-out, run it again.
The command also removes the saved password or sign-in key. If it says that
it could not confirm that removal, run
`amplifier-unified browsers key --remove`. There is no sign-out for one
device. More: [Sign out all browsers](#sign-out-all-browsers).

### Turn remote access off

1. Remove the Tailscale forwarding rule. From this moment no device reaches
   Amplifier through Tailscale.

   ```
   tailscale serve --tcp=<port> off
   ```

2. Remove the Tailscale address.

   ```
   amplifier-unified config set public_origins "[https://127.0.0.1:<port>]"
   ```

3. Stop Amplifier. Then start it again. Amplifier now refuses the Tailscale
   address, and the password opens nothing from another device.
4. Sign out all browsers, so that no sign-in from another device stays valid
   if you turn remote access on again later. This also signs out the browser
   on this PC.

   ```
   amplifier-unified browsers sign-out
   ```

5. Wait a few seconds. Then open Amplifier again on the PC.

The certificate still names the Tailscale address. That gives nothing to
anyone: Amplifier refuses the address. For a certificate without it, stop
Amplifier, run `amplifier-unified setup-tls force` and start Amplifier. On a PC
that was set up with the Windows setup file, Windows then asks about the
certificates again, as in step 4 of
[Turn remote access on](#turn-remote-access-on).

### Certificates that `setup-tls force` replaced

This is for a PC that was set up with the Windows setup file. Each
`amplifier-unified setup-tls force` there makes a new certificate named
"Amplifier Unified (this PC only)". Windows keeps the earlier one in your list
of trusted root certificates until it is deleted. Nobody can use an earlier
certificate: no signing key exists for it, and it names only addresses of this
PC.

The Windows questions about an earlier certificate come from the Start-menu
entries of the PC. A PC whose Start-menu entries are from a setup file that did
not have these questions yet does not ask them. The end of this section says
what stays on such a PC.

- `setup-tls force` saves the certificate that it replaces in the file
  `replaced-roots.pem`, in the folder `app\config\tls` of the installation.
  The file holds certificates only, no key.
- When you open Amplifier after `setup-tls force`, Windows asks one question
  for the new certificate and one question for each earlier certificate that
  it still holds. That is normally two questions. Amplifier's message before
  them gives the number of questions. It gives the thumbprint of the new
  certificate and of up to five earlier ones.
- **No** to a deletion keeps that certificate. Windows asks about it again
  after your next **Yes** to install Amplifier's certificate, for example
  after the next `setup-tls force`. It also asks at an uninstall.
- When you uninstall Amplifier, Windows asks one question for each of
  Amplifier's certificates that it holds: the current one and each earlier one.
  That is normally one question. If there are more, Amplifier's first question
  gives the number.
- Amplifier asks Windows to delete only a certificate that this file names,
  that has the fixed form of Amplifier's own certificate, and that Windows
  holds exactly. It never finds a certificate by its name, and it never asks
  about another certificate.
- The file never loses a certificate that Windows may still hold. It has room
  for about 50. If it is full, `setup-tls force` first drops the ones that
  Windows no longer holds. If it is still full, the command makes no new
  certificate and says so. Delete earlier certificates with the steps below,
  then run the command again.

**To see which certificates are Amplifier's**, run this. Use the full command
of [Before you start on a Windows host](#before-you-start-on-a-windows-host)
in place of `amplifier-unified`.

```
amplifier-unified setup-tls status
```

On a PC that was set up with the Windows setup file, the output ends like
this:

```
Thumbprint in Windows (SHA-1): AE04AE23 D93C087C D02B5D4F 20FF6449 23D284D5
Earlier certificates that Windows still holds: 1
  F1B8CACF 2815A074 8B2F3F3A B793CFB2 41A5ECAA
```

The first line is the certificate that Amplifier uses now. Keep it. Each line
below the count is an earlier certificate of this installation that Windows
still holds. You can delete those, and only those.

**To delete an earlier certificate yourself:**

1. Press the Windows key and **R**, type `certmgr.msc` and press Enter.
2. Open **Trusted Root Certification Authorities**, then **Certificates**.
3. Double-click a certificate named "Amplifier Unified (this PC only)". Select
   the **Details** tab and look at **Thumbprint**. Windows can show the 40
   characters in small letters and without spaces.
4. If that thumbprint is on a line below "Earlier certificates that Windows
   still holds" in the output above, close the window and delete the
   certificate. Windows asks you to confirm.
5. Do steps 3 and 4 for each certificate of that name.

Leave every other certificate, also one of the same name. Do not delete a
certificate because of its name. Do not delete one because the command shows
no thumbprint, or because it says that it could not read something: nothing is
then known about that certificate.

A certificate of that name that is on no such line can be the one that an
Amplifier installation uses now, in this folder or in another folder of your
account. It can also be one that a version before this change replaced. No
file names those, and Amplifier cannot tell the two apart. A certificate that
stays does no harm: no signing key exists for it, and it names only addresses
of this PC.

Two kinds of earlier certificate stay after `setup-tls force`:

- one that a version before this change replaced. No file names it, and the
  steps above do not delete it.
- one on a PC whose Start-menu entry is from a setup file before this change.
  The file names it, and Windows asks only the first question. The status
  command lists it, so the steps above delete it. An uninstall with a later
  Start-menu entry also asks about it.

If you deleted the certificate that Amplifier uses now, open Amplifier from the
Start menu. Windows asks whether to install it again. Select **Yes**.

### LAN without Tailscale

These steps are not measured on a Windows host. They differ from the steps
above in three places.

1. In step 1 of "Turn remote access on", use the LAN address of the PC, and
   add it to `bind` in the same command. Keep `127.0.0.1` first in both lists:
   the PC itself uses it.

   ```
   amplifier-unified config set public_origins "[https://127.0.0.1:<port>,https://<LAN address>:<port>]" bind "[127.0.0.1,<LAN address>]"
   ```

   Use a LAN address that does not change. If the PC gets another address,
   Amplifier does not start; do these steps again with the new address.
2. In place of step 5, an administrator adds a Windows Firewall rule that lets
   TCP port `<port>` in, for private networks only. Amplifier does not add
   this rule and does not ask for administrator rights.
3. To turn remote access off, do these four things in this order. The
   administrator removes the firewall rule. Set both lists back with the
   command below. Stop Amplifier and start it again. Sign out all browsers
   with `amplifier-unified browsers sign-out`, wait a few seconds, and open
   Amplifier again on the PC.

   ```
   amplifier-unified config set public_origins "[https://127.0.0.1:<port>]" bind "[127.0.0.1]"
   ```

On the other device, open `https://<LAN address>:<port>`.

### What is measured, and limits

Measured on Windows 11 with Tailscale 1.104.5 from an ordinary account: the
forwarding rule needs no administrator and no firewall rule, the other device
receives Amplifier's own certificate, and the same account removes the rule
again.

Not measured: these steps from start to end on a PC that was set up with the
Windows setup file; a real phone; a Windows restart with the forwarding rule in
place; a tailnet with MagicDNS off; the LAN steps; the Windows question about
an earlier certificate on a real PC (a test computer cannot answer it; the
calls behind it are measured on a private test list of certificates).

- Never use Tailscale Funnel. It puts the address on the public internet.
- `tailscale cert` is not used. It publishes the name of the PC in a public
  certificate log for good.
- The password is the one way in for a browser on another device. While
  Amplifier checks no password after wrong ones, no other device can sign in.
  The reasons are in the
  [design](design/native-windows-installation.md#remote-browser-sign-in-on-a-windows-host).
- The key command has no Start-menu entry and no button in the app. It needs
  a terminal. The password needs none.
- The way to the password page goes through the Settings section
  **Advanced**, its group **Performance & runtime**, the page **Install app**
  and the link **Devices & apps**. Settings has no entry of its own for it.
- A sign-in lasts seven days (`session_ttl_seconds`). After that, the device
  signs in again with the password or the key.
- The password was not tried with a password manager or on a phone. The
  field is a password field, so that a password manager can save and fill it.
- Linux, macOS and WSL hosts do not use these passwords or keys. Sign in
  there with the host account password on the sign-in page.

## Sign out all browsers

To end every browser session on every device at once, for example after a
phone is lost, run this on the host as the account that owns Unified:

```sh
amplifier-unified browsers sign-out
```

Pass the same `--data-dir` value if Unified uses a non-default data directory.
The command saves a time in the owner's private configuration
(`config/auth/session-epoch`). It needs no running host, no password and no
network request: the ability to write that private folder is the authority.
A running host applies the change within a few seconds. It then refuses every
session from before that time, drops pending sign-in tickets, and ends open
live-update connections so that a signed-out browser cannot keep watching.
On a Windows host it also refuses a saved password or sign-in key from before
that time. A sign-in ticket, password or key that is made or used during
those few seconds opens no session, so that one from before the sign-out cannot
outlast it. Wait a few seconds after the command. Then open Amplifier again on
the PC, and set a new password there for another device.
If the command says that it could not confirm the sign-out, run it again.
Sign in again in the usual way for your platform.

Limits: this signs out **all** browsers; there is no sign-out for one device.
Connected terminals, apps and devices with their own access are not browser
sessions and stay connected; remove those in the app. The saved time never
moves backward. If the record is damaged, the host signs everyone out once and
repairs it.

## Keep the host running

On Linux, including a WSL distribution with systemd enabled, install and manage
Unified with:

```sh
amplifier-unified service install
amplifier-unified service status
```

Available lifecycle commands are `install`, `start`, `stop`, `restart`, `status`, `logs`, and `uninstall`. Repeating `install` with the same configuration reuses its definition and starts an inactive service without restarting a running host. After a package update, use `amplifier-unified service restart` to load the new version.

`service status` checks every locally assigned numeric address resolved from its
configured binds, not just the first successful response. Each `Host listener`
line is a matching authenticated health response with the actual HTTP or HTTPS
scheme. Failed probes and configured binds remain separate from verified
listeners; the singular JSON `url` remains the first success, while `listenerUrls`
contains all successes, `listenerChecks` retains individual failures, and
`listenerScope` states the evidence limit and `listenerScopeComplete` reports
whether candidate enumeration/admission was complete.
Service readiness can still be true when one listener responds and another
probe fails; inspect the individual results. A name is resolved once per pass
and only locally assigned results are probed, with no second DNS lookup on
connect. Probes use at most four concurrent two-second requests. On Linux and
macOS, wildcard binds enumerate assigned IPv4/IPv6 addresses directly through
the OS `getifaddrs` interface snapshot, including LAN, VPN and scoped link-local
addresses, without public DNS. Each numeric candidate must still pass a local
ownership check and authenticated health probe. Unsupported/failed enumeration
falls back to loopback and reports **PARTIAL** scope, never complete wildcard
coverage. Interfaces can change after the snapshot. This is not an inventory of
unconfigured sockets, and it does not infer LAN reachability from loopback
success. Configured public addresses are listed separately as
**not checked**: a healthy host does not prove DNS, network access or certificate
trust on another device. The authenticated status probe never follows redirects
or selects `public_origins` as credential-bearing probe targets. Nonlocal or
unresolved bind names remain configuration only, not verified listeners; Linux
nonloopback probes fail closed if global `ip_nonlocal_bind` prevents establishing
local ownership. DNS resolution uses the operating system's resolver timeout,
not the per-request HTTP timeout. Normal launch still
opens the configured public address when one is set; verify it on the client.

Foreground `serve` announces the listener and schedules browser opening only
after all configured addresses bind successfully. An occupied port, unavailable
address or permission refusal gives a specific next step. It does not stop an
existing process, choose another interface, or change the saved configuration.
Name-resolution failures give hostname guidance rather than a port-conflict
diagnosis. A configured name may use its available address family, but success
for one configured entry never substitutes for another unavailable entry.

The CLI acquires and holds the actual listening sockets before settings migration
or application construction. A bind failure therefore starts no app controllers
and creates no new app diagnostic capture. Every held socket is released on a
failed start; successful startup transfers the same sockets to the HTTP server
without closing and rebinding. TCP connections may queue during construction:
a TCP connect alone is **not** readiness. Continue to use the authenticated
HTTP(S) health checks above. Later application/TLS/filesystem failures keep their
own identity rather than being reported as bind errors.

Service-manager retry policies are unchanged, including launchd's `KeepAlive`.
An unavailable VPN address still causes failed starts until it returns or the
operator deliberately changes the configuration or stops the service. This fix
does not suppress process retries, rewrite service definitions or repair/delete
historical failed-start captures.

If the generated definition differs, rerun the same install command with `--replace` to save a timestamped private backup and restart with the new definition. The command reports the backup location. Custom service files are preserved unless `--replace` is explicit; review them before replacing. Expected service setup errors are reported without a Python traceback and do not undo a separately completed package installation.

Generated systemd services use `KillMode=mixed`: shutdown signals the host first,
allowing it to close its workers deliberately, while the service manager retains
final cleanup of the entire control group. Custom instance generators should
use the same policy. Older units using `control-group` can signal workers before
the host begins cleanup and record a false worker failure during a restart;
updating the package alone does not replace those unit definitions.

The installer supplies the selected `uv` and app executable directories to the
launcher before service registration. The service captures that `PATH` and
supplements it with the user's standard helper directories (`~/.local/bin` and
`~/.cargo/bin`; also Homebrew locations on macOS). Startup applies the same rule
to older service definitions, including after reset, without changing their
workspace, data directory, or custom configuration. Existing PATH precedence and
dispatcher symlinks are preserved. No shell startup scripts are evaluated.

If `uv` lives in a custom directory and works in a terminal but not in the app,
run `amplifier-unified service install --replace` from that terminal, preserving
any original `--data-dir` and `--workspace` options. This backs up the definition
and restarts the service; saved chats and settings remain. Confirm with
`amplifier-unified service status --wait 60`, then refresh the browser and retry
the connection. A reboot or reinstall alone does not change an older service's
captured custom PATH. `amplifier-unified doctor` checks helper discovery as well
as application settings; it does not claim provider sign-in succeeded.

Installation acceptance must include a fresh Linux user with no curl/Git/uv,
OS-correct grouped prerequisite guidance, installer-provisioned uv absent from
the original PATH, service startup with that environment, and later helper
discovery with a stale service PATH. Missing helpers during ChatGPT sign-in
must produce an actionable setup error, while arbitrary provider errors stay
redacted. These are covered by `test_installer_script.py`,
`test_host_dependencies.py`, and `test_deployment_service.py`; retain them in
the onboarding smoke checks when changing installation or service ownership.

For WSL, enable systemd in `/etc/wsl.conf`, restart WSL with `wsl --shutdown`
from Windows PowerShell, then run the Linux installation commands above.

On macOS, `amplifier-unified service install` creates and starts a per-user
launchd agent. Its standard output and error logs are private files under
Unified's data directory. Use `amplifier-unified service status` to inspect the
agent and `amplifier-unified service logs` to read its recent output.

## Installed app opens a cross-origin error

An installed PWA or external link can enter through its service worker.
Chromium's `fetch(event.request)` forwards that navigation with
`Sec-Fetch-Mode: navigate` but changes `Sec-Fetch-Dest` from `document` to the
literal `empty`. Servers that require `document` for every entry reject the
forwarded launch with `{"error": "Cross-origin requests are not permitted."}`.

The entry check permits this forwarded form only for GET/HEAD `/` and `/login`,
with no Origin header. Host validation, login/session authentication, exact
Origin checks, CSRF checks, and cross-site API/frame rejection remain in place.
Do not work around this error by disabling these protections or accepting
arbitrary origins. A server containing this fix handles already-installed
workers; reinstalling the PWA is not required for this particular failure.

The regression in `tests/test_browser_auth.py` exercises the shipped service
worker and real Chromium request metadata before and after worker control,
including sign-in and blocked API/frame navigation. It is not a physical
Android Edge installation test.

## Desktop window controls overlay

The manifest prefers `window-controls-overlay` with `standalone` fallback.
Supporting installed Chromium/Edge apps can enable it from their browser-owned
title-bar controls. Declaring it does not prove the browser has enabled it:
the frontend activates only when `navigator.windowControlsOverlay.visible`
reports a valid title-bar rectangle.

The compact header occupies that usable rectangle, reserving the native
buttons on either side. Small usable areas keep a blank draggable strip and
the header below it. Focused Canvas, dialogs, loading, login and offline pages
also preserve native clearance. Buttons, links, tabs, editors and contributed
interactive controls are excluded from dragging. Appearances supply chrome
colors; they do not supply the native geometry.

After rebuilding the frontend, run `npm run test:wco-browser` and
`npm run test:window-chrome-browser` under Node 22+. The former substitutes
native geometry, not application CSS; neither establishes native dragging.
For an optional real Linux installed-window probe with an available desktop:

```sh
AMPLIFIER_TEST_INSTALLED_PWA=1 uv run --with playwright python -m pytest -q -s \
  tests/test_browser_auth.py::test_installed_pwa_launch
```

It installs and removes only a disposable profile's app, signs in with synthetic
credentials, and records whether overlay actually became visible. It does not
enable the browser's overlay toggle or click native window controls. Keep
that result separate from a real macOS/Windows check of dragging, button
operation, appearance changes and browser fallback. A successful standalone
launch is not overlay acceptance. Deployment and physical-phone verification
remain separate from these local tests.

## Limits

Unified owns its own HTTPS listener and authentication. It does not trust a reverse proxy, shared cookies, or `X-Forwarded-*` headers. Configure exact public origins rather than broad address patterns.

The public bootstrap endpoints are limited to `/login`, `/setup`, `/api/ca`, `/ca.crt`, and `/api/health`.

## Debug-only profiling

Opaque server extensions remain unchanged on load and save. The profiling
consumer accepts only a literal boolean `true`; other extension values do not
enable capture or prevent startup.

The optional `amplifier-profiling` library must be installed in the serving host's
Python environment. Normal installations do not require it. An operator can
enable it through `amplifier-unified config set debug.profiling true`; no app
action, bundle or tool configuration can enable this setting.

The user waived the ten optional IC-10 readiness checks on November 10, 2026;
this is not a claim that profiling is qualified on the current runtime. See the
[scope and reactivation rule](validation/integration-coverage.md#ic-09-and-ic-10-readiness-waiver).
Missing library access is not a normal-installation or release blocker, and
does not require another source-location request.

`profiling.status`, `profiling.targets`, `profiling.start`, `profiling.read`,
`profiling.stop` and `profiling.release` are authenticated shared actions.
Mutations require a stable command ID. Start accepts only the process-local target
returned by targets, 0.1–60 seconds, and 1–50 Hz. Results are memory-bounded and
process-local, not retained across restart; small admission receipts prevent an
exact retry from starting another capture. An unsettled receipt after restart is
reported unavailable, never replayed. Download settled captures from the returned
authenticated URL before explicitly releasing their retention slot.

Disable through `amplifier-unified config set debug.profiling false`. While a
capture is active, the host rechecks config every 250 ms (subject to event-loop and
filesystem delays); malformed, missing or unreadable config fails closed.
Download authorization is rechecked before returning bytes; already-returned
bytes cannot be recalled. Shutdown stops the sampler.

This is cooperative sampling of all Python threads in **the serving host
process**, not arbitrary PID attachment. It records symbol metadata, not locals,
arguments, source text, prompts or event bodies. GIL delays, missed samples and
backend limitations remain visible. Function observations are wall-stack counts,
not per-function CPU percentages. Do not infer native-thread/C-stack coverage.
Profiles never enter canonical Context Intelligence capture or ordinary app-state
publication. The native event JSONL and resume transcript remain authoritative.

Enabling this setting grants the authenticated owner and its agents access to
process-wide symbol metadata. Unified is currently single-owner; do not enable
this adapter in a multi-tenant shared process without separate operator-only
authorization and tenant-isolated targets. Hosted containers need no ptrace or
root privileges for self-sampling. External/native sampling is not supplied.

## Rebuildable history metadata

The app's `native-catalog.sqlite3` stores derived metadata, not canonical
transcripts or context-intelligence event bodies. Cache loading validates
workspace path and availability types before discovery consumes them. An invalid
cache is reported as unavailable and bypassed on ordinary and forced refreshes;
original history and the rejected cache are preserved. This fallback is not
permission to delete or repair canonical history. Resume admission continues to
read native metadata rather than trusting cached classification.
