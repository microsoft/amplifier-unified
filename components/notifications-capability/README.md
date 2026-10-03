# Amplifier Unified notifications capability

Optional, shared **ntfy-compatible push notifications**. Desktop/browser permissions, local desktop notifications and unsent settings edits remain client responsibilities. This package imports no Amplifier runtime and does not list chats, load history, or start agents.

The Node capability speaks only its private bounded stdio protocol to the Python owner. The owner persists private settings, redacted exact-command receipts and delivery outcomes under an explicit private directory. It sends only trusted, completed-turn or policy-selected schedule notices. It does not expose a general message-send or URL-fetch action.

## Composition

```js
import {createNotificationsCapability} from '@amplifier/unified-notifications-capability';
const notifications = createNotificationsCapability({
  owner: {command: '/owned/venv/bin/amplifier-unified-notifications',
          args: ['--config', '/owned/notifications.json']},
  inspectSession: uri => host.inspectSession(uri),
  waitForTurn: (uri, commandId, timeout) => host.waitForTurn(uri, commandId, timeout),
  onInvalidate: (topic, scope) => invalidate(topic, scope),
  onMayBeIdle: () => supervisor.notifyMayBeIdle(), // advisory, never idle proof
});
```

Merge `manifest/read/action/actionSchemas/quiescenceAccess` through the distribution's ordinary capability composition. Register `notifications.quiescenceParticipant` as the actual owner of this topic. Do not advertise configured notifications without that participant. The read-only action classification preserves exact receipt/settings inspection during maintenance; no name-based mutation exemption is used.

After the host's durable turn receipt settles, call `notifications.turnSettled(event)` with `{session,commandId,status,inputOrigin}`. Only completed non-scheduled turns qualify. Disabled settings require no session, result-body or credential read. Preview-off delivery reads selected metadata only; preview-on reads the exact completed result through the provided public host callback. It sends no prompt and never alters the completed turn's outcome. Both completion methods contain their errors and return a receipt/refusal; a notification failure must never fail completed agent work.

For Operations `notifySchedule(uri, run)`, apply its **explicit** `run.notificationDecision.notify === true` before calling:

```js
await notifications.notifyAttention({
  session: uri, eventId: 'schedule:' + run.id,
  text: sanitizedBoundedPhaseAndDetail,
});
```

The notification owner does not infer schedule policy. Call these two methods only from authenticated internal completion/policy boundaries. They are not advertised browser/native-agent actions. Session identity/title comes from the injected authorized `inspectSession`, not an untrusted title or path in a browser request.

Python launcher JSON:

```json
{"stateDirectory":"/absolute/private/notifications"}
```

Optional trusted launcher fields are `defaultServer` (defaults to `https://ntfy.sh`) and `legacySettingsPath` (an exact absolute old `config/notifications.json`). A legacy import occurs only on first owner creation, reads at most 16 KiB, maps `push` to `enabled`, validates all retained delivery fields, and leaves the source untouched. Import never sends. Missing legacy files use disabled defaults; malformed/oversized existing files require explicit correction. Desktop settings are not imported to shared state.

## Public topic and actions

`amplifier-capability://notifications/settings`, topic `notifications`, scope `host`, schema version1. Standard `resourceRead` returns a bounded snapshot with `data.notificationSettings`:

```json
{"revision":0,"enabled":false,"server":"https://ntfy.sh","preview":false,
 "topicConfigured":false,"tokenConfigured":false}
```

| Action | Args | Result |
| --- | --- | --- |
| `notifications.get` | `{}` | Redacted settings |
| `notifications.save` | `{expectedRevision,patch}` | Exact completed/rejected settings receipt and current topic update |
| `notifications.receipt` | `{commandId}` | Exact redacted settings or delivery receipt; unavailable is explicit |

