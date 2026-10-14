import {
  createLinuxSystemdSourceObserver,
  createManualSystemdHandoffLauncher,
  createManualIngressGate,
} from '@amplifier/unified-distribution-update-owner';

/** Trusted composition example, not a discovery/adoption command.
 * createApplication must register ingress as a required public participant with
 * explicit package/source/root provenance, alongside EVERY actual app owner.
 * createAccess must use the attached HTTP/WS hooks before accepting any traffic.
 * Neither callback may start an independent legacy stop/restart authority.
 */
export async function launchInstrumentedSource(options) {
  const wrapper = await createManualSystemdHandoffLauncher({
    directory: options.sourceDirectory,
    expected: options.expected,
    bindings: options.bindings,
    observer: createLinuxSystemdSourceObserver({unit: options.unit, python: options.python}),
    qualifyCurrent: options.qualifyCurrent,
  }); // Exclusive durable launch guard MUST precede the application/listeners.
  const ingress = await createManualIngressGate({
    directory: options.ingressDirectory,
    id: options.ingressOwnerId,
    onMayBeIdle: options.onMayBeIdle,
  });
  let app, access;
  try {
    app = await options.createApplication({
      serviceLifecycle: wrapper.serviceLifecycle,
      ingressBinding: {
        owner: ingress.participant,
        storage: {
          packageName: '@amplifier/unified-distribution-update-owner',
          packageVersion: '0.16.1',
          revision: options.qualifiedOwnerRevision,
          configKey: options.ingressConfigKey,
          rootRole: 'service-ingress',
          stateDirectory: options.ingressDirectory,
        },
      },
    });
    // The root callback projects its actual configured census here; do not copy
    // expectedOwners into this return value to make the comparison pass.
    if (!app.requiredOwners.includes(ingress.participant.id)) {
      throw Error('manual_ingress_not_required');
    }
    access = await options.createAccess({ingressGate: ingress});
    const control = await wrapper.attach({
      host: app.host,
      requiredOwners: app.requiredOwners,
      expectedOwners: options.expectedOwners,
      close: async () => {
        await access.close();
        await app.close();
        ingress.close();
      },
      exit: () => process.exit(0),
    });
    // Inspection only. Stop is authenticated, held, retired, and executed by
    // the source controller; no sampled-idle or signal-based success shortcut.
    return {inspect: control.inspect};
  } catch (error) {
    // Startup failure retains the consumed launch guard. No silent restart.
    await access?.close();
    await app?.close();
    ingress.close();
    throw error;
  }
}
