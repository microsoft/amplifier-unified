# Provider isolation #297 — qualification and ownership

The owner approved implementing this behavior on October 17. The governing
[contract](../provider-failure-isolation.md) covers the existing native
Core/Foundation runtime; Amplifier Agent is not a dependency or replacement.

## Dependency boundary

The prior installed native Core 2.0.1 wheel lacks
[before-load admission #117](https://github.com/microsoft/amplifier-core/pull/117)
and [transactional mount rollback #116](https://github.com/microsoft/amplifier-core/pull/116).
The same 18 upstream checks pass against a privately built 2.0.2 wheel and fail
13 cases against the unchanged 2.0.1 wheel. The version-only
[Core #119](https://github.com/microsoft/amplifier-core/pull/119) does not change
the native-wheel shipping policy.

Review of actual provider implementations also exposed a silent-mount defect:
Azure OpenAI and chat-completions may return without mounting when configuration
is incomplete. Core then treated an older default provider as the newly requested
named account. [Core #122](https://github.com/microsoft/amplifier-core/pull/122)
rejects no-op provider mounts and missing/unchanged named mount slots through the
existing rollback path, before readiness or account remapping. All ten new cases
fail against the earlier wheel. The rebuilt native wheel passes 40 provider and
session-initialization checks plus ten existing multi-instance checks. No Rust
code changed. Non-provider no-op mounts remain supported.

The privately tested Core candidate is `eea30d9eaf7f70df46afcd27eef71fc488d436ee`.
It merged as `9614052f7c9e493977dcba3f5c98ebd4d8591ec5`; canonical publication
is a separate gate owned by the release coordinator.
Foundation is installed from `85532ee4190c687dd7b582861a4c99c8323446ca`; its
public preparation callback and `PreparedBundle.create_session(before_initialize)`
already exist. The final routing package was independently installed from
`edf3603295c03fbe4cc06dc9d818fa84d9405d33`; availability is checked before exact
or glob resolution, with only explicitly declared candidate fallback.

Core 2.0.2 was published from that merge on October 17, after all 27 publication
jobs passed, including six platform builds and 18 installed-wheel Python cells.
Unified's host, worker and Work acceptance projects now require at least 2.0.2;
both committed locks resolve its published registry wheels. The final Foundation
lock is `8d26eceb9b7cafe6e93346f8f4294596bd45dcd1`, whose difference from the
privately qualified `85532ee` is documentation only. Unified retains its prohibition
on compiling Core during installation. Explicit older runtime overrides retain
the former strict preflight policy. Final integrated qualification uses the
published wheel, independently of the earlier private-wheel evidence below.

## Reviewed provider boundaries

All nine entries in `provider_sources.json` were reviewed at their mount,
constructor and metadata boundary. This is source review, not live authentication:

| Provider / reviewed revision | Relevant boundary |
| --- | --- |
| Anthropic `af32c269886df2cf3f23f838d3f81ae2638cd6e9` | Required API-key schema; metadata construction receives no saved secret; mount may decline missing credentials. |
| OpenAI `38fee172673d196ba54b5e99e434490b86290c5d` | API-key preflight and lazy client; exact instance identity and declared priority remain authoritative. |
| OpenAI ChatGPT `810fca8e381b0613ec87e7a2e4b1a405b59f5ab0` | Mount validates auth mode/paths without authenticating or refreshing tokens. Later account authentication remains a provider operation, not a claimed mount success. |
| Gemini `5eb610ab0894acd082cbeaeb527421ef70476d98` | Schema/config metadata and numeric priority parsing; model/inference calls are outside preflight. |
| GitHub Copilot `ed942e2c8a0c6d4ff6d878bc4ba480592f6c676a` | Process-wide SDK/token ownership remains restricted to one credential per conversation. Different credentials still require separate conversations; this change does not claim SDK account isolation. |
| Azure OpenAI `3a67ed10e70d185bd1061ec7b0d4aa9bd361b136` | Optional key versus managed identity; missing endpoint/auth can silently decline mount. Metadata is distinct from token-provider acquisition. |
| Chat completions `bd0de352072efca17ee332912d90a6b0633742bf` | Keyless endpoints supported; absent base URL silently declines mount. Endpoint and configured name semantics remain provider-owned. |
| Ollama `f86ae6c7bdf55745294cc1719b5df5dda4f84c2b` | Lazy client, optional cloud/proxy credential and host-derived model defaults; no eager network availability inference. |
| vLLM `985dcf464a2ef917a3e56bc5ac0d82d39a19668d` | Metadata constructor requires the explicitly configured endpoint; key may be optional. Preserve the earlier endpoint fix. |

The first five revisions are the actual installed worker packages used during this
investigation. The latter four are source reviews of their current canonical
repositories. Provider-specific implicit priority cannot be safely guessed after
constructor failure: unknown/invalid priority therefore refuses automatic choice,
while an explicitly selected healthy account remains usable. Stored rows are not
removed, reprioritized, or rewritten to hide failure.

## Executed checks and routine owner

All execution was in `dtu-unified-codex-dev-20261014`, with a private native wheel,
interpreter, source copy, synthetic state and no provider requests or host changes.

- Ten native checks in `tests/fixtures/provider_isolation_native.py` pass using
  the actual installed Core wheel and Foundation lifecycle. They exercise
  partial rollback, silent no-op mount, pre-loader secret refusal, preparation
  failure, non-provider fatal preparation, explicit/default/named/role/glob
  selection, same-module accounts, child repair/refusal and saved-child resume.
  Failed resume leaves transcript and metadata bytes unchanged and executes no input.
- The focused host, configuration, image-default, controls and integration-owner
  set passes 150 tests. Three cases were deselected by the focused filter: the existing
  `test_real_foundation_child_lifecycle_and_delegate_contract` and
  `test_real_community_recipe_completes_its_report_step` remain owned by the
  publication `release-runtime` lane (not claimed by this run); the new
  `test_real_foundation_prepare_keeps_failed_provider_but_not_failed_tool`
  was then run in the complete ten-case native fixture. Counts overlap and
  must not be summed as distinct tests.
- Old-Core strict secret handling and new per-instance refusal both pass. Invalid
  selection and reset-to-broken-default preserve the prior saved selection;
  public choices retain safe unavailable rows. Unknown priorities fail closed.

The **automatic native integration lane** explicitly selects the ten-test fixture
file and provisions the real routing package/import. Missing Core/Foundation/
routing prerequisites fail that lane, rather than yielding skipped success.
Ordinary unit collection does not pretend to cover the native fixture. The
original 87-skip ledger is unchanged; this is additive owned coverage.

Reproduce after preparing the canonical native integration interpreter:

```sh
python scripts/integration_checks.py run --lane native \
  --python /owned/native-integration/.venv/bin/python \
  --env-json /owned/native-integration/environment.json \
  --output /owned/evidence/native
# For the scoped ten-case proof in that same real interpreter:
/owned/native-integration/.venv/bin/python -m pytest -q \
  tests/fixtures/provider_isolation_native.py
```

Private detailed receipts and failed setup attempts are retained under
`output/afk-approved-20261017/provider`. Desired result: zero provider calls,
history rewrites or substituted accounts for denied execution; no readiness
callback for a failed provider mount; a healthy explicitly selected account can prepare despite unrelated
failure. Local synthetic timings are regression witnesses, not live-provider
latency targets or whole-release/platform qualification.
