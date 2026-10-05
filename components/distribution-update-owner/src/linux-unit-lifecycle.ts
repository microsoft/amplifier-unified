import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { readFile, writeFile, rename, mkdir, realpath } from "node:fs/promises";
import { join, isAbsolute, resolve } from "node:path";
import { randomUUID } from "node:crypto";
import { setTimeout as delay } from "node:timers/promises";
import type { ServiceProcessLifecycle } from "./service-owner.js";
import type { ServiceProcessCustody } from "./service-types.js";
import type { OwnedChildIdentity, OwnedExitProof } from "./posix-process.js";
import type { InitialStartCustody } from "./service-owner.js";
import type { OwnedProcessOptions } from "./lifecycle.js";
import { prepared, same, token, type RestartRequest, type RunningIdentity } from "./types.js";

const exec = promisify(execFile);
const quote = (v: string) => {
  if (/[\x00\r\n]/.test(v)) throw Error("invalid_unit_argument");
  return '"' + v.replaceAll('\\', '\\\\').replaceAll('"', '\\"').replaceAll('%', '%%').replaceAll('$', '$$') + '"';
};
type UnitState = Record<string, string>;

/** One stable installed user unit contains every
 * local descendant (including reparented children). All configured launchers must
 * enter the existing ServiceLifecycleOwner/ServiceStore authority first. Arbitrary
 * same-user systemctl calls are outside this cooperative contract.
 *
 * This adapter NEVER inherits the supervisor's interpreter. resolve() supplies
 * the independently qualified role entrypoint. It never adopts the legacy child
 * adapter's PID/IPC proof, nor uses a main PID as proof the tree is empty. */
