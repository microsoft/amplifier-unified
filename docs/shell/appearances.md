# Web appearances

**Current direction: user-approved PR1916 rollback.** After the `.078` visual
regression, restore the pre-cutover direction at
`7cfb76cbbadbfdf19d6a142e2b6e910abe16ed07`. Reference and Global / This browser
profiles are temporarily retired, not alternative active rendering paths.
The [interaction contract](appearance-contract.md) owns observable promises;
[Themes](themes.md) describes the restored complete-theme and format-1 API.
The [November 9 design note](../ux/decisions/20261109-appearances.md) is retained
as a superseded, deferred plan, not current authoring instructions or acceptance.
Documentation alone does not establish installed or browser conformance.

## The experience

A person can choose, share or ask an agent to adapt a coherent web experience:
its visual identity, arrangement, information hierarchy and interaction feedback.
A quiet writing workspace and a detailed research workspace present the same
underlying work, essential actions and truthful status.

Appearances belong to the web client. Native and terminal clients retain their
own presentation. A web appearance never changes agent execution, providers,
tools, permissions, automation, conversation content or artifact identity.

## Contract

An Appearance may compose themes, supported layout and registered components,
artifact viewers, information hierarchy and interaction feedback. These are
separate published capabilities, not one atomic package API. Accessibility and
personal preferences constrain every appearance; applying a theme must not
silently change scheme, decorations, Reveal, permissions or automation.
Required decisions and recovery remain discoverable.

The host owns behavior, security, drafts, conversation data, artifact versions
and action semantics. Inline previews, galleries and Canvas retain the same
saved artifact identity and version; changing views does not regenerate work.

## One foundation, deliberate differences

This heading retains the redesign's link target. Its Reference-delta proposal is
deferred. The restored baseline supports complete themes and CSS skins, with the
old built-in default when no legacy theme is available. Common functional repairs
still belong in shared components; this does not require a Reference renderer or
authorize rewriting saved custom CSS.

## Global or browser settings

The Global / This browser selector and complete profiles are temporarily retired.
The restored model has an app-wide committed theme and client-targeted temporary
preview, with independent shell presentation preferences. Library save/import/
update does not apply a theme; apply and revert use the established theme owner.
No cross-host preference synchronization is implied.

Preserve newer `shared-reference-v1`, browser-local profiles and format-2 saved
libraries inert and undeleted. They do not seed or override the legacy path.
If the old theme was removed by `.078`, use the safe old default; do not claim
the personal theme was automatically migrated or recovered. See the
[rollback boundary](appearance-contract.md#temporary-rollback-boundary).

## Portable and extensible

Legacy portable CSS and version-1 ZIP styling packages are re-enabled. The ZIP
contains a complete stylesheet, supported images/fonts and attribution; it does
not install components, layout, permissions, automation or user preferences.
Unsupported contents fail explicitly without a library mutation.

A single package spanning all appearance surfaces, a unified experience gallery,
cross-device synchronization, arbitrary transcript/composer replacement and
suggested experience profiles remain future work. Any extension needs explicit
ownership, capabilities, persistence, accessibility, failure recovery, preview
and revert contracts. Importing a package never grants executable capabilities.

<a id="user-and-agent-authoring"></a>

## Authoring and evidence

People and agents use the same operations: discover, inspect, propose, preview,
verify, apply and retain a way back. Identify the intended client and distinguish
client-local preview from app-wide apply. Preserve unaffected components and
drafts with revision and dirty-edit guards. A saved library entry, successful
validation and observed browser activation are different facts.

The [appearance-authoring skill](../../skills/appearance-authoring/SKILL.md) ships
with the released interface; [amplifier-shell](../../skills/amplifier-shell/SKILL.md)
covers supported composition. Package validation is not visual or accessibility
certification. Compare realistic content in both schemes and exercise keyboard,
narrow layout, reduced motion, forced colors and recovery.

## Shipped building blocks and remaining work

[Themes](themes.md), [components](components.md) and the [shell API](README.md)
describe complete themes, preview/revert, client presentation, registered
navigation modules, named contributions and Canvas renderers. The
[design note's delivery plan](../ux/decisions/20261109-appearances.md#delivery-and-checks)
is deferred with the redesign; it is not a new rollback test campaign or release
gate. Source changes, qualification, publication and live adoption remain separate.
