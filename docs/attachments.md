# Conversation attachments

Attach up to eight files per message through the file picker, paste, drop, or
the shared `attachment.add` action. Each file can be up to 32 MiB (33,554,432
bytes). The app preserves original bytes in private storage. Images enter the
conversation as native image content; documents and other large files remain
available to tools through their local paths.

The app's intake limit is distinct from a provider's limits on image dimensions,
encoded image sizes, and total requests. A successfully attached file does not
guarantee that every selected model will accept the image unchanged. The app does
not silently resize the original.

Uploads use base64 JSON, so the HTTP and agent worker transports allow its
expansion beyond the original file size. Attachment bytes stay out of shared UI
state and are loaded only when needed. Feedback submissions retain their
separate limits of eight files, 8 MiB per file, and 24 MiB total.
