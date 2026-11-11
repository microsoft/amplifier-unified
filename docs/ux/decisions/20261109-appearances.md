# Web appearance foundation and preference scope

**Accepted product direction, November 9, 2026; UX correction November 10.**
The consumer-accessibility requirement remains accepted. The latest November 10
product-owner correction replaces per-setting overrides and the browser subpage
with one Global / This browser source switch and identical settings controls.
Earlier subpage captures and the running review build are not accepted evidence
for this new whole-profile direction.
Source baseline: Unified `d7b16b8d637c86370b11423c850cf8407be9b54c`.
No runtime change, browser acceptance, DTU deployment or PR readiness is implied.
[Vision](../../shell/appearances.md) and [contract](../../shell/appearance-contract.md)
are the canonical product owners; this note explains choices and delivery.

## Decisions from the product owner

- Always compose appearances over a neutral, no-frills foundation; keep it out of
  the ordinary gallery, with advanced access. Other appearances contain minimal
  intentional differences. Shared fixes should not require editing each skin.
- Reduce incidental drift, accepting visual changes after real A/B review rather
  than preserving every current pixel. Preserve useful, intentional identities.
- Keep portable ZIP packages. Plan for presentation settings and more complete
  web experiences beyond CSS, without implementing every extension now.
- Appearance belongs to the web client. Preserve agent awareness and operations
  through explicit supported boundaries, not coupling to native execution.
- Choose one complete Global or browser profile, replacing the earlier per-field
  scope proposal. Package uploads/storage remain global; profile selection does
  not fork the library. Losing browser-local settings returns to Global.
- Retcon the shipped system. No legacy custom-CSS support, format conversion,
  flattened compatibility export, old/new renderer selection or migration
  emulation. Do not delete unrelated files. Existing non-appearance contracts
  and independent component trust boundaries remain outside this clean break.
- Update the appearance authoring skill and examples with the interface. Trim
  duplicate guidance and historical workarounds instead of appending procedures.
- Use a separate container DTU through the Smart Tool, accessible on the LAN for
  the product owner's test drive before calling the result ready to PR.

## Preference interaction

