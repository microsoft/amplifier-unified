# Stopped artifact recovery qualification

The #831 product source at `8e8c0e8c900e6f706ec4fb44b555e187067375a4`
was integrated with main `185bb2f5acac78416c77738cc2d598d9a78cefb9`.
Source-level independent review found no remaining authority or receipt blocker.
Qualification ran on October 18, 2026 in owned spark-2 DTUs; no live installation
or user chat was used.

## Real Incus recovery

An isolated nested Incus backend used the qualified legacy adapter 0.4.1
(`241849071f682706791ae5f4a1e5462d0e316df7`) and pinned Ubuntu ARM64 image
`2712816095c8141221a5d7d9b950781ac152a1714543a34aacc8b43baa292ada`.
The current helper prepared and launched an offline guest. Export while running
was refused. The guest then created a 513 MiB workspace file and two artifact
entries (one regular file and one safe relative symlink), and was stopped.

Normal files-retaining removal failed its 512 MiB selected-files limit, leaving
an incomplete retention receipt and the stopped guest intact. Without replaying
that removal, artifacts-only recovery succeeded in 11.93 seconds. Its 10,240-byte
tar contained only the expected artifacts, with 22 logical bytes and SHA-256
`198031db3dd5337abcd04f032bc510d0a21034d99a098aeb327a03fb2ed0bca1`.
Archive contents, digest and symlink were independently checked. Workspace bytes
and digest, stopped state, configuration and last-used timestamp were unchanged.
Observed subprocesses included no start, stop, execute, destroy or delete command.
The artifact receipt supplied no removal-retention grant.

The first fixture launch failed because the nested daemon's default UID range
exceeded its disposable outer container's delegation. The owned nested daemon
was configured for its delegated range and a fresh operation/guest was used;
the earlier partial launch was not replayed. The expected failed-removal exception
also ended the first harness sequence; continuation verified unchanged state and
its incomplete receipt before performing only the distinct export operation.
Neither failed attempt is counted as a passing test.

## Focused and rendered checks

- 189 backend tests passed, zero skipped: retention, task environments, Incus and
  Docker workers, and environment recovery.
- 21 frontend environment tests passed, zero skipped; integrated client rebuilt.
- The existing `test-environments` browser owner covers desktop/narrow layouts,
  exact artifact owner/revision, prior operation receipt preservation, stopped
  state, unknown export refusal and no replay after refresh or later navigation.
  Its backend transport is controlled; the real Incus proof above is separate.

The default nested ARM Chromium launch crashed before rendering. The same browser
scenario runs with explicit `AMPLIFIER_CHROMIUM_ARGS` of `--no-zygote` and
`--disable-gpu`; it remains multiprocess. This is isolated test configuration,
not a shipping browser or application setting.

Evidence is retained at `output/artifact-recovery831-20261018/` in the owning
workspace, including raw failed attempts, real backend receipts, observed commands,
archive, focused logs and rendered screenshots/reports. Real Docker allocation,
live provider execution, physical touch and complete keyboard traversal were not
qualified. The operation remains bounded by the whole backup input limit: this
proves recovery beyond the workspace selection limit, not arbitrarily large guests.