Patch fields: `enabled`, `server`, `topic`, `token`, `preview`, `clearTopic`, `clearToken`. Blank topic/token preserve their current value; explicit clear is required. `desktop` is accepted for compatibility and ignored by the shared owner. A client persists it in its own storage. Stale settings revisions return `{status:'rejected',executed:false}`; no write occurs. Reusing a settings command ID returns only its original receipt if the exact fingerprint agrees, never reapplies the patch. Changed args on that ID are refused.

Clients must **not** place topic/token bytes in local mutation journals, logs, shared snapshots, or URLs. Retain only operation identity/hash and the command ID, then inspect the receipt after an uncertain reply. The host's normal capability receipt stores a fingerprint, and this owner stores only redacted command results.

## Delivery and endpoint policy

Every destination is an explicitly saved HTTPS ntfy-compatible base endpoint. Self-hosted servers, ports and path prefixes are supported. HTTP, userinfo, query strings and fragments are rejected. The public API has no arbitrary method, body, headers, response reader or per-send destination. Saving settings never contacts the endpoint.

Each delivery performs one fixed JSON POST `{topic,title,message}` with an optional private Bearer token, system TLS verification and a 15-second total timeout. Redirects are disabled, including same-origin redirects. No response body is read, surfaced or persisted. A server accepted result means **HTTP2xx only**, never device delivery. Preview defaults off; the generic message says a response is ready. Preview is at most1000 characters and selected title at most200 characters. No attachments, transcripts or queued prompts are loaded.

Configured HTTPS endpoints can include the user's authorized private/self-hosted network; this is deliberate ntfy compatibility, not general network discovery. Deployments must authorize shared settings changes as other user/agent shared actions. Credentials are private owner data, never resource output.

The durable delivery identity is `notification:` plus SHA256 of the canonical `[session,eventId]`. A redacted receipt is reserved **before** scheduling a send. `accepted` means owner admission; `dispatching` means transport has begun; `server-accepted` means a confirmed HTTP2xx; `rejected` means a known HTTP rejection; `unknown` means the reply or process boundary was uncertain. No timeout, crash, redirect, failed reply or restart causes automatic resend. Duplicate exact event IDs return the receipt. Changing a previously used event's payload is refused. The accepted/dispatching restart state becomes unknown even if the process might have died before transmitting; absence of proof is not replay authorization.

## Bounded work and maintenance

The owner holds an OS-managed SQLite lifetime lease **before** migrations or opening durable intake state. Another process cannot mutate that state. SIGKILL releases only the OS lease, not the durable delivery/fence evidence. Private directory creation uses0700 and the settings DB0600; settings public flags and credential rows are separate, so disabled/read-only paths do not query secrets.

At most32 deliveries are admitted concurrently and at most4 network requests run at once. The stdio frame limit is128KiB, request limit64, and no transcript cache exists. Historical redacted receipts live on disk and exact reads use primary-key indexes. Unknown historical receipts do not alone imply current background work; they remain evidence and are never replayed.

Foundation `DurableIntakeFence` closes intake atomically only when actual calls/background work are absent, and preserves the held fence across restart. Confirmed release records exact context, outcome and proof; changed-proof retries fail. The Node side retains uncertain request slots until a late reply or actual process exit. A busy worker is never killed for updater convenience. Shutdown closes stdin and waits for the owner to finish bounded admitted requests. `onMayBeIdle` is advisory only.

## Qualification

Run `npm ci && npm test` for Node tests. Set `NOTIFICATIONS_PYTHON` to an installed interpreter containing the Python wheel, and `HOST_MODULE` to the installed host public entrypoint to include actual two-client AHP tests. `NOTIFICATIONS_MODULE` can select the installed Node package entrypoint. Run `python -I -m pytest` in `python/` for owner tests; `-I` prevents legacy monorepo package shadowing. Tests use fixtures only: one local HTTPS server, injected transport failures, two AHP clients, bounded work, actual SIGKILL and restart, duplicate suppression, blank-preserve/CAS, legacy import, and held/changed release proof. No real ntfy account or physical device acceptance is claimed.

## Persistent service fences

