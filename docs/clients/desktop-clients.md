# Desktop clients: ownership, compatibility and acceptance

Implementation/coordination record, not another contract. The shared
[vision](VISION.md), [CX](../../contracts/client-experience.v1.md),
[CS](../../contracts/client-state.v1.md), [CD](../../contracts/client-development.v1.md)
and [DC](../../contracts/desktop-client.v1.md) own the promises.

## Consumers and boundaries

| Consumer / owner | Current scope | Owns | Does not own |
| --- | --- | --- | --- |
| [Amplifier for Mac](https://github.com/microsoft/amplifier-client-macos) | Released 0.1.0 early preview; native quick chat, notifications and optional Tempo; full workspace opens web/PWA | AppKit interaction, OS credentials, notifications, navigation adapter, signing and updates | Shared attention/history, command admission, provider/model decisions |
| Windows client | Planned; no repository or implementation assigned by this record | Future platform-specific client, installation and acceptance | A second definition of desktop status, reply recovery or host permissions |
| Unified host and bundled web | Existing shared APIs and workspace | Pairing/revocation, command admission, chat classification, attention receipts and exact-chat links | Platform materials, local credentials or client update installers |

Tempo names an optional interaction surface, not the family-wide app identity.
The Mac app is Amplifier for Mac. Windows naming and implementation choices remain
open; no Windows parity or deliverable is claimed here.

## Current public seam

The Mac preview uses current HTTP APIs: native attachment with `protocolVersion:1`,
the bounded companion/activity adapter, and host chat commands. Browser approval
issues a restricted `companion-v1` credential. This is not completed AHP/ACP adoption.

Canonical specifications:

- [Native pairing](native-pairing.md): approval, proof, scope, client binding and revocation.
- [Observations](observations.md): pagination, chat classification, canonical attention.
- [Live sessions](live-sessions.md): current command/receipt semantics.
- [Chat links](chat-links.md): exact target, authentication, warm window reuse and errors.

Mac-specific consumption and exact reviewed revisions belong in its
[compatibility record](https://github.com/microsoft/amplifier-client-macos/blob/main/docs/compatibility.md).
Windows should choose the current supported public observation resources after
reviewing their compatibility; do not copy the temporary `/api/companion` adapter
or treat a future AHP lane as a prerequisite for an independent desktop client.
No app identifier, platform artwork or release location is a new host permission.

## Qualification checkpoint

Mac 0.1.0/build 1 was released from
`22acd60acf4c3a46ab55b00a6f0ffa5b5d01a394`. Its
[release record](https://github.com/microsoft/amplifier-client-macos/blob/main/docs/release-readiness.md)
distinguishes signed distribution and synthetic integration from uncompleted
physical interaction coverage. Pairing was integrated by
[PR #813](https://github.com/microsoft/amplifier-unified/pull/813); the earlier PWA
and classification changes from #603 shipped through
[release PR #619](https://github.com/microsoft/amplifier-unified/pull/619).
These are integration facts, not proof of every current client/host combination.

The initial Mac acceptance used an owned synthetic host fixture with pairing
changes overlaid on its source. It was not an untouched released-host matrix.
The oldest compatible host release is **not yet qualified**. The fixture's version
label must not become a minimum-version claim. Latest main is likewise not a test.
Windows has no source, device, package or host-pair qualification in this record.

## Shared acceptance journeys

Each run records exact client and host source/artifact versions, protocol and
capabilities, OS/architecture, fixture/provider type, cold/warm state, observed
result and limitations. The platform repo retains sanitized evidence and gaps.

| Journey | Clauses | Observable scenario / failure |
| --- | --- | --- |
| DJ1: attention across views | DC1–2, CS2, CX6 | Finish work, view its latest response in a focused web chat, then observe desktop clearing within its stated refresh bound. Work alone has no unread count; read is not approval. Fail on duplicate attention authority or theme-dependent meaning. |
| DJ2: exact navigation | DC4, CX1 | Open two notifications into a warm workspace and one while cold. Preserve drafts, use the exact host/chat, handle missing/forbidden targets visibly. Fail on duplicate warm windows, reload, wrong target or implicit acknowledgement. |
| DJ3: replies and recovery | DC6, CS1/4, CX2–3 | Send while connected, disconnect around admission, restart with saved input, repeat a callback and change the selected chat. Fail on automatic resend, lost intent, wrong destination or acceptance presented as completion. |
| DJ4: human focus | DC3, CX6/8 | Type in another app between drag, scroll and expand/collapse operations; then explicitly open chat/composer/settings. Fail on stolen passive focus, lost typing or inaccessible controls. |
| DJ5: enrollment and authority | DC5, CS1/8 | Approve matching code, deny/expire another request, revoke access during a pending command, disconnect/resume/forget. Fail on credential leakage, new authority without approval or revocation presented as cancelling accepted work. |
| DJ6: independent lifecycle | DC5–6, CD3/6 | Hide/show companion, quit, wake/reconnect, upgrade and reopen the same client. Fail on loss of saved intent/settings, background work replay or an automatic host update. |
| DJ7: platform comprehension | DC2–3, CX6–8 | Use keyboard and assistive technology, light/dark backgrounds, reduced motion/transparency and multiple displays. Compare complete action counts. Fail on color-only status, unreadable controls, missing wheel capture or unnecessary interaction. |

## Work derived from the gaps

| Item | Owning repo / dependency | Completion evidence |
| --- | --- | --- |
| DSK1: qualify Mac preview gaps | Mac client; existing host APIs | DJ1–7 mapped to Mac clauses, fresh cold Reply/warm PWA, accessibility/display/network and performance evidence; retain untested cases |
| DSK2: establish compatibility floor | Host + each independently released client | Exact released-host matrix including pairing absence, unsupported protocol, revocation and bounded observations; no inferred minimum |
| DSK3: start Windows client | Future Windows owner; shared DC/CX/CS and public APIs | Platform vision/contracts, OS mechanism choices, explicit supported subset, fixture peer and Windows DJ1–7 evidence before parity claims |
| DSK4: integrated workspace | Client + bundled web owners; separate design | Versioned trusted-origin bridge, sign-in, files and navigation acceptance; no arbitrary native API exposure |
| DSK5: optional device execution | Shared UI-use/provider and canonical computer-use owners, host authorization, platform client | Separately revocable execution grants, exact device/profile/tab identity, local Stop/Take over and unknown-effect recovery; chat credentials alone are insufficient |

DSK4–5 are future design work, not preview release prerequisites. A Windows client
need not copy Mac widgets or a Mac-specific companion to satisfy the supported
journeys. Its supported surfaces and omissions must be explicit.

## Change impact and method

This documentation change identifies an existing client and extracts existing
desktop promises; it changes no wire fields, runtime, permissions or release policy.
Mac maintainers own platform evidence; Unified maintainers own shared seams.
The Windows owner is unassigned. Future shared changes use the
[CD impact record](development-plan.md#shared-change-impact-record).

This applies the method already adopted in [direction](direction.md): explicit
state, stable promises, one owner per rule, gap-derived work and falsifiable evidence.
It does not enroll these clients in Converge's service or impose its lane topology,
document line budgets or formal ledger machinery.
