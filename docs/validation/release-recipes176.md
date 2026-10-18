# Release 0.20.176 recipe prerequisite correction

Publication run `39060984326` did not publish a release. Its runtime lane passed
125 checks and failed the real community-recipe child probe because
`tree_sitter_bash` was absent. Recipes checkout
`2e14948bee27a1ec52bef0940c8295eed86c3293` declares that dependency in
`modules/tool-recipes/pyproject.toml`; the fixture imported that source directly,
but the release environment had never installed the module's requirements.

The release and manual Core-wheel qualification workflows now add that exact
checked-out module to their runtime project before resolving, freezing and
installing the graph. They consume the module's own dependency declaration rather
than maintaining another parser-version list. The build-cache identity also binds
the Recipes checkout revision before selecting a cache. Existing before/after
runtime graph, source, native Core-wheel and publication receipt checks remain.

This corrects the qualification harness. Normal shipping activation already
installs the selected module and its declared dependencies through Foundation's
`ModuleActivator` or batch installer. At qualified Foundation
`8d26eceb9b7cafe6e93346f8f4294596bd45dcd1`, those owners invoke `uv pip install`
on the selected module with `--no-sources`, preserving its ordinary requirements
while ignoring development-only source overrides. No application behavior,
shipping dependency declaration, provider selection, version or client asset
changes here.

## Validation

All execution used the existing owned Linux ARM DTU and a private copy of the
qualified Core 2.0.2 runtime. The original runtime and its package identities were
verified unchanged.

- Unchanged negative control: the actual recipe-child test failed with the same
  missing `tree_sitter_bash` / `PARSER_PROFILE` error after the three recipe
  prerequisite distributions were absent from the private copy.
- Actual `uv add --frozen --editable` and lock resolution of the checked-out
  module succeeded. Installing that declaration added only the recipe module,
  `tree-sitter` and `tree-sitter-bash`; every existing distribution identity stayed
  unchanged.
- The exact 126-test release surface selection passed, zero skips, in 25.853 s.
  This includes the real recipe report, child history, inherited approval and
  refused subprocess-isolation assertions.
- Release qualification and automation contracts: 58 passed, zero skips, in
  0.724 s. New controls require declared module installation before lock/cache
  selection in both workflows and invalidate cache identity when Recipes changes.

The original publication failure, negative control, corrected JUnit, package
graphs and source hashes are retained under
`output/release176/recipe-prerequisite`. The local run used a private qualified
runtime clone; the hosted workflow separately resolves and qualifies its fresh
complete graph. No full browser rerun or physical-device acceptance is claimed.
