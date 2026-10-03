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
    chmodSync(directory, 0o700);
    const path = join(directory, "updates.sqlite3");
    try {
      if (lstatSync(path).isSymbolicLink()) throw Error("linked_ledger");
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
    this.db = new DatabaseSync(path);
    chmodSync(path, 0o600);
    this.db.exec(
      "PRAGMA busy_timeout=5000; PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; CREATE TABLE IF NOT EXISTS owner (id INTEGER PRIMARY KEY, pid INTEGER NOT NULL, token TEXT NOT NULL); CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, value TEXT NOT NULL); CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, value TEXT NOT NULL); CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, value TEXT NOT NULL)",
    );
    try {
      this.db.exec("BEGIN IMMEDIATE");
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
      this.db.exec("COMMIT");
    } catch (error) {
      this.db.exec("ROLLBACK");
      this.db.close();
      throw error;
    }
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
  save(state: OwnerState) {
    this.db
      .prepare("UPDATE state SET value=? WHERE id=1")
      .run(JSON.stringify(state));
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
        "SELECT value FROM operations WHERE json_extract(value,'$.admissionSettlement.state') IN ('pending','unknown') ORDER BY rowid",
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
