# Preparation reuse and saved worker history — 0.20.100

Base: `73233813c9c15b0b8ea4d1cf89ab6ee509511a3a`. All application checks ran in
this project's owned ARM64 Linux DTU; no shared installation was changed.

## Runtime preparation (#494)

Two regression cases failed before the fix: adding the exact built-in Work
registration, and adding a registration shadowed by an existing sources.bundles
entry. Both previously created a new custom runtime despite identical effective
bundle registrations. The configuration key now follows Config.registrations
precedence for these redundant added entries. New aliases remain significant,
including aliases only used by lazy agents. Local content signatures, edited
plans, parent generations, qualification, cancellation and source checks remain.

The retained no-inference Work probe used fresh state, real dependency preparation,
a placeholder key and warm download caches. Two concurrent cold callers shared
one runtime: 13.30 s and 13.22 s. Warm calls took 1.20 ms and 1.00 ms; adding the
redundant registration reused that runtime in 1.06 ms without another attempt.
This is one preparation sample, not an end-to-end latency or speedup estimate.
The frozen d84730ae multi-chat benchmark is unchanged. Run the probe only in an
owned DTU using tests/fixtures/profile_preparation_benchmark.py with a fresh root.

## Worker history (#265)

A saved worker's read-only restriction now takes precedence over its retained
blocked/yielded ownership notice. It says Worker history and explains the
restriction without offering an impossible takeover. Current ownership transfers
and running-call status retain their existing behavior. No lock is cleared and
no worker is promoted into a root conversation.

Focused runtime, native-history, ownership and release checks: 442 passed,
13 optional skips, two subtests passed. All 577 frontend tests passed. Browser coverage exercises draft retention,
readable history, takeover refusal, real root takeover/retry, keyboard controls
and mobile layout using production assets and synthetic ownership events.
Physical macOS CLI exit/network switching and voice/device acceptance remain
deferred; this does not establish complete recovered-chat lifecycle parity.

## Companion provider transport (#305)

microsoft/amplifier-module-provider-openai#126 enables TCP keepalive on owned
HTTP clients while retaining unlimited quiet reasoning and existing retries.
A synthetic Linux packet-drop probe detected a lost peer in 61.33 s; a separate
healthy connection returned after 65.01 s of silence. Direct and environment
proxy socket tests passed. No real model requests or credentials were used.
The provider has its own component update; injected clients retain their policy.
A responsive proxy does not prove its upstream model is healthy.
