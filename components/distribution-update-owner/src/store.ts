import {preflightLedger,validateLedger,markLedger} from './sqlite-ledger-schema.js';
import {
  acceptedNoticeDigests,
  mergeReleaseNotes,
  type ReleaseNotesPublication,
  type ReleaseNotesWarning,
  type NoticeReview,
} from "./release-notes.js";
import { DatabaseSync } from "node:sqlite";
import { mkdirSync, lstatSync, chmodSync } from "node:fs";
import { join } from "node:path";
import { randomUUID, createHash } from "node:crypto";
import type { Operation, OwnerState } from "./types.js";

/** One live owner per private ledger. Dead-owner recovery marks uncertainty,
 * never reconstructs work from the queue. Command identities are not evicted. */
export class Store {
  private db: DatabaseSync;
  private lease = randomUUID();
  constructor(directory: string, initial: OwnerState) {
    mkdirSync(directory, { recursive: true, mode: 0o700 });
    if (lstatSync(directory).isSymbolicLink())
      throw Error("linked_owner_directory");
    const path = join(directory, "updates.sqlite3");
    try {
      if (lstatSync(path).isSymbolicLink()) throw Error("linked_ledger");
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
    const fresh=preflightLedger(path,'updates',initial);
    this.db = new DatabaseSync(path);
    let begun=false;
    try {
      chmodSync(directory,0o700);chmodSync(path,0o600);
      this.db.exec('PRAGMA busy_timeout=5000; PRAGMA synchronous=FULL');
      if(fresh)this.db.exec('PRAGMA journal_mode=WAL');
      this.db.exec('BEGIN IMMEDIATE');begun=true;
      validateLedger(this.db,'updates',initial,fresh);
    this.db.exec(
      "CREATE TABLE IF NOT EXISTS owner (id INTEGER PRIMARY KEY, pid INTEGER NOT NULL, token TEXT NOT NULL); CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, value TEXT NOT NULL); CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, value TEXT NOT NULL); CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, value TEXT NOT NULL); CREATE TABLE IF NOT EXISTS release_notes (id INTEGER PRIMARY KEY, value TEXT NOT NULL, revision TEXT NOT NULL, warning TEXT); CREATE TABLE IF NOT EXISTS notice_reviews (digest TEXT PRIMARY KEY, receipt_id TEXT NOT NULL, reviewed_at INTEGER NOT NULL)",
    );
    this.db.exec("CREATE TABLE IF NOT EXISTS preference_revision (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL); CREATE TABLE IF NOT EXISTS preference_reset_reviews (id TEXT PRIMARY KEY, value TEXT NOT NULL); CREATE TABLE IF NOT EXISTS preference_reset_commands (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, value TEXT NOT NULL)");
      const held = this.db.prepare("SELECT pid FROM owner WHERE id=1").get() as
        | { pid: number }
        | undefined;
      if (held) {
        let alive = true;
        try {
          process.kill(held.pid, 0);
        } catch (error) {
          if ((error as NodeJS.ErrnoException).code === "ESRCH") alive = false;
          else if ((error as NodeJS.ErrnoException).code !== "EPERM")
            throw error;
        }
        if (alive) throw Error("owner_already_running");
      }
      this.db
        .prepare("INSERT OR REPLACE INTO owner VALUES (1,?,?)")
        .run(process.pid, this.lease);
      this.db
        .prepare("INSERT OR IGNORE INTO state VALUES (1,?)")
        .run(JSON.stringify(initial));
      if (this.state().schema !== 1) throw Error("unsupported_state_schema");
      if (this.state().dataScope !== initial.dataScope)
        throw Error("owner_scope_conflict");
      this.db.prepare("INSERT OR IGNORE INTO preference_revision VALUES (1,?)").run(randomUUID());
      // Older ready receipts establish app readiness, not that intake reopened.
      // Recover that distinction without replaying a stop, launch or update.
      for (const row of this.db
        .prepare(
          "SELECT value FROM operations WHERE json_extract(value,'$.admission') IS NOT NULL OR json_extract(value,'$.admissionSettlement') IS NOT NULL",
        )
        .all()) {
        const op: Operation = JSON.parse(row.value as string);
        if (
          !op.admissionSettlement ||
          op.admissionSettlement.state === "pending"
        ) {
          op.admissionSettlement = {
            state: "unknown",
            outcome:
              op.phase === "ready"
                ? "ready"
                : op.phase === "pre_restart_refused"
                  ? "unchanged"
                  : "unknown",
            updatedAt: Date.now(),
          };
          this.write(op);
        }
      }
      for (const op of this.pending())
        if (op.status !== "unknown") {
          if (op.command === "check") {
            const state = this.state();
            state.catalog = null;
            state.lastCheckSucceeded = false;
            this.save(state);
          }
          op.status = "unknown";
          op.errorCode = "owner_interrupted";
          op.updatedAt = Date.now();
          this.write(op);
        }
      markLedger(this.db);
      this.db.exec("COMMIT");
    } catch (error) {
      if(begun)this.db.exec("ROLLBACK");
      this.db.close();
      throw error;
    }
  }
  transaction<T>(action: () => T): T {
    this.db.exec("BEGIN IMMEDIATE");
    try {
      const value = action();
      this.db.exec("COMMIT");
      return value;
    } catch (error) {
      this.db.exec("ROLLBACK");
      throw error;
    }
  }
  notesRevision(): string {
    return (
      (this.db.prepare("SELECT revision FROM release_notes WHERE id=1").get()
        ?.revision as string) ?? "empty"
    );
  }
  notes(): {
    publication: ReleaseNotesPublication;
    warning: ReleaseNotesWarning;
  } {
    const row = this.db
      .prepare("SELECT value,warning FROM release_notes WHERE id=1")
      .get();
    return row
      ? {
          publication: JSON.parse(row.value as string),
          warning: row.warning as ReleaseNotesWarning,
        }
      : {
          publication: {
            schema: "distribution-release-notes-publication-v1",
            entries: [],
          },
          warning: "release_notes_unavailable",
        };
  }
  saveNotes(
    incoming: ReleaseNotesPublication | undefined,
    warning: ReleaseNotesWarning,
    currentVersion: string | null,
  ): void {
    const saved = this.notes(),
      previous = saved.publication;
    const publication = incoming
      ? mergeReleaseNotes(previous, incoming, currentVersion)
      : previous;
    if (
      this.notesRevision() !== "empty" &&
      saved.warning === warning &&
      JSON.stringify(previous) === JSON.stringify(publication)
    )
      return;
    this.transaction(() => {
      this.db
        .prepare("INSERT OR REPLACE INTO release_notes VALUES (1,?,?,?)")
        .run(JSON.stringify(publication), randomUUID(), warning);
      const retained = new Set(
        publication.entries.flatMap((e) =>
          e.notices.flatMap((n) => acceptedNoticeDigests(e.version, n)),
        ),
      );
      for (const row of this.db
        .prepare("SELECT digest FROM notice_reviews")
        .all())
        if (!retained.has(row.digest as string))
          this.db
            .prepare("DELETE FROM notice_reviews WHERE digest=?")
            .run(row.digest);
    });
  }
  reviews(): Map<string, { reviewedAt: number; reviewReceiptId: string }> {
    return new Map(
      this.db
        .prepare("SELECT * FROM notice_reviews")
        .all()
        .map((row) => [
          row.digest as string,
          {
            reviewedAt: row.reviewed_at as number,
            reviewReceiptId: row.receipt_id as string,
          },
        ]),
    );
  }
  saveReview(review: NoticeReview, op: Operation): void {
    const inserted = this.db
      .prepare("INSERT OR IGNORE INTO notice_reviews VALUES (?,?,?)")
      .run(review.contentDigest, op.id, op.updatedAt);
    if (inserted.changes)
      this.db
        .prepare("UPDATE release_notes SET revision=? WHERE id=1")
        .run(randomUUID());
  }
  state(): OwnerState {
    return JSON.parse(
      (
        this.db.prepare("SELECT value FROM state WHERE id=1").get() as {
          value: string;
        }
      ).value,
    );
  }
  save(state: OwnerState, preferencesWritten = false) {
    // Track ordinary writes, activation/rollback changes, and resets alike.
    // A value hash would miss A -> B -> A and permit an obsolete review.
    if (preferencesWritten || JSON.stringify(state.preferences) !== JSON.stringify(this.state().preferences))
      this.db.prepare("UPDATE preference_revision SET value=? WHERE id=1").run(randomUUID());
    this.db
      .prepare("UPDATE state SET value=? WHERE id=1")
      .run(JSON.stringify(state));
  }
  preferenceRevision(): string {
    return this.db.prepare("SELECT value FROM preference_revision WHERE id=1").get()!.value as string;
  }
  resetReview(id: string): Record<string, any> | null {
    const row = this.db.prepare("SELECT value FROM preference_reset_reviews WHERE id=?").get(id);
    return row ? JSON.parse(row.value as string) : null;
  }
  saveResetReview(id: string, value: Record<string, any>): void {
    this.db.prepare("INSERT INTO preference_reset_reviews VALUES (?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value").run(id, JSON.stringify(value));
  }
  resetCommand(id: string): {fingerprint: string; receipt: Record<string, any>} | null {
    const row = this.db.prepare("SELECT fingerprint,value FROM preference_reset_commands WHERE id=?").get(id);
    return row ? {fingerprint: row.fingerprint as string, receipt: JSON.parse(row.value as string)} : null;
  }
  saveResetCommand(id: string, fingerprint: string, receipt: Record<string, any>): void {
    this.db.prepare("INSERT INTO preference_reset_commands VALUES (?,?,?)").run(id, fingerprint, JSON.stringify(receipt));
  }
  accept(op: Operation): { operation: Operation; fresh: boolean } {
    const fingerprint = createHash("sha256")
      .update(JSON.stringify([op.command, op.args]))
      .digest("hex");
    const old = this.db
      .prepare("SELECT fingerprint,value FROM operations WHERE id=?")
      .get(op.id) as { fingerprint: string; value: string } | undefined;
    if (old) {
      if (old.fingerprint !== fingerprint)
        throw Error("command_identity_conflict");
      return { operation: JSON.parse(old.value), fresh: false };
    }
    this.db
      .prepare("INSERT INTO operations VALUES (?,?,?)")
      .run(op.id, fingerprint, JSON.stringify(op));
    return { operation: op, fresh: true };
  }
  read(id: string): Operation | null {
    const row = this.db
      .prepare("SELECT value FROM operations WHERE id=?")
      .get(id) as { value: string } | undefined;
    return row ? JSON.parse(row.value) : null;
  }
  write(op: Operation) {
    this.db
      .prepare("UPDATE operations SET value=? WHERE id=?")
      .run(JSON.stringify(op), op.id);
  }
  pending(): Operation[] {
    return this.db
      .prepare(
        "SELECT value FROM operations WHERE json_extract(value,'$.status') IN ('queued','running','waiting','unknown') ORDER BY rowid",
      )
      .all()
      .map((row) => JSON.parse(row.value as string));
  }
  recent(limit = 50): Operation[] {
    return this.db
      .prepare("SELECT value FROM operations ORDER BY rowid DESC LIMIT ?")
      .all(limit)
      .map((row) => JSON.parse(row.value as string));
  }
  unsettledAdmissions(): Operation[] {
    return this.db
      .prepare(
        "SELECT value FROM operations WHERE json_extract(value,'$.admissionSettlement.state') IN ('pending','unknown') AND COALESCE(json_extract(value,'$.maintenanceRecovery.state'),'') != 'settled' ORDER BY rowid",
      )
      .all()
      .map((row) => JSON.parse(row.value as string));
  }
  commit(op: Operation, state: OwnerState) {
    this.db.exec("BEGIN IMMEDIATE");
    try {
      this.write(op);
      this.save(state);
      this.db.exec("COMMIT");
    } catch (error) {
      this.db.exec("ROLLBACK");
      throw error;
    }
  }
  event(value: Record<string, unknown>) {
    this.db
      .prepare("INSERT INTO events(value) VALUES (?)")
      .run(JSON.stringify(value));
    this.db.exec(
      "DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 100)",
    );
  }
  events(): Record<string, unknown>[] {
    return this.db
      .prepare("SELECT value FROM events ORDER BY id")
      .all()
      .map((row) => JSON.parse(row.value as string));
  }
  close() {
    this.db.prepare("DELETE FROM owner WHERE id=1 AND token=?").run(this.lease);
    this.db.close();
  }
}
