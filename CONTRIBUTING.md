# Contributing

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

For release acceptance, use the [platform and first-use checklist](docs/validation/release-acceptance.md).

For changes to cross-surface artifact controls, routing or presentation actions,
update the affected [control inventory entry](docs/canvas.md#artifact-controls-and-reuse)
or explain why its contract is unchanged, and extend the linked focused test.
Reuse the existing card and viewer owners; do not add parallel controls merely
because the content has a different format.