The November 10 hands-on review rejected the implementation's repeated
full-width **Shared / This browser** controls, technical explanation, half-width
gallery and oversized checkbox tiles. A later review rejected the complexity
of even a separate direct-edit subpage and removed per-field scope entirely.
The [shared-client vision](../../clients/VISION.md#2-common-meaning-native-interaction)
and [CX8](../../../contracts/client-experience.v1.md) own the consumer-accessible
baseline; the [appearance contract](../../shell/appearance-contract.md#interaction-economy)
specializes it. The current review build is not visually accepted.

### Revised controls: one complete profile

Use the sidebar Browse/Status two-choice selector pattern with **Global** and
**This browser**. This selects the profile actually used, not merely an edit
destination. The same page, gallery and setting controls render in both modes,
and reversible edits save directly to the selected destination with Undo and
local failure feedback. Remove the browser subpage and per-row clear, share and
scope indicators rather than retaining an alternative partial-override path.

Default to Global. The first explicit local selection copies all current Global
settings with defaults resolved, including nested visibility choices. Thereafter
the two sets are independent. Global changes affect Global browsers only;
switching back and forth retains the browser profile without publishing it.
The source choice is itself browser-local, versioned and acknowledged before
activation. Opening or closing Settings writes nothing. A failed source switch
or local edit retains the last confirmed presentation and never writes Global.

Keep the gallery outside the generic preference row and use responsive,
single-selection preview cards with one visible selected indicator, not a radio,
check, outline and selected-name pill all repeating it. Retain accessible
single-selection semantics without a redundant **Use [name]** button.
Use a small visible light/dark/system choice and normal-sized toggles/choice
groups. Group conversation display separately; advanced appearance options and
package authoring belong at the bottom of the same page in both modes.
Do not silently change saved values or hide required operational feedback when
simplifying these controls. Validate this screen itself at narrow widths and
enlarged text; successful screenshot capture or no overflow does not establish
consumer usability.

### External patterns informing the proposal

Official documentation checked November 10, 2026:

- Slack [shares the selected theme across devices](https://slack.com/help/articles/205166337-Change-your-Slack-theme)
  but [makes dark mode device-specific](https://slack.com/help/articles/360019434914-Use-dark-mode-in-Slack).
  The product supplies ordinary defaults rather than a persistence choice per edit.
- Notion offers [system/light/dark appearance and separately device-local high contrast](https://www.notion.com/help/account-settings).
  Following the system is a meaningful value, not an instruction about storage.
- Chrome puts [account-saving controls in one management location](https://support.google.com/chrome/answer/165139?hl=en&co=GENIE.Platform%3DDesktop)
  and documents themes/settings following the signed-in account.

These informed the earlier simpler-default proposal, not an
identical whole-profile UI to copy. They establish documented behavior, not
pixel-verified layouts or measured usability. Unified must describe its actual
connected-host scope rather than imply these products' cloud account behavior.

| Example | Scope | Result |
| --- | --- | --- |
| Select Global, then Lightline | Global | Browsers using Global use Lightline. |
| Select This browser, then Dark | This browser | The complete copied browser profile stays independent of all Global changes. |
| Return to Global, then This browser | Browser source choice | Global resumes, then the retained local set resumes; neither is copied over the other. |

`false` values and empty supported collections remain deliberate profile values,
not inheritance markers. A stale Global edit conflicts without replacing unrelated
fields or a dirty editor. Private package editor drafts remain a separate journey.

**This browser** covers tabs in one browser profile for the same
host and authenticated preference owner. Tabs share the committed complete
browser profile and active source, not
previews or editor drafts. A browser-wide local change may reach another tab,
but must not replace its active preview or dirty draft. If tab-independent
committed preferences are desired later, name that scope explicitly rather than
silently changing this one. This scope does not introduce a new auth model.

Persist the small complete browser profile and source choice under a stable host/data-root and
preference-owner namespace supplied by the authenticated adapter. Do not key
account ownership from caller-supplied client IDs, process IDs or credentials.
Storage loss returns to Global. Transient disconnect is different:
retain last-confirmed values with honest freshness and do not replay shared edits.
A different owner or host must not inherit another namespace's browser profile.

### Account reality

Today's service has one authenticated host owner, not distinct application user
accounts. The initial shared record belongs to that owner on that host. Resolve
its identity server-side. Keep this small boundary usable by a future account
provider, but do not add a multiuser/authentication service or advertise cross-host
account synchronization as part of this appearance change. The library remains
at its existing authorized shared scope, independently of preference scope.

## Foundation and package composition

Reference is web-client infrastructure, not a selectable skin that packages
copy. It owns semantic color/type/surface defaults, form/control primitives,
component structure, responsive sizing, message-body/footer separation, focus,
scroll clearance, local feedback and accessibility fallbacks. Components still
own their lifecycle; CSS does not infer permission from activity attributes.

Appearances own authored palettes, font-role selections, material, artwork and
supported geometry. Add hooks for demonstrated common needs, not every CSS
property. Fix repeated selection insets, wrapper resets, on-accent foregrounds,
font inheritance and readable artwork backing in the common components first.
Avoid label/child-position selectors and specificity arms races. Thumbnail,
preview, applied view and startup cache must use the same composition identity.

Proposed cascade: reference/component defaults, intentional appearance treatment,
explicit presentation choices, then narrow functional/accessibility safeguards.
Choose concrete CSS layering only after removing copied old defaults. Compatible
base fixes propagate; a package fingerprint alone is not render identity when
base bytes change. Preview binds the actual base/build, package and preference
revisions. The chosen envelope is ZIP `formatVersion: 2`, requiring
`base: "reference-v1"` and one delta stylesheet with declared assets/notices.
Library authoring accepts a named delta; import/export transport ZIP packages,
not a second complete-CSS format. Export requires a saved library ID. No
old-format branch is retained to accommodate this redesign.

The first package slice is styling/assets/notices over Reference. Future profiles
can supply allowlisted density, disclosure, metrics, plan and panel choices;
executables/components retain their separate trust contracts.

**Accepted interaction:** ordinary selection changes the look only. Adopting an
appearance's suggested experience settings requires a separate, explicit preview
and apply action, not a side effect of selection. Show intended field
replacements and honor the active complete profile; neither action
switches its source. Package updates do not silently reapply suggestions over
personal choices or accessibility preferences. The supported profile fields and
concrete controls remain to be designed; acceptance of this interaction does not
mean profiles are implemented or expand the initial styling-package slice.

## Web/service and agent seam

Retain authenticated server validation, fingerprint-bound stale-write protection,
library storage and receipts in a web-owned capability. Generic host/runtime owns work
facts and routing, not CSS, browser layouts or a mandatory appearance catalog.
Use canonical activity/attention projections read-only. Native/terminal clients
need not render, persist or implement web appearance settings.

Reuse shell revision, preparation, preview, dirty-instance and recovery machinery
rather than add another transaction store. Agents discover supported web targets,
read bounded effective/Global/browser profile state with one active source, and use the same
scoped operations as UI controls. Retire shared shell presentation seeding and
the competing full-skin store. Existing public shell presentation controls remain
useful: explicit client-scoped presentation changes use a complete browser
profile, preserving other resolved values rather than introducing partial
inheritance. Check shell revisions/dirty instances before issuing a change;
activate mixed composition changes atomically only on successful persistence
acknowledgement from the admitted browser generation.

Library mutations and preference mutations are separate. Cancel-preview and
undo-commit are distinct. Unsupported/disconnected
clients have explicit availability; a saved result is not browser activation.
Do not expose private drafts just to inspect appearance settings. No AHP/ACP
cutover, repository extraction or generic Canvas rewrite is prerequisite here.

## Delivery and checks

1. Land the agreed design in the existing owners; keep current API instructions
   honest until code and documentation are replaced together.
2. In an owned worktree, establish Reference, semantic hooks and one rendering
   path. Convert all bundled appearances, including Amplifier, to small deltas.
3. Implement complete Global and browser profiles with one active source. Remove old
   snapshots, stock-CSS migration tables and superseded actions with their paths.
   Select the replacement package/API shape before rewriting author examples.
4. Compare A/B with identical realistic content: navigation, long chat/code,
   expanded composer, settings save, errors/approvals and Canvas. Label intended
   identity, incidental simplification and functional corrections separately.
5. Verify two browser profiles and multiple tabs: first-use copying, complete
   independence including nested settings, Global updates, retained sets across
   repeated source switches, storage loss/failure, stale edits, previews, reload,
   disconnect and conditional undo. Exercise every bundled appearance and Reference
   in light/dark/system, narrow/zoom, decorations off, reduced motion and forced
   colors. Demonstrate one base fix reaching every bundled appearance unchanged.
6. Round-trip ZIPs with assets/notices. Reject unsafe/unsupported members before
   mutation. Validate actual packaged skill discovery and companion examples;
   fewer prompt bytes are not proof of better authoring behavior.
7. Launch one owned user-review DTU via the Smart Tool after implementation.
   Qualify app startup/authentication, ARM images and explicit LAN app binding.
   Keep DTU management/Gitea private, app data separate from production, and record
   universe/port/source ownership and cleanup. Verify from the user's LAN browser;
   container health or a printed localhost URL is not LAN/browser acceptance.
8. Incorporate the user's test-drive feedback, then assess focused PR readiness.

The ordinary managed test runner publishes no ports; it cannot provide the LAN
preview. Inspected DTU `0.6.9` at `88d9d84149eee914372c1ca8d68e9eb9f4ba3290`
supports deterministic CLI/library launch with Compose profiles. Its advertised
MCP launch surface is insufficient: discover the installed interface rather than
inventing a launch tool. No installation or environment was created for this
research. Setup, actual LAN reachability and ARM support remain unqualified.

## Method and source basis

Converge `c63f1e6bea6d93e5ffb86b0e3dbcc698391cbe9c` and Method
`86b9a4a457df22c48b80a4c423b3e421dd309292` were refreshed November 9 with the
registered family and directly material integration repos. Their useful lessons
are one owner per promise, explicit scope, falsifiable outcomes and separate
evidence. Unified's [vision/contract guidance](../../vision-and-contracts.md)
owns their application here; formatting quotas and governance services are not
new dependencies. This note is not proof of runtime conformance.