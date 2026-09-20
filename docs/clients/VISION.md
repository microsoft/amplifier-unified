# Amplifier shared clients — Vision (DRAFT)

This is a proposed destination for Unified and its connected clients.
It does not describe the current release or replace a client's own direction.
Observable promises live in the [contracts](direction.md); implementation
evidence and missing work live in the linked assessment and plan.

## What Amplifier shared clients are

Amplifier is a continuing conversation with work happening on an identified host.
A person reaches that work through a browser, terminal, desktop or mobile client.
The interface fits the device; the conversation, its meaning and its progress
remain recognizable when the person changes interfaces.

Unified hosts execution and exposes the same public operations to clients and
agents. The web and TUI are the first two connected experiences. Future clients
join through those boundaries without needing to reproduce an existing frontend
or embed the execution runtime.

Several people’s windows and devices can observe authorized work concurrently.
Opening another view neither creates another execution nor takes another view
away from what its user is doing. Work continues when its viewers leave.

Foundation supplies reusable storage, configuration and ownership mechanisms.
Applications choose their roots, policies and presentation. Existing standalone
interfaces participate through shared storage and cooperative ownership; they
are distinct from clients attached to Unified's running session.

## Principles

### 1. Continuity is the product
The person recognizes the conversation, its workspace, its history and its next
available action. Moving between devices does not become an import/export task.
Moving execution to another machine is a separate, explicit capability.

### 2. Common meaning, native interaction
People learn the concepts once. A terminal uses its keyboard and scrollback well;
a browser uses its panels and rich views well. Neither imitates the other's
layout at the expense of being useful on its own device.

### 3. Independent attention, shared work
A person's selection, unfinished input and reading position belong to that view.
Accepted work belongs to the conversation. Clients follow only the information
they need, while the host continues unattended work and preserves its results.

### 4. Feedback makes the next action clear
People can tell whether input is pending, work is running, a connection is lost,
or a decision is needed. Feedback lives where the person acted and uses motion,
state and emphasis economically. It never claims completion from mere acceptance.

### 5. Configuration has a place and a time
People understand which host, workspace or conversation a change affects and
when it becomes effective. Another device can observe that change without losing
its user's unfinished settings edits. Device capabilities remain device-specific.

### 6. Authority survives changes of interface
An interface presents the host's actual permissions, ownership and capabilities.
A new display does not grant new execution authority. Public operations give
agents the same meaningful actions and outcomes available through client controls.

### 7. Clients can advance independently
A shared behavioral change has a clear home and named consumers. Teams can improve
rendering, accessibility and platform interaction independently while testing the
same journeys. Compatibility is checked against specific revisions and releases.

### 8. Evidence stays honest
Source inspection, simulated transport, a real terminal and a released device
each prove different things. Direction describes the intended experience;
the assessment says how much of it has actually been demonstrated.

## What this deliberately resists

- A second execution runtime for every connected window.
- A browser-shaped specification that makes the terminal a lesser client.
- Implicit replication of credentials, files or execution between hosts.
- Different authoritative names or histories for the same stored session.
- An API success response presented as finished work or an undone tool effect.
- A second copy of shared rules in every repository.
- Requiring all client teams to approve an internal implementation improvement.
- Treating a draft contract, a passing fixture or a merged PR as a shipped result.

## How you can tell it is working

- A person uses **2 browser tabs and 2 TUIs** across **2 conversations** and keeps
  every view's selection and unfinished draft throughout concurrent updates.
- Two people observing one authorized conversation see **1 accepted input** for
  a deliberately retried command, and **0 repeated tool effects** on reconnect.
- A terminal user leaves **1 conversation** working, reconnects through the web,
  and finds its latest accepted state without resubmitting the request.
- A person changes **1 workspace default** and can identify its scope and effective
  state in both clients without affecting a second, overriding workspace.
- A reviewer traces **every shared behavior change** to its contract clause,
  affected consumers and evidence; **0 unmeasured claims** are reported as passes.

## Changelog

| Date | Change | Basis |
| --- | --- | --- |
| 2026-09-20 | Initial draft. | Shared-client design discussion and the sources in the direction index. |
