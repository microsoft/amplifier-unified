# Appearances: interaction contract

See [Appearances](appearances.md) for the umbrella vision and authoring model.

An appearance is a coherent presentation of the whole application, not only a
palette. It covers surfaces, typography, density, spacing, borders, icons,
focus, interaction states, motion and the hierarchy of information. The host
owns behavior and accessibility; an appearance changes their presentation.

## Independent preferences

- Theme: semantic colors, type, surface treatment and optional artwork. Use
  host tokens, including the contrasting on-accent foreground, rather than
  assuming that accent buttons always have white text.
- Scheme: light, dark or system. Every state must work in both palettes.
- Decorations: artwork on/off, independent of operational feedback.
- Detail/reveal: Focus through Everything changes how much supplementary
  information is shown. It never hides pending work, errors, required decisions,
  or the means to cancel/recover. Everything is not permission for more motion.
- Motion: honor the operating system's reduced-motion preference. The current
  host does not expose a separate global app motion preference. Static feedback
  must carry the same meaning; motion must never be the only signal.
- Contrast and input: forced colors, keyboard, touch, zoom and narrow screens
  remain usable regardless of appearance or detail level.

The theme definition, shell presentation preferences and operating-system
preferences are separate inputs. Do not silently write accessibility or detail
preferences when applying a theme.

## Waiting and saving

| Situation | Feedback | Interaction |
| --- | --- | --- |
| User starts an action | Initiating control looks unavailable; its existing icon becomes a compact busy indicator; use an action-specific label such as Saving… or Checking… where the owning component has lifecycle state | Prevent duplicate activation immediately; retain keyboard focus |
| Server accepted a long operation | Keep local status/label until the operation actually settles | Acknowledgement alone does not re-enable conflicting actions |
| Background refresh | Keep prior content visible, with a small local text status when useful | Keep reading, navigation and unrelated editing available |
| Save requires a lock | Explain Saving… outside the disabled fields; dim only affected controls | Use native disabled controls/fieldset for the precise write scope; preserve entered values |
| Failure or uncertain completion | Show a local explanation and supported retry/check-status action | Keep the draft; do not silently resend uncertain work |
| Successful completion | Restore the normal control; show a brief local confirmation when useful | Preserve focus and layout; do not flash the whole section |

`aria-busy` and `data-region-pending` express activity, not authorization to lock
an entire subtree. Do not derive `disabled`, `inert`, opacity or pointer blocking
for a region from these attributes. The owner of the save knows which inputs
conflict. Close, cancel, help and diagnostics remain accessible unless their
specific operation makes them unavailable.

For shared HTTP action feedback, buttons use `aria-disabled` plus captured
activation suppression so keyboard focus stays in place. Components continue
to own native `disabled` for validation and long operations. Native keyboard
activation produces the same guarded click as a pointer. Programmatic dispatch
and backend idempotency remain separate concerns.

## Visual rules

- No animated region background tint, traveling top-edge stripe, or page-wide
  pulse. Pending regions retain their authored background, border and shadow.
- Preserve the dimensions of existing icon slots. Labels and accessible names
  remain meaningful. Do not inject generic DOM text into React-owned labels.
- Busy icons inherit the control foreground; reduced motion uses a static broken
  ring with the same label/status. Forced colors uses system control colors.
- Use one clear local status, not a spinner on every child of a busy section.
- A save frame is an optional future treatment, not the default. If introduced,
  reuse the existing border or an inset overlay without changing box size;
  never obscure a focus ring. Its static version must be sufficient, and it
  must be tested across every appearance and motion/contrast preference.
- Essential feedback is the same at every detail level. More detailed modes
  may disclose phases, counts, timings and diagnostics behind disclosure.

## What is implemented and what still needs work

Existing theme schemas cover complete light/dark palettes, artwork and portable
CSS; shell preferences cover presentation, and Canvas apps receive host color,
contrast and motion context. See [themes](themes.md) and
[conversation surfaces](conversation-surfaces.md).

Shared action feedback supplies immediate busy semantics, duplicate-click
suppression and an in-place icon indicator. Activity regions retain lifecycle
semantics without visual surface effects. Components own working labels,
long-operation lifetime and native input locking. Text-only controls need a
component-owned working label or adjacent status; the shared layer does not
fabricate one or assume an HTTP receipt completes the job.

This contract is the design baseline, not a claim that all existing controls
conform. Remaining work is to inventory text-only actions and save scopes,
standardize component-owned labels/locking, and extend automated appearance
coverage across all surfaces. Custom CSS remains capable of overriding host
styles; schema validation alone does not prove visual/accessibility compliance.
A richer versioned interaction-token schema and an app-level motion preference
are future work, not options the current UI already offers.

## Acceptance

### Chat Plan placement

`presentation.planPlacement` accepts `composer` (the default when absent) or
`inline`. Settings → Appearance → Plan location exposes these as **Above
composer** and **Inline in chat**. It uses the existing saved presentation
protocol, including agent-driven presentation changes. Changing placement must
not change the plan, execute work, or reset the conversation.

Both placements consume one read-only projection of the latest successful todo
receipt for the selected chat. Child-chat plans do not replace a root's plan.
Canonical event logs own the reports; the activity page and loaded transcript
window do not determine whether the Plan is available. The projection can be
rebuilt after restart and is not an independently editable task store.

Show the reported completion count and the current step, with an expandable
checklist above the composer or an initially expanded checklist in chat. A
reported in-progress step is only shown as working when its owning turn is
currently active. Paused/failed work remains unfinished. Completed checklist
items are agent reports, not proof of an output, test, publication or release.
An empty reported list clears the Plan. Unreadable or oversized current reports
show an unavailable state rather than substituting an older plan.

Essential Plan information remains available at every Reveal level. Existing
appearance tokens own colors and spacing; the first implementation uses no
animation, so reduced motion does not lose status information.

Hold requests and long-operation completions independently. Check immediate
feedback, duplicate pointer/keyboard suppression, failure cleanup, draft
preservation and dismissal. Compare pre/pending/post geometry. Confirm a
refresh does not disable unrelated fields or alter its parent surface.

Exercise light/dark/custom themes, decoration on/off, Focus/Balanced/Everything,
normal/reduced motion, forced colors and narrow layouts. Check readable status,
visible focus, accessible names and live announcements. Verify actual busy
states; static theme thumbnails and screenshots alone are insufficient.
