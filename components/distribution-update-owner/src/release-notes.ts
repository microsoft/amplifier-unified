import { createHash } from "node:crypto";

export const PUBLISHED_RELEASES_URL =
  "https://github.com/microsoft/amplifier-unified/releases";
export const MAX_RELEASE_NOTES_BYTES = 256 * 1024;
export interface ReleaseNotice {
  id: string;
  title: string;
  detail: string;
  action: string;
  previousTitles?: string[];
}
export interface ReleaseNotesEntry {
  version: string;
  title: string;
  changes: string[];
  notices: ReleaseNotice[];
  publishedUrl: string;
}
export interface ReleaseNotesPublication {
  schema: "distribution-release-notes-publication-v1";
  entries: ReleaseNotesEntry[];
}
export type ReleaseNotesWarning =
  | null
  | "release_notes_unavailable"
  | "release_notes_invalid";
export interface NoticeReview {
  version: string;
  noticeId: string;
  contentDigest: string;
}
export interface ReleaseNotesSummary {
  schema: "distribution-release-notes-v1";
  revision: string;
  currentVersion: string | null;
  recommendedVersion: string | null;
  publishedReleasesUrl: string;
  warning: ReleaseNotesWarning;
  totalEntries: number;
  unreviewedCount: number;
}
export interface ReviewedReleaseNotesEntry
  extends Omit<ReleaseNotesEntry, "notices"> {
  notices: Array<
    Omit<ReleaseNotice, "previousTitles"> & {
      contentDigest: string;
      reviewedAt: number | null;
      reviewReceiptId: string | null;
    }
  >;
}
export interface ReleaseNotesPage extends ReleaseNotesSummary {
  entries: ReviewedReleaseNotesEntry[];
  nextCursor: string | null;
}
export interface ReleaseNotesQuery {
  cursor?: string;
  limit?: number;
}
const hash = (value: unknown) =>
  createHash("sha256").update(JSON.stringify(value)).digest("hex");
