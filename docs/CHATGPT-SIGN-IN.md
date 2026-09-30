# ChatGPT connections

AI connections and Advanced → Provider configuration use the same sign-in action.
New connections default to **ChatGPT Codex · device sign-in**
(`auth_mode: chatgpt_codex`). Existing device credentials and the previous
`legacy_codex` configuration spelling remain compatible. Unified keeps this
default while the capability differences, including voice, are investigated.

Users can explicitly choose **ChatGPT plan · browser sign-in**
(`auth_mode: chatgpt_plan`) to authorize Amplifier Unified using OpenAI's public
ChatGPT-plan flow. No existing connection is migrated automatically. These are
two provider contracts, not interchangeable login screens; their endpoints,
model lists and supported features can differ. API-key OpenAI connections and
Unified's existing voice authentication remain separate.

The browser for the new flow must run on the Amplifier host: its callback uses
loopback. For Spark or another remote server, follow the provider's
[local sign-in and SSH import instructions](https://github.com/microsoft/amplifier-module-provider-openai-chatgpt/blob/main/docs/CHATGPT_PLAN_SIGN_IN.md#self-hosted-server).
This uses the standalone `amplifier-chatgpt-auth` utility, not amplifier-app-cli.
The destination keeps its own host identity and owns token refresh after import.
Never paste a credential file into chat or browser settings. Set the imported
profile's `token_file_path`, `auth_mode: chatgpt_plan`, and the import command's
`host_file_path` in that connection's Advanced configuration. When import uses
its default host file, this is `host.json` beside the imported profile. Keeping
the same path preserves the server's identity during later sign-ins. Preserve
the legacy file until the replacement works.

Signing in and granting plan usage are distinct. If plan permission was not
granted, settings offer **Enable ChatGPT plan access**. The model picker shows
**Using ChatGPT plan** for a mounted plan connection and links to
[Manage usage](https://chatgpt.com/settings/usage). No API-key fallback occurs.

## Ownership and compatibility

The provider owns dynamic client registration, PKCE and loopback verification,
verified identity, private token files, rotating refresh, revocation, public model
discovery and Responses transport. Unified owns configuration, progress,
account/permission display and catalog invalidation. Auth workers run separately
from conversations and return only bounded progress. Token/identity-hint URLs
and raw provider stdout never enter public app state.

A failed or cancelled login leaves saved connection settings intact. Each attempt writes a separate private profile; only a completed, successful
sign-in changes the connection's token path. The previous token file is kept,
including custom files shared with other applications. A connection edited or removed during login is not resurrected by its
completion. Refreshing a login does not restart or replay an active conversation.

This feature requires the provider's `plan_auth` support. Update included
components before selecting the new method. Automated acceptance uses local
protocol fixtures; real-account consent and remote-device acceptance remain
separate checks.

References: [OpenAI sign-in guidance](https://developers.openai.com/cookbook/articles/sign-in-with-chatgpt),
[public model/inference contract](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference).
