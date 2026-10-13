# General artifact presentation and retained reads

Status: implementation direction approved October 13, 2026; acceptance is pending.
This document records the approved refinement of CX5/CX7 and CS2/CS4/CS7/CS8.
It does not amend the ratified shared-client contracts or claim implementation.

## Source intent and decision

The user requests that all non-image-generation content consistently support a
compact chat launcher, a renderer using the full chat content width with a clear
Open in Canvas action, and Canvas presentation. An agent may choose either chat
presentation and open Canvas as well. The supplied compact-card reference has a
file icon, descriptive title, version label and expand control. Image generation
has a separate experience and intent and must not be reused for ordinary images.

The reviewed recommendation is approved for implementation: use existing artifact
owners and saved versions; remove recurring transcript qualification from retained
media reads; retain strict admission checks; unify general presentation without
redesigning Foundation storage. Fable's design review is advisory, not acceptance
of source or runtime behavior. No merge, release or production install is included.

## Ownership and lifetime

1. Resources, OutputRegistry and Canvas library/versions remain their existing
   canonical owners. A presentation adapter can reference them; it must not add a
   competing artifact store or duplicate media bytes for a second surface.
2. Attach/publication validates the caller, origin, workspace, exact input hash,
   media structure and write authority. Retained reads validate owning conversation,
   exact saved object/version/hash, current authenticated client scope and resource
   integrity. Appending or revising history does not require re-proving that a
   previously admitted object exists. Message identity remains provenance, not a
   perpetual dependency on a stable live transcript.
3. Imports and transfers validate reference-to-output binding and ownership at
   admission. Existing signed/reviewed transfer checks remain mandatory. A payload
   flag, raw resource hash or caller-supplied provenance is not read authority.
   Retained origins need not still appear in active history: validation must not
   restore the recurring-history dependency under a different name.
4. Forks retain current surviving-origin preflight, cutoff semantics and target
   ownership, remapping exact output and Canvas references. Unanchored and future
   publications do not leak into the target. Equal text does not establish origin.
5. Unlink removes the active relationship and automatic publication, not retained
   authorized library access or bytes. This deliberately aligns media with generic
   output semantics. Closing/collapsing a view is not unlink or deletion. Existing
   reviewed conversation deletion and resource-retention/GC rules continue to own
   actual removal. No new delete operation is introduced.

A source file may disappear without invalidating its immutable saved version.
History revision retains artifacts in the original authorized conversation, even
when an old publication anchor is not in active history. Preserve existing IDs,
version links, hashes and bodies. Legacy GIF wrappers may use a compatibility
adapter; never fabricate a message anchor. If a trustworthy anchor is absent,
keep a library-only version rather than publish it under an unrelated message.

## Presentation contract

Two choices are independent:

- Saved publication mode: `chat: none | compact | inline`.
- One-time display intent: `canvas: unchanged | open` for one eligible client.

The five useful combinations are compact, inline, Canvas-only, compact+Canvas,
and inline+Canvas. These refer to one exact content version. `unchanged` never
closes Canvas. Canvas opens in normal split view, not forced full-screen focus.

New explicitly general publications default to compact unless a mode is supplied.
Legacy displayed images/auto-preview formats retain inline behavior; legacy compact
documents stay compact. Canvas-only content remains discoverable in the library.
The same host operations are available through authenticated user and agent paths;
never spoof UI origin to invoke a UI-only media action.

Save/publication outcome and requested-client display outcome are separate. Dirty
Canvas can block opening while publication succeeds, with explicit per-surface
results. Unknown commands are reconciled by the existing stable command identity,
not reattached or automatically replayed. Reconnect restores local view state but
never replays a stale Canvas-open effect.

User expansion/collapse and renderer zoom, reading position and playback are
client-local, retained on that client across reload where supported, not routinely
shared with peers. The durable publication mode is the fallback, not a command to
erase an existing local override. Dirty view guards remain effective on collapse,
replacement and release.

## General shell versus image generation

The compact card follows the reference: icon, descriptive title, version and a
clear Open in Canvas affordance. An inline renderer spans the chat content lane,
not a fixed image-generation tile. Content retains appropriate aspect ratio,
height/scroll behavior and controls, with Open in Canvas and Collapse available.

Only host-verified generation provenance selects the image-generation gallery.
Preserve its progress, stable receipt slots, variants and edit/follow-up workflow.
An image kind/MIME or untrusted generation identifier alone is insufficient.
Ordinary screenshots, plots and images use the general renderer. Shared codecs and
render primitives do not imply shared generation-specific presentation chrome.

## Renderer and concurrent-view scope

Required scope is the existing supported general kinds: Markdown, text, code,
JSON/JSONL, image, HTML, Mermaid, DOT, PDF, Babylon, GIF, MP4, CanvasApps, MCP Apps,
browser references and A2UI. They share the presentation contract, but mounting and
interaction capabilities must be qualified per renderer.

Each live surface has its own view ID/generation, transient state, reports and
cleanup. Media starts paused and respects reduced motion; simultaneous views must
not duplicate audio/autoplay or overwrite one another's observations. Two HTML or
3D instances are independent runtimes, not magically shared script state. CanvasApp
durable state follows its existing revision contract while unsaved edits remain
private. Mounting an MCP view must not repeat its launch/tool operation. Browser
URLs remain references rather than fabricated snapshots; existing sandbox and
network capabilities are unchanged. A2UI events retain existing delivery semantics.

Respect bounded mounts. The current eight-inline-view cap refuses an extra mount;
it does not authorize evicting dirty previews. Expose a clear compact/Canvas handoff
when a mount cannot proceed. Any genuinely unsupported inline/concurrent capability
is an explicit remaining gap for its named renderer, not a silently removed portion
of the user's requested scope or a successful blank view.

## Acceptance before a reviewable PR

1. Retained reads: real saved historical media opens during repeated actual history
   saves/appends and after history revision, without live transcript scans on the
   read path. Wrong owner/client/ID/version/hash, corruption, malformed imported
   references and revoked conversation access still fail. HEAD/Range/download and
   decoded GIF/MP4 bytes retain their current safety and resource bounds.
2. Lifecycle: same artifacts survive reload, cold restart and source removal;
   unlink suppresses automatic chat display but exact library access survives;
   fork cutoff/remapping, signed transfer/import, old GIF links and GC preserve
   identities and prevent cross-conversation access. No invented legacy provenance.
3. Presentation: exercise all five combinations through actual UI and agent host
   actions; exact version remains fixed; Canvas-only is discoverable; card/inline
   switches persist locally without changing a peer. Dirty guards, partial results,
   unknown command reconciliation and reconnect never replay effects.
4. Rendering: representative named kinds at desktop and narrow widths, keyboard,
   reduced motion, failed/retry states, real media motion and pause, two simultaneous
   views, independent observations, bounded mount/decoder/frame resources, cleanup,
   interactive unsaved state, and no repeated MCP work. Imagegen fixtures retain
   progress/variants/receipt identity and ordinary images never enter its gallery.
5. Delivery evidence: exact current-main source, normal build, installed noneditable
   package, focused backend/full frontend tests and real browser journeys; inspect
   rendered artifacts and applicable hosted checks, then independent code/result
   review. Source presence, mocked transport or a save receipt alone is not delivery.

## Scope exclusions

No Foundation commit descriptors or checkpoint locks, new canonical store, content
body duplication, sandbox/CSP relaxation, new paid model/image request, new account,
shared clipboard effect, arbitrary live user-state migration, or automatic merge,
release, installation, destructive deletion or unrelated issue work.
