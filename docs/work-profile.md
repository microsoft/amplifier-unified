# Work profile in Unified

The [Amplifier Work bundle](https://github.com/bkrabach/amplifier-bundle-work)
owns portable composition and operating instructions. This application owns the
integration, preview launcher, and real-provider/browser acceptance tools.
Both repositories are private during development; use normal GitHub access.

Use Unified v0.11.11 or later for the voice-correction, recovered-history,
provider-restoration, and saved-canvas fixes. The integration environment below
installs Unified from this checkout, with the app's normal packaged worker.

## Opt in

Unified v0.13.1 includes **Work** in the standalone bundle picker by default,
alongside Anchors. Select it using **Conversation bundle**, or set it as an app,
workspace, or shared default. Existing registrations and source overrides take
precedence over the built-in branch-tracking source. The private repository requires
GitHub access when first loaded; listing the choices does not download it.

In Unified v0.12.0 or later, use **Conversation bundle** beside the model to choose
a registered root, preview an idle conversation switch, or fork with another root.
The new-conversation options also accept a bundle source:

```text
git+https://github.com/bkrabach/amplifier-bundle-work@main#subdirectory=bundle.md
```

Keep `@main` for branch-tracking updates; record the resolved commit in validation
evidence. You can also register the source as a **standalone bundle** named `work` in Settings →
Capabilities → Add capabilities. Registration adds a choice; it does not change
the default bundle or existing conversations. The bundle keeps configured
providers and model choices. See its README for composition with an existing
bundle rather than using the small standalone root.

## Run acceptance

From this repository root:

```sh
uv sync --project scripts/work_profile --locked --group dev --group browser
uv run --project scripts/work_profile --locked pytest -q scripts/work_profile/tests
uv run --project scripts/work_profile --locked --group browser python -m playwright install chromium
uv run --project scripts/work_profile --locked --group browser python scripts/work_profile/browser_acceptance.py \
  --allow-live --provider <configured-instance-id> --packaged-worker \
  --output /absolute/private/new-run-directory
```

The runner uses a branch-tracking Work bundle source. `--bundle` overrides it with another
reviewed Git URI or an absolute local bundle path. It resolves the bundle through
Foundation, without assuming sibling repositories or a particular working directory.

Runs copy only the selected configured provider into a new private environment,
preserve model/effort, bound output to 8,192 tokens, and use synthetic files and
history. They make paid calls. Keep output outside repositories. Existing app
instances and real session histories are not mounted.

The browser interaction uses two clients, a pending child, a side question and
correction, streaming, offline completion, reconnection, and independent drafts.
Use `--scenario compaction` for input during visible compaction. On macOS,
`--voice-audio --voice-phrasing forwarded` sends synthetic speech through the real
audio connection. This does not record or validate the physical microphone.
Automation uses an origin-scoped control token; physical audio, barge-in, and
PAM login require separate acceptance.

For HTTP/SSE acceptance without a browser:

```sh
uv run --project scripts/work_profile --locked python scripts/work_profile/acceptance.py \
  --allow-live --provider <configured-instance-id> --scenario interaction \
  --output /absolute/private/new-api-run
```

Other scenarios are `compaction` and `cancellation`. `--input-mode voice-backend`
exercises the voice-to-work adapter without audio. `--profile baseline` changes
only the context policy; it is not a complete old-versus-new harness benchmark.

## Optional isolated preview

```sh
uv run --project scripts/work_profile --locked python scripts/work_profile/preview.py init \
  --directory /absolute/private/new-preview --provider <configured-instance-id> --port 8956
uv run --project scripts/work_profile --locked python scripts/work_profile/preview.py serve \
  --directory /absolute/private/new-preview
```

Use `--also-provider` to include another configured provider. The normal
authentication and packaged worker are retained; `serve` preserves saved data and
`init` refuses to overwrite a directory. This preview is optional and is not
required to use the Work bundle in an ordinary Unified installation.

See [historical acceptance evidence](work-profile-validation.md) for earlier
fixed-revision results and known limitations. Passing integration checks does not
establish general model quality or parity with ChatGPT Work.

## Published bundle validation

On September 20, 2026, the browser interaction scenario passed all 11 checks
using bundle revision `e87f692a34c43debe445b644dfc74e695d00c9d5`, Unified
`75b083f5dab641b08fc30da622f91b6b2ee6f57f`, the normal packaged worker, and the
configured Terra provider. This covered side answers while a child was pending,
retained corrections, one tool execution, verified child output, public streaming
to both clients, offline completion, draft isolation, reload, and no browser
errors or visible alerts. The completion screenshot was also inspected. This
migration run did not repeat the earlier compaction or audio scenarios.


The optional `anchors-work` preset lives at `presets/anchors-work.md` in the same
bundle repository. It retains Anchors capabilities while composing Work behavior
last. Register that source as a standalone alias too. See
[scoped bundle defaults and switching](SHARED-CONFIGURATION.md#root-bundle-defaults-in-unified)
for app, workspace, and shared defaults and history-preserving root transitions.

## Default and display names

Work is Unified’s default for new installations and when no bundle choice is saved. Existing app, workspace, and shared choices remain in effect; existing conversations retain their own bundle. Use the app or workspace bundle default control to change that choice.

Bundle pickers use optional `bundle.display_name` metadata from the selected local or cached manifest (or Foundation registry), then fall back to the existing built-in label or bundle ID. Labels are sorted alphabetically; stored aliases, namespaces, and source URIs stay unchanged. Labels from a different registered source are ignored. Opening a picker does not fetch or load remote bundles.
