import {DatabaseSync} from 'node:sqlite';
import {lstatSync} from 'node:fs';

const fail = () => { throw Error('protection_authority_unavailable'); };
const present = path => {
  try { const stat = lstatSync(path); if (!stat.isFile()) fail(); return true; }
  catch (error) { if (error.code === 'ENOENT') return false; throw error; }
};

/** The two protection coordinators have always owned this complete, single
 * authoritative table. Validate it under their existing exclusive owner lease
 * before any writable open; indexes remain rebuildable after admission. */
export function inspectProtectionAuthority(path) {
  let db;
  try {
    const main = present(path), sidecars = ['-wal', '-shm', '-journal'].map(suffix => present(path + suffix));
    if (!main) { if (sidecars.some(Boolean)) fail(); return; }
    db = new DatabaseSync(path, {readOnly: true});
    const version = db.prepare('PRAGMA user_version').get().user_version;
    if (version !== 0) fail();
    const schema = db.prepare("SELECT type FROM sqlite_schema WHERE name='protections' LIMIT 2").all();
    if (schema.length !== 1 || schema[0].type !== 'table') fail();
    const columns = db.prepare("SELECT name,type,hidden FROM pragma_table_xinfo('protections') LIMIT 4").all();
    if (columns.length !== 3 || columns.some((column, index) => column.name !== ['command', 'state', 'body'][index] || column.type.toUpperCase() !== 'TEXT' || column.hidden !== 0)) fail();
  } catch { fail(); }
  finally { db?.close(); }
}
