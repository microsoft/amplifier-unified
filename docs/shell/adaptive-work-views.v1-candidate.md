# Adaptive work documentation amendment (DRAFT)

Target: `docs/shell/adaptive-work-views.md`

Base: `5da1e1ce137866b780c37d0ebb9a4b63aecfcadc`

Target SHA-256: `2cca8f4256bfd064b491068e394d6cbf04c783395e960f9639217c0cf3cd0045`

This proposes an amendment to the existing document, preserving its identity
and promise numbers. The candidate filename does not create a new API version.
The target remains unchanged pending review of the exact wording.

## The exact change

### Identify current notes and their owner

Current text:

```markdown
# Adaptive work views — preview contract

Requested for an isolated user trial on 2026-10-19. This changes the web client's
arrival experience; it does not adopt the separate AHP/ACP architecture or change
conversation execution. No release or production adoption is implied.
```

Replacement:

```markdown
# Adaptive work views — implementation and review notes

These notes record the web client's Home/sidebar design progression, concrete
behavior, and bounded acceptance evidence. The current released baseline is
[v0.20.188](https://github.com/microsoft/amplifier-unified/releases/tag/v0.20.188).
The [Home/sidebar contract](work-discovery.v1.md) owns the observable promises;
these notes supply implementation detail and historical context.

Dated trial restrictions below describe their original checkpoints. Publication,
live adoption, and designer acceptance remain separate facts. This work does
not adopt the separate AHP/ACP architecture or change conversation execution.
```

## The evidence

The current introduction still calls this a preview contract requested for an
isolated trial. The work has since shipped through PRs #860, #875, and #886.
The file mixes durable promises, implementation details, trial refinements, and
historical acceptance. Its existing detail is useful; a short agreement should
be the review surface rather than silently treating every implementation choice
as permanent product direction.

## What does not change

Every section after the introduction remains intact, including historical
limits and evidence. No implementation, test result, or live adoption is created
by this amendment. The new contract proposes durable behavior; the existing
notes retain concrete layout and implementation choices for future comparison.

## Dependencies

Apply together with the accepted new Home/sidebar contract; otherwise keep the current introduction.

## Decision

Pending. Record the exact reviewed revision, decision-maker, date, and accepted
wording: ratified / ratified with edits / declined / later. On acceptance,
apply only the agreed replacements and a dated changelog entry; retain this
proposal and its decision. No acceptance or implementation result is inferred.
