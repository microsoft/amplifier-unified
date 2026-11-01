# Unattended workflow qualification

`tests/test_unattended_acceptance.py` exercises a synthetic journey across the
existing owners. It changes no production behavior and is a focused check, not
a new release gate. Run it only inside the project's owned test environment.

The operator selects app-wide Full once, before any conversation exists. A
trusted system then sends an input to its own registered conversation. That
conversation's mounted native root requests work from a peer. No human message,
attached browser, approval response or fabricated Shared Work association is
used. A system message retains `inputOrigin: system`; a subsequent peer request
is attributed to the actual native sender.

The journey uses actual Core/Foundation root and child sessions, the native
Worker and RuntimeManager bridge, filesystem tools and the streaming loop's tool
hooks. A child reads context outside its project and writes an initial candidate.
The root submits a canonical queued check. A separate native peer reads the
actual source, identifies a missing behavior and returns a hash-bound file. The
original child resumes with the verified review text and hash-bound reference in
its assigned input. The test checks that input in the child's native context
before the scripted correction writes any files; a second check then runs. Final
source and test-output files are retained through the ordinary collaboration
reply/artifact owners, and remain readable after deleting the original result
file. Retrying the exact declaration retains its original artifacts.

The test backend implements a controlled lifecycle protocol. Each command is a
real subprocess in a private copied working directory inside the outer owned
test environment. Exit status and stdout come from that process. Source archives,
result archives and their hashes are created and checked from actual files;
completed process directories are removed after retention. The protocol fixture
does **not** launch another container or prove physical Docker/Incus behavior.
Peer delivery and terminal-event transport are scripted. Terminal attribution is
derived from the actual saved native assistant row using the Worker checkpoint
adapter. There is no live model/provider efficacy or installed-release claim.

Additional cases prove that an active check can be cancelled through its native
root, the actual process terminates, output survives and a repeated submission
does not execute again. Stop fences a queued check before any process starts.
Revoking Full fences an accepted, delayed file-policy change before commit.
Routine managed checks already use ordinary task authority in Ask; changing Full
alone is intentionally not treated as cancellation of those checks.

One separate case sends `workspace.tests.read` and `workspace.tests.result`
through an actual Full child after a real test completes. Both responses must
succeed and match the completed canonical check and saved output, without relaying
them through the parent. A refusal, stale result or malformed response fails the
case. This observation never runs another command.

Set `UNATTENDED_ACCEPTANCE_EVIDENCE` to an owned temporary output directory to
retain concise native actor traces, canonical receipts and actual result archive
copies. These generated files are private evidence and do not belong in source
review. The outer environment, dependencies and live services are never created,
reconfigured or removed by this test.
