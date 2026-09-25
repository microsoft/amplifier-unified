# Amplifier Unified

Amplifier Unified is a local, authenticated workspace for Amplifier. It provides a web app for chat, voice, and work with your configured Amplifier bundles and tools.

## Set up Unified on your computer

Open only the section for the computer where Unified will run. You need Python
3.13+ and [uv](https://docs.astral.sh/uv/).

<details>
<summary><strong>Linux</strong></summary>

Install Unified, then set it up as a background service:

```sh
uv tool install git+https://github.com/microsoft/amplifier-unified
amplifier-unified service install
```

Open [http://127.0.0.1:8941](http://127.0.0.1:8941) and sign in with the
account that runs Unified.

</details>

<details>
<summary><strong>Windows with WSL</strong></summary>

Use a Linux distribution in WSL with systemd enabled. In the WSL distribution,
add this to `/etc/wsl.conf`:

```ini
[boot]
systemd=true
```

From Windows PowerShell, restart WSL:

```powershell
wsl --shutdown
```

Open WSL again, then install Unified and set it up as a background service:

```sh
uv tool install git+https://github.com/microsoft/amplifier-unified
amplifier-unified service install
```

Open [http://127.0.0.1:8941](http://127.0.0.1:8941) in your Windows browser
and sign in with the account that runs Unified.

</details>

<details>
<summary><strong>macOS</strong></summary>

Install Unified, then set it up as a background service:

```sh
uv tool install git+https://github.com/microsoft/amplifier-unified
amplifier-unified service install
```

Open [http://127.0.0.1:8941](http://127.0.0.1:8941) and sign in with the
account that runs Unified.

</details>

The first message can take a few minutes while Unified prepares its runtime.

Already installed the older hackathon version from `bkrabach`? Follow the
[Unified-only fresh install guide](docs/HACKATHON-RESET.md) to clear its setup
while keeping your Amplifier CLI, shared settings, and native chats.

## Create a dedicated desktop or mobile app

You can add Unified to a desktop or mobile device as its own app-like window or home-screen icon. This does not install or move the Unified service; it opens the same workspace more directly.

After opening Unified in a browser:

- **Chrome or Edge:** use the install icon in the address bar or the browser's install menu.
- **Safari on macOS:** choose **File → Add to Dock**.
- **Safari on iPhone or iPad:** choose **Share → Add to Home Screen**.

This works on desktop and mobile platforms supported by your browser. Unified must keep running at the same address.

## Access Unified over your LAN or Tailscale

<details>
<summary><strong>Set up access from another device</strong></summary>

Unified listens only on `127.0.0.1:8941` by default. To use it from another
device, configure trusted HTTPS with the exact address you will use:

```sh
amplifier-unified config set public_origins '["https://your-host.tailnet.ts.net:8941"]'
amplifier-unified setup-tls
amplifier-unified config set bind '["100.64.0.10", "127.0.0.1"]'
amplifier-unified doctor
```

Replace the example origin and Tailscale IP with your host's real addresses. Before opening the remote address, export Unified's public CA on the host, verify its fingerprint with `doctor`, and trust that CA on each device. Do not disable certificate checks.

For the complete setup—including certificate transfer and trust, reverse-proxy limits, and Linux service management—see the [deployment guide](docs/DEPLOYMENT.md).

</details>

## Contributing

> [!NOTE]
> This project is not currently accepting external contributions, but we're actively working toward opening this up. We value community input and look forward to collaborating in the future. For now, feel free to fork and experiment!

Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit [Contributor License Agreements](https://cla.opensource.microsoft.com).

When you submit a pull request, a CLA bot will automatically determine whether you need to provide
a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft
trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.
Any use of third-party trademarks or logos are subject to those third-party's policies.
