import { DatabaseSync } from "node:sqlite";
import { mkdirSync, lstatSync, chmodSync } from "node:fs";
import { join } from "node:path";
import { randomUUID, createHash } from "node:crypto";
import type { ServiceRecord, ServiceIdentity } from "./service-types.js";
/** Private fully synchronous command ledger. Retained PID only refuses a second
 * ledger writer; it can NEVER adopt, stop or signal a service process. */
export class ServiceStore {
  private db: DatabaseSync;
  private lease = randomUUID();
  constructor(
    directory: string,
    binding: Pick<ServiceIdentity, "installationId" | "dataScope" | "ownerId">,
  ) {
    mkdirSync(directory, { recursive: true, mode: 0o700 });
    if (lstatSync(directory).isSymbolicLink())
      throw Error("linked_service_directory");
    chmodSync(directory, 0o700);
    const path = join(directory, "service.sqlite3");
    try {
      if (lstatSync(path).isSymbolicLink())
        throw Error("linked_service_ledger");
    } catch (e) {
      if ((e as NodeJS.ErrnoException).code !== "ENOENT") throw e;
    }
    this.db = new DatabaseSync(path);
    chmodSync(path, 0o600);
    this.db.exec(
      "PRAGMA busy_timeout=5000;PRAGMA journal_mode=WAL;PRAGMA synchronous=FULL;CREATE TABLE IF NOT EXISTS owner(id INTEGER PRIMARY KEY,pid INTEGER,token TEXT);CREATE TABLE IF NOT EXISTS binding(id INTEGER PRIMARY KEY,value TEXT);CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,fingerprint TEXT,value TEXT)",
    );
    try {
      this.db.exec("BEGIN IMMEDIATE");
      const old = this.db.prepare("SELECT pid FROM owner WHERE id=1").get();
      if (old) {
        let alive = true;
        try {
          process.kill(Number(old.pid), 0);
        } catch (e) {
          if ((e as NodeJS.ErrnoException).code === "ESRCH") alive = false;
          else if ((e as NodeJS.ErrnoException).code !== "EPERM") throw e;
        }
        if (alive) throw Error("service_owner_already_running");
      }
      this.db
        .prepare("INSERT OR REPLACE INTO owner VALUES(1,?,?)")
        .run(process.pid, this.lease);
      this.db
        .prepare("INSERT OR IGNORE INTO binding VALUES(1,?)")
        .run(JSON.stringify(binding));
      if (
        this.db.prepare("SELECT value FROM binding WHERE id=1").get()!.value !==
        JSON.stringify(binding)
      )
        throw Error("service_owner_binding_conflict");
      for (const op of this.all())
        if (
          op.status === "running" ||
          op.admissionSettlement?.state === "pending"
        ) {
          if (op.status === "running") op.status = "unknown";
          if (op.admissionSettlement?.state === "pending")
            op.admissionSettlement.state = "unknown";
          op.errorCode = "service_owner_interrupted";
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
  accept(record: ServiceRecord, args: unknown) {
    const fingerprint = createHash("sha256")
        .update(JSON.stringify(args))
        .digest("hex"),
      old = this.db
        .prepare("SELECT fingerprint,value FROM commands WHERE id=?")
        .get(record.commandId);
    if (old) {
      if (old.fingerprint !== fingerprint)
        throw Error("command_identity_conflict");
      return {
        fresh: false,
        record: JSON.parse(String(old.value)) as ServiceRecord,
      };
    }
    this.db
      .prepare("INSERT INTO commands VALUES(?,?,?)")
      .run(record.commandId, fingerprint, JSON.stringify(record));
    return { fresh: true, record };
  }
  read(id: string): ServiceRecord | null {
    const row = this.db
      .prepare("SELECT value FROM commands WHERE id=?")
      .get(id);
    return row ? JSON.parse(String(row.value)) : null;
  }
  all(): ServiceRecord[] {
    return this.db
      .prepare("SELECT value FROM commands ORDER BY rowid DESC")
      .all()
      .map((row) => JSON.parse(String(row.value)));
  }
  write(record: ServiceRecord) {
    this.db
      .prepare("UPDATE commands SET value=? WHERE id=?")
      .run(JSON.stringify(record), record.commandId);
  }
  close() {
    this.db.prepare("DELETE FROM owner WHERE id=1 AND token=?").run(this.lease);
    this.db.close();
  }
}
