const keys = ['installationId', 'dataScope', 'ownerId', 'instanceId', 'releaseDigest'];
const token = (value) => typeof value === 'string' && value.length > 0 && value.length <= 200 && !/[\x00-\x1f]/.test(value);
export function serviceIdentity(value) {
    if (!value || typeof value !== 'object' || Object.keys(value).length !== keys.length || keys.some(key => !token(value[key])))
        throw Error('Exact bounded service identity required');
    return Object.fromEntries(keys.map(key => [key, value[key]]));
}
export function sameIdentity(left, right) { return keys.every(key => left[key] === right[key]); }
export function validateServiceRelease(context, outcome, proof) {
    const allowed = new Set(['verified', 'fenceId', 'commandId', 'outcome', 'instanceId', 'dataScope', 'receiptId', 'kind', 'serviceOutcome', 'expected', 'observed', 'resumeCommandId', 'exitReceiptId', 'readyReceiptId', 'refusalReceiptId']);
    if (!proof || Object.keys(proof).some(key => !allowed.has(key)))
        throw Error('Exact authenticated service lifecycle release proof required');
    const expected = serviceIdentity(proof?.expected), observed = serviceIdentity(proof?.observed);
    if (!context.serviceIdentity || !sameIdentity(expected, context.serviceIdentity) || proof.kind !== 'service-lifecycle' || proof.verified !== true || proof.fenceId !== context.fenceId || proof.commandId !== context.commandId || proof.dataScope !== context.dataScope || proof.outcome !== outcome || proof.instanceId !== observed.instanceId || !token(proof.receiptId) || keys.some(key => key !== 'instanceId' && expected[key] !== observed[key]))
        throw Error('Exact authenticated service lifecycle release proof required');
    if (outcome === 'unchanged' && proof.serviceOutcome === 'stop-refused' && sameIdentity(expected, observed) && token(proof.refusalReceiptId) && !['resumeCommandId', 'exitReceiptId', 'readyReceiptId'].some(key => key in proof))
        return;
    if (outcome === 'ready' && proof.serviceOutcome === 'resumed' && expected.instanceId !== observed.instanceId && !('refusalReceiptId' in proof) && ['resumeCommandId', 'exitReceiptId', 'readyReceiptId'].every(key => token(proof[key])))
        return;
    throw Error('Exact authenticated service lifecycle release proof required');
}
export function evidenceKey(value) { const canonical = (row) => Array.isArray(row) ? row.map(canonical) : row && typeof row === 'object' ? Object.fromEntries(Object.keys(row).sort().map(key => [key, canonical(row[key])])) : row; return JSON.stringify(canonical(value)); }
