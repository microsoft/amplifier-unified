# Image generation across Amplifier Unified bundles

The shared Image generation behavior exposes a tool and skill across ordinary
root bundles. It uses the chosen image account/model
independently of the conversation's selected chat provider/model. The application
owns setup, file policy and saved outputs; the generic image tool owns requests and
files; the provider owns its native image backend.

The portable [imagegen bundle](https://github.com/microsoft/amplifier-bundle-imagegen)
owns the module, library, skill and brief capability guidance. It does not change
the selected root, orchestrator, context manager or conversational provider.
Mounting it makes no image request and creates no account or credentials. Missing
backend configuration is reported by capabilities; it does not prevent ordinary
chat. Requested generation/editing still requires an explicitly enabled backend.

## Enable through existing controls

1. In **Settings → AI connections**, choose a saved connection and open its
   **Images** settings. Use **Automatic** to select the latest supported stable
   image model reported by that provider's catalog, or choose a specific model.
   Save the image settings for the intended connection. This leaves the chat
   provider/model and credential source unchanged; an explicit image opt-out is
   preserved. Advanced options remain available for named backends and overrides.

   OpenAI API and Google Gemini API connections provide native image backends.
   Gemini uses its image-output-capable generateContent models, including supported
   Nano Banana models. A model that can read images is not necessarily able to
   generate them. The provider catalog determines supported choices; checking it
   does not generate an image or prove the account can make a paid image request.

   Other connections can use an explicitly configured image backend without
   changing their chat model. A ChatGPT sign-in, GitHub Copilot subscription, or
   another chat connection does not by itself grant access to a different
   provider's image API. Unsupported connections show guidance rather than an
   image-model selector. Account access is confirmed by an actual requested image.

2. **Settings → Advanced → Configured bundles** shows **Image generation** as an enabled app behavior
   by default, including configurations with an older saved app-behavior list or
   an empty list. It applies to any ordinary root bundle without changing the
   order of existing behaviors. An explicit disable or removal is retained.
   Disable or remove it through the same controls used for other behaviors.
   To restore a removed capability, use **Add capabilities**, role **behavior**,
   with this source:

   ```text
   git+https://github.com/microsoft/amplifier-bundle-imagegen@main#subdirectory=behaviors/imagegen.yaml
   ```

   The behavior permits requested image calls through the separately configured
   backend. Keep the conversation's chosen root bundle. The same agent path
   is `bundles.add` with this URI and `role: behavior`; read its schema first.
   The existing `providers.save` action accepts the same nested configuration.

3. Start a fresh conversation, keeping the desired root and chat model.
   Ask it to inspect `image_generate` capabilities. Ready means the tool/backend
   are configured, not that the account's image entitlement was independently
   checked. The first successful generation qualifies actual API access.

If the tool is absent, the optional behavior is not active in that runtime. If
capabilities report `selected_image_backend_not_mounted`, enable the chosen
provider's image backend and start a fresh conversation. An unavailable or unsupported
model is reported by the selected provider. Provider listing, a successful chat
request or a vision model does not establish image-generation access. Existing running sessions keep
their mounted capabilities until remounted through normal host controls.

The existing Work image behavior/preset forwards to the same portable behavior.
Composing it in both a root and the host does not add another image tool. An
imagegen host behavior retains an already-declared image tool's source/configuration,
including paid-call opt-outs and named instances, while adding image skill discovery.
Other skill directories and explicit module settings remain effective. Saving an
explicit app-behavior list does not change this preservation rule. Other behavior
overlays keep their normal precedence.

The bundle and module sources track `main`. Normal source overrides and generation
qualification apply. Git access to the bundle repository is required to resolve it;
an inaccessible source is a setup failure, not proof of a missing image account.
Saved snapshots remain complete plans and are not injected with new behaviors.
Active worker generations keep their mounted capabilities until normal refresh.
No production settings are changed by the implementation or acceptance scripts.

## Generation, edit and delivery

The tool returns a workspace PNG, SHA-256, and `receiptPath`. Use the shared
`outputs.attachImage` action to save the exact completed receipt and image. Supply
the originating `messageId` to show the image inline in that turn; a later message
never becomes the origin automatically. Omit it to save in the library only.
The saved output and Canvas share one immutable image body, including when the
conversation is forked. Saving does not open Canvas or replace an unsent draft. For
an edit, attach the original first and supply its saved `parentId`; the target hash
in the receipt must match that exact parent. Generated files never replace inputs.
The standard outputs library exposes the saved versions and downloads. Call
`outputs.image` directly through `app_control` to send their exact saved PNG pixels
to a vision-capable model. Receipt text and browser display are separate evidence.

Inline generation placeholders reflect observed `image_generate` generate/edit
calls with a known request ID and originating turn. Delegated calls require a
recorded parent chain back to that turn. The `nano-banana` generate/edit path also
shows progress using its observed call identity; analysis calls do not.
Capabilities and status checks do not create placeholders. Request-linked results
appear after the work and responses for their originating request, before the
next request. Animation stops when execution stops, and completed
saved images replace the matching placeholders. No estimated progress or automatic
generation retry is inferred from these displays.

The host passes effective filesystem read and write restrictions to the image tool,
including root and child declaration denials. Image access stays inside the
execution workspace. Receipt metadata is producer-reported provenance; the host
independently verifies bytes, dimensions and hashes, not the provider account's
remote identity. No arbitrary URL download or publication is performed.

Interrupted image calls may still have cost or a server-side effect. Their durable
request IDs are never automatically replayed. Read `image_generate` status for the
same ID and reconcile an unknown outcome before deliberately requesting new work.
Provider-reported token usage is saved when available; this initial image tool is
not integrated with chat-token capacity enforcement or account quota reporting.

## Bounded live acceptance

Install the reviewed generic image tool and provider sources in this checkout's
isolated Python environment. Then run:

```sh
.venv/bin/python scripts/work_profile/image_generation_acceptance.py \
  --allow-live --provider EXISTING_OPENAI_INSTANCE \
  --image-model CHOSEN_IMAGE_MODEL --output /absolute/private/new-directory
```

This makes at most two low-quality 1024×1024 requests: a blue circle and a red edit.
It uses the explicitly selected ordinary API profile only, keeps credentials in
memory, and preserves a failed or unknown result without retry. It verifies the
real tool, API, shared saved-output actions, parent hashes and exact saved pixels.
It does not start a chat model, test natural skill selection or prove browser/UI
acceptance. Report these limits alongside the retained images and receipts.

For a fresh ordinary Work mount without any provider requests, run
`image_setup_acceptance.py` with `--phase prepare` and then `--phase mount` in
separate processes, using the same `--output` and reviewed `--work-source`,
`--tool-source`, and `--provider-source` directory arguments. It exercises the
existing provider-save and behavior-add paths with a synthetic credential,
prepares dependencies, mounts a new normal Work session and loads its image skill.
It verifies that the configured chat instance/model are preserved while a separate
image provider instance owns the ready backend. Source overrides exist only in that isolated
acceptance directory; no generation is attempted with the synthetic credential.
