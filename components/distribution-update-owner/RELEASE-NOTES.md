# Release information contract v1

Application release information belongs to the external distribution supervisor.
It is independent of native component inventory. Reading or reviewing it never
checks the network, scans caches, loads sessions, installs, or restarts anything.
Manual checks obtain notes inside the existing signed channel response.

## Public owner API and AHP actions

- `owner.releaseNotes({cursor?, limit?})` / `updates.application.releaseNotes`
  returns the page below. `limit` defaults to 10 and is 1–20; `cursor` is opaque.
  A changed history or review set rejects an old cursor with
  `release_notes_cursor_stale`; restart pagination without replaying a mutation.
- `owner.reviewNotice(commandId, {version, noticeId, contentDigest})` /
  `updates.application.reviewNotice` persists a receipt for exactly the displayed
  notice. Its completed receipt has `command: "review-notice"`,
  `phase: "notice_reviewed"`, and `noticeReview` containing those three fields.
  A changed or missing notice yields a failed receipt (`notice_changed` or
  `notice_not_found`). Duplicate command IDs return the original receipt;
  different arguments with the same ID are rejected. Reviews are installation
  shared; client expansion, filters, and drafts are not stored by this owner.
- `owner.inspect().releaseNotes` is the same summary as the page, without
  `entries` or `nextCursor`. Normal inspection does not project the full history.
- `owner.receipt(commandId)` reads the durable result. Existing supervisor push
  notifications and the `application-updates` topic invalidate on review/check.
- All reads are permitted during held admission. Review is a mutation and is
  subject to the application facade's intake gate. No additional UI-only API.

```ts
interface ReleaseNotesSummary {
  schema: "distribution-release-notes-v1";
  revision: string; // SHA-256 over retained information and current projections
  currentVersion: string | null;
  recommendedVersion: string | null;
  publishedReleasesUrl: string; // HTTPS, no credentials/query/fragment
  warning: null | "release_notes_unavailable" | "release_notes_invalid";
  totalEntries: number;
  unreviewedCount: number;
}
interface ReleaseNotesPage extends ReleaseNotesSummary {
  entries: Array<{
    version: string;
    title: string;
    changes: string[]; // plain text, never HTML or executable Markdown
    publishedUrl: string;
    notices: Array<{
      id: string;
      title: string;
      detail: string;
      action: string;
      contentDigest: string;
      reviewedAt: number | null; // epoch milliseconds
      reviewReceiptId: string | null;
    }>;
  }>;
  nextCursor: string | null;
}
```

Missing notes do not block checking/installing releases. The fallback is
`https://github.com/microsoft/amplifier-unified/releases`. Entries may provide
an explicit HTTPS published URL, otherwise the official version-tag link is
constructed. Release text is publication data, not execution authority.

## Signed publisher input

The optional `releaseNotes` member of `distribution-channel-v1` is covered by
its existing Ed25519 signature:

```json
{
  "schema": "distribution-release-notes-publication-v1",
  "entries": [{
    "version": "1.0.0",
    "title": "Faster manual updates",
    "changes": ["Manual checks and installs start immediately."],
    "notices": [{
      "id": "manual-updates",
      "title": "Immediate update checks",
      "detail": "Manual checks bypass the completed-result cache.",
      "action": "Use Check for updates to request a fresh result."
    }]
  }]
}
```

History is bounded to 100 versions / 256 KiB, including skipped releases
provided by the publisher. Each entry allows 30 changes of 1,500 characters
and five notices (title 160, detail 2,000, action 1,500). An optional
`previousTitles` array (at most five) explicitly preserves review across title
corrections only; changed detail/action always requires review again. Exact
current version is retained preferentially when bounded history is pruned.
Previously verified history remains available offline or after failed checks.
Malformed optional notes produce a sanitized warning while release trust and
installation validation remain unchanged. Invalid channel signatures still fail.
