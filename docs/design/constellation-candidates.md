# Candidate Constellation ideas from Unified

Use this note when a need first seen in Unified may also matter to another
agent or experience. Search for the user problem and add the new example to
the matching entry. Link the originating issue, PR or evidence and explain
what the existing interfaces let each product do. Keep implementation work
with its owner. [Constellation's collaboration guide](https://github.com/microsoft/amplifier-constellation/blob/main/docs/working-together.md#agree-where-responsibilities-meet)

An entry is a question for review. When examples show a shared need, bring the
specific choice, affected owners and an interoperability check to the Unified
product owner and the people who own the affected Constellation interfaces.
Record the decision here with a link to its owner. [Vision and contract guidance](../vision-and-contracts.md)

## Seeing where a model or provider changed in a conversation

**Open question.** In a long thread, someone reading an older answer wants to
know where a different model or connection began handling turns. Unified can
show the change at the affected turn. The picker can separately explain known
costs before the person makes the change. A switch can reuse prior context,
so evidence of the switch alone cannot establish that compaction took place.
[Unified model-change behavior](../../contracts/model-request-recovery.v1.md#a-deliberate-model-change-in-an-existing-conversation)

**What other products already use.** ACP's live notice has a title and optional
detail. Its published examples include Zed and the Claude Agent and Codex
adapters. A notice can disappear on reconnect. ACP configuration updates convey
current choices. When compaction actually occurs, ACP already defines a separate
event with an identity, a timeline position and history replay.
[Session notices](https://github.com/agentclientprotocol/agent-client-protocol/blob/main/docs/rfds/session-notices.mdx) · [Configuration options](https://github.com/agentclientprotocol/agent-client-protocol/blob/main/docs/protocol/v1/session-config-options.mdx) · [Compaction](https://github.com/agentclientprotocol/agent-client-protocol/blob/main/docs/rfds/session-compaction.mdx)

**What would move this forward.** Does another agent or conversation experience
need to recover the same setting change when opening history on another device?
Add a concrete example identifying who can attest to the change and who may
see it. A useful joint check would reopen the conversation in both experiences,
find the change beside the same turn exactly once, and verify that it triggers
no new input for participants. Layout and explanatory copy can suit each client.
[Conversation](https://github.com/microsoft/amplifier-constellation/blob/main/contracts/conversation.md) · [Interaction](https://github.com/microsoft/amplifier-constellation/blob/main/contracts/interaction.md)
