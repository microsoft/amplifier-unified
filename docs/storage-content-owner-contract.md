# Storage accounting by content

The composition and Native administrator declare accounting roots through the
existing passive storage port. Its optional `contentVersion: 1` extension supplies
`contentRoots` with an exact path, stable root identity, scope, content category,
and optional absence marker. Categories are `workspaces`, `sessions`, `runtime`,
`backups`, `cache`, and `other`; a null category excludes that boundary. These
roots confer no mutation or cleanup authority. Legacy peers keep their existing
root-based overview.

The workspace owner adds authorized registered workspace roots. The most specific
root owns its files; nested roots are excluded from parent totals and hard links
are counted once. A co-located CLI home is excluded except for explicitly declared
shared history. Missing optional roots are empty, while unreadable, linked,
changing, or bounded-out entries make the result a labelled lower bound.

The existing `storage.inspect` action adds a category selection, then uses the
original root/workspace identity for relative folder drill-down. Cross-boundary
navigation must select the owning category. No path supplied by a client becomes
a root, no file content is read, and visiting storage does not start agent work.
