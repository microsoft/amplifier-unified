# Nineteen-owner managed-file disposal acceptance

`managed-files.integration.test.mjs` uses the public distribution, real packaged
capability owners, a separate catalog process, the native ACP executable and actual
Core/Foundation. The provider is an offline fixture; no account or paid model is
used. Run explicitly with all four paths supplied (otherwise the test is skipped):

```sh
AMPLIFIER_ACP_PYTHON=/owned/native-consumer/bin/python \
UNIFIED_OWNERS_PYTHON=/owned/product-owner-consumer/bin/python \
UNIFIED_CATALOG_PYTHON=/owned/catalog-consumer/bin/python \
RECOVERY_NATIVE_PROVIDER=/owned/native-source/tests/fixtures/provider \
node --test test/managed-files.integration.test.mjs
```

Set `UNIFIED_DISTRIBUTION_ENTRY` to the absolute installed package `src/index.js`
to repeat against an immutable distribution archive. All implementation imports
then come through that public entry and its declared dependencies. No owner
quiescence port, native receipt, catalog method, or resource store is replaced.
`KEEP_MANAGED_FILES_FIXTURE=1` retains only the test's temporary directories for
inspection; default cleanup removes them after the distribution closes.

The fixture includes nineteen required owners and both distinct product facades:
history cleanup hides discovery, while managed-file disposal removes a proved
allocation after review. It verifies:

- A cross-chat Canvas file reference, saved through the public API, refuses
  disposal and retains the allocation. The exact refused receipt is stable.
- An actual future schedule protects its selected allocation; a second client
  subscribed to the selected chat also refuses review before effects.
- Two real historical Foundation children are included in exact sorted AHP
  identity order. Native and product family counts agree, and every acquired
  product lease conclusively releases from the same effect receipt.
- Dropping the successful wire acknowledgement leaves the original command
  recoverable. Reconnection and a complete distribution restart return that
  receipt; repeated mutation cannot execute again.
- Canonical transcript, metadata, child history, and event-file hashes remain
  identical. Historical chat reads and Markdown export work after the allocation
  directory disappears. Reactivation is refused.
- After initial fixture turns, native worker startup is replaced by an impossible
  executable. Review, disposal, receipt recovery, history and export still work;
  the public host reports zero active agents throughout those paths.
- The separate cleanup facade can subsequently hide the referenced conversation
  while preserving its directory and the other chat's immutable Canvas body.

The first real run exposed a deployment-blocking scanner-order defect in catalog
a22491ed: a child discovered before a managed parent retained the legacy derived
parent URI, making the managed root's indexed family falsely empty. Catalog
36fe815 repairs native parent mappings transactionally, independently of scan
order. Native cb7bac6 independently checks a bounded selected-project metadata
family under the native-home writer gate before disposal. Use both successors;
the older root049 archive is compatible only when configured with these corrected
Python packages. Prior source/package evidence alone did not establish this case.

This qualifies local protocol/composition behavior, not a browser, physical
device, live service, or uncooperative external filesystem writer. Native disposal
conservatively refuses while any cooperating worker/request owns the native home.
Source correctness tests separately exercise incomplete metadata, races, limits,
process death and retained unknown outcomes. The complete native suite has four
known steering negotiation failures on the frozen loop dependency graph; they
also reproduce on b201 and are not represented as disposal acceptance.
