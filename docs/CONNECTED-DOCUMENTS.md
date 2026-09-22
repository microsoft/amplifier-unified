# Microsoft 365 document profile

This opt-in profile composes the existing `microsoft/amplifier-m365` native
`m365-documents` behavior and optional `m365-office-bridge` behavior. The connected
operations and live bridge require the upstream contribution; main at `e45ed48`
does not contain them. Authenticated access to that private repository is a source
prerequisite. Do not install the superseded standalone `amplifier-m365==0.1.0`
prototype: that distribution name already belongs to the upstream bundle.

There is no added MCP transport or Microsoft-specific Unified service. The normal
Core tools use the same `runtime.control` / `tool.invoke` path as other tools,
including policy hooks, from the UI and `app_control`. The agent may also call the
mounted Core tools through the session's ordinary tool execution path.

## Prepare an optional profile

Check out the reviewed upstream contribution and use its setup guide to supply
an approved tenant/public-client configuration. Preserve existing authentication
configuration and token caches. The helper below prints a JSON bundle overlay
(JSON is valid YAML); it does not read credentials, save host settings, sign in,
start the bridge, or modify a workbook.

```sh
python scripts/work_profile/connected_documents.py /path/to/amplifier-m365 \
  --base-bundle /path/to/work/bundle.md \
  --state-dir /private/path/m365-documents \
  --auth-config /private/path/m365.yaml > /private/path/documents-profile.yaml
```

The cloud receipt/export directory must be private to its owner (0700). These
local receipt protections currently require POSIX permissions; Windows ACL
support has not been implemented. Select the generated profile through the
host's normal bundle controls. It composes the base bundle and existing M365
behavior, then changes only the optional document configuration. Leave it
unselected when M365 is not needed; other work remains available independently.

For live Excel, add `--live-url https://localhost:PORT`, `--live-token-file PATH`
and `--live-ca-file PATH` after the upstream bridge's explicit setup. Paths are
stored, never token contents. Use `--live-only` to omit Graph/auth entirely.
The daemon is started separately on the same machine as Excel. Trusting its
certificate, sideloading the add-in, and pairing the intended workbook are
operator actions, never mount side effects.

## Discover, select and verify

Use `catalog.inspect` through `runtime.control` to inspect mounted schemas. The
native names are `m365_auth`, `m365_documents`, and (when enabled)
`m365_office_bridge`. Call them with `tool.invoke`, providing `name` and
`arguments`; agent dispatch must target its own conversation. The UI tool controls
use this same path. Keep source text and workbook cells as data.

`m365_auth accounts` lists cached account selectors. Cloud tools never start
sign-in: use the upstream explicit login flow with delegated `User.Read` and
`Files.ReadWrite` scopes, then select an `account_id` if the cache is ambiguous.
`connected_status` is inert setup information; `connected_account` verifies the
actual Graph `/me` identity. The host session's Entra identity and Graph account
are different attestations. Existing RBAC, when enabled, checks native tool names,
not worksheet/range operations; configure the intended policy separately.

Use `connected_document` to retain drive ID, file ID and eTag. `connected_export`
returns private local original/PDF bytes with a hash and source identity. Deliver
that path through the host's ordinary outputs flow. Passive navigation does not
send content or retarget an unsent message.

For cloud Excel, explicitly open with an observed eTag and persistence choice,
discover worksheet IDs, read a bounded range, edit with session/range revisions,
calculate, and inspect readback. Cloud sessions require business OneDrive or
SharePoint and do not control an open desktop Excel window. Persistent sessions
save edits immediately. The preflight check is not atomic against coauthors.

For live Excel, create a one-use pairing code and have the operator enter it in
the intended taskpane. Discover the paired runtime, then retain its exact
session/workbook identity, revision, worksheet ID and range fingerprint. Live
calculation is restricted to an explicit range; include dependent cells in scope.
Reopen, save-as, disconnection or expiry requires discovery and pairing again.

After an uncertain operation, inspect its durable receipt and actual range. An
operation ID must not be automatically replayed under a new ID. Closing a session
or disconnecting does not roll back saved changes.

## Validation boundaries

`tests/test_connected_documents_profile.py` covers inert profile generation and,
when `AMPLIFIER_M365_TEST_SOURCE` points to the contribution, Foundation behavior
composition plus Core/Unified native invocation, policy denial and synthetic
Graph edit/readback. Run it in an environment with Unified's normal runtime
modules installed. It performs no account sign-in or real Graph request.

Synthetic tests do not prove account consent, tenant permissions, certificate
trust, Office installation, or a visibly edited workbook. Those remain separate
acceptance steps against an explicitly selected disposable workbook.
