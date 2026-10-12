# Amplifier Tempo host and browser acceptance

Host implementation: draft PR #567, based on Unified 0.20.111. The native app now
lives in [bkrabach/amplifier-temp](https://github.com/bkrabach/amplifier-temp)
(private access required). This preview connects to
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

The original native package was verified at `e68187a1` in
[macOS CI run 38204602783](https://github.com/microsoft/amplifier-unified/actions/runs/38204602783).
The later Tempo 0.1.2 package (`67eedc011`) passed seven Swift tests, the native
focused-editor keyboard check, and the universal build in
[run 38236638243](https://github.com/microsoft/amplifier-unified/actions/runs/38236638243).
It was installed on the authorized acceptance Mac: synthetic-host connection and
128 × 160-point window were verified, and the user confirmed the native test
notification banner. Ad-hoc updates were observed to require Keychain reapproval.
Standalone builds and downloads now belong to the Tempo repository.

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

Remaining native acceptance includes notification replies to the correct chat,
cold-launch delivery, accessibility, sleep/wake, multiple displays, and measured
idle resource consumption. Test-banner visibility alone does not prove those
flows. The preview still uses a manually supplied full-control host token;
browser pairing and restricted, revocable device credentials remain unfinished.
Private ad-hoc downloads are authorized; Developer ID signing, notarization,
updates, and complete uninstall UX remain future work.

See [the macOS client README](../../clients/macos/README.md) for build and connection
instructions, reply recovery behavior, and the remaining milestones.
