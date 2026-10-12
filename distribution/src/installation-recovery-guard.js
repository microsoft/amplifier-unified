import {lstat} from 'node:fs/promises';
import {join} from 'node:path';

export async function assertInstallationRecoverySettled(directory){
 try {await lstat(join(directory,'RECOVERY-PENDING.json'));}
 catch(error){if(error.code==='ENOENT')return;throw error;}
 throw Error('installation_recovery_requires_inspection');
}
