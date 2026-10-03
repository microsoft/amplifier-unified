/** Bind each advertised effect to its real owner. Missing participants stay gaps. */
export function composeQuiescence(config, owners, {bindings = new Map(), verifyRelease,
 onMayBeIdle, nativeHost = true} = {}) {
 if (!config || typeof config !== 'object') throw Error('Explicit quiescence configuration required');
 const requiredOwners = new Set(), participants = new Map(), capabilities = {}, resources = {};
 for (const owner of owners) {
  const topics = Object.keys(owner.manifest?.topics ?? {}).sort();
  if (!topics.length) throw Error('A capability owner must declare its topics');
  const suggested = 'capability:' + topics[0];
  const declared = bindings.has(owner) ? bindings.get(owner) : owner.quiescenceParticipant;
  const participant = typeof declared === 'function' ? declared.call(owner, suggested) : declared;
  const id = participant?.id ?? suggested;
  requiredOwners.add(id);
  if (participant) {
   if (participants.has(id) && participants.get(id) !== participant) throw Error('Conflicting quiescence owner: ' + id);
   participants.set(id, participant);
  }
  for (const topic of topics) {
   if (Object.hasOwn(capabilities, topic)) throw Error('Duplicate quiescence topic: ' + topic);
   capabilities[topic] = id;
  }
  for (const provider of owner.resourceProviders ?? (owner.resourceProvider ? [owner.resourceProvider] : [])) {
   if (typeof provider.write !== 'function') continue;
   if (Object.hasOwn(resources, provider.scheme)) throw Error('Duplicate quiescence resource writer: ' + provider.scheme);
   resources[provider.scheme] = id;
  }
 }
 return {instanceId: config.instanceId, dataScope: config.dataScope,
  timeoutMs: config.timeoutMs, requiredOwners: [...requiredOwners],
  participants: [...participants.values()],
  coverage: {capabilities, resources, ...(nativeHost ? {nativeHostOwners: [...requiredOwners]} : {})},
  verifyRelease, onMayBeIdle};
}

/** Evidence comes from the configured recovery owner; client assertions are ignored. */
export function recoveryReleaseVerifier({instanceId, dataScope, nativeAuthority, recovery, fallback}) {
 return async request => {
  if (request.purpose !== 'recovery') {
   if (typeof fallback !== 'function') throw Error('No authenticated release authority is configured for this operation');
   return fallback(request);
  }
  const evidence = recovery()?.readReleaseEvidence(request);
  if (!evidence || request.outcome !== 'unchanged' || evidence.outcome !== 'unchanged' ||
      evidence.nativeAuthority !== nativeAuthority || evidence.nativeLeaseReleased !== true ||
      !['released', 'not-acquired'].includes(evidence.nativeLeaseDisposition) ||
      !['prepared', 'succeeded', 'refused'].includes(evidence.terminalState) ||
      !evidence.receiptId || ['fenceId', 'commandId'].some(key => evidence[key] !== request[key]) ||
      evidence.instanceId !== instanceId || evidence.dataScope !== dataScope ||
      request.instanceId !== instanceId || request.dataScope !== dataScope) {
   throw Error('Recovery has no exact settled native lease release proof');
  }
  return {verified: true, fenceId: request.fenceId, commandId: request.commandId,
   outcome: 'unchanged', instanceId, dataScope, receiptId: evidence.receiptId};
 };
}
