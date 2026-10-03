# Unified Recall capability

An independent product owner over the optional `amplifier-recall` Foundation library. It owns workspace consent, personalization, retention (50 versions), note limits (8,000 characters), model budgets, selected-source authority and background task lifecycle. No Core/Foundation/Unified engine package is imported. The owner uses host callbacks; it does not read native history paths itself.

```js
const recall = createRecallCapability({
  owner: {command: '/owned/venv/bin/amplifier-unified-recall',
          args: ['--config', '/owned/recall.json']},
  inspectSession: (...args) => host.inspectSession(...args),
  listRecallSources: (...args) => host.listRecallSources(...args),
  inspectRecallSource: (...args) => host.inspectRecallSource(...args),
  readRecallSource: (...args) => host.readRecallSource(...args),
  readUserMessage: (...args) => host.readUserMessage(...args),
  readSessionContext: (...args) => host.readSessionContext(...args),
  nativeControlExisting: (...args) => host.nativeControlExisting(...args),
  onInvalidate: (topic, session) => invalidate(topic, session),
});
```

Configuration is `{"dataDir":"/owned/recall"}`. Install the public Recall wheel and this owner wheel in an isolated environment. Use the installed launcher, or `python -I -m amplifier_unified_recall.server`; `-I` prevents an unrelated working directory or inherited PYTHONPATH from substituting another package. Never start two owners over one state directory: a process lease enforces this.

`manifest`, `actionSchemas`, `read`, `action`, and `close` implement the scoped capability interface. Topic `recall` projects `{recall:{[sessionURI]:{coverage,memory}}}`. `memoryContext(session,{expected?})` handles the trusted native `memory.context` request; `idle(session)` is an explicit host completion hook. Only advertise native memory when these hooks are connected. The owner does not register a timer or eagerly observe every catalog session.

## Bounds and authority

Startup reads no session catalog/history. Explicit index refresh traverses authorized metadata pages of at most 25 sources, then complete native history pages of at most 50 turns/1,000 rows/2 MiB. It stages batches of 100 rows and atomically publishes only after a final matching source revision. Failed or cancelled stages preserve the previous generation. Coverage records retain at most 25 errors; at most four background jobs run. Search pages contain at most 50 matches, and candidate visibility checks use indexed metadata only. Selected reads validate the native stamp before returning at most 4,000 characters. Missing or changed sources fail visibly. Native logs remain read-only.

Original Recall and memory action names are retained. `recall.search` adds a query/index-revision cursor; `recall.refresh` adds explicit task/workspace/all scope, defaulting to workspace. Child/internal sources are refused until the host advertises an appropriate inventory. `memory.command {commandId}` reads the exact selected-session mutation ledger after a missing response, without repeating a mutation. Mutation IDs bind scope, operation and arguments.

Contribution and automatic use default off. Contribution uses at most three model attempts per workspace/day by default (explicitly configurable 1–10), at most 16,000 complete source characters, and the native 4,096-output-token limit. Only exact complete host-admitted user text qualifies. Native/imported provenance is retained as unverified and cannot authorize changes. Scheduled/question inputs are excluded. Model results retain verbatim source quotations and explicit derived-wording provenance; unsupported quotations and stale source/consent revisions are rejected. Claims are durable before the model call; interruption, restart and lost transport never replay it.

Consolidation compares a complete maximum of 100 scoped notes and the policy's 12,000-character reference window; exceeding either refuses with visible activity rather than omitting possible contradictions. Automatic context likewise reports omitted coverage above 100 scoped notes. This conservative bound remains a scalability limitation for large note stores, distinct from the bounded conversation index. Source logs, existing backups and previously published references are not rewritten by note deletion.

## Qualification

`python -I -m pytest -c python/pyproject.toml --import-mode=importlib python/tests` exercises atomic index changes, hidden sources, exact host-attributed consent, opt-in generation/quote verification, context delivery and restart/no-replay. `RECALL_PYTHON=/installed/python npm test` runs the actual Node → installed Python owner boundary with passive fixture callbacks and no model. Public host native-history acceptance and web browser acceptance are separate gates. The copied pure policy helpers are pinned and attributed in `PROVENANCE.json`; the engine-bearing amplifier-memory package is not installed here.

## Restart admission

`quiescenceParticipant(ownerId)` uses the optional Foundation operations durable intake ledger under the owner's existing process lease. Background indexing and consolidation retain ownership through completion; a busy acquisition emits an advisory `onMayBeIdle` after all work settles. Held and unknown fences survive restart. Only exact authenticated release proof reopens intake. Historical model attempts alone never imply live work and are never replayed. `quiescenceAccess` permits selected status, wait, note and command reads; search, context delivery and source validation can update projections and remain fenced.
