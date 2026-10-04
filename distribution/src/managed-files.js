import {createHash} from 'node:crypto';
import {z} from 'zod';
import {FacadeFence} from './facade-fence.js';

const identity = z.string().min(1).max(200).regex(/^[^\x00-\x1f]+$/);
const session = z.string().regex(/^ahp-session:\/[^\s\x00-\x1f]{1,480}$/);
const protectedSessions = z.array(session).max(200).refine(items => new Set(items).size === items.length, 'Duplicate identities');
const definitions = {
  'managedFiles.preview': {description: 'Review files in a verified managed conversation allocation. This does not authorize removal.', input: z.object({sessionId: session, protectSessionIds: protectedSessions.optional()}).strict()},
  'managedFiles.dispose': {description: 'Remove exactly the reviewed managed files after native ownership and held product reference checks. Canonical history and events remain.', input: z.object({sessionId: session, reviewId: identity, reviewHash: z.string().regex(/^[a-f0-9]{64}$/), expectedHistoryRevision: identity, protectSessionIds: protectedSessions.optional()}).strict()},
  'managedFiles.receipt': {description: 'Inspect the original managed-file command after a lost response. Never repeat disposal.', input: z.object({sessionId: session, commandId: identity}).strict()},
  'managedFiles.reconcile': {description: 'Reconcile held product protection from the exact settled native effect receipt. Never repeat disposal.', input: z.object({sessionId: session, commandId: identity}).strict()},
};
const topic = 'managed-files', uri = 'amplifier-capability://managed-files';
const childCommand = (sessionId, commandId) => 'managed-files:' + createHash('sha256').update(JSON.stringify([sessionId, commandId])).digest('hex');
const bounded = value => { if (Buffer.byteLength(JSON.stringify(value)) > 512 * 1024) throw Error('Managed-files projection exceeds its bound'); return value; };
const metadata = {mode: 'reviewed-owned-files',preservesCanonical: true,preservesEvents: true,ownership: 'native-verified-managed-allocation',referenceCheck: 'held-at-apply',maxFamily: 101,replayUnknown: false};

/** Public shared user/agent facade. It never takes paths or native identities
 * from a caller; the host resolves the authorized managed allocation. */
export function createManagedFilesCapabilities({host, protection, authorize, directory, onMayBeIdle, onInvalidate = () => {}}) {
  if (typeof host !== 'function' || typeof authorize !== 'function' || !directory) throw Error('Managed-files requires trusted host, authorization and owned intake storage');
  const gate = new FacadeFence({directory, id: 'managed-files', onMayBeIdle, serviceStop: true, retentionHide: true});
  let closed = false, revision = 0;
  const schemas = Object.fromEntries(Object.entries(definitions).map(([name, {description, input}]) => [name, {description, schema: z.toJSONSchema(input)}]));
  const check = async (context, args, operation) => {
    if (closed) throw Error('Managed-files facade closed');
    if (context.origin === 'agent' && args && (typeof context.session === 'string' ? context.session : context.session?.uri) !== args.sessionId) throw Error('Agent managed-files access requires its own trusted session');
    await authorize(context, {operation, session: args?.sessionId});
  };
  const actor = context => ({actorId: identity.parse(context.actorId || context.clientId), ...(context.clientId ? {clientId: identity.parse(context.clientId)} : {})});
  return {
    manifest: {version: 1, topics: {[topic]: {version: 1, uri, watch: true, scope: 'host'}}, actions: Object.fromEntries(Object.keys(definitions).map(operation => [operation, {topic, operation, method: 'x-amplifier/capabilityAction'}]))},
    quiescenceParticipant: gate.participant,
    quiescenceAccess: {'managedFiles.receipt': 'read', 'managedFiles.reconcile': 'reconcile'},
    actionSchemas: () => structuredClone(schemas),
    async read(request, context) {
      await check(context);
      if (request.topic !== topic || request.scope !== 'host' || request.uri?.split(/[?#]/, 1)[0] !== uri) throw Error('Managed-files requires host scope');
      return {topic, scope: 'host', revision, data: {managedFiles: structuredClone(metadata)}};
    },
    async action(request, context) {
      if (request.version !== 1 || request.topic !== topic || !Object.hasOwn(definitions, request.operation)) throw Error('Unadvertised managed-files operation or scope');
      const args = definitions[request.operation].input.parse(request.args ?? {});
      const trustedSession = typeof context.session === 'string' ? context.session : context.session?.uri;
      if (!['ahp-root://', 'host'].includes(request.channel) && !(context.origin === 'agent' && request.channel === args.sessionId && trustedSession === args.sessionId)) throw Error('Managed-files requires host scope or the agent’s own session channel');
      await check(context, args, request.operation);
      const passive = request.operation === 'managedFiles.receipt' || request.operation === 'managedFiles.reconcile';
      return gate.run(passive, async () => {
        const target = host();
        let result;
        if (request.operation === 'managedFiles.preview') {
          const {sessionId, ...input} = args;
          result = await target.previewManagedFiles(sessionId, input, actor(context));
        } else {
          const original = identity.parse(passive ? args.commandId : request.commandId), ownerCommandId = childCommand(args.sessionId, original);
          if (request.operation === 'managedFiles.dispose') {
            const {sessionId, ...input} = args;
            result = {commandId: original, ownerCommandId, receipt: await target.disposeManagedFiles(sessionId, {...input, commandId: ownerCommandId}, actor(context)), replayed: false};
          } else {
            // The host's passive native inspection can recover a lost terminal
            // acknowledgement. Only the coordinator reads that effect as proof.
            const receipt = await target.managedFilesReceipt(args.sessionId, ownerCommandId);
            const authority = typeof protection === 'function' ? protection() : undefined;
            if (request.operation === 'managedFiles.reconcile' && !authority) throw Error('Managed-files protection authority is not configured');
            result = {commandId: original, ownerCommandId, receipt,
              protection: request.operation === 'managedFiles.reconcile' ? await authority.reconcile(ownerCommandId) : authority?.receipt(ownerCommandId) ?? null, replayed: false};
          }
          revision++; onInvalidate(topic, 'host');
        }
        return {accepted: true, result: bounded(result ?? null), updates: []};
      });
    },
    async close() { closed = true; await gate.drain(); gate.close(); },
  };
}