export class LinuxUnitLifecycle implements ServiceProcessLifecycle {
  readonly supportsReleaseActivation = true as const;
  private exits = new Map<string, OwnedExitProof>();
  private mutation = false;
  readonly processes: ServiceProcessLifecycle["processes"];
  readonly ownedPid = null; // No generic PID authority; legacy handoff cannot use this adapter.
  private unitFile: string;
  constructor(private options: Pick<OwnedProcessOptions, "resolve" | "initialProvisioning"> & {
    ownerId: string;
    unit: string;
    unitDirectory: string;
    /** Authenticated runtime observation, after its actual domain locks/init. */
    inspect(): Promise<(RunningIdentity & { invocationId: string; intakeClosed: boolean }) | null>;
    observationMs?: number;
  }) {
    if (process.platform !== "linux") throw Error("linux_unit_required");
    if (!/^amplifier-[a-z0-9-]+\.service$/.test(options.unit) || !isAbsolute(options.unitDirectory))
      throw Error("invalid_owned_unit");
    this.unitFile = join(options.unitDirectory, options.unit);
    this.processes = {
      ownerId: token(options.ownerId),
      inspect: () => ({ state: "unknown", reason: "ownership_unproven" }),
      exitProof: (expected) => this.exits.get(expected.instanceId) ?? null,
    };
  }
  private async ctl(...args: string[]) {
    return (await exec("systemctl", ["--user", ...args], { maxBuffer: 256 * 1024 })).stdout.trim();
  }
  private async state(): Promise<UnitState> {
    const raw = await this.ctl("show", this.options.unit, "--property=LoadState,ActiveState,SubState,InvocationID,ControlGroup,FragmentPath,KillMode,ExitType,Delegate,Restart,SendSIGKILL,TimeoutStopUSec,Environment");
    return Object.fromEntries(raw.split('\n').map(line => {
      const n = line.indexOf('='); return [line.slice(0, n), line.slice(n + 1)];
    }));
  }
  private async owned(state: UnitState) {
    if (state.LoadState !== "loaded" || await realpath(state.FragmentPath) !== await realpath(this.unitFile) ||
        state.KillMode !== "control-group" || state.ExitType !== "cgroup" || state.Delegate !== "no" ||
        state.Restart !== "no" || state.SendSIGKILL !== "no" || state.TimeoutStopUSec !== "infinity")
      throw Error("unit_custody_unqualified");
  }
  private binding(state: UnitState, expected: OwnedChildIdentity) {
    for (const [key, value] of Object.entries({
      AMPLIFIER_UNIT_INSTANCE: expected.instanceId, AMPLIFIER_UNIT_SCOPE: expected.dataScope,
      AMPLIFIER_UNIT_RELEASE: expected.releaseDigest,
    })) if (!state.Environment.split(' ').includes(key + '=' + value)) throw Error("unit_identity_conflict");
  }
  private async populated(state: UnitState) {
    if (!state.ControlGroup) return false;
    const path = resolve("/sys/fs/cgroup", '.' + state.ControlGroup);
    if (!path.startsWith('/sys/fs/cgroup/')) throw Error("invalid_unit_cgroup");
    try {
      // cgroup.events populated includes descendants, not merely cgroup.procs.
      return /^populated 1$/m.test(await readFile(join(path, 'cgroup.events'), 'utf8'));
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT' && ['inactive', 'failed'].includes(state.ActiveState)) return false;
      throw error;
    }
  }
  async inspect(): Promise<RunningIdentity | null> {
    try {return await this.inspectOwned();} catch {return null;}
  }
  // The normal updater uses its configured ServiceLifecycleOwner branch. Keep
  // accidental legacy calls fail-closed: no second admission or restart path.
  async admitRestart(): Promise<never> {throw Error("service_authority_required");}
  async restart(_request: RestartRequest): Promise<never> {throw Error("service_authority_required");}
  async inspectOwned(): Promise<RunningIdentity & { intakeClosed: boolean }> {
    const state = await this.state(); await this.owned(state);
    const actual = await this.options.inspect();
    if (!actual?.ready || state.ActiveState !== 'active' || !await this.populated(state) ||
        !state.InvocationID || actual.invocationId !== state.InvocationID)
      throw Error("unit_readiness_unconfirmed");
    this.binding(state, {instanceId:actual.instanceId,dataScope:actual.dataScope,releaseDigest:actual.identity.digest});
    return actual;
  }
  async inspectActivationReady(): Promise<RunningIdentity> {
    const actual = await this.inspectOwned();
    if (actual.intakeClosed !== true) throw Error("unit_activation_intake_open");
    return actual;
  }
  async observeExit(expected: OwnedChildIdentity): Promise<OwnedExitProof | null> {
    const state = await this.state(); await this.owned(state); this.binding(state, expected);
    if (!['inactive', 'failed'].includes(state.ActiveState) || await this.populated(state)) return null;
    const proof = this.exits.get(expected.instanceId) ?? {
      ownerReceiptId: 'unit-exit:' + randomUUID(), ownerId: this.options.ownerId,
      instanceId: expected.instanceId, observedAt: Date.now(), code: null, signal: null,
    };
    this.exits.set(expected.instanceId, proof); return proof;
  }
  private async wait<T>(read: () => Promise<T | null>): Promise<T> {
    const until = Date.now() + (this.options.observationMs ?? 30000);
    do {
      const value = await read(); if (value !== null) return value;
      await delay(25);
    } while (Date.now() < until);
    // Observation budget is NOT interruption authority. Keep the real unit and
    // original operation intact; reconciliation observes, never relaunches.
    throw Error("unit_observation_unconfirmed");
  }
  private async mutate<T>(work: () => Promise<T>): Promise<T> {
    if (this.mutation) throw Error("lifecycle_busy");
    this.mutation = true; try { return await work(); } finally { this.mutation = false; }
  }
  private async exact(expected: OwnedChildIdentity) {
    const state = await this.state(); await this.owned(state); this.binding(state, expected);
    if (!['active', 'activating', 'deactivating'].includes(state.ActiveState) || !await this.populated(state))
      throw Error("unit_not_running");
    return state;
  }
  private async unitCustody(expected: OwnedChildIdentity): Promise<ServiceProcessCustody> {
    const state = await this.exact(expected);
    if (!/^[a-f0-9]{32}$/.test(state.InvocationID)) throw Error("unit_generation_unconfirmed");
    return {kind:"linux-unit",unit:this.options.unit,invocationId:state.InvocationID,
      instanceId:expected.instanceId,dataScope:expected.dataScope,releaseDigest:expected.releaseDigest};
  }
  async captureCustody(expected: OwnedChildIdentity) {
    // Initial/live custody acquisition requires authenticated runtime identity.
    // A dead endpoint cannot create a new retained generation from caller labels.
    const actual = await this.inspectOwned();
    if (actual.instanceId !== expected.instanceId || actual.dataScope !== expected.dataScope ||
        actual.identity.digest !== expected.releaseDigest) throw Error("unit_identity_conflict");
    return this.unitCustody(expected);
  }
  async verifyCustody(expected: OwnedChildIdentity, retained: ServiceProcessCustody) {
    const actual = await this.unitCustody(expected);
    if (!retained || Object.entries(actual).some(([key,value]) =>
      retained[key as keyof ServiceProcessCustody] !== value)) throw Error("unit_generation_conflict");
  }
  async stopOwned(expected: OwnedChildIdentity) {
    return this.mutate(async () => {
      await this.exact(expected);
      // Caller already drained accepted work. The unit disables timed SIGKILL;
      // ordinary stop cannot turn a slow child into implicit force authority.
      await this.ctl("stop", "--no-block", this.options.unit);
      return this.wait(() => this.observeExit(expected));
    });
  }
  async interruptOwned(expected: OwnedChildIdentity, retained: ServiceProcessCustody) {
    return this.mutate(async () => {
      await this.verifyCustody(expected, retained);
      // Only the separately authorized ServiceLifecycleOwner stop mode calls this.
      // Never reached from wait(), an update timeout, or passive reconciliation.
      await this.ctl("kill", "--kill-whom=all", "--signal=SIGKILL", this.options.unit);
      await this.ctl("stop", "--no-block", this.options.unit);
      return this.wait(() => this.observeExit(expected));
    });
  }
  async startInitialOwned(request: RestartRequest, hooks: InitialStartCustody) {
    if (!this.options.initialProvisioning) throw Error("initial_authority_required");
    if (!hooks?.onClaim || !hooks?.onCustody) throw Error("initial_custody_required");
    return this.mutate(async () => {
      const claim = await this.options.initialProvisioning!.claim(request);
      if (claim.kind !== 'pristine-installation' || claim.commandId !== request.commandId ||
          claim.instanceId !== request.instanceId || claim.dataScope !== request.dataScope ||
          claim.targetDigest !== request.target.identity.digest) throw Error("initial_authority_unconfirmed");
      // Claim and actual unit generation join the existing ServiceStore operation
      // BEFORE readiness. An interrupted claim is never retried or respawned.
      hooks.onClaim(claim);
      await this.launch(request, hooks.onCustody);
    });
  }
  async resumeOwned(request: RestartRequest, onCustody?: (custody: ServiceProcessCustody) => void) {
    return this.mutate(() => this.launch(request, onCustody));
  }
  private async launch(request: RestartRequest, onCustody?: (custody: ServiceProcessCustody) => void) {
    const target = prepared(request.target), state = await this.state();
    if (state.LoadState !== 'not-found') {
      await this.owned(state);
      if (!['inactive','failed'].includes(state.ActiveState) || await this.populated(state))
        throw Error("existing_unit_tree_not_stopped");
    }
    const spec = await this.options.resolve(target);
    if (!isAbsolute(spec.command) || (spec.cwd && (!isAbsolute(spec.cwd) || /[\x00\r\n]/.test(spec.cwd))))
      throw Error("qualified_absolute_entrypoint_required");
    const env = { ...spec.env, AMPLIFIER_DISTRIBUTION_LIFECYCLE: "linux-user-unit", AMPLIFIER_UNIT_INSTANCE: token(request.instanceId),
      AMPLIFIER_UNIT_SCOPE: token(request.dataScope), AMPLIFIER_UNIT_RELEASE: target.identity.digest,
      // Runtime identity verifies the signed entrypoint independently. These
      // generation bindings come from this launch, never ambient shell values.
      AMPLIFIER_DISTRIBUTION_INSTANCE_ID: token(request.instanceId),
      AMPLIFIER_DISTRIBUTION_DATA_SCOPE: token(request.dataScope) };
    for (const [key,value] of Object.entries(env))
      if (!/^[A-Z_][A-Z_0-9]*$/.test(key) || typeof value !== 'string') throw Error("invalid_role_environment");
    const lines = ['[Unit]', 'Description=Amplifier Unified owned installation', '[Service]',
      'Type=exec', 'ExitType=cgroup', 'Delegate=no', 'KillMode=control-group', 'Restart=no',
      'SendSIGKILL=no', 'TimeoutStopSec=infinity',
      ...Object.entries(env).map(([k,v]) => 'Environment=' + quote(k + '=' + v)),
      ...(spec.cwd ? ['WorkingDirectory=' + spec.cwd.replaceAll('%', '%%')] : []),
      // env -i excludes ambient provider credentials. Only systemd's invocation
      // identity is explicitly forwarded, independently of the controller process.
      'ExecStart=/usr/bin/env -i "INVOCATION_ID=${INVOCATION_ID}" ' +
        [...Object.entries(env).map(([k,v]) => k + '=' + v), spec.command, ...spec.args].map(quote).join(' '), ''];
    request.signal.throwIfAborted();
    await mkdir(this.options.unitDirectory, {recursive:true,mode:0o700});
    await writeFile(this.unitFile + '.next', lines.join('\n'), {mode:0o600});
    await rename(this.unitFile + '.next', this.unitFile);
    await this.ctl('link', '--runtime', this.unitFile);
    await this.ctl('daemon-reload');
    await this.ctl('start', this.options.unit);
    onCustody?.(await this.unitCustody({instanceId:request.instanceId,
      dataScope:request.dataScope,releaseDigest:target.identity.digest}));
    await this.wait(async () => {
      let actual: RunningIdentity;
      try { actual = await this.inspectActivationReady(); } catch { return null; }
      return actual.instanceId === request.instanceId && actual.dataScope === request.dataScope &&
        same(actual.identity, target.identity) ? actual : null;
    });
  }
}
