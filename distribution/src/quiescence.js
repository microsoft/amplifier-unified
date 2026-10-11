/** Bind each advertised effect to its real owner. Missing participants stay gaps. */
export function composeQuiescence(config, owners, {bindings = new Map(), verifyRelease, verifyAdmissionAbort,
 onMayBeIdle, nativeHost = true, serviceLifecycle, runtimeOwners = []} = {}) {
 if (!config || typeof config !== 'object') throw Error('Explicit quiescence configuration required');
 if (verifyAdmissionAbort !== undefined && typeof verifyAdmissionAbort !== 'function') throw Error('Trusted admission abort verifier must be a function');
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
 for(const participant of runtimeOwners){
  if(!participant?.id||typeof participant.acquire!=='function'||requiredOwners.has(participant.id))throw Error('Conflicting or invalid runtime owner: '+participant?.id);
  requiredOwners.add(participant.id);participants.set(participant.id,participant);
 }
 return {instanceId: config.instanceId, dataScope: config.dataScope,
  timeoutMs: config.timeoutMs, requiredOwners: [...requiredOwners],
  participants: [...participants.values()],
  coverage: {capabilities, resources, ...(nativeHost ? {nativeHostOwners: [...requiredOwners]} : {})},
  // Only the trusted launcher supplies service authority. Participants retain their
  // own explicit serviceStop markers; missing coverage must remain a host refusal.
  verifyRelease, ...(verifyAdmissionAbort ? {verifyAdmissionAbort} : {}), onMayBeIdle, ...(serviceLifecycle ? {serviceLifecycle} : {})};
}

/** Evidence comes from the configured recovery owner; client assertions are ignored. */
export function recoveryReleaseVerifier({instanceId, dataScope, nativeAuthority, recovery, fallback}) {
 return async request => {
  if (request.purpose !== 'recovery') {
   if (typeof fallback !== 'function') throw Error('No authenticated release authority is configured for this operation');
   return fallback(request);
  }
  const evidence = recovery()?.readReleaseEvidence(request);
  const unchanged=request.outcome==='unchanged'&&request.instanceId===instanceId;
  const completedMetadata=request.outcome==='ready'&&request.instanceId!==instanceId&&
      evidence?.metadataSettlement===true&&evidence.originalInstanceId===request.instanceId&&
      evidence.operation==='recovery.workspace.remove'&&evidence.terminalState==='succeeded'&&
      evidence.nativeLeaseDisposition==='not-acquired'&&/^[a-f0-9]{64}$/.test(evidence.workspaceArchiveReceiptHash??'')&&
      /^[a-f0-9]{64}$/.test(evidence.nativeResultHash??'')&&!!evidence.nativeCommandId;
  if (!evidence || !(unchanged||completedMetadata) || evidence.outcome !== request.outcome ||
      evidence.nativeAuthority !== nativeAuthority || evidence.nativeLeaseReleased !== true ||
      !['released', 'not-acquired'].includes(evidence.nativeLeaseDisposition) ||
      !['prepared', 'succeeded', 'refused'].includes(evidence.terminalState) ||
      !evidence.receiptId || ['fenceId', 'commandId'].some(key => evidence[key] !== request[key]) ||
      evidence.instanceId !== instanceId || evidence.dataScope !== dataScope ||
      request.dataScope !== dataScope) {
   throw Error('Recovery has no exact settled native lease release proof');
  }
  return {verified: true, fenceId: request.fenceId, commandId: request.commandId,
   outcome: request.outcome, instanceId, dataScope, receiptId: evidence.receiptId,...(completedMetadata&&evidence.participantProof?{participantProof:structuredClone(evidence.participantProof)}:{})};
 };
}
