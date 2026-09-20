# Message delivery and editing

The browser clears the composer and shows a user bubble immediately. The draft being sent is captured independently from subsequent typing. Request IDs identify delivery across disconnects and browser reloads; they do not depend on the current presentation client ID.

A server-side bubble can exist before the worker accepts input. `conversation.send` receipts and messages therefore distinguish `sending`, `accepted`, and `unknown`. Duplicate requests return the current recorded outcome without executing again. A lost reply shows **Check delivery**, using exactly the original ID and payload. A definite rejection shows **Retry**, which starts a new admission attempt. An unsent message can be edited before retry. Reload never resubmits automatically. A browser tab keeps its pending payloads in session storage; this is not a cross-device drafts store.

The new browser clears its scoped draft before submitting and sends `preserveDraft: true`, so a late acknowledgement cannot clear a newer draft containing the same text. Older clients keep their existing draft behavior. Message delivery and view changes have separate queues so a pending admission does not block typing or navigation.

`message.edit` accepts `mode: current | fork`. The UI defaults to the current conversation, including the last user message. An explicit checkbox creates a new conversation instead. Omitted mode retains the legacy fork behavior for existing callers.

Current-conversation edits require idle execution and exclusive Foundation ownership. The worker checks pending inputs, interactions and delegated work, prepares a reliable transcript boundary, records revision evidence, replaces the active context and submits the edited input once. The original event log and pre-edit context evidence remain available. External tool effects are never undone or replayed. Earlier message identities and execution records remain in the active conversation. Older compacted history without a reliable boundary returns an error and leaves the original intact.

Prepared revision evidence is private and durable. After interruption, recovery classifies the saved transcript; it never overwrites a newer owner's changes or automatically retries the edited input. A committed edit with an unconfirmed response is shown as interrupted for review.

Validation covers delayed admission and identical newer drafts, duplicate delivery across clients and restart, uncertain results without replay, failed local edits, current/fork selection, preserved earlier tool context, rollback and interrupted edits. `RUN_HISTORY_EDIT_WORKER=1 pytest tests/test_history_edit_worker.py` additionally runs the actual worker with a local fixture provider through edit, parking and restart. It uses isolated session files and does not contact a model provider.
