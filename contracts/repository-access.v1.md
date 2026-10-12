# Repository access contract v1

Status: implementation requirement for Unified and extracted host/client components.

## Public and authenticated access

Public repository reads MUST work without GitHub CLI, account sign-in, organization
SSO authorization, or a managed device. A signed-in account rejected by organization
policy MUST NOT make otherwise-public content inaccessible.

- Public HTTP metadata and release downloads use anonymous HTTPS first. Existing
  authentication may satisfy private reads or API rate limits in automatic mode.
- Git reads and package preparation may use configured authentication first. A
  rejected GitHub authentication attempt gets at most one anonymous retry, scoped
  to that download. Preserve the original URL, revision, integrity checks and
  destination; never substitute a branch or another repository.
- Anonymous retry disables inherited credentials, helpers, authorization headers,
  cookies and personal URL rewrites in child processes. It does not edit account,
  Git or service configuration. Propagate policy to nested installers.
- Explicit `--anonymous` / `AMPLIFIER_GITHUB_ACCESS=anonymous` forbids authenticated
  fallbacks. Automatic fallback must not disable private-source access globally.
- Private sources still require valid authorization. A failed anonymous retry is
  an actionable failure, never success or evidence that a repository is public.
- Retry only reads/downloads or isolated package preparation. Never retry pushes,
  issue creation, comments, feedback submission or other remote mutations under
  this policy. Preserve their existing authorization and idempotency contracts.

## Reliability and diagnostics

Requests remain bounded in time and output size. Cancellation terminates children
and does not trigger retry. TLS verification and proxy/certificate configuration
remain enabled. Public API rate limits use documented non-API release links where
an equivalent authoritative lookup exists; never scrape or guess release identity.
Exact commit resolution and artifact checksum/qualification remain mandatory.

Diagnostic receipts may record a boolean anonymous retry and sanitized failure
class. Never store tokens, response bodies, credential-bearing URLs or raw error
text in exported diagnostics. Failed downloads do not activate partial updates;
repair retains its backup and rollback semantics.

## Coverage and ownership

The host owns this policy across repair, bootstrap installation, app and component
update checks/downloads, runtime dependency preparation, bundle discovery, Smart
Tool installation, workspace clone, terminal release distribution and read-only
feedback metadata. Clients invoke these host paths instead of implementing their
own transport or assuming `gh auth status` proves repository access. Local Git
inspection does not require network authentication. Account-identity queries and
remote writes remain authenticated operations.

Any new Git/GitHub integration must classify read versus write and reuse the shared
transport policy, or document and test an equivalent bounded implementation.
External dependency loaders must propagate the policy and report access failures
without exposing credentials; compatibility probes preserve that classification
through wrapped exceptions.

Acceptance covers no CLI/token, restricted-token SAML/403 with anonymous success,
private authenticated success, both attempts failing, explicit anonymous mode,
nested preparation, unchanged credentials/configuration, API rate limiting,
cancellation and deadlines, output bounds, and no anonymous retry of mutations.
