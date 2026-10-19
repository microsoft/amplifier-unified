# Merged upstream qualification, 2026-10-17

The two previously pending upstream repairs are merged. Their delivery does not
require a new Unified dependency mechanism or a change to saved user settings.
Recipes is composed through bundle module sources, rather than a static worker
requirement. Unified's default computer-use behavior and `native-desktop` extra
already use the canonical repository's `main`. A newly resolved source can adopt
the merged change; an existing immutable source or running worker does not change
merely because an upstream PR merged. Spark-1 was not modified.

## Recipes #121

[The merged repair](https://github.com/microsoft/amplifier-bundle-recipes/pull/121)
is `2e14948bee27a1ec52bef0940c8295eed86c3293`, tree
`fce71763a71507798adc0d1c0402d1ec6de0a35e`. Its tree equals the final reviewed PR
head. Three commits after the earlier Unified qualification add conditional
regular-expression handling and associated tilde/witness corrections, so the
earlier receipt was not relabeled as qualification of this merged source.

Fresh checks ran in the owned spark-2 DTU against all 410 verified source files:

| Check | Result |
| --- | --- |
| Complete tool module suite | 1,858 passed; six existing deprecation warnings |
| Complete runner suite | 596 passed |
| Conformance | 19 passed in the main run; the remaining activation fixture passed separately after supplying Git history and its installation prerequisites |
| Existing legacy serialization goldens | All five byte-identical; no baselines changed |
| Canonical/mirrored shell-source implementations | All 19 modules match |
| Sanctioned real installed CLI `engine_info` and recipe validation | Passed in 0.868 seconds; reports the exact merged SHA and module path |

The CLI proof used its existing independently installed environment, a fresh
synthetic home/configuration, read-only source/interpreter mounts, and a private
network namespace. It made no model call. The historical activation fixture ran
unchanged, including its pre-hotfix rejection control, in disposable environments;
it used package-registry access and locally mapped exact Git objects. Initial
missing-history, cache, and local-mirror prerequisite failures are retained as
setup failures, not concealed as passing product checks.

These checks retain the upstream Bash 5.2.21/tree-sitter profile and cover the
published recipes/tests, not arbitrary unseen recipes or Windows/macOS shell
execution. Existing source qualification and this exact-source follow-up are
separate records.

## Computer-use #29

[The merged package repair](https://github.com/microsoft/amplifier-bundle-computer-use/pull/29)
is `e03ac2eae884a463ac23fe44553a6b063e22caf8`. Git independently confirms its full
tree `1bfb93571174066d07658acf02afe3ab86155b75` equals the previously qualified
head `63871d4691d44bac4bad25875f19e3909b79ffc7`; there is no source delta requiring
a repeated package suite. The earlier receipt covers runtime-asset identity and
package integrity, including the PowerShell bridge/overlay files. It does not
claim installed Windows desktop execution or native input acceptance.

## Provider source review for the fresh native lane

The final worker qualification must use and record the actual resolved sources
and installed SDK versions, rather than treating source review as runtime proof:

| Provider | Exact reviewed source | Mount/configuration implications |
| --- | --- | --- |
| OpenAI | `cd94b6cb0532468fab8a04624cd3e3773acf1094` | Mount/constructor unchanged; lazy, instance-bound optional image methods. OpenAI SDK minimum is now 3.29.0. |
| OpenAI ChatGPT | `b1341edd1545ba4b89a416184797ccfbc346413b` | Validated retry settings and Core-owned request retries; exposed output stops retry. Account selection and mount ownership remain unchanged. |
| Gemini | `629fb37d5e73194dd2bd58c681888df414d1c047` | Mount/constructor unchanged; optional image capabilities inspect the selected connection locally. SDK requirement is now `google-genai>=2.30.1,<3`. |

No blocker was found in the independently reviewed mount/config/instance
boundaries. This is not exhaustive image-operation or live-provider acceptance.
The final installed native lane with published Core 2.0.2 remains the release
owner's separate qualification responsibility.

Private raw logs, source identities, prerequisite failures, and cleanup receipts
are retained in the workspace's `output/afk-approved-20261017/upstream-closeout`
and `output/afk-approved-20261017/provider/source-review` evidence directories.
