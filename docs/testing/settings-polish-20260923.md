# Settings polish acceptance

This follow-up starts from 0.20.16. The previous approved Settings release is already published.

## User journeys

- AI connections: connection actions have separate spacing; GitHub CLI is the default only when the host returns a token. Environment credentials remain an explicit choice alongside a private key for another account.
- Smart Tools: Installed is first; an empty installation offers the catalog; users can add a GitHub URL directly. Catalog selection, review, and installation/results occupy the same space as exclusive steps. Failed packages can be retried without reinstalling successful ones.
- Voice: one model; named voices with descriptions before selection; sample audio using the selected model and voice. Environment or separate private OpenAI key. Preview has no microphone, conversation history, delegation backend, or tools; it blocks conflicting calls/updates and has bounded duration. Voices without official tone descriptions are identified without inventing characteristics.
- Appearance: choosing a built-in appearance saves it immediately. Advanced CSS preview/apply remains available. Reload preserves the choice.
- Updates: automatic installation defaults on when unset, preserving explicit opt-outs. Active stage and disabled-action reason are visible above collapsed details; errors stay at the top. Read notices are acknowledged only when their contents are visible in a focused tab. Owner-managed editable previews cannot replace themselves with a normal app release.

## Evidence

- Focused Python suite: 275 passed, including voice lifecycle/credentials, setup, updates, shared settings, and agent actions.
- Frontend unit suite: 328 passed.
- `node frontend/tests/settings-refinements-browser.mjs`: provider removal, signed-in state, root navigation, credential choices, model/voice selection, notification saving, badge trail; 38 screenshots; no browser errors.
- `node frontend/tests/settings-polish-browser.mjs`: 323 screenshots covering all Settings destinations in Graphite at 390/1280 px and light/dark; basic destinations in all four appearances; empty/populated tools, installation failure/retry, voice sample, visibility acknowledgment, busy/error updates, appearance reload. Synthetic provider/tool results are used to exercise failures without changing an account.
- A real provider sample and deployment checks are separate from fixture evidence. Fixture audio validates playback wiring, not provider availability or physical audio quality.

## Preview environment

Environment-key detection reports what the running host process can access. A service that explicitly strips credentials will not expose the user's login-shell keys. The isolated Spark-2 preview unit is adjusted to inherit existing provider variables from its user service manager; no values are copied into frontend state or logs. This operational correction is scoped to that preview.
