# Local source validation notices

Reproduced on 0.19.14: a relative path or file URL resolves to an absolute source before validation. Comparing that resolved source with the original editor text hides a successful validation or save notice. Changing the source type also leaves a module receipt eligible for display in the bundle editor.

Receipts now retain both the submitted source and resolved source. The host sets the candidate identity, and the UI matches the submitted text, source type, registered name, section and scope. Saved configuration still uses the resolved path. No second validation cache or persistence format is introduced.

The regression tests failed three cases before the fix, then all 15 registry tests passed. The browser check covers relative and file URLs, unchanged settings after check-only validation, canonical saved paths after reload, source-type changes, rejected candidates and stale results. These checks use a deterministic validator and the real shared action/persistence paths; they do not initiate a model call.
