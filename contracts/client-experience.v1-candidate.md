# Client experience UX amendment (DRAFT)

Target: `contracts/client-experience.v1.md`

Base: `5da1e1ce137866b780c37d0ebb9a4b63aecfcadc`

Target SHA-256: `57891e20ae3935631669d48abd2dfebef6f2b7ba9705cb1adc44b4227c244cf6`

This proposes an amendment to the existing document, preserving its identity
and promise numbers. The candidate filename does not create a new API version.
The target remains unchanged pending review of the exact wording.

## The exact change

### Extend CX1 to arriving and finding

Current text:

```markdown
1. **CX1 — Orient and navigate consistently.** Every client identifies the execution
   host, workspace and conversation, and offers discoverable selection, creation,
   rename and history navigation. Selection responds locally while loading/preparation continues.
   Broken: a click leaves the old selection unexplained, or viewing a chat needs its execution lock. Affected: readers switching work.
```

Replacement:

```markdown
1. **CX1 — Orient and navigate consistently.** Every client makes its execution
   host, workspace when present, and conversation discoverable. People can start
   standalone work, find required attention, and retrieve saved conversations
   without a mandatory project choice or knowledge of agent internals. Selection, creation,
   rename and history navigation remain available; selection responds locally
   while loading/preparation continues.
   Broken: returning requires reconstructing context, a click leaves the old
   selection unexplained, or reading needs an execution lock. Affected: people finding work.
```

### Clarify deliberate repeated access in CX8

Current text:

```markdown
   recovery. Evaluate the whole journey, including menu opening and repeated setup,
   alongside cognitive load and keyboard/focus behavior.
```

Replacement:

```markdown
   recovery. Evaluate the whole journey, including menu opening and repeated setup,
   alongside cognitive load and keyboard/focus behavior. Frequently used actions
   may appear on independently scrolled surfaces when that avoids return travel;
   their labels and behavior remain consistent.
```

## The evidence

The product owner's first brief separates orienting to waiting work from
browsing/searching for something known. They later explicitly requested Search
and New chat in the sidebar on Home because the two surfaces can be scrolled
independently. Removing those entry points to avoid duplication would recreate
the travel cost they identified. The existing workspace first-use direction
already permits a chat without a workspace; CX1 should not imply setup is required.

## What does not change

CX2–CX7, authoritative action meanings, safety checks, keyboard access,
workspace creation semantics, and protocol plans remain unchanged. This grants
no new API operation or permission. Web-specific controls remain with the web
contract; other clients satisfy the shared journey in their own form.

## Dependencies

Review alongside the client vision amendment; neither depends on a protocol migration.

## Decision

Pending. Record the exact reviewed revision, decision-maker, date, and accepted
wording: ratified / ratified with edits / declined / later. On acceptance,
apply only the agreed replacements and a dated changelog entry; retain this
proposal and its decision. No acceptance or implementation result is inferred.
