# Microsoft 365 document profile

This optional profile consumes the `amplifier-m365-mcp` stdio API through existing
[Smart Tools connections](CONNECTORS.md). The runtime is an independently installed
portable candidate; it is not bundled or enabled by default. No application service
registration, account inheritance, device consent, or background connection is added.

The MCP adapter exposes separate `m365_*` document retrieval/export,
`excel_cloud_*` Graph workbook, and `excel_live_*` paired Office add-in operations.
Graph cloud sessions require delegated work/school access to business OneDrive or
SharePoint. Office live requires its original add-in and an explicitly paired
open workbook. Selecting a cloud item never implies control of desktop Excel.

## Configure an installed candidate

Install the runtime using its supplied README, choose private state/export
folders, and set the corresponding host environment variables. Each variable
named below is an environment **reference**, never a credential value saved in
Unified state. The runtime performs its own explicit MSAL CLI sign-in; Unified's
MCP OAuth button is not used to impersonate Microsoft Graph authorization.

```sh
python scripts/work_profile/connected_documents.py /absolute/path/to/amplifier-m365-mcp
```

The command prints a `smartTools.configure` action without applying it. Pass
`--live` to include the configured loopback HTTPS bridge URL, host token-file path
and CA-file path references. The bridge must run on the same machine as Excel;
remote deployment or forwarding is outside this profile.

Apply that exact registration with the shared `app_control` action or equivalent
Settings controls, then explicitly `smartTools.connect`. Discover schemas using
`smartTools.discover` and `smartTools.schemas`, retaining their catalog revision.
Invoke tools through `smartTools.call`, from either the user control or agent path.
Saving configuration alone never signs in or connects.

Start with `m365_readiness`. Its saved authorization state is not proof of access;
`m365_account` verifies Microsoft `/me`. The host's generic account status remains
unknown for this local stdio profile, while adapter responses supply Graph account
identity. Do not label that as the separate remote OAuth account-attestation
contract. User-facing readiness must distinguish missing application registration,
account consent, bridge configuration, and an actually paired workbook.

## Exact targets and visible outcomes

Read document metadata before export, retaining drive ID, item ID and eTag. Exports
return a local path, source metadata and content hash; use normal output delivery
to present the artifact. This profile does not attach arbitrary downloaded account
data automatically or retarget unsent-message context.

For cloud Excel, open explicitly with a persistence choice, discover worksheet
IDs, read, edit with session and range revisions, calculate, and verify readback.
Persistent sessions save immediately. The preflight range guard is not atomic
against Microsoft coauthors; use a dedicated test workbook for acceptance.

For live Excel, create a one-use pairing code, let the user enter it in the
intended taskpane, then enumerate the resulting runtime session. Keep session ID,
workbook ID, revision and exact range fingerprint on every operation. Calculate
only named ranges; do not claim application-wide calculation or physical UI
acceptance. Reopening, save-as, disconnect, or expired pairing requires discovery
and explicit pairing again.

Transport timeout can mean a remote edit completed. Inspect the runtime operation
receipt and actual range before a new operation; Unified must never auto-replay it.
Closing a session or disconnecting does not roll back saved changes.

## Acceptance

`tests/test_connected_documents_profile.py` is an optional integration test using
an explicitly installed candidate, the production SmartToolsManager, real MCP
stdio and synthetic Graph HTTP. It verifies shared UI/agent discovery and actual
edit/recalculate/readback dispatch. Run with `AMPLIFIER_M365_TEST_PACKAGE` pointing
to the candidate root and `AMPLIFIER_M365_TEST_PYTHON` pointing to its Python.
Without that explicit package, the optional integration test skips.

No real Microsoft account, tenant consent, Excel sideload, trusted certificate
installation, production host change or physical workbook interaction is established
by synthetic tests. Record these separately before deployment acceptance.
