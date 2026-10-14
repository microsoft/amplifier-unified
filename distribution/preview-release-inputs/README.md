# Qualified preview release input

`full-owner-runtime.tgz` is the exact installed distribution bundle exercised by
the full-owner Linux qualification. The adjacent provenance file records every
installed Node package and the original component archive identities.

The development publisher binds the release component Git fields to the commit
containing this exact archive. Those fields describe the distribution bundle;
they do not invent upstream Git revisions for registry dependencies. Preparation
verifies the archive and provenance blobs before the normal signed adapter checks
the fresh promotion ref, signature, file inventory, modes and package census.

The reviewed `preview/ahp-acp-preview` ref advances with qualified runtime/channel
updates. The implementation branch can continue independently. These inputs are
excluded from the npm package's explicit `files` list. They contain no fixture
trust, signing keys, operator configuration, live history or activation authority.

See the dated architecture evidence for the exact package, browser, Linux and live
acceptance boundaries; committing this bundle does not deploy it.
