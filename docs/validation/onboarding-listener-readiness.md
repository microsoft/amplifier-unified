# Onboarding listener readiness — October 16, 2026

This follow-up fixes three confirmed onboarding defects without changing
authentication, allowed origins, certificate trust or service ownership. A
working host listener is distinguished from an unverified public address;
supplied certificate/key readiness is independent of an app-owned CA; expected
listener startup failures have actionable guidance. Reset uses the same local
readiness qualification. No remote probe receives the control token.

## Additive ordinary smoke inventory

| ID | Current qualified result | Desired behavior and limits |
| --- | --- | --- |
| `SETUP-URL-01` | A real pinned HTTPS health request reaches the expected synthetic installation while an unusable `.invalid` public origin remains explicitly unverified. Only the configured local listener is contacted; redirects are disabled. CLI output names the actual HTTPS listener and labels public access separately. | Local success must never claim client DNS, reachability or trust. Normal launch retains the user's chosen public browser address with the explicit qualification. |
| `SETUP-TLS-02` | Public `setup-tls status` accepts a loadable externally supplied pair without creating or requiring an app-owned CA. Missing, malformed and mismatched pairs are rejected; all certificate/config bytes and modification times are retained. | Listener readiness and CA export availability are separate. Certificate expiry, hostname coverage and native-device trust are not established by this loadability check. |
| `SETUP-BIND-01` | A real occupied TCP port fails through the public CLI entrypoint without a traceback, readiness banner, browser launch or takeover. Address-unavailable and access-denied errors receive focused guidance. Unexpected I/O and file-permission errors retain their original identity. | Announce readiness only after every socket binds. Never silently choose another interface/port, stop its owner, or alter configuration. The unavailable/permission errors are controlled OS-error fixtures; this does not qualify macOS privileged-port policy. |
| `SETUP-URL-01a` | Reset's existing isolated repair fixture prints host-local readiness and separates unverified public URLs. Foreground browser scheduling and background launch address choice are covered by CLI fixtures. | A successful local post-repair check is not proof of remote browser usability. No real reinstall or service-manager action is performed by these fixtures. |
| `SETUP-URL-02` | Proposed, unexecuted here: open a configured public URL from each supported native client, with trusted and untrusted certificate/DNS states. | Preserve separate native DNS, network and trust qualification; do not mark these passed based on host tests. |

The new listener test file has twelve cases. Against unchanged baseline
`2bc6869e0`, seven fail and five negative controls pass; the fixed focused suite
passes. Tests use synthetic certificates, a local aiohttp listener and a reduced
application factory for real socket binding, with no provider/tool invocation.

The checks execute only inside the existing project-owned spark-2 DTU. Evidence,
exact test receipts, source hashes, baseline failures, and cleanup are retained
in `output/onboarding-readiness-20261016`. This does not qualify native macOS,
Windows, external DNS, or live deployment. The opt-in full `uv` repair-install
test remains separate from presentation-only reset coverage.
