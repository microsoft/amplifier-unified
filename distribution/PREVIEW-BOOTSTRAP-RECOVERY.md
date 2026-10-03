# Failed startup recovery for the isolated preview

This is a transitional, exact-artifact operator procedure. It is not a general
cold-state API for interchangeable owners, and it is not a signed handoff or
retirement proof. The original failed authority and unit remain evidence.

## Required boundary

One trusted operator holds the complete offline window. Every configured start
path must be enumerated. The restored manual service must be effectively masked
at systemd's winning unit path, with its original definition saved and hashed.
A runtime mask is insufficient when a higher-priority user configuration unit
exists: the real synthetic test demonstrated a successful start despite the
runtime mask reporting success. Verify LoadState=masked, UnitFileState=masked,
MainPID=0, inactive/dead, empty original descendants, and actual start refusal.

The failed instrumented unit is not masked or rewritten: its retained witness
binds its definition. Its immutable launch code plus permanent authenticated
source guard must be independently reviewed to reject any restart before app
owner construction. Bind the unit and relevant launcher/owner files by hashes.
No claim or supervisor authority may exist. The failed source's public ready
receipt must be absent. Its wrapper's "ready" phase alone is not public readiness.

The operator must keep these gates held through the successor's owner
acquisition. The API now requires assertExclusionHeld before/after preparation
and consumption; the launcher calls it again around application construction
and before opening public intake. These checks detect lost gates; they do not
turn a snapshot into an exclusion mechanism. Do not remove masks automatically
on success or failure. Only the operator may restore a fallback after separately
qualifying the then-current state.

This boundary covers the reviewed cooperative unit/process topology and private
roots. Privileged actors, arbitrary same-account scripts, unreviewed remote
publishing services, or independent native writers are not excluded by a /proc
snapshot. Unknown start paths, owner roots, writers, or unreadable processes
without an exact independently reviewed exception must refuse. Do not advertise
this procedure as a general safe takeover implementation.

## Policy preparation

1. Preserve the stopped original and failed-start archives. Prior snapshots are
   forensic provenance; once the fallback has run again, create a fresh verified
   stopped backup before deriving the recovery policy. Do not silently replace a
   historical baseline with current contents. Review any differences in
   maintenance or catalog metadata. Keep the exact historical unknown receipts
   and canonical history unchanged.
2. Enumerate every authoritative root, including external catalog, native,
   workspace, ingress and private-home paths. Cover the exact configured twenty
   owners and all their roots. Bind every configured component's exact artifact;
   a package version alone is insufficient.
3. For each present SQLite file, use a verified private baseline file with its
   SHA-256, exact table schema and full typed row multiset. Classify every table
   as empty-effect-journal, preserved-history or startup-default. Only reviewed
   history/default tables may be nonempty. Original unknown records retain
   their count and digest. Newly created/pending/uncertain journals refuse.
   Use the owner-reviewed incident query recipe for this exact graph, not
   generic substring heuristics or write-enabled owner constructors.
4. Explicitly list absent new-owner databases. Do not create empty databases to
   satisfy the reader. The reader rejects a later appearance, including WAL or
   journal files. New startup-only defaults may be bound only to the preserved,
   verified failed-start archive after their owner review.
5. Enumerate every file, directory and symlink under the roots. Bind kind,
   permissions, UID and content digest (or exact link target). SQLite files are
   checked semantically, not by unstable file page order. No undeclared entries
   or nonempty transient SQLite sidecars are accepted.
6. Hash the policy file. Keep it private and include its path/hash and the
   preserved predecessor configuration path/hash in bootstrapRecovery. This
   configuration itself becomes an existing-state file binding. The reader
   rechecks both bound inputs on every use.
7. Independent review must approve the complete policy, including named process
   exceptions, all start paths, absence of failed readiness/admission, every
   effect table, and complete graph/root coverage. A self-generated inventory
   or a copied "approved" field does not supply this review.

The policy schema is exact-preview-recovery-policy-v1, scope
operator-held-offline-window. Required fields are owners, ownerCoverage, roots,
bootId, maskedUnits, guardedFiles, artifacts, absentPaths, admissionTables,
databases, entries, unreadableProcessExceptions, successorUnit, sourceDirectory,
predecessorConfigurationSha256 and profileDigest. The installed synthetic
fixture supplies a complete example outside the source repository.

The configuration addition is preview-bootstrap-recovery-v1 with the same
scope, predecessorConfiguration:{path,sha256} and policy:{path,sha256}.
The signed composition derives required absence of the original claim directory,
supervisor directory, and receiptDirectory/<failed-instance>-ready.json. It also
requires the original authority.json and key in guardedFiles.

## Preparation and launch

Use createBootstrapRecoveryOptions({configuration,expected,api,preparing:true})
in the reviewed installed composition. expected is the exact successor service
identity, including its fixed future instance ID, release digest and stable
installation/owner/data scope. Then call the public
prepareFailedBootstrapRecovery(options). This creates one authenticated permit;
it does not start anything, stop anything or retire the predecessor.

Start only the exact reviewed successor unit with that fixed identity and
configuration. The normal launcher preflights trust, credentials and TLS before
source allocation. It loads the signed qualifier, independently rechecks kernel
exit and every scoped policy condition, persists exclusive permit consumption,
then creates the successor source authority and owners. No interrupted permit
is retried or replayed. A new instance ID alone is never recovery authorization.

Run existing full-graph installed composition tests on the newly assembled
artifact before the live window. The isolated recovery fixture proves the
installed owner API and root qualifier through source acquisition with twenty
synthetic stores and actual systemd masks; it does not construct the twenty real
owners, establish browser readiness, or prove real-account effects. Root must
run its existing combined twenty-owner candidate qualification, then separately
verify live readiness and canonical history after activation.

## Future owner contract

A general solution needs owner-owned passive stopped-state inspection plus
continuous acquire/transfer of writer rights through successor acquisition.
Constructors that migrate or turn running records into unknown are not passive
readers. A generic snapshot DTO cannot replace that ownership contract.
