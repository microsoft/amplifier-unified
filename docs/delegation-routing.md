# Delegation routing and observed usage

The conversation model selector chooses the root conversation's model. Worker
selection also depends on the bundle, agent configuration, and delegation
preferences. The model picker discloses the mounted routing policy using the
same `delegationRouting` state returned by `configuration.providers` and the
runtime report.

The report identifies whether the live loop enables
`inherit_effective_model`, whether a model-role resolver is mounted, and the
resolver's optional name and user/bundle provenance. It reads the mounted
resolver; it does not resolve a model, discover providers, or read private
configuration files. Unknown provenance is omitted.

For Unified-managed children, explicit delegation provider preferences take
precedence over conversation inheritance. Agent provider declarations and
model roles also suppress inheritance. Otherwise an opted-in loop can inherit
the explicit conversation selection. A retained effective selection can be
restored on resume. A missing inherited provider instance fails before child
execution rather than silently selecting a substitute.

Each worker's activity shows the provider and model observed at its latest
foreground call. Auxiliary naming and compaction do not relabel that worker.
The spawning parent's observed provider is retained for comparison, and a new
worker run clears the previous observed model until another call is seen.
Provider identifiers come from the public provider protocol; they do not prove
which credential, billing account, or allowance paid for the call.

Worker usage is the existing descendant-call rollup, not an additional charge.
Unavailable usage remains unavailable rather than becoming a measured zero.
All individual calls remain expandable, including calls using different models.
Worker activity stays under the original delegate call and initiating turn.

## Cross-provider consent prerequisite

`crossProviderRestriction: "not_enforced"` explicitly describes the current
state. Disclosure is not consent or a billing guarantee. Saved routing matrices
and deliberate agent/provider overrides are unchanged.

A restriction in Unified's child launcher alone cannot enforce a portable
provider boundary. Foundation and CLI have other spawners; orchestration,
Foreman, A2A, observers, and pipeline/agent runtimes can create sessions directly
or use their own registered spawn capability. Some work uses subprocesses or
host-owned provider calls. These paths can bypass Unified's launcher.

An enforceable opt-in requires a portable contract, carried through every child
creation/resume path and checked at the actual provider dispatch boundary:

1. Bind the allowed provider instance/connection to the user's explicit
   selection. Provider family names alone cannot distinguish billing accounts.
2. Carry policy and explicit cross-provider opt-in provenance into children and
   subprocesses without allowing an agent overlay or routing hook to widen it.
3. Validate the resolved provider before its first request, including custom
   direct sessions and auxiliary calls. Unknown policy or unavailable allowed
   providers must produce a clear, actionable result, not a silent fallback.
4. Define cancellation, resume, nested delegation, policy changes, and deliberate
   bundle-routing migration behavior. Preserve existing saved settings until
   the user makes a supported choice.
5. Expose the same effective policy, alternatives, and consent action to agents
   and the UI. Test every spawner/dispatcher class before claiming enforcement.

Until that contract is implemented, the UI reports that other connected
providers may be used. It does not offer a restriction toggle that these
portable paths could bypass.
