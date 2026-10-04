import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {createRetentionProtection} from '../src/retention-protection.js';
import {createManagedFilesProtection} from '../src/managed-files-protection.js';

function fixture(kind, directory, calls = {count: 0}) {
  const participant = {id: 'inert-owner', retentionHide: {version: 1}, managedFiles: {version: 1, preservesCanonical: true},
    async acquire() { calls.count++; throw Error('Lost inert acquisition acknowledgement'); }, async reconcileRelease() { throw Error('No release proof'); }};
  const options = {directory, instanceId: 'instance', dataScope: 'scope', participants: [participant],
    readItemReceipt: async () => null, readEffectReceipt: async () => null};
  return {calls, create: () => kind === 'retention' ? createRetentionProtection(options) : createManagedFilesProtection(options)};
}
function input(kind) {
  return kind === 'retention' ? {commandId: 'original', session: 'ahp-session:/example', operation: 'hide'} :
    {commandId: 'original', session: 'ahp-session:/example', operation: 'dispose-owned-files', reviewHash: 'c'.repeat(64),
      allocation: {allocationId: '01234567-1234-1234-1234-123456789abc', executionDirectory: '/owned/example/files',
        allocationHash: 'a'.repeat(64), treeHash: 'b'.repeat(64), entryCount: 2, bytes: 12}};
}
const hash = path => createHash('sha256').update(readFileSync(path)).digest('hex');
const directory = t => { const path = mkdtempSync(join(tmpdir(), 'protection-authority-')); t.after(() => rmSync(path, {recursive: true, force: true})); return path; };
async function unknown(f, kind) { const owner = f.create(); await assert.rejects(owner.acquire(input(kind))); assert.equal(owner.receipt('original').state, 'unknown'); owner.close(); }

for (const kind of ['retention', 'managed']) {
  test(`${kind}: healthy original unknown remains inspectable without repeated acquisition`, async t => {
    const path = directory(t), f = fixture(kind, path); await unknown(f, kind);
    const owner = f.create(); try { assert.equal(owner.receipt('original').state, 'unknown'); await assert.rejects(owner.acquire(input(kind))); assert.equal(f.calls.count, 1); } finally { owner.close(); }
  });
  test(`${kind}: lost protections table refuses before writable startup and releases its lease`, async t => {
    const path = directory(t), f = fixture(kind, path); await unknown(f, kind);
    const file = join(path, 'protection.sqlite3'), db = new DatabaseSync(file); db.exec('DROP TABLE protections'); db.close();
    const before = hash(file);
    for (let i = 0; i < 2; i++) { assert.throws(f.create, /protection_authority_unavailable/); assert.equal(hash(file), before); }
    assert.equal(f.calls.count, 1);
  });
  test(`${kind}: invalid table, columns and schema version refuse without rewriting authority`, t => {
    for (const sql of ["CREATE TABLE protections(command TEXT,state TEXT)", "CREATE VIEW protections AS SELECT 'x' command, 'unknown' state, '{}' body", "CREATE TABLE protections(command TEXT,state TEXT,body TEXT);PRAGMA user_version=9"]) {
      const path = directory(t), file = join(path, 'protection.sqlite3'), db = new DatabaseSync(file); db.exec(sql); db.close();
      const before = hash(file); assert.throws(fixture(kind, path).create, /protection_authority_unavailable/); assert.equal(hash(file), before);
    }
  });
  test(`${kind}: existing empty main and every empty, nonempty or dangling orphan refuse`, t => {
    for (const suffix of ['', '-wal', '-shm', '-journal']) for (const form of ['empty', 'nonempty', 'dangling']) {
      const path = directory(t), file = join(path, 'protection.sqlite3' + suffix);
      if (form === 'dangling') symlinkSync(join(path, 'absent'), file); else writeFileSync(file, form === 'empty' ? '' : 'retained evidence');
      const before = form === 'dangling' ? null : hash(file);
      assert.throws(fixture(kind, path).create, /protection_authority_unavailable/);
      if (before) assert.equal(hash(file), before);
      if (suffix) assert.throws(() => readFileSync(join(path, 'protection.sqlite3')), {code: 'ENOENT'});
    }
  });
  test(`${kind}: missing derived indexes rebuild while preserving original unknown`, async t => {
    const path = directory(t), f = fixture(kind, path); await unknown(f, kind);
    const db = new DatabaseSync(join(path, 'protection.sqlite3')); db.exec('DROP INDEX IF EXISTS protection_state;DROP INDEX IF EXISTS protection_unfinished'); db.close();
    const owner = f.create(); try { assert.equal(owner.receipt('original').state, 'unknown'); await assert.rejects(owner.acquire(input(kind))); assert.equal(f.calls.count, 1); } finally { owner.close(); }
  });
  test(`${kind}: actual crash-left WAL is read before admission; missing table and missing main refuse`, async t => {
    for (const damage of ['none', 'table', 'main']) {
      const path = directory(t), file = join(path, 'protection.sqlite3');
      const script = `import {DatabaseSync} from 'node:sqlite';
        import {createRetentionProtection} from ${JSON.stringify(new URL('../src/retention-protection.js', import.meta.url).href)};
        import {createManagedFilesProtection} from ${JSON.stringify(new URL('../src/managed-files-protection.js', import.meta.url).href)};
        ${fixture.toString()};${input.toString()};
        const f=fixture(${JSON.stringify(kind)},${JSON.stringify(path)});const owner=f.create();try{await owner.acquire(input(${JSON.stringify(kind)}));}catch{}
        ${damage === 'table' ? `const db=new DatabaseSync(${JSON.stringify(file)});db.exec('DROP TABLE protections');` : ''}
        process.kill(process.pid,'SIGKILL');`;
      const child = spawnSync(process.execPath, ['--input-type=module', '-e', script], {encoding: 'utf8', timeout: 5000});
      assert.equal(child.signal, 'SIGKILL', child.stderr); assert.ok(readFileSync(file + '-wal').length > 0);
      if (damage === 'main') rmSync(file);
      const wal = hash(file + '-wal'), main = damage === 'main' ? null : hash(file), f = fixture(kind, path);
      if (damage !== 'none') {
        for (let i = 0; i < 2; i++) { assert.throws(f.create, /protection_authority_unavailable/); assert.equal(hash(file + '-wal'), wal); if (main) assert.equal(hash(file), main); }
      } else { const owner = f.create(); try { assert.equal(owner.receipt('original').state, 'unknown'); await assert.rejects(owner.acquire(input(kind))); } finally { owner.close(); } }
      assert.equal(f.calls.count, 0);
    }
  });
}
