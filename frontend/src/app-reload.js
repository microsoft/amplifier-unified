export function changedFrontend(loaded,installed){
 const valid=value=>typeof value==='string'&&/^[a-f0-9]{12,64}$/.test(value);
 return valid(loaded?.id)&&valid(installed?.id)&&(loaded.id!==installed.id||loaded.version!==installed.version);
}

export async function reloadWhenSaved({prepare,reload}){
 await prepare();
 reload();
}
