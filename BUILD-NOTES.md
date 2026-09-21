# Release 0.3.0

Amplifier Unified is a standalone Python host and packaged React SPA. Installable source and release channel: private `microsoft/amplifier-unified`.

This release adds provider and routing setup; bundle discovery/composition and portable custom bundles; scoped module/source management and Core validation; modes, goals, limits and tool/skill controls; full transcript history/forks/import/export; notification preferences; staged ecosystem/app updates; full private backups and reversible reset; and headless terminal workflows.

Delegates, workers, tools and model calls appear inline as expandable conversation activity. Usage is aggregated from deduplicated model calls; provider-reported prices are distinguished from unavailable prices. Voice usage is recorded where supplied, with continuous Live session totals kept separate from response-level Realtime calls.

The default for new conversations is anchors. The existing user's conversation remains anchors-amp-dev. All controls share the agent-accessible action catalog and state; credentials remain private.

## Verification

- 150 Python checks passed; two Foundation-only checks are separately exercised in the real Foundation environment. Coverage includes service/runtime, provider setup, scoped registry, bundle portability, update isolation, recovery, transcript forks, headless HTTP and voice transport tests.
- 37 frontend tests and production build, including real component rendering, nested activity, usage rollups and notification privacy.
- Real configured main session delegated to a Foundation explorer child, which ran bash once. Six completed model calls were captured. Their per-call, child, delegate and turn totals matched without double counting; provider-reported aggregate was $0.49553775.
- Actual Foundation export/load roundtrip preserved 26 tools and 96 agents from the user's composed anchors setup.
- Actual context-simple Core validation: nine structural checks and ten behavioral tests passed.
- Runtime after community preparation has no amplifier-app-cli or amplifier-loop-live-cli imports.

See docs/CLI-PARITY.md and docs/UPDATES.md for detailed capability and update boundaries. Published-release installation and isolated update staging passed. The installed v0.3.0 host serves 74,031 bytes of CSS and 68 actions at port 8941. All 50 existing conversation messages and the selected anchors-amp-dev bundle were preserved; new conversations default to anchors. Daily update checking is enabled and automatic installation is off. A private full-state backup was created before replacement.

## Device verification still needed

Browser visual automation is unavailable due to its administrator-policy verification failure. Real microphone/playback and provider account sign-in were not exercised. Voice transport tests use controlled protocol fixtures. Native notification replies, device sync and STT/agent/TTS fallback remain future work as requested.

The command amplifier-unified coexists with the unrelated amplifier-web tool. Data stays in ~/.amplifier-unified. The loop-live implementation is pinned upstream; see docs/loop-live-upstream.
