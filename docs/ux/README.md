# Home and sidebar design review

This is a proposed agreement for the next UX review. The aim is to help a person
arrive, understand what needs their attention, and find familiar work without
first learning how Unified organizes execution.

The implementation baseline is [v0.20.188](https://github.com/microsoft/amplifier-unified/releases/tag/v0.20.188).
The documents below are **draft proposals**. They change no existing agreement
until the product owner accepts the exact wording. Shipping the UI did not
ratify these new documents, and passing interaction tests did not establish
that the experience is clear to its intended audience.

## Start here

1. Read the stakes below and the [proposed Home and sidebar promises](../shell/work-discovery.v1-candidate.md).
2. Try the [six review journeys](review.md#review-with-real-work), using the
   designer's familiar work and ordinary devices.
3. Record observations against a promise or an open design question using the
   [feedback form](review.md#record-one-observation).
4. Review the exact wording changes in the table below. Return the edited
   documents and the decisions to the implementation agent.

## Stakes to carry forward

These summarize the product owner's expressed intent. The exact draft wording
is in the linked candidates; this summary does not create a second set of rules.

| Stake | Why it matters | Proposed home |
| --- | --- | --- |
| Support arriving and finding | Sometimes a person wants the next useful action; sometimes they already know the chat or project they need. Neither journey should require switching their whole working style. | Client vision; CX1 |
| Let work begin before organization | A one-off question is legitimate work. Choosing a project filters and supplies context; it is not a prerequisite for asking for help. | Client vision; existing workspace first-use promise |
| Change the view, preserve the meaning | Focus, Brief, and Studio present the same work at different densities. Appearance supplies a starting preference; a person can choose another view. | WD2; Appearance |
| Tell the truth about attention | An unread response is not an accepted deliverable. An idle conversation is not a completed project. Required decisions remain findable. | WD1; workspace attention promise |
| Remember the person's place | Returning Home restores its context. Sidebar organization is independent. A temporary Focus pass does not dismiss anything. | WD3, WD5 |
| Keep useful access nearby | Browse and Status share one sidebar row. Search and New chat remain available there even when Home offers them too. | WD4; CX8 |
| Make scope predictable | “Projects” currently refers to registered workspaces. Filtering cannot move work or quietly select another location. | WD6; existing workspace ownership |
| Keep the experiment affordable | Reuse saved data and bounded discovery. This iteration adds no summary jobs, model calls, or session lifecycle. | WD8; existing working-set promises |

The choice of a lighter or denser view reflects the person's preference. It is
not evidence that one layout is universally easier to use. The designer's review
should identify where these choices help and where they still export complexity
to the person doing the work.

## What remains open to design

- The clearest name for the sidebar's Status view and for “Ready for you.”
- Card emphasis, wording, spacing, typography, counts, and how discreet the
  three-view switch can be without becoming hard to find.
- The balance between one featured item and surrounding context in each view.
- Whether “Projects” helps people understand the existing folder-backed scope.
  A new logical grouping model is a separate product decision.
- How fresh, stale, unavailable, and empty information should look using the
  existing facts. A new classifier or new data collection needs a separate proposal.

The current layout is a useful baseline for comparison. Exact column counts,
pixel measurements, and today's card composition are not proposed as permanent
product promises. User-requested access and preservation behavior remains part
of the draft agreement; changing it should be an explicit discussion.

## Proposed document changes

| Draft | Decision it puts in front of you |
| --- | --- |
| [Client vision amendment](../clients/VISION.v2-candidate.md) | Make arrival, purposeful work, and different attention needs explicit in the shared destination. |
| [Client experience amendment](../../contracts/client-experience.v1-candidate.md) | Add discovery without setup and allow useful repeated access across independently scrolled regions. |
| [Workspace experience amendment](../workspaces/contracts/experience.v1-candidate.md) | Make the three-section sidebar the Browse organization, allowing the approved alternative without changing workspace ownership. |
| [Home and sidebar contract](../shell/work-discovery.v1-candidate.md) | Eight observable promises for the current web experience. |
| [Adaptive work notes amendment](../shell/adaptive-work-views.v1-candidate.md) | Separate the long implementation history from the short agreement and correct its obsolete preview-only framing. |
| [Review and evidence](review.md) | Review journeys, known documentation gaps, evidence limits, and a repeatable handoff. This is a note, not another contract. |

No amendment is proposed to execution, storage, protocol, permission, or
working-set authority. Their existing owners remain linked from the drafts.
The shared vision describes meaning across clients; it does not require a TUI
or native client to copy the web layout.

## How the collaboration works

The designer brings observations and alternatives. The product owner and designer
settle the intended experience together; the product owner records acceptance,
or explicitly delegates that decision. The agent identifies affected promises,
drafts precise wording, implements accepted changes, and returns evidence.
It does not accept its own proposal.

A review can result in **ratified**, **ratified with edits**, **declined**, or
**later**. Ordinary language expressing the same decision is sufficient. Record
the document, exact revision or hash, clause, accepted wording, decision-maker,
date, and reason. “Looks good” about a screenshot is not automatically approval
of every behavioral proposal beside it.

When feedback reveals a broken existing promise, fix that gap within the
authorized scope. When feedback changes the promise, agree to the amendment
first. When it concerns typography or composition within the promises, retain
the baseline, try the revision, and compare the same journeys.

Keep current files unchanged while these proposals are reviewed. Once accepted,
apply only the agreed changes with dated changelog entries, retain the decision,
and update the evidence table. Do not mark the documents locked before the
people reviewing them agree and the promises have examples of right and wrong.

This uses Converge's direction, proposal, and evidence discipline. It does not
install Converge, start managers, change the app's runtime, or add a collaboration
service. The optional Direction tool may later help present documents; its
wording decisions must still be distinguished from product ratification.

## Method sources

The [source record](sources.json) pins the nine repositories reviewed and Unified's
baseline. The [Method document rules](https://github.com/microsoft/amplifier-converge-method/blob/86b9a4a457df22c48b80a4c423b3e421dd309292/contracts/documents.v1.md)
separate visions, contracts, and notes. Its [settling rules](https://github.com/microsoft/amplifier-converge-method/blob/86b9a4a457df22c48b80a4c423b3e421dd309292/contracts/settling.v1.md)
distinguish draft, ratified, and locked documents. The
[Direction experience](https://github.com/microsoft/amplifier-converge-direction/blob/1ea1568d7afd59e7391ac98b4f15b9cc3af1c981/contracts/experience.v1.md)
preserves exact source and proposed revisions.

Unified already adopted a scoped use of this method in its
[client direction](../clients/direction.md#historical-references-and-refreshed-method).
The portable bundle and Method use different historical document formats and
state vocabularies. This packet follows Method's short promise format and
Unified's existing ownership. It does not claim full bundle-kit conformance or
import its hooks, line ceilings, session topology, or formal ledger machinery.
