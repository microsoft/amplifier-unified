Portable ledger evidence index

This package preserves the current 331-row ledger with zero status or acceptance-text changes. Every evidence cell now points to evidence/parity/ledger-claims.json#<featureId>. JSON fragments resolve by controls[].featureId. Supporting selected claims cover ten historical/pending overlay packets including Preview4; historical transitions must not be replayed.

All published files are selected public derivatives. Original private ledger, seal, claims and evidence-cell hashes remain explicitly distinct from public derivative hashes in MANIFEST.json and per-row provenance. Original locators and observed mirror hashes are retained in a separate private-only map, excluded from this package. Raw proof bytes are not copied, and legacy proof contents were not newly verified. Complete citation indexing is not full functional parity.

F09 remains pending in f09-pending-scoped-intake.json with exactly seven proposed control updates; the current public ledger still preserves the F22 snapshot statuses. Root must reconcile that scoped operation separately after its privacy audit.

Install the evidence/parity directory relative to the ledger documentation directory, and keep MANIFEST.json/FORMAT.json/index adjacent for provenance. Root should use a clean integration commit and independently inspect the complete staged diff, including deleted lines and any other artifacts. This package makes no acceptance claim about existing public history or unrelated source/PR files. No Root/history/source mutations or new runtime/account tests were performed.
