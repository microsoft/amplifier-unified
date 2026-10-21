# Work landing pages and sidebar design review

Help a person arrive, understand what needs their attention, and find familiar
work without first learning how Unified organizes execution.

The product owner accepted this direction on October 20. The [decision record](decisions/20261020.md)
links the reviewed proposal and records the requested corrections. The [landing-page extension](decisions/20261020-landing-pages.md) records the next accepted iteration. The design
remains open to refinement; agreement is separate from evidence that it works.

## Start here

1. Read the [Home and sidebar promises](../shell/work-discovery.v1.md).
2. Try the [review journeys](review.md#review-with-real-work) with familiar
   work and ordinary devices. Record confusion, pauses, and wrong turns.
3. Use the [feedback form](review.md#record-one-observation) to name the promise
   or design question involved and suggest a concrete alternative.
4. Return the edited document and agreed wording to the implementation agent.

Promise names and ordinary numbered lists are sufficient references. You do
not need to learn abbreviations or a special numbering vocabulary.

## Stakes to carry forward

| Stake | Why it matters |
| --- | --- |
| Support arriving and finding | Sometimes a person wants the next useful action; sometimes they already know the chat or project they need. Both routes deserve clear access. |
| Let work begin before organization | A one-off question is legitimate work. Choosing a project is not a prerequisite for asking for help. |
| Change the view, preserve the meaning | Focus, Brief, and Studio present the same work at different densities. Appearance supplies a starting preference; the person can choose another view. |
| Tell the truth about attention | An unread response is not an accepted deliverable. An idle conversation is not a completed project. Read/unread controls reflect the current state. |
| Remember the person's place | Landing pages restore their context. Sidebar organization is independent. Passing a Focus item does not dismiss it. |
| Keep access and representation consistent | Browse and Status use the same chat controls and display preferences. Search and New chat remain accessible from either scrolled surface. |
| Keep routine refresh quiet | Background updates preserve usable content and layout. Initial loading, failures, and user-initiated operations retain meaningful feedback. |
| Make scope predictable | Use Workspaces consistently for registered workspaces. Filtering cannot move work or quietly select another location. |
| Keep the experiment affordable | Reuse saved data and bounded discovery. This iteration adds no summary jobs, model calls, or session lifecycle. |

The [client vision](../clients/VISION.md), [shared client experience](../../contracts/client-experience.v1.md),
[workspace experience](../workspaces/contracts/experience.v1.md), and
[Home/sidebar contract](../shell/work-discovery.v1.md) own these commitments.
The [implementation notes](../shell/adaptive-work-views.md) retain concrete
choices and historical evidence. This summary adds no separate rules.

## What remains open to design

- The clearest names for Status and Ready for you.
- Card emphasis, wording, spacing, typography, and use of counts.
- The balance between one featured item and surrounding context.
- How to make the stable Layout switch and Display detail controls easy to distinguish.
- Responsive composition, with essential actions and accessibility preserved.

Exact columns, pixel measurements, and today's card arrangements are a baseline
for comparison. New logical projects, classifiers, review workflows, or additional
processing need a separate scope decision. Personal preference is useful design
input; it does not establish a measured improvement for other people.

## How the collaboration works

The designer brings observations and alternatives. The product owner and designer
settle the intended experience together; the owner records acceptance or delegates
that decision. The agent identifies the affected promise, implements accepted
changes, and returns evidence. It does not accept its own proposal.

For a broken promise, fix the gap within the authorized scope. For a change in
meaning, agree to the wording first. For presentation changes within the promises,
compare the same journeys and preserve a baseline to return to.

Record the document revision, promise name, accepted wording, decision-maker,
date, and reason. Converge calls the decisions **ratified**, **ratified with edits**,
**declined**, or **later**; ordinary language with the same meaning is sufficient.
A screenshot preference, saved draft, or agent summary does not silently approve
a different behavioral change. Keep decisions and implementation evidence apart.

## Method and evidence

The [review basis](review.md) separates historical synthetic checks from human
review and current defects. The [source record](sources.json) pins Converge,
Method, Direction, and the other related repositories studied for this work.

Unified uses Converge's short promises, exact-revision proposals, explicit human
decisions, and honest evidence. See [the existing method adoption](../clients/direction.md#historical-references-and-refreshed-method).
This is a documentation and collaboration practice; it adds no manager service,
model calls, runtime dependency, or automatic release gate.
