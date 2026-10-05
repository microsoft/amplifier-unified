# Public original attachment composition

The distribution composes the existing Host and Resources ports. Resources calls
`Host.verifyOriginalForkAttachment` for exact completed Native creation and row
lineage. Host receives Resources `retainForkAttachments` and
`resolveOriginalAttachment` callbacks. Neither callback reconstructs authority or
reads another owner's private database.

`Host.reconcileOriginalForkAttachments(sourceSession, commandId)` is the explicit
trusted continuation after Native fork creation. It pages original occurrences
and never repeats the fork. Ordinary history/grant reads do not copy or grant.
Missing or unknown grants remain unavailable; completion is not inferred from
copied history or existing source bytes.

When a prompt references a retained original, the distribution resolves that
reference through Resources. Only a completed matching target grant supplies a
transient access URI to existing Resources prompt materialization, which retains
scope and immutable-body validation. The caller card and Native original reference
remain unchanged. Unknown/mismatched grants refuse before input admission; they
cannot fall back to source access. A missing grant uses the existing route, which
still rejects an original source URI in a different session.

Older Host/Resources packages receive no unsupported original grant ports. Other
URI schemes retain existing prompt resolution. Native mounted support and passive
storage negotiation remain Host/Native responsibilities; these composition hooks
do not attest worker support or backfill older histories.

Executable coverage: `test/original-attachments.test.mjs` covers compatibility,
unrelated schemes and target access refusal. The opt-in
`test/public-originals.integration.test.mjs` runs the actual `createDistribution`
factory with sealed installed Native/Host/Resources packages. Its owned fixture
uses a real worker/loop/Managed context and deterministic provider, performs
upload, passive cold restart, A→B→C forks, scoped body hash checks, actual target
prompt encoding, and filesystem-copy failure with unknown restart/explicit retry.
It uses public ports and owned filesystem state only. The fixture builder and
run directory must be explicitly bound by `UNIFIED_ORIGINALS_FIXTURE_BUILDER`,
`UNIFIED_ORIGINALS_FIXTURE_BUILDER_SHA256`, and
`UNIFIED_ORIGINALS_RUN_DIRECTORY`.

Qualification limits: current-primary exact row lookup only; archived rows remain
pageable, but exact archived grant recovery is not qualified. Retained-grant
unknown is separate from Native generation unknown. Offline provider evidence
does not establish default Work/delegated-worker, model, browser, account, complete
dynamic dependency closure or user-preview adoption.
