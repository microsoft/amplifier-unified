# Provider preflight and endpoint constructors

This change partially addresses [issue #182](https://github.com/microsoft/amplifier-unified/issues/182).
It fixes the host discarding an explicitly saved endpoint during provider metadata
construction. It does not implement isolation of arbitrary unavailable providers.

## Current behavior

- Schema discovery gives providers empty `config` and no saved API/token fields.
  A public constructor declaring `base_url` receives that instance's explicitly
  configured endpoint, expanded using the existing environment rules. Endpoint
  bytes are not normalized, and no endpoint is invented or borrowed from another
  account. Schema inspection does not call model listing or inference.
- Setup's schema action uses only the matching saved module and account's endpoint.
  A new provider without a saved endpoint can still report missing configuration.
- Model-list/test probes pass the materialized endpoint to the same constructor
  parameter. These actions continue to perform their requested model-list call.
- Bundle preflight gathers schema/configuration failures for root and nested
  instances before updating either the bundle or its prepared mount plan. Failure
  preserves every provider, priority, role, and account choice and prevents mount.
- Allowlisted diagnostics identify the module and optional instance, with distinct
  schema/configuration reasons. Raw exceptions and configuration values are not
  included. Feedback exports reason counts, not account identities.

## Remaining isolation contract

A missing required secret on an enabled, otherwise unselected provider still stops
bundle preparation. Removing that row would let priority or model-role resolution
select a different account silently. This PR deliberately preserves that failure.

Full isolation needs a supported per-instance preparation/mount outcome that keeps
the original module, account, priority, and model identity available to selectors
while making the unavailable instance non-executable. Foundation's
`PreparedBundle.create_session` currently exposes no pre-initialize instance
factory/loader hook. Core's loader caches by module ID and refuses alternate source
paths for an already loaded module, so per-account source swapping is unsuitable.

Routing must also distinguish an unavailable configured provider from an absent or
nonmatching candidate, and propagate a typed failure when it is required instead
of treating model-list failure as no match. This contract must cover both the
`model_role_resolver` capability and routing's separate session-start/resume agent
resolution pass. Its current glob resolver catches `Exception` and may continue
to another candidate; a host placeholder alone cannot guarantee no fallback.

The smallest upstream work is to agree that per-instance outcome and explicit
fallback policy, expose a public installation seam, and test explicit/default,
named-account, role/glob, child, and resume selection before host adoption.

## Validation boundary

Credential-free synthetic constructors reproduce the reported endpoint failure
before this change and prepare successfully afterward. Tests cover endpoint
identity, missing endpoint/secret, nested atomicity, cancellation, sanitization,
setup account binding, and the actual isolated probe entry point. No live provider
request, Spark session, provider installation, or production adoption is used.
