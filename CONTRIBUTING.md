# Contributing

Use the [source and ownership map](docs/source-context.md) to select relevant
authored code, contracts and tests, and distinguish generated output and retained
evidence. The path selector does not change packaging or release validation.

## Development setup

A raw Git checkout is a development source tree, distinct from a published source
archive. Use Python 3.13, uv and Node.js 22 with npm in an owned test environment,
following the workspace's DTU rules. Prepare dependencies once, then build the
client after relevant client or build-input changes:

```sh
uv sync --locked --group dev --group artifacts
python scripts/web_client_build.py prepare
npm ci --prefix .build/web-client
npm ci --prefix tests/web
python scripts/web_client_build.py build
```

Author the client in [amplifier-client-web](https://github.com/microsoft/amplifier-client-web).
[Component ownership and assembly](components/README.md) explains the selected
exact public source selection and disposable prepared source. `npm test --prefix .build/web-client`
runs client units; `npm test --prefix tests/web` runs Unified's integration/producer
units. Built-asset checks use `npm run test:artifact-assets --prefix .build/web-client`.
See [host browser checks](tests/web/README.md) for affected real-server journeys.

A running Amplifier Unified gives environment variables to the shells that it
starts; a chat's shell tool is such a shell. An installed one can keep its data in
folders below your home folder. When pytest loads `tests/conftest.py`, that file
does three things for the pytest process:

- It removes the environment variables in its two lists. The first list holds
  variables that such a shell inherits from the service. The second list holds the
  product's default variables for provider credentials (`setup.PROVIDER_ENV`).
- It sets four of the product's folder variables (`AMPLIFIER_WEB_HOME`,
  `AMPLIFIER_HOME`, `AMPLIFIER_SESSION_STATE_HOME`, `AMPLIFIER_TERMINAL_HOME`) to
  folders of the test run.
- Where Python has the `pwd` module, a lookup of your own account by ID
  (`pwd.getpwuid`) gives a home folder of the test run. The account database does
  not change.

It does nothing else:

- It does not change `HOME` (`USERPROFILE` on Windows). The product still reads a
  file or a folder that it finds through that variable and not through one of these
  four, for example a ChatGPT sign-in file below `~/.amplifier`. Tools such as Git
  and uv keep their own files below your home folder.
- It removes no variable outside its two lists, also not one that a connection
  names for its credential.
- A program that a test starts gets your real home folder from the account database.
- The first list comes from a shell of an installation on Linux. An installation on
  macOS or Windows sets more variables for its service; the file does not remove them.
- The file does not act in a pytest run with `--noconftest`, or when you start
  product code yourself.

These are development setup commands, not a per-release checklist. For releases,
use the [Fast Release Guide](docs/validation/release-qualification.md): reuse the
prepared environment and run `release.py prepare --build` once instead of adding
another manual build. Refresh dependencies only when missing or stale.
The canonical frontend
build writes `amplifier_web/static`; keep that generated output committed for now.
Do not edit minified assets. Follow the [release preparation and asset recovery
guidance](docs/validation/release-qualification.md#preparing-a-version-change) when
moving an exact build from its owned environment back to the source checkout.

Published wheels **and source archives** already contain this complete built UI,
including vendor assets and licenses. Building a wheel from a released source
archive uses the declared Python build backend, without Node, npm or a frontend
download. It still requires the declared Python build dependencies; installation
resolves the normal application/runtime dependencies. Release packaging verifies
the same static file set and bytes in both formats before publishing.

## Contribution guidance

For behavior or architecture proposals, use [vision and contract guidance](docs/vision-and-contracts.md).
Keep one owner per promise, distinguish proposed behavior from qualified behavior,
and connect changed promises to focused checks under the existing release policy.

Keep repository content useful without exposing a contributor's environment.
This applies to code, fixtures, documentation, screenshots, logs, issues and
pull-request descriptions.

- Use synthetic people, host labels, paths and data in examples and tests.
  Keep private machine names, people's real names, account home directories,
  private service addresses and credentials out of repository content.
- GitHub aliases, public repository links and required license/copyright
  attribution may remain. Never remove third-party attribution when sanitizing.
- Keep exact private resource mappings, access details and cleanup commands in
  the owner's private operational record outside Git. Public evidence should
  retain source revisions, platform classes, results and limitations.
- Inspect screenshots and diagnostics before publishing. Prefer a synthetic
  reproduction; redact private identifiers without changing the result claimed.
- Check tracked filenames as well as file contents before committing. A cleanup
  of the current tree does not remove information from published Git history;
  report that limitation instead of silently rewriting shared history.

For performance-sensitive changes, use [performance validation](docs/validation/performance.md)
at design and review. Describe the affected operation, scaling inputs, measured
budget and semantic checks in the PR; distinguish source tests from installed or
live evidence. Select checks for the changed risk under the
[release policy](docs/validation/release-qualification.md#qualification-policy).

For routine publication, follow the [Fast Release Guide](docs/validation/release-qualification.md).
Use the [platform and first-use checklist](docs/validation/release-acceptance.md)
when qualifying an affected platform or first-use change; it is not a mandatory
campaign for every release.

Keep native test dependencies in the development group. When a fixture package
and its dependency declare the same source, align their authored refs and retain
the exact qualified commits in `uv.lock`; do not resolve a test-only conflict with
a root `[tool.uv].override-dependencies` entry, which affects the whole resolver.
Check the shipped-source policy and affected real native fixtures after changing
these declarations. Runtime selections retain their separate source policy.

For changes to cross-surface artifact controls, routing or presentation actions,
update the affected [control inventory entry](docs/canvas.md#artifact-controls-and-reuse)
or explain why its contract is unchanged, and extend the linked focused test.
Reuse the existing card and viewer owners; do not add parallel controls merely
because the content has a different format.
