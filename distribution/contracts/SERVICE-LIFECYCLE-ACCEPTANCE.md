# Service lifecycle acceptance

The implemented public contract lives in the independently versioned
`@amplifier/unified-distribution-update-owner` package. The distribution opts in
through installer configuration, composes every configured host participant,
and exposes installed lifecycle controls. It does not register or adopt an OS
service. `service-lifecycle.ts` points to the package contract rather than
maintaining a divergent proposal.

Qualified isolated installed behavior:

- Active work and missing coverage refuse stop before signaling.
- Held admission binds command, installation, exact instance, scope and fence.
- Only the retained child process handle can establish stopped proof. A stale
  PID, disappeared endpoint or foreign process cannot authorize adoption.
- Lost replies retain uncertainty, exact receipts and held intake; reconciliation
  never repeats a stop, initial launch, or resume.
- Reopening the saved supervisor validates private namespace, configuration,
  release trust and installation/owner binding, and starts no application.
- Explicit resume requires its stopped receipt, retained signed bytes and a new
  instance. The old fence releases only with authenticated service-specific
  proof; readiness and settled intake remain separate facts.
- The installed CLI offers serve/status/stop/resume/receipt/reconcile/watch.
  Duplicate runners refuse ownership. SIGINT/SIGTERM request qualified stop,
  never force termination of busy or unknown work.
- Explicit application prepare/activate controls use the same restart proof and
  admission path as direct Install; preparation itself leaves the child running.

The POSIX adapter is an owned Node child, with inherited IPC and actual exit
receipts. It is not systemd, launchd or Windows service-manager ownership.
No boot registration, manager restart policy, supervisor-crash adoption or
uninstall is implemented. Running it in an arbitrary manager unit cannot make
those cases qualified: manager stop timeouts/cgroup signaling could bypass held
admission, and Restart=always could turn uncertainty into an unauthorized launch.
Those require a dedicated platform-owner adapter and installed manager tests.

Native/external Python and registry currency remain separate owners. Read the
retained qualification receipt for the exact configured owner graph; minimal
fixture coverage must not be presented as full-product or production acceptance.
