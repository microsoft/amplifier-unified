# Compare two saved images

Discover `outputs.images` with `app_control` using `list_actions` and the prefix
`outputs.`. On hosts that offer it, call it directly with two saved output IDs
and their SHA-256 hashes:

```json
{
  "operation": "dispatch",
  "args": {
    "action": "outputs.images",
    "args": {
      "images": [
        {"id": "original-output-id", "sha256": "original-sha256"},
        {"id": "edited-output-id", "sha256": "edited-sha256"}
      ]
    }
  }
}
```

Both outputs must belong to the calling conversation. They must be different
RGB/RGBA PNGs, at most 4096 pixels per side and 8 MiB combined. A rejected pair
leaves the current image selection unchanged. UI/API callers supply the
conversation's `sessionId` through the same shared action.

The next supported model request receives both exact snapshots in the supplied
order. Its comparison status lists requested, delivered, omitted and replaced
image identities. Compare only when `comparisonReady` is true. The tool receipt
alone does not prove pixel delivery.

The pair replaces the previous image selection for the current input. It
remains eligible while the exact direct tool call and successful receipt remain
in context. A new input or restarted runtime needs another explicit inspection.
If either image becomes unavailable before transport, neither is delivered.
This operation does not generate, edit, publish or overwrite images.

`outputs.image` still inspects one image and replaces the previous selection.
Sequential single-image calls retain only the latest image. On an older host
without `outputs.images`, report that direct two-image comparison is unavailable.
Do not present descriptions from earlier requests as simultaneous pixel access.
