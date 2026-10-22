# Release 0.20.178 combined qualification

The candidate combines the reviewed performance checkpoint at `11ba0b5a5e3e5ba0bdb062b3ab0c8e8bd7261c54` with main `54edb898e` (diagnostic fix #844, stopped test artifact recovery #831 and history classification test fix #841). Production sources did not overlap. Only generated client files conflicted; these were rebuilt from the combined source. Independent review checked 2,325 non-generated files and found no new production change.

## Changed-flow validation

Inside the owned spark-2 DTU on October 18, 2026:

- 148 focused Python tests passed across diagnostic acknowledgement, provider capture/windows, history residency, shell query/resource ownership and release notes.
- 1,039 frontend tests passed, with no skips.
- Two consecutive frontend builds produced identical committed assets.
- Provider-recording Chromium checks passed for delayed exact-call capture, explicit preview/download access, desktop and narrow layouts.
- Canonical-Work Chromium checks passed, including legacy recording navigation through collapsed saved pages.
- Wheel/source distribution validation and an installed-wheel worker bootstrap probe passed outside the source tree. Eleven changed product modules matched the installed wheel byte for byte.

The initial provider browser run failed because its child projector loaded an older installed wheel from a frontend working directory while its parent used combined source. That failure is retained. Installing the combined wheel before browser checks and running those checks from the source root aligned both processes. No production change or relaxed assertion was needed.

## Evidence and limits

Machine receipts, input/asset hashes, logs, screenshots, independent review and the initial failure remain in the workspace evidence directory `output/release178-20261018/combined/qualified/evidence`. Synthetic browser fixtures exercise the built client and worker path; they are not new live-provider acceptance. Existing reviewed alert acknowledgement uses AppService tests; the unchanged browser visibility timer is not newly qualified here.

Further performance tuning remains paused. The frozen scale results and unmet latency targets in `frame-projection633-qualification.md` remain unchanged; this release does not claim those targets were met. No spark-1 adoption occurred. Broad Python/browser/platform checks run after publication and must be reported separately from publication success.

Combined client build: `{'version': '0.20.178', 'id': 'ce18a0a69358b5ad'}`.

Local qualified wheel SHA-256: `ebc94ff21a77ced44352467b3adb5cc823c3c685a0a0359fee4334d35e422870`. The publisher builds its own immutable artifact and verifies matching receipts.
