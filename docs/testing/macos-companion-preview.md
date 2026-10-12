# Initial macOS companion preview

Implementation: draft PR #567, based on Unified 0.20.111. This preview connects to
an existing candidate host; it does not package or modify the host installation.

## Verified

- 28 host tests passed: `tests/test_companion.py` and `tests/test_live_clients.py`.
  Observation is authenticated, bounded, does not hydrate histories or change
  browser selection, and excludes private conversation contents.
- Seven Swift tests passed on macOS: endpoint validation, notification baselines,
  host restart identity, notification filtering, durable reply identity, corrupt
  journal rejection, exact reply target/headers, and changed-host rejection.
- Universal Apple Silicon/Intel app built, ad-hoc signature verified, and all
  three sprite atlases included in the ZIP.
- Browser navigation parser test and production frontend build passed.
- Real synthetic-host browser check passed: notification link selects its chat,
  removes the query, preserves selection through reload, and sends no chat turn.

The Mac package and seven-test result are from commit `e68187a1`,
[macOS CI run 38204602783](https://github.com/microsoft/amplifier-unified/actions/runs/38204602783).
Subsequent browser acceptance fixture and documentation changes do not change the
app binary. ZIP SHA-256:
`6563fffce10220b3e06d01b8fc314e795b3c0143601572f688fbf9851683a2f0`.

Host and browser checks ran on spark-2 inside the owned synthetic DTU
`unified-macos-companion`. No shared application instance or real account state
was used. The browser check used Chromium with Node 22, because the DTU's system
Node 18 is below the installed Playwright requirement. Initial browser checks
needed selector corrections for the duplicated desktop/mobile navigation rows.

## Reproduce browser acceptance

Inside an isolated DTU with the project environment, built frontend, Node 22,
and Playwright Chromium installed:

```sh
cd frontend
DTU_CHROMIUM_SINGLE_PROCESS=1 node tests/companion-browser.mjs
```

The fixture starts its own temporary host and browser context and cleans them up
at exit. All provider configuration and authentication are synthetic.

## Still requires acceptance

Compilation and core tests do not prove native notification permission, replying
from a notification, cold-launch delivery, visual rendering, Keychain prompts,
sleep/wake, multiple displays, accessibility, or idle resource consumption on a
user's Mac. Those need device acceptance. The developer build uses a manually
supplied full-control host token; scoped pairing and revocation are required for
consumer distribution. Public Developer ID signing, notarization, updates, and
uninstall UX are also unfinished.

See [the macOS client README](../../clients/macos/README.md) for build and connection
instructions, reply recovery behavior, and the remaining milestones.
