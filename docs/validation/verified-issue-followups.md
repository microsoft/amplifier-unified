# Verified issue followups for 0.10.9

Reviewed all 18 open issues through September 19, 2026, against main `81f2182` (0.10.8). Findings distinguish current failures from older-version reports and product proposals.

| Issue | Change and evidence boundary |
| --- | --- |
| #32, #47 | Browser projections show recent app messages and compact execution nodes, with earlier pages and full text available on demand. Full agent reads, scoped reads, canonical history, approvals, and usage totals remain available. Native history browsing retains its existing pagination. A stalled initial connection offers a read-only retry after ten seconds. The original permanent hang was not reproduced. |
| #37 | Pin the upstream observation-provenance change and display tagged service observations as expandable activity. Full text and model-facing role/content are retained. Identical user quotations remain user messages. Existing unmarked rows are unchanged; native provider steering protocols were not changed or live-model tested. |
| #38 | Buffer the confirmed conversation-name field locally and save explicitly. Slow acknowledgements cannot discard characters. Main composer typing passed the original reproduction; the broader report remains open for a failing example. |
| #40 | Dismiss one error occurrence using its attention fingerprint; retain its record in reviewed Activity. A new occurrence reappears. |
| #41, #45 | Show pending-send feedback immediately, bind saved drafts to their conversations, and let selection proceed during a slow send acknowledgement. Late receipts cannot erase another conversation's draft or resurrect the submitted draft. This establishes behavior under controlled latency, not the cause of the reporter's original server delay. |
| #44, #54 | Freeze chooser order during interaction and show operational status. Workspace scope stays unchanged. |
| #49, #55 | Adopt and validate Marc Goodner's PR #51 theme contribution; new installs follow device appearance. Existing explicit choices are retained. MCP Apps receive explicit/system appearance changes without remounting. |
| #53 | Coalesce feedback draft writes and protect newer local typing from older acknowledgements. Shared-view isolation belongs to the separate multi-device task. Feedback is never automatically submitted by a state update. |
| #56 | Terminal job outcomes take precedence over stale progress for the same call, while preserving the child identity and genuinely active workers. |

Feedback now defaults to reproduction diagnostics: app and loaded-frontend version/build identity, browser and OS family, viewport and display preferences, connection/PWA state, pending-action counts, and selected conversation/library counts and statuses. The form shows build/device facts, explains additional state facts, and retains opt-out. Diagnostics are captured once with the durable submission identity. No transcript, workspace path, raw log, URL, provider configuration, or credential is collected automatically.

## Validation

- Full Python and frontend unit suites, plus upstream loop-live's 42 tests.
- Browser checks: delayed rename/send/navigation, drafts, repeat-error dismissal, chooser order, system theme, initial-connection retry, feedback diagnostics, heavy-detail paging, full-text retrieval, clipboard/edit/fork, voice timeline positioning, composer/attachments, native history, 206-chat library, and official MCP Apps SDK integration.
- Heavy fixture: two active chats with 400 tool nodes and 10 KB summaries each fell from approximately 8.23 MB to 305 KB per browser state response. Navigation responses remained approximately 305 KB.
- Combined fixture: 520 + 549 markdown-heavy messages plus 800 tool nodes produced approximately 855 KB; the selected chat initially mounted 60 messages. Browser checks loaded 120 messages and 200 nodes and retrieved complete text. Measurements are local fixture results, not production latency guarantees.
- Tool-event burst tests bound each published frame and preserve immediate approvals. Wheel/source verification includes the generated frontend and build manifest.

No live model delegation, reporter-specific device/theme, or production deployment was used as proof. #39 and #50 still need reproducing evidence. #42, #43, and #48 remain product proposals; this change does not accept a diff viewer, workspace redesign, or video feature.