function text(value: unknown, limit: number): string {
  if (
    typeof value !== "string" ||
    !value.trim() ||
    value.length > limit ||
    /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/.test(value)
  )
    throw Error("release_notes_invalid");
  return value;
}
export function releaseNotesVersion(value: unknown): string {
  const version = text(value, 80);
  if (!/^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$/.test(version))
    throw Error("release_notes_invalid");
  return version;
}
function publishedUrl(value: unknown): string {
  const raw = text(value, 2048),
    url = new URL(raw);
  if (
    url.protocol !== "https:" ||
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    /[\x00-\x20\x7f]/.test(raw)
  )
    throw Error("release_notes_invalid");
  return url.href;
}
export function noticeReview(value: NoticeReview): NoticeReview {
  const version = releaseNotesVersion(value?.version),
    noticeId = text(value.noticeId, 80);
  if (
    !/^[a-z0-9][a-z0-9-]{0,79}$/.test(noticeId) ||
    !/^[a-f0-9]{64}$/.test(value.contentDigest)
  )
    throw Error("invalid_notice_review");
  return { version, noticeId, contentDigest: value.contentDigest };
}
export function noticeDigest(version: string, notice: ReleaseNotice): string {
  return hash([version, notice.id, notice.title, notice.detail, notice.action]);
}
export function acceptedNoticeDigests(
  version: string,
  notice: ReleaseNotice,
): string[] {
  return [
    noticeDigest(version, notice),
    ...(notice.previousTitles ?? []).map((title) =>
      noticeDigest(version, { ...notice, title }),
    ),
  ];
}
export function parseReleaseNotes(value: unknown): ReleaseNotesPublication {
  if (Buffer.byteLength(JSON.stringify(value) ?? "") > MAX_RELEASE_NOTES_BYTES)
    throw Error("release_notes_invalid");
  const input = value as ReleaseNotesPublication;
  if (
    input?.schema !== "distribution-release-notes-publication-v1" ||
    !Array.isArray(input.entries) ||
    input.entries.length > 100
  )
    throw Error("release_notes_invalid");
  const entries = input.entries.map((entry) => {
    const version = releaseNotesVersion(entry?.version);
    if (
      !Array.isArray(entry.changes) ||
      entry.changes.length < 1 ||
      entry.changes.length > 30 ||
      !Array.isArray(entry.notices) ||
      entry.notices.length > 5
    )
      throw Error("release_notes_invalid");
    const notices = entry.notices.map((notice) => {
      const id = text(notice?.id, 80);
      if (!/^[a-z0-9][a-z0-9-]{0,79}$/.test(id))
        throw Error("release_notes_invalid");
      if (
        notice.previousTitles !== undefined &&
        (!Array.isArray(notice.previousTitles) ||
          notice.previousTitles.length > 5)
      )
        throw Error("release_notes_invalid");
      return {
        id,
        title: text(notice.title, 160),
        detail: text(notice.detail, 2000),
        action: text(notice.action, 1500),
        ...(notice.previousTitles
          ? { previousTitles: notice.previousTitles.map((t) => text(t, 160)) }
          : {}),
      };
    });
    if (new Set(notices.map((n) => n.id)).size !== notices.length)
      throw Error("release_notes_invalid");
    return {
      version,
      title: text(entry.title, 160),
      changes: entry.changes.map((c) => text(c, 1500)),
      notices,
      publishedUrl: publishedUrl(
        entry.publishedUrl ?? `${PUBLISHED_RELEASES_URL}/tag/v${version}`,
      ),
    };
  });
  if (new Set(entries.map((e) => e.version)).size !== entries.length)
    throw Error("release_notes_invalid");
  const result = {
    schema: "distribution-release-notes-publication-v1" as const,
    entries,
  };
  if (Buffer.byteLength(JSON.stringify(result)) > MAX_RELEASE_NOTES_BYTES)
    throw Error("release_notes_invalid");
  return result;
}
// Compare full versions deterministically, not lexically (1.10 precedes 1.9).
// BigInt avoids precision loss for untrusted numeric version segments.
export function newestNotesFirst(
  a: ReleaseNotesEntry,
  b: ReleaseNotesEntry,
): number {
  const parts = (v: string) => v.split(/[+-]/)[0].split(".").map(BigInt);
  const x = parts(a.version),
    y = parts(b.version);
  for (let i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i] > y[i] ? -1 : 1;
  const pre = (v: string) => v.split("+")[0].split("-").slice(1).join("-");
  const p = pre(a.version),
    q = pre(b.version);
  if (!p !== !q) return p ? 1 : -1;
  const left = p.split("."),
    right = q.split(".");
  for (let i = 0; i < Math.max(left.length, right.length); i++) {
    if (left[i] === right[i]) continue;
    if (left[i] === undefined) return 1;
    if (right[i] === undefined) return -1;
    const numericLeft = /^[0-9]+$/.test(left[i]),
      numericRight = /^[0-9]+$/.test(right[i]);
    if (numericLeft !== numericRight) return numericLeft ? 1 : -1;
    if (numericLeft && numericRight) {
      if (BigInt(left[i]) !== BigInt(right[i]))
        return BigInt(left[i]) > BigInt(right[i]) ? -1 : 1;
    } else return left[i] > right[i] ? -1 : 1;
  }
  return a.version < b.version ? 1 : a.version > b.version ? -1 : 0;
}
export function mergeReleaseNotes(
  previous: ReleaseNotesPublication,
  incoming: ReleaseNotesPublication,
  currentVersion: string | null,
): ReleaseNotesPublication {
  const merged = [
    ...new Map(
      [...previous.entries, ...incoming.entries].map((e) => [e.version, e]),
    ).values(),
  ].sort(newestNotesFirst);
  const priority = [
    ...merged.filter((e) => e.version === currentVersion),
    ...merged.filter((e) => e.version !== currentVersion),
  ];
  const result: ReleaseNotesPublication = {
    schema: "distribution-release-notes-publication-v1",
    entries: [],
  };
  for (const entry of priority) {
    if (result.entries.length === 100) break;
    result.entries.push(entry);
    if (Buffer.byteLength(JSON.stringify(result)) > MAX_RELEASE_NOTES_BYTES)
      result.entries.pop();
  }
  result.entries.sort(newestNotesFirst);
  return result;
}
export function notesRevision(value: unknown): string {
  return hash(value);
}
export function notesPage(
  summary: ReleaseNotesSummary,
  entries: ReviewedReleaseNotesEntry[],
  query: ReleaseNotesQuery = {},
): ReleaseNotesPage {
  const limit = query.limit ?? 10;
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 20)
    throw Error("release_notes_query_invalid");
  let offset = 0;
  if (query.cursor !== undefined) {
    if (
      typeof query.cursor !== "string" ||
      !/^([a-f0-9]{64}):([0-9]{1,3})$/.test(query.cursor)
    )
      throw Error("release_notes_query_invalid");
    const [revision, position] = query.cursor.split(":");
    if (revision !== summary.revision)
      throw Error("release_notes_cursor_stale");
    offset = Number(position);
    if (offset > entries.length) throw Error("release_notes_query_invalid");
  }
  return {
    ...summary,
    entries: structuredClone(entries.slice(offset, offset + limit)),
    nextCursor:
      offset + limit < entries.length
        ? `${summary.revision}:${offset + limit}`
        : null,
  };
}
