# Client vision UX amendment (DRAFT)

Target: `docs/clients/VISION.md`

Base: `5da1e1ce137866b780c37d0ebb9a4b63aecfcadc`

Target SHA-256: `82dbec57160bc2f052af089fa9f4b80075188ff633694bb5d118a66863ede720`

This proposes an amendment to the existing document, preserving its identity
and promise numbers. The candidate filename does not create a new API version.
The target remains unchanged pending review of the exact wording.

## The exact change

### Describe arrival and retrieval

Current text:

```markdown
Amplifier is a continuing conversation with work happening on an identified host.
A person reaches that work through a browser, terminal, desktop or mobile client.
The interface fits the device; the conversation, its meaning and its progress
remain recognizable when the person changes interfaces.
```

Replacement:

```markdown
Amplifier is a continuing conversation with work happening on an identified host.
A person can begin with a one-off request or an established project, then return
to work that needs attention or find something they already know. They reach
that work through a browser, terminal, desktop or mobile client. The interface
fits the device; the work, its meaning and its progress remain recognizable
when the person changes interfaces.
```

### Make return a useful experience

Current text:

```markdown
The person recognizes the conversation, its workspace, its history and its next
available action. Moving between devices does not become an import/export task.
Moving execution to another machine is a separate, explicit capability.
```

Replacement:

```markdown
A person returning to work can tell what needs their input, what has a response
to read, and what is running. They can find a familiar conversation without
reconstructing its organization. The conversation, workspace when present,
history, and next available action remain recognizable. Their current context
survives visits elsewhere in the same client. Moving between devices does not
become an import/export task; moving execution to another machine remains a
separate, explicit capability.
```

### Preserve meaning across density

Current text:

```markdown
People learn the concepts once. A terminal uses its keyboard and scrollback well;
a browser uses its panels and rich views well. Neither imitates the other's
layout at the expense of being useful on its own device.
```

Replacement:

```markdown
People learn the concepts once. A terminal uses its keyboard and scrollback well;
a browser uses its panels and rich views well. A quiet view and a detailed view
remain ways into the same work, with the same essential actions. Neither density
nor a change of interface changes what a status means or grants more authority.
Clients express these promises in the form that fits their device.
```

### Add observable signs

Current text:

```markdown
- A reviewer traces **every shared behavior change** to its contract clause,
  affected consumers and evidence; **0 unmeasured claims** are reported as passes.
```

Replacement:

```markdown
- A reviewer traces **every shared behavior change** to its contract clause,
  affected consumers and evidence; **0 unmeasured claims** are reported as passes.
- A returning person identifies **1 required decision**, **1 unread response**,
  and **1 running conversation** without inspecting execution settings.
- A person begins **1 standalone request** with **0 mandatory project or folder
  choices**, then can find that conversation again.
- A person visits **2 conversations**, returns to their work overview, and
  reconstructs **0 previously chosen filters or reading positions** in that client.
```

## The evidence

The product owner described both arriving to see what needs attention and
retrieving known work, including one-off requests and established projects.
Their live review exposed context reconstruction on returning Home, a missing
temporary Focus pass, and misleading retained selection. The shipped Home
notes record those corrections. The older vision discusses continuity across
devices but does not state these arrival and retrieval needs.

The proposed signs are acceptance goals, not measured outcomes. The reported
“too technical” and workspace confusion are qualitative feedback; the designer
review must establish what is confusing in concrete journeys.

## What does not change

AHP/ACP remains the separate architectural destination. Current native runtime,
execution ownership, permissions, device-specific interaction, private-state
ownership, and cross-host migration boundaries do not change. This does not
require every client to copy Focus/Brief/Studio or the web sidebar, nor promise
logical project regrouping or automatic migration of private view choices.

## Dependencies

None.

## Decision

Pending. Record the exact reviewed revision, decision-maker, date, and accepted
wording: ratified / ratified with edits / declined / later. On acceptance,
apply only the agreed replacements and a dated changelog entry; retain this
proposal and its decision. No acceptance or implementation result is inferred.
