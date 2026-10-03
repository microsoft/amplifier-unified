# Application update parity boundary

The external supervisor owns application release checks, preparation,
replacement, retained rollback, preferences, and release information. Native
source inventory and runtime generations belong to the native agent. Client
expansion, filtering, and drafts belong to each client.

## Owner and public API work

F12.03–.06 now have one supervisor contract for signed high-impact notices,
content-bound review receipts, bounded current/historical release notes, and a
published-release fallback. `updates.application.releaseNotes` reads a page;
`updates.application.reviewNotice` records the exact displayed digest.
Ordinary `inspect` returns only the release-information summary. Reads remain
available during held intake, while reviews follow the same mutation admission
as other agent/user actions.

A manual check retrieves optional notes in its existing signed channel request.
Reading history or reviewing notices does not start checks, inventory caches,
or prepare any runtime. The exact signed installed receipt seeds offline notes;
verified skipped releases are retained within 100 versions and 256 KiB.
Missing or malformed optional notes do not block updates. Invalid signatures
still fail. A publication must include `releaseNotes` before signing to expose
release-authored information; the fallback link remains available without it.
No legacy history or review receipts are silently migrated from another owner.

F12.20 now accepts intervals through one week, so hourly, six-hour, daily, and
weekly client choices are representable. This does not delay manual dispatch.

The facade already invalidated a pre-effect partial-acquisition rollback after
an unknown outcome. An added same-process regression proves that both the live
lease and reconciliation path refuse `admission-refused` after uncertainty.
Exact authenticated outcome evidence is still required to release that fence.

## Remaining acceptance and owner boundaries

- Web owns rendering high-impact notices, review controls, current/history
  expansion and fallback links. API/installed acceptance does not establish
  browser accessibility or visual parity. Do not mark F12.03–.06 fully migrated
  on API tests alone.
- F12.02 ahead-of-channel/build variant presentation and all failure variants
  still need assembled client acceptance. Current/catalog identities remain
  distinct; do not infer successful replacement from a matching version label.
- F12.14's legacy staged-activation button needs an explicit product disposition.
  The supervisor's prepare-and-wait-for-admission behavior is not proof of an
  independent reviewed activation control.
- F12.23–.25 require client acceptance of original versus reconciled receipts,
  failure history, bounded phase events and sanitized copy. Those use the public
  receipt/diagnostics operations; do not create another update-state authority.
- F12.07–.11 and F12.21 require native-owned bounded source pages with canonical
  source identity, tracking ref, current/available revisions, copy count, usage
  evidence (`configured`, `historical`, or `unknown`) and precise source issues.
  Other cached sources should load only on an explicit details request. Do not
  scan every cache on an ordinary check or expose local credentials/paths.
- A combined app/native/MCP update control coordinates independent public owners
  and their receipts. It must not confuse app replacement with native runtime
  adoption or replay uncertain work after a disconnect.

Validation for the new contract covers signed/unsigned boundaries, malformed
optional notes, review persistence and idempotency, stale digest refusal,
editorial aliases, deterministic ordering, limits, no extra fetch, offline
installed notes, authenticated RPC, facade authorization/intake and real signed
application replacement/offline resume in an isolated fixture. Production
publication/deployment and full browser parity remain separate qualification.
