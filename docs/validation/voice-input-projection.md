# Readable delegated voice input

Native voice requests include host instructions and a recent-speech reference.
Previously the shared history reader displayed that entire input as an ordinary
user message, alongside the public spoken transcript.

The reader now presents the current request when canonical user-input metadata
identifies a voice delegation and the complete wrapper parses. It retains the
original content-derived native identity and input ID. Provider history and
command receipts are not rewritten. Typed messages with ordinary input IDs,
malformed wrappers, and inputs without voice provenance retain their text.

Live records an explicit utterance-item binding when a delegation contains one
captured utterance and its saved text still matches. The history merger requires
that binding, the same call, and an unambiguous native input before reusing the
spoken bubble. Multiple utterances, changed speech, and historical records without
a binding retain separate readable entries. It does not guess which occurrence
of repeated speech owns a native edit boundary. Realtime's tool-authored request
has no such utterance binding and retains a separate readable delegation entry.

The same projection is used for passive history retrieval. Markdown export also
retains speech found only in the saved reference, labels uncertain historical
placement, and preserves the current request's message ID for scoped export.
The existing legacy export decoder remains available for pre-metadata history.
Recovery copies retain native input metadata; edit/fork validation checks both
the canonical content identity and the unique input identity.

Regression coverage includes real native-history loading and passive retrieval,
explicit and missing bindings, repeated speech, wrong-call and changed-speech
refusal, malformed wrappers, ordinary typed lookalikes, old raw display copies,
intervening replies, reload, exact fork boundaries, editing a recovery copy,
full/scoped Markdown export, and changed/duplicate canonical input IDs. A real
AppService with a synthetic runtime verifies the Live utterance binding while
keeping the call pinned to its conversation. No provider request is sent.

This is a transcript presentation and boundary repair. Physical audio,
interruption timing, spoken handoffs, and OS/device suspension still need their
separate acceptance evidence.

Local qualification on main `2359ac175e207d23430331d7ddb008ee0dbde757` in the
owned readiness DTU: 295 affected Python tests passed in 109.99 seconds. The
initial failing regression and subsequent runs are retained under
`output/voice-input-projection-20261009` in the owning workspace. The hosted
public-readiness workflow includes the new projection and voice regression files.
