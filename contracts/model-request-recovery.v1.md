# Recovery of an interrupted model request

The host's portable provider boundary may retry an unreturned model request in
its still-running execution. Browser reconnect restores views; it never sends a
new turn or triggers this policy. Other clients observe the same host-owned work.

- Retry only provider-declared retryable timeout/unavailable errors, at most two
  additional attempts with bounded backoff (2 and 5 seconds, respecting provider
  retry-after up to 30 seconds). Provider/SDK internal retry policies remain their
  own responsibility; these bounds count host attempts, not physical API calls.
- Retain the exact prepared request, selected connection/model, options, current
  generation and completed tool results. Never restart the orchestrator, repeat
  a tool or append the human message again. Changed requests or authority decline
  retry. User Stop cancels the current request or its backoff.
- Any yielded streaming event, exposed response/output hook, or tool invocation
  prevents generic retry. Stateful native transports retain their provider-owned
  reconciliation policy. They are never resubmitted through this wrapper.
- Authentication, invalid input, unknown errors, cancellation and exhausted retries
  keep the existing truthful failure/recovery path. Attempt diagnostics contain
  metadata only, never request or response bodies.

This is recovery of a surviving worker after transient transport loss (including
host suspension). It does not infer process death was sleep, automatically start
terminated workers, resume partial output, or replay requests from diagnostic
recordings. A terminated process or provider-owned native request needs a durable
checkpoint/receipt qualified by its execution owner before automatic continuation.
A provider may have performed and billed inference before a response was lost;
retry does not promise exactly-once remote inference or unchanged generated text.

Tests exercise retry exhaustion, Stop, changed authority, partial output,
provider opt-out, exact input retention, and root/child adapter installation with
synthetic providers. Physical sleep and paid inference require separate evidence.
