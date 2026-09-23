# Composer primary action

The composer uses one fixed-position primary button. Its accessible name, tooltip,
icon and public `data-action` change together:

| Composer state | Primary action |
| --- | --- |
| Text or attachments ready | Send message (Send a correction during a response) |
| Upload in progress | Send, disabled until the attachment is ready |
| Empty, voice connecting or connected | X: End voice call |
| Empty, voice ending | X: End voice call, disabled until cleanup finishes |
| Empty, response or workers active, no voice call | X: Stop response |
| Empty, message delivery pending, no active call/work | Send, disabled |
| Empty and idle | Start voice call |

Text and attachments take priority over both voice and response cancellation.
Sending during a call leaves voice connected. Ending voice leaves background
work running, after which the empty composer offers Stop if work is still active.
The existing voice status strip retains microphone and End call controls, including
while a draft makes Send the primary action. Whitespace alone is not a message.
Enter in an empty textarea does not start voice or invoke cancellation. The button
remains keyboard accessible, and existing history, ownership and upload guards apply.

All actions use the existing shared dispatch paths: `call.start`, `call.end`,
`conversation.send` and `conversation.stop`. No transport or runtime protocol changed.

## Verification

Verified with production-built assets and disposable application state:

- `npm test`: 283 tests passed, including the action priority and disabled states.
- `npm run test:composer-primary-action-browser`: connecting cancellation,
  connected voice, typed submission without ending voice, agent-updated drafts,
  attachments, response/worker cancellation, keyboard activation, microphone
  permission failure/recovery, and fixed button geometry at 1280, 390 and 320 pixels.
- `npm run test:new-chat-browser`: new-chat submission, attachments, corrections,
  Stop transitions, retained drafts and mobile layout.
- `npm run test:composer-readiness-browser`: retained draft and blocked Send while
  restored history loads, followed by one explicit submission.
- `npm run test:voice-visual-browser`: retained typed draft, UI/agent capture,
  screen grant cleanup and call-end behavior.
- `npm run build` and `git diff --check` passed.

Browser fixtures use synthetic WebRTC signaling, media and model execution.
These checks do not claim physical microphone/speaker or live-provider audio
quality. No production service was restarted or upgraded for this verification.
