# Web appearances

**Accepted direction, November 9, 2026; implementation pending.** This is the
web client's destination, not a claim about installed capabilities. The
[interaction contract](appearance-contract.md) owns observable promises; the
[design note](../ux/decisions/20261109-appearances.md) separates accepted choices
from proposals and delivery work. [Themes](themes.md) describes the current API
until client, service adapter, examples and skills change together.

## The experience

A person can choose, share or ask an agent to adapt a coherent web experience:
its visual identity, arrangement, information hierarchy and interaction feedback.
A quiet writing workspace and a detailed research workspace present the same
underlying work, essential actions and truthful status.

Appearances belong to the web client. Native and terminal clients retain their
own presentation. A web appearance never changes agent execution, providers,
tools, permissions, automation, conversation content or artifact identity.

<a id="contract"></a>

## One foundation, deliberate differences

A neutral **Reference** foundation is always present. It supplies usable
light/dark defaults, semantic typography and surface roles, component geometry,
responsive layout, accessibility and common interaction presentation. It has no
artwork or decorative motion and is available through advanced controls and
recovery, not the ordinary gallery. The ordinary Amplifier appearance is another
treatment over Reference, not the foundation itself.

Every bundled appearance adds only intentional differences: palette, font roles,
material, artwork and supported geometry. Common repairs belong in shared
components. A compatible base correction reaches every appearance without
copying the base into packages or editing every skin. Distinctive design stays;
incidental drift can be simplified through actual before/after review.

## Global or browser settings

The package library remains shared/global within the installation's authorized
library scope. Uploading, saving or sharing a package is independent of selecting
it. A local selection does not create a private package or duplicate assets.

One familiar **Global / This browser** selector chooses the complete set of
appearance settings in use. Both choices show the same controls and layout,
with no per-setting scopes or mixed inheritance. Global settings live in the
service; the browser's complete settings and source choice live in its browser
profile. On first use, This browser starts from Global; later switches preserve
the independent sets. Global changes never replace a saved browser setting.
Losing browser storage returns to Global without changing it. Global means this
authenticated host owner's service, not cross-host account synchronization.
Keep ordinary selection direct and mark it once; advanced appearance options
belong at the bottom, not in a second settings experience.

## Portable and extensible

ZIP remains the portable package envelope for small appearance treatments,
supported images/fonts and attribution. A package declares the web appearance
contract it needs, not a frozen copy of the application. Unsupported contents
fail explicitly. This redesign replaces the shipped appearance formats and skins
together: no legacy custom-CSS emulation or dual rendering path. This does not
authorize deleting unrelated files or weakening package validation.

The destination extends beyond CSS to allowlisted presentation profiles and,
later, compatible shell/component composition. Selecting an appearance changes
its look; adopting its suggested experience settings is a separate, explicit
preview-and-apply action. Neither switches the active Global/browser profile.
New surfaces reuse existing trust, revision, preview and recovery mechanisms.
Importing a package never grants executable capabilities. Profile controls,
supported fields and later component packaging remain unimplemented.

<a id="user-and-agent-authoring"></a>

## Authoring and evidence

People and agents use the same scoped operations: discover, inspect, propose,
preview, verify, apply and retain a way back. Account writes and local-client
writes identify their scope. Preview never publishes shared preferences. A saved
choice, successful validation and observed browser activation are different facts.

The [appearance-authoring skill](../../skills/appearance-authoring/SKILL.md) ships
with the released interface; [amplifier-shell](../../skills/amplifier-shell/SKILL.md)
covers supported composition. Package validation is not visual or accessibility
certification. Compare realistic content in both schemes and exercise keyboard,
narrow layout, reduced motion, forced colors and recovery.

## Shipped building blocks and remaining work

[Themes](themes.md), [components](components.md) and the [shell API](README.md)
describe current support, not completed Reference/profile synchronization. The
[design note](../ux/decisions/20261109-appearances.md#delivery-and-checks) owns this
redesign's implementation and LAN test-drive plan; human review precedes PR readiness.
