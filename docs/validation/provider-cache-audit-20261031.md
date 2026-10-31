# Provider cache audit, October 31, 2026

Scope: the nine providers in `amplifier_web/provider_sources.json`, their actual
request/usage owners, and focused offline tests. This is a dated source audit,
not evidence of live acceptance on every endpoint or a second capability registry.
The [performance guide](performance.md#provider-requests-and-cache-observations)
owns the continuing design and validation rules.

| Provider / inspected base | Finding and action |
| --- | --- |
| OpenAI `63a2f0cd` | Automatic tool-result cache boundaries covered Astra but omitted other supported GPT-6 tiers. [provider-openai #138](https://github.com/microsoft/amplifier-module-provider-openai/pull/138) extends the existing model capability set, uses the actual endpoint owner and tests initial/continued requests. Synthetic API samples on Sol, Sol 6.1 and Luna established reuse; they do not establish a wall-time improvement. |
| Azure OpenAI `3a67ed10` | Inherits OpenAI request construction. Deployment-specific policy must opt in for supported Standard models and exclude PTU. Arbitrary deployment aliases do not prove their underlying model. The companion adapter correction uses the same policy point, not a second request compiler. |
| Anthropic `af32c269` | Already has stable-prefix inference and rolling cache boundaries, including ephemeral-tail and tool-pair rules. Existing cache/usage checks pass (45). No blanket switch to longer paid retention or additional cache writes. |
| Gemini `39546071` | GenerateContent usage preserves reported cached tokens; implicit caching is service-owned. Existing usage/write-accounting checks pass (9). No automatic explicit-cache-object lifecycle is added. |
| OpenAI ChatGPT `b1341edd` | Separate Codex/plan request paths, not the public OpenAI adapter. Parser and normalized response discarded reported cached-input counts. Correct both response and event reporting; retain unknown when absent. Do not assume public OpenAI cache-marker fields work on these routes. |
| Generic Chat Completions `f46d148e` | Real zero cached tokens became unknown in both streaming and nonstreaming responses. Preserve zero separately from omitted usage; cost arithmetic stays unchanged. Endpoint owners still control caching. |
| GitHub Copilot `ed942e2c` | SDK event normalization already carries reported cache usage and distinguishes absence. Source-reviewed only; no new SDK-backed inference qualification claimed. |
| Ollama `f86ae6c7` | Does not invent cache hits from prompt-evaluation counts. Existing absence checks pass (3); model/server reuse is outside this adapter's reported usage. |
| vLLM `985dcf46` | Preserves reported cache counts and absence. Existing usage checks pass (9); no serving-side prefix-cache configuration or live GPU benchmark claimed. |

Azure's [documented cache rules](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/prompt-caching)
distinguish Standard breakpoints from unsupported PTU-M breakpoints. The
[Anthropic guide](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
defines block/automatic cache controls and paid retention choices. The
[Gemini guide](https://ai.google.dev/gemini-api/docs/caching) distinguishes implicit
reuse from API/model-specific explicit cache support. Apply the rules for the
API actually used, rather than copying fields between providers.

The request-size investigation found genuine older image inputs in native
conversation history, not duplicate images injected by current preparation.
That evidence does not authorize truncation. A future byte-reduction change
belongs to the installed context/native-history owner, with exact original
retrieval, pending effects and resume qualified. No Core change or generic
cache abstraction is required for the corrections above.
