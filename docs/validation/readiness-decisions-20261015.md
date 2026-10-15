# Readiness decisions and dependency findings — October 15, 2026

This is the decision queue for the isolated standard-UX qualification. It does
not approve new product behavior or claim that an external dependency is fixed.
Confirmed Unified defects are being repaired on `integration/readiness-20261015`.
Nothing in this document authorizes deployment, a main merge, or replay of saved
work. Private captures and raw browser evidence stay outside this repository.

## Decisions to reserve for the product owner

| Issue | Recommendation and concrete interaction | Decision boundary |
| --- | --- | --- |
| [745](https://github.com/microsoft/amplifier-unified/issues/745), assistant reactions | Allow an attributed acknowledgement such as “Amplifier · 👍 Acknowledged” beside the user's message. Keep execution status separate and accessible. | Whether assistant reactions should replace acknowledgement prose. A reaction must not imply successful work or suppress a needed answer. |
| [676](https://github.com/microsoft/amplifier-unified/issues/676), coordination prose | Group routine coordination into a compact row, for example “2 related tasks updated · Details”. Keep approvals, failures and requests for the user visible. | Which events are routine, and whether their history is collapsed by default. Preserve owner and task attribution. |
| [675](https://github.com/microsoft/amplifier-unified/issues/675), diagram renderer | Qualify the existing renderer's fit, live pan, wheel zoom and touch behavior first. Those concrete defects have a separate repair and 22 browser assertions. | A new renderer/editor is a separate dependency and interaction choice. Current repairs do not select React Flow or promise diagram editing. |
| [294](https://github.com/microsoft/amplifier-unified/issues/294), feedback follow-ups | Offer an owner-scoped “Review changes” screen showing the original issue, proposed edit and new attachments before submission. | Original-edit permission, conflict resolution and attachment retention. Do not turn a follow-up into an implicit overwrite. |
| [293](https://github.com/microsoft/amplifier-unified/issues/293), execution visibility | Extend the existing Work details and usage views with missing facts; keep one accounting source. | Which additional context-health facts are useful and reliably available. Avoid a second parallel progress system. |
| [290](https://github.com/microsoft/amplifier-unified/issues/290), first-class emitter | Reuse the bounded canonical observation stream with explicit source, scope, timestamp and stale-state semantics. | Consumers, ownership, redaction and cross-host contracts must be agreed before adding another transport. |
| [285](https://github.com/microsoft/amplifier-unified/issues/285), native mobile app | Finish narrow-layout/PWA acceptance; identify the native OS capability that justifies an additional client. | Native-client scope and maintenance ownership. Browser touch checks are not native iOS/Android acceptance. |
| [277](https://github.com/microsoft/amplifier-unified/issues/277), top-level automation | Start with a read-only index linking to each automation's owning conversation. Keep creation and execution scoped to that owner. | Whether global creation or cross-conversation authority is desired. |
| [77](https://github.com/microsoft/amplifier-unified/issues/77), notification consolidation | Define one delivery owner and deduplication identity before routing through the notify bundle. | Transport ownership and migration. Avoid duplicate notifications or replay during adoption. |
| [36](https://github.com/microsoft/amplifier-unified/issues/36), voice wait messages | After a short quiet period, speak a meaningful known phase once, such as “Still waiting for the model.” | Timing, repetition and interruption preferences. Never invent progress or claim an unavailable phase. |
| [29](https://github.com/microsoft/amplifier-unified/issues/29), numbered sidebar shortcuts | Mock a hold-to-reveal overlay, with platform-specific modifiers and focus restoration. | Key mapping and accessibility, including conflicts with browser/OS shortcuts. |
| [679](https://github.com/microsoft/amplifier-unified/issues/679), recovery by a peer | Preserve Stop/error ownership protection. A human's new explicit input may establish fresh execution; any peer-triggered recovery needs a new, explicit recovery contract. | An earlier authorization does not make an unknown or suppressed handoff safe to replay. Four current service controls passed; no status-tweaking workaround was added. |

These are proposed interactions, not screenshots of implemented features. Each
can be decided independently of the qualified fixes.

## Reproduced dependency defects

The original probes ran inside the owned spark-2 DTU against exact public source
revisions. Two canonical repairs are now prepared as unmerged PRs. Neither changes
Unified dependency pins or qualifies live adoption. The recipe defect remains open.

| Unified report | Current canonical result | Remaining acceptance |
| --- | --- | --- |
| [649](https://github.com/microsoft/amplifier-unified/issues/649), shell safety wrappers | [Bash PR24](https://github.com/microsoft/amplifier-module-tool-bash/pull/24), commit `63c92139c735`, inspects `nice`, `setsid` and `stdbuf` options before applying existing effective-command checks. The original source failed 51 new controls. Candidate: 352 package tests passed, 10 Windows-specific tests skipped locally; final 180 safety tests passed. All six Linux/macOS/Windows Python CI legs and CLA passed. **No destructive test command executed.** | Unmerged; version-bound Unified adoption remains separate. CI is not installed Windows or live-user acceptance. Existing allowlists and unrestricted profiles retain their behavior. |
| [630](https://github.com/microsoft/amplifier-unified/issues/630), recipe shell substitution | [Recipes PR120](https://github.com/microsoft/amplifier-bundle-recipes/pull/120) remains unchanged at `3a8a0915464f` and on Request Changes. The exact renderer-function probe still demonstrates nested-expansion injection. [Owner policy `46e9c5fb`](https://github.com/microsoft/amplifier-bundle-recipes/blob/46e9c5fb521bea07836d2eafb0c00bb6ac8658f6/contracts/recipe-dependency-manifest.v1.md) already requires benign-pattern compatibility and initial shell-source confinement. This is **implementation and environment-readiness work**, not a pending product decision. | Canonical owner must implement and qualify its admitted design across both engines, the unchanged compatibility corpus/goldens, package isolation, activation and sanctioned CLI proof. There is no production candidate to adopt. A private handoff records the available isolated spark-2 environment; no second Unified parser or partial quoting fix is proposed. |
| [667](https://github.com/microsoft/amplifier-unified/issues/667), computer-use qualification | [Computer-use PR29](https://github.com/microsoft/amplifier-bundle-computer-use/pull/29), commit `63871d4691d4`, binds package runtime assets and packaging policy in source and installed identities. Six original regressions fail; 167 plus 13 focused checks pass. Two actual wheels contain 31 bound runtime files, including both PowerShell scripts; source, RECORD and installed identities agree, and harmless asset mutations are detected. Python 3.11/3.12, Ruff and CLA checks pass. | Draft PR remains unmerged. Installed Windows execution and optional Unified dependency adoption remain unqualified; package asset proof is not a desktop execution claim. |

The inspected qualified runtime had Core 2.0.1 and Foundation
`85532ee4190c687dd7b582861a4c99c8323446ca`; recipes and computer-use were absent.
Its bash metadata did not provide a source commit, so the upstream validator
result is not represented as a verified installed-bash result. The repository's
optional computer-use pin also differs from the latest audited upstream source.
Each dependency requires its own version-bound adoption check.

Other reports retain their canonical owners: [668](https://github.com/microsoft/amplifier-unified/issues/668)
and [613](https://github.com/microsoft/amplifier-unified/issues/613) concern
amplifier-agent's SQLite export and AGENTS loader; [571](https://github.com/microsoft/amplifier-unified/issues/571)
concerns Tempo's native Keychain/main-thread behavior. These are not demonstrated
Unified defects. [470](https://github.com/microsoft/amplifier-unified/issues/470)
belongs to the secondary AHP/ACP qualification track, which cannot substitute for
main-application acceptance. [258](https://github.com/microsoft/amplifier-unified/issues/258)
still needs actual provider/audio language evidence; a synthetic voice lifecycle
pass cannot establish the cause of a historical language change.

## Existing safety boundaries that remain intentional

The current recovery path for [750](https://github.com/microsoft/amplifier-unified/issues/750)
passed explicit removal, replacement-model selection, reload persistence and a
fresh send in a real Settings/composer browser flow. It did not replay the earlier
uncertain input. Seventeen backend recovery tests also passed. No new provider
fallback was introduced. Broader isolation in [297](https://github.com/microsoft/amplifier-unified/issues/297)
requires the Core/Foundation/routing owners to agree on preparation boundaries;
silently selecting another account is not a repair.

The [635](https://github.com/microsoft/amplifier-unified/issues/635) fix isolates
tool update attempts, preserves successful tools, and offers explicit Retry/Skip
with bounded diagnostics. It does not automatically repeat an uncertain update
after restart. The UV policy/reset controls passed 66 checks; one explicitly
opt-in real UV-install reset test was skipped and is not counted as acceptance.
Historical editable-path installations are not silently rewritten. The original
spark-1 0.20.165 failure retained only an OSError class, with no errno or failing
operation; disk exhaustion remains plausible but unproven. Ownership-checked
cleanup and improved future diagnostics do not retroactively prove that cause.
