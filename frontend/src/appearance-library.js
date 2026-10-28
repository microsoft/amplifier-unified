// Keep edit targets bound to the exact read, not a later background listing.
export function appearanceMutationTarget(entry){
 if(entry?.source!=='saved'||!entry.id?.startsWith('saved:')||!/^[a-f0-9]{64}$/.test(entry.fingerprint||''))throw Error('Read a saved appearance before updating or deleting it.');
 return {id:entry.id,expectedFingerprint:entry.fingerprint};
}

export function appearanceImportArgs(fileName,css){
 return {css,fallbackName:fileName.replace(/\.amplifier\.css$|\.css$/i,'').slice(0,100)||'Imported appearance'};
}