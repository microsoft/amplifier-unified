# File references in chat

Unified expands file references in new messages before sending their contents to
the model. Your original message remains visible in the chat.

- `@README.md` reads a file in the conversation's workspace.
- `@project:rules.md` reads `.amplifier/rules.md` in that workspace.
- `@user:rules.md` reads `rules.md` in your Amplifier home.
- `@~/notes.md` reads a file in your home directory.
- `@foundation:context/CONTEXT_POISONING.md` reads a resource from the
  `foundation` namespace, when that bundle is part of the active composition.

This uses Foundation's shared mention loader, as the CLI does. It applies to
messages, retries, edit-and-send, scheduled instructions and child-session
instructions and steering. References are read afresh for each new input;
confirming an already accepted retry does not read or send it again.

Installing a Python package or sourcing one tool from a bundle does not add
that bundle's resource namespace. A missing or unreadable reference is left in
the text, without injected contents. Unified does not activate another bundle
to resolve it. Put whitespace after a reference; punctuation can be interpreted
as part of its filename by the shared parser.
