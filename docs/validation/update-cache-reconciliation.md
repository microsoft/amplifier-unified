# Update cache reconciliation — 0.11.6

Integrates PR36 (`98297a7`) and PR21 (`b48a190`) with contributor ancestry preserved, starting from main `8f1b943` (0.11.5).

## Confirmed problems and behavior

- An actual wheel build of wiki-weaver upstream `cd855a4` modifies its tracked version stamp. Current Unified treated this exact generated artifact as a protected edit. The reviewed hook-content check now recognizes only the known output. Genuine edits, unknown hooks, staged changes, unexpected file types/modes and moving Git HEAD remain protected. Inventory does not execute hooks or modify cache files; normalization occurs only in the isolated staging copy.
- Historical cache failures were presented without usage context. Source usage is now separate from update status and eligibility. Positive configuration evidence is shown as configured; all other caches have unknown usage. Their failures remain visible, eligible updates still install, and all cache copies remain preserved.
- PR21 was adapted to the current shared global/project/local/session settings contract. The read-only helper shares runtime merging and uses the runtime session identity, including native identity fallback. It does not load keys or prepare/import caches.
- Missing directories retained for workspace and conversation history are skipped without an invented configuration warning. A directory that returns is read even if its saved availability flag is stale.
- Repository/ref identity is checked again before staging installation, alongside revision and protected edits.

## Validation

- Full Python suite: **1,021 passed, 8 skipped**.
- Full frontend suite: **154 passed**.
- Focused cache/update/shared-settings suite: **130 passed**, including wiki-weaver artifact verification and real temporary Git fetch/checkout/staging protection.
- Six additional shared-usage tests cover global/project/local/session scopes, runtime/native session identity, obsolete private copies, retained missing workspaces, returned directories and identical read-only/runtime merging.
- Production browser checks: cache reporting on desktop and 390px mobile; configured versus unknown grouping; errors visible with inventory collapsed; unknown updates remain installable; pins retained; synthetic check/install actions. No browser errors or horizontal overflow.
- Broader production Settings browser, empty-host first input/draft persistence, and review-during-pending-send browser checks passed.
- Production assets rebuilt twice with identical tracked output. Wheel and source distribution verified as 0.11.6.
- Release pipeline now includes the cached-source browser check, guarded for immutable older releases that lack that fixture.

The browser fixture uses synthetic sources and intercepts check/install operations. Real temporary Git operations are exercised by the Python tests. This is not a live ecosystem installation, model-call test, or deployed-host restart. No live caches or user settings were changed during validation.
