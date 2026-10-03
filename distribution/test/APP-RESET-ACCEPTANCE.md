# Assembled app-local reset acceptance

The assembled distribution registers three reset owners: native app defaults,
notifications, and the independent update supervisor. Reset changes only the
explicit reviewed parts. Shared settings, shared credentials, conversation history,
host policy, and unselected app state are preserved.

`app-reset.integration.test.mjs` uses the installed public supervisor and host
control transports with authentication. The host supplies its configured recovery
owner census; the caller's fence is never accepted as proof. The test verifies all
20 real owners remain held throughout each supervisor reset operation, resets and
restores update preferences through public recovery, and reopens the distribution
to inspect the original receipt without repeating work. A counted loopback ingress
tracks the real client connection. All state is new and test-owned; native workers
are configured with an impossible executable and no inference is requested.

```sh
PYTHONDONTWRITEBYTECODE=1 \
APP_RESET_AMPLIFIER_PYTHON=/owned/native/bin/python \
UNIFIED_OWNERS_PYTHON=/owned/owners/bin/python \
UNIFIED_CATALOG_PYTHON=/owned/catalog/bin/python \
APP_RESET_ACCEPTANCE_RECEIPT=/owned/evidence/app-reset.json \
node --test test/app-reset.integration.test.mjs
```

For archive validation, install the distribution tarball in a new consumer and
copy this test and `counted-ingress-fixture.mjs` under that installed package's
`test/` directory. Set `UNIFIED_DISTRIBUTION_ENTRY` to its installed `src/index.js`.
This makes both the application and its dependencies resolve from the archive.
The Python packages remain separate qualified artifacts; do not copy virtual
environments between platforms.

A native metadata reader can briefly hold the native home gate while reset's
read-only preflight requests exclusive access. Only the native typed
`native-maintenance-busy` RPC error with `executed:false` and `replayed:false`,
before any owner command has been reserved, is classified as a safe refusal.
Transport loss, other errors, and any previously reserved effects remain unknown
for inspection. Old unknown jobs are not rewritten or retried.

This fixture validates local public package composition. It does not establish
Linux service handoff, browser rendering, account access, or published deployment.
