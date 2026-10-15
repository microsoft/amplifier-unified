# Standard UX completion evidence

The October15 inventory remains198 workflows and663 required outcomes after the
reviewed deltas. Its original four-input-mode matrix is retained. Root approved
a risk-based qualification model after reviewing the workload: all ordinary
reachable paths and their effects need actual browser evidence, while repeated
input/layout behavior can use an explicit shared-component crosswalk. Source
reads, test titles and family-wide passes never count as executed behavior.

`frontend/tests/standard-ux-completion-policy.json` records the policy, an initial
14 explicit ordinary-path mappings and seven candidate shared patterns. The next
evidence reconciliation expands this to44 explicit paths, including actual
navigation, messages, connection settings, notifications, synthetic import and
private backup journeys. Form-only endpoint checks and synthetic voice/device
checks retain their limits; they do not qualify a real provider or OS device. A pattern
is only eligible for reuse after its current source owner and exact assertions
are linked; it does not certify an unrun save, destructive scope, data identity or
navigation target. Pending, unknown, stale-owner, failure/retry and destructive
states remain mandatory targeted checks in the layouts that expose them.

The evidence ledger now reports three separate questions: whether the explicit
ordinary path was observed, whether all required outcomes were observed in named
contexts, and which additional input/layout variants remain useful. At the first
policy snapshot,21 workflows had all outcomes observed in some context and14
explicit ordinary paths were mapped and observed. This is not a claim that21
workflows work with every real provider, OS device or input mode. The other184
ordinary paths still need explicit mapping; some already have outcome evidence.
Every per-outcome gap remains open until supported or explicitly reviewed as an
external capability/product limitation. The original ledger snapshot and exact
proof references remain under `output/readiness-day-20261015/ui-inventory`.

The next explicit mapping revision adds the observed Work disclosure/paging, support, workspace creation and decision paths. Workspace tab identity remains partial until the selected sidebar state is asserted. API-only storage entry and Advanced screen capture are retained as contextual evidence without ordinary-path entry credit.
