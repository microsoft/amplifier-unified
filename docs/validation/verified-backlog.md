# Verified release and registry defects

Reproduced against the 0.19.12 source tree:

- #30: release planning silently selected an older existing tag when HEAD had new code but the same version. Both lightweight and annotated tag regressions failed before the fix. The planner now requires the tag's peeled commit to match the selected HEAD; exact retries still succeed. Tags and published assets remain immutable.
- #32: modules.validate preferred a module's direct source over the effective source override used by runtime. Validation now uses raw scoped configuration and the runtime source-selection helper, including the existing provider precedence. Public display redaction is not a source resolver.

`sources.validate` checks a candidate module source without saving settings. `sources.save` with `validate: true` checks that exact candidate before saving and leaves settings unchanged on failure. Both require the module section. The source editor uses these same actions and always validates a module replacement before saving; bundle-source behavior is unchanged. This checks a candidate's Core contract, not provider credentials or a paid model conversation. Higher-priority runtime configuration can still override a scoped saved source.

Validation: release/registry regression tests, scoped precedence and provider policy, no configuration credentials in validator payloads, nonpersistent candidate checks, failed saves, successful saves, and stale-success revalidation. The browser fixture exercises real shared actions and persistence with a deterministic isolated validator: check without save, failed save, changed draft, validated save, and reload. It does not call a model or download a third-party candidate. Full frontend suite: 212 passing. The existing update/rollback real-process and export/feedback follow-up regressions also pass.
