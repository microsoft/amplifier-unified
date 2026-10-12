import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm,writeFile,readFile,symlink,chmod} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {acquireInstallationBackupLease} from '../src/installation-backup-lease.js';

const binding={installationId:'backup-fixture',dataScope:'backup-scope'};
async function fixture(t){const root=await mkdtemp(join(tmpdir(),'backup-lease-'));await chmod(root,0o700);t.after(()=>rm(root,{recursive:true,force:true}));return root;}
test('only one controller may publish discovery, including within the same process',async t=>{
 const root=await fixture(t),first=acquireInstallationBackupLease(root,binding);t.after(()=>first.close());
 assert.throws(()=>acquireInstallationBackupLease(root,binding),/backup_controller_already_running/);
 assert.throws(()=>acquireInstallationBackupLease(root,{...binding,dataScope:'other'}),/backup_controller_binding_conflict/);
 first.close();first.close();
 const next=acquireInstallationBackupLease(root,binding);next.close();
 assert.throws(()=>acquireInstallationBackupLease(root,{...binding,installationId:'other'}),/backup_controller_binding_conflict/);
});
test('a departed controller can be replaced without modifying uncertain receipts',async t=>{
 const root=await fixture(t),receipt=join(root,'active.json'),original='{"commandId":"original","status":"unknown"}\n';
 await writeFile(receipt,original,{mode:0o600});
 const module=new URL('../src/installation-backup-lease.js',import.meta.url).href;
 const child=spawnSync(process.execPath,['--input-type=module','-e',`import {acquireInstallationBackupLease} from ${JSON.stringify(module)};acquireInstallationBackupLease(${JSON.stringify(root)},${JSON.stringify(binding)});process.exit(0);`],{encoding:'utf8'});
 assert.equal(child.status,0,child.stderr);
 const next=acquireInstallationBackupLease(root,binding);next.close();
 assert.equal(await readFile(receipt,'utf8'),original);
});
test('controller ownership refuses linked or exposed storage',async t=>{
 const root=await fixture(t),outside=join(root,'other');await writeFile(outside,'',{mode:0o600});
 await symlink(outside,join(root,'controller.sqlite3'));
 assert.throws(()=>acquireInstallationBackupLease(root,binding),/backup_private_record_required/);
 await rm(join(root,'controller.sqlite3'));await chmod(root,0o755);
 assert.throws(()=>acquireInstallationBackupLease(root,binding),/backup_private_directory_required/);
});