The trusted quiescence participant advertises `serviceStop: {version: 1}` only
for the retained service-stop contract. Acquisition copies the complete
`serviceIdentity` (`installationId`, `dataScope`, `ownerId`, `instanceId`,
`releaseDigest`) into its existing durable intake fence. Restarting does not
open intake or replay admitted work. Python owners require the installed
Foundation `DurableIntakeFence.SERVICE_STOP_VERSION` marker.

Release requires the host-authenticated `kind: 'service-lifecycle'` proof bound
to the exact fence, command, original identity, and observed instance. A resumed
service needs distinct-instance exit/readiness receipts; a refused stop needs
the exact original-instance refusal receipt. The complete proof is retained.
Exact completed retries are passive, and changed proofs refuse even as the first
request after restart. Generic recovery/update proofs do not release this fence.

An `admission-refused` rollback is restricted to the newly acquired live lease.
It is unavailable through reconciliation or after an unknown outcome. Reads of
existing receipts stay available while intake is held. Platform authentication,
process ownership, stop/resume signaling, and aggregate coverage remain with the
host and supervisor; this owner does not infer them from a PID or missing socket.

The distribution's installed owner-service matrix covers fresh reopen, changed
proof, unknown rollback, and public bridge behavior on Node22/Python311+313.


## Held retention inspection

The actual quiescence participant advertises `retentionHide: {version: 1}`.
Acquire it with purpose `retention-hide` and the exact coordinator fence context.
The returned live lease exposes
`inspectRetentionReferences({sessions: ["ahp-session:/..."], limit: 101})`.
The family must contain 1–101 distinct explicit session URIs. The result is
`{coverage: "complete" | "partial", protected: [{session, reasons}], omissions}`.
Only the original held lease can inspect; release, uncertainty or replacement
invalidates that authority. Release reconciliation requires the same authenticated
coordinator proof and never retries effects. A pre-effect admission rollback is
allowed only on the original live lease. The purpose grants no mutation rights.

Inspection reads bounded indexed owner metadata while intake remains closed.
It never starts a native worker, scans canonical histories, deletes product records,
or interprets absence of a runtime as absence of deferred work. `complete` describes
this owner's reference coverage, not permission to hide or delete a conversation.
The coordinator must hold every configured owner, check all results, and use the
native history owner's separate preservation-first hide boundary.

Active work blocks acquisition. Retained uncertain owner commands protect their explicitly associated conversations; unscoped uncertainty cannot produce a false absence proof.

## Managed-file disposal protection

`quiescenceParticipant.managedFiles = {version: 1, preservesCanonical: true}` is a separate contract from `retentionHide`. It holds the real owner intake with purpose `managed-files-disposal`, preserving every owner record. The acquired lease exposes:

```ts
inspectManagedFilesReferences({
  sessions: [rootSession, ...descendants], // explicit AHP URIs, at most 101
  limit: 101,
  allocation: {allocationId, executionDirectory, allocationHash, treeHash, entryCount, bytes}
}) // {coverage: 'complete' | 'partial', protected: [{session, reasons}], omissions: [...] }
```

The allocation comes from the trusted host's native-reviewed managed allocation, never a browser path. It is bound with the exact selected family on the first inspection of the live lease; a different review is refused. This read does not grant file deletion. The coordinator must bind release to its exact durable managed-files effect receipt, keep unknown fences held, and refuse partial/protected coverage. Lease inspectors expire before release begins, including unknown release; admission-refused rollback is available only on the original live lease. Restart reconciliation requires the existing exact verified release proof.

Accepted, dispatching and unknown deliveries protect selected conversations. Finished notification payloads are stored copies, not execution-file handles.

Inspection uses bounded selected metadata or indexed overlap probes, not native transcripts, a global history projection, or worker/model startup. Existing retention-hide and service-stop lifecycles are unchanged. The package tests cover the independently installed owner transport and held/unknown/exact-release behavior; full host/native/browser disposal acceptance remains a composition responsibility.
