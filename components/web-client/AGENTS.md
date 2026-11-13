# Working on the web client

Read [README](README.md) for the current HTTP/SSE boundary, pinned upstream
vision/contracts and build/test scopes. The repository is private; preserve its
visibility. Unified selects a client revision and qualifies its packaged pairing;
source work here does not publish or adopt a new default automatically.

Author browser behavior in `src/`, standalone pages in `public/`, client checks in
`tests/`, and build/preparation tools in `scripts/`. `dist/`, `.build-inputs/`,
generated public branding/licenses and dependencies are disposable output. Do not
edit them or load them into routine source review. Change canonical catalogs,
SDK and branding with their Unified owner, then select a fresh exported lock here.

`npm test` owns client unit checks; `test:artifact-assets` checks built output.
Named browser journeys and the client registry exercise synthetic client peers;
`test:artifact` exercises an installed artifact. Unified owns actual host/browser,
producer, release and example checks in `tests/web` and assembly in `scripts/`.
Keep each assertion with its owner. Shared generic helper APIs retain one
implementation with injected test-framework facades, not copied implementations.
A fixture pass is not actual-server, provider, release or cross-version acceptance.

For interaction work, follow upstream
[CX8](https://github.com/microsoft/amplifier-unified/blob/main/contracts/client-experience.v1.md#the-promises)
and the [client vision](https://github.com/microsoft/amplifier-unified/blob/main/docs/clients/VISION.md):
design for ordinary information workers and consumers, with familiar labels,
accessible controls and low cognitive load. UX is like golf: fewer strokes win,
without losing authority, feedback or recovery.

## Temporary dual-repository maintenance

While private, maintain both this canonical source and Unified's committed
`components/web-client` snapshot and source record. Canonical-only or copy-only
changes are not release-ready. Author here, then mechanically refresh the whole
snapshot from the exact reviewed, accepted commit, or make a named integration
handoff with exact revisions and pending work. Do not hand-edit the copy or import
unreviewed moving-main extras. Follow the [README workflow](README.md#temporary-dual-repository-maintenance);
only an explicitly approved delivery transition updating both repositories'
guidance retires this obligation, not public availability alone.

The delivery copy and prepared source (`.build/web-client`) are consumers, not
alternative authoring locations; preserve unknown local experiments.
Keep one explicit paired server revision in artifact evidence. Do not add a
compatibility registry, runtime asset override or automatic fallback. Follow the
workspace's owned-DTU rules for builds/browser work and remove obsolete owned
copies when their review purpose ends; preserve useful finite evidence.
