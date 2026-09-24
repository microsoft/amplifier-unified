# Conversation Markdown exports

Chat details → Export chat lets a user preview the full conversation, a selected range, or everything from a chosen message onward. Range boundaries are chosen from messages loaded in the chat; load earlier messages before choosing an older boundary. Full exports read the complete saved public transcript, independent of paging.

The preview shows exact Markdown, message and reference counts, UTF-8 byte size, capture time and omissions. Minimal context omits the conversation identifier. No model-specific token estimate is claimed. Copy and download deliver the immutable reviewed snapshot, even after new messages arrive. Changing range or minimal mode discards the review until a new preview is generated. JSON export retains its existing behavior.

Only visible user and assistant text is included, including recorded voice exchanges. Hidden system/developer messages, private reasoning and raw tool payloads are excluded. Attachments and Canvas artifacts are references, not file bytes. Range exports omit artifacts that cannot be linked to a message in the range. Message text is preserved exactly, so users should review their own text before sharing it outside the app. An export does not publish feedback or upload a transcript.

Agents use the same actions:

- `session.export` with `format=markdown`, `destination=none`, optional `scope=all|from|range`, `fromMessageId`, `throughMessageId`, and `minimal`. Missing, ambiguous or reversed boundaries fail; the host never silently substitutes the full conversation.
- Read the returned `statePath` with `state.get`, in pages if necessary. The result includes immutable `snapshotId`, counts, byte size and omissions.
- `session.exportDeliver` with `snapshotId` and `destination=clipboard|download` sends the reviewed bytes to a connected browser showing the calling chat. An agent must select `clientId` when multiple eligible browsers exist. It cannot deliver another chat's snapshot. Download confirmation means the browser started the download, not that a file was saved successfully.

No task is rerun. Existing history and Canvas records are unchanged. Snapshots survive service restart and are available only through the authenticated host.
