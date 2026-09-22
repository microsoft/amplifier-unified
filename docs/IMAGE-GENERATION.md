# Image generation in ordinary Work conversations

Image generation is an optional paid tool. It uses the chosen image account/model
independently of the conversation's selected chat provider/model. The application
owns setup, file policy and saved outputs; the generic image tool owns requests and
files; the provider owns its Images API backend.

## Enable through existing controls

After the reviewed module/provider/Work changes are published:

1. In **Settings → Model providers**, choose the existing **OpenAI API** connection
   whose ordinary API account should pay for image calls. Keep its instance name,
   chat model, credential source and other settings. Under **Show advanced
   configuration**, add this member to its existing JSON object:

   ```json
   "image_generation": {
     "enabled": true,
     "id": "images",
     "model": "YOUR_CHOSEN_IMAGE_MODEL"
   }
   ```

   Save for the intended scope. This is nested provider configuration supported
   by the existing JSON editor and `providers.save`; it does not require a new
   provider instance or select that provider for chat. Configure exactly one
   backend with ID `images`. A ChatGPT sign-in connection does not grant ordinary
   Images API access. Use an existing ordinary OpenAI API connection or create one
   through the same provider controls, keeping the chat selection unchanged.

2. In **Settings → Capabilities → Add capabilities**, add this source with role
   **behavior**, then enable it:

   ```text
   git+https://github.com/microsoft/amplifier-bundle-work@main#subdirectory=behaviors/work-images.yaml
   ```

   This explicitly enables requested paid image calls and adds the tool as an app
   behavior overlay. Leave the conversation root as **Work**. The same agent path
   is `bundles.add` with this URI and `role: behavior`; read its schema first.
   The existing `providers.save` action accepts the same nested configuration.

3. Start a fresh ordinary **Work** conversation, keeping the desired chat model.
   Ask it to inspect `image_generate` capabilities. Ready means the tool/backend
   are configured, not that the account's image entitlement was independently
   checked. The first successful generation qualifies actual API access.

If the tool is absent, the optional behavior is not active in that runtime. If
capabilities report `selected_image_backend_not_mounted`, enable the chosen
provider's image backend and start a fresh conversation. A missing model is
reported as `image_model`. Provider listing, a successful chat request or a vision
model does not establish image-generation access. Existing running sessions keep
their mounted capabilities until remounted through normal host controls.

No production settings are changed by the implementation or acceptance scripts.
The Work image skill remains available with other compatible image tools too.
Before the new `microsoft/amplifier-module-tool-image` repository exists, use an
explicit local source override in an isolated environment; the branch-tracking
remote will not resolve yet.

## Generation, edit and delivery

The tool returns a workspace PNG, SHA-256, and `receiptPath`. Use the shared
`outputs.attachImage` action to save the exact completed receipt and image. For
an edit, attach the original first and supply its saved `parentId`; the target hash
in the receipt must match that exact parent. Generated files never replace inputs.
The standard outputs library exposes the saved versions and downloads. Call
`outputs.image` directly through `app_control` to send their exact saved PNG pixels
to a vision-capable model. Receipt text and browser display are separate evidence.

The host passes effective filesystem write restrictions to the image tool,
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
It verifies that the configured chat instance/model are preserved while the
independent image backend is ready. Source overrides exist only in that isolated
acceptance directory; no generation is attempted with the synthetic credential.
