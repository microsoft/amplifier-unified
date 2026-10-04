import { createHash } from "node:crypto";
import { lstat, realpath, open } from "node:fs/promises";
import { constants } from "node:fs";
import { isAbsolute } from "node:path";
import { ServiceLifecycleOwner } from "./service-owner.js";
import { token } from "./types.js";
import type { ExistingStateHandoffCommand, ExistingStateHandoffSource } from "./existing-state-types.js";

export interface ExistingStateBinding {
  id: string;
  path: string;
  kind: "file" | "directory";
}
/** Read-only binding of the existing roots/configuration selected by the trusted
 * launcher. No mkdir, copying, rebinding, migration or application-data writes.
 * Directory inode identity is stable while contents change during ordinary work. */
export async function inspectExistingStateBindings(bindings: ExistingStateBinding[]): Promise<string> {
  if (!Array.isArray(bindings) || !bindings.length || bindings.length > 128 ||
      new Set(bindings.map(b=>b.id)).size !== bindings.length) throw Error("existing_state_bindings_required");
  const rows = await Promise.all(bindings.map(async b=>{
    const id = token(b.id);
    if (!isAbsolute(b.path) || !["file","directory"].includes(b.kind)) throw Error("existing_state_binding_invalid");
    const info = await lstat(b.path,{bigint:true});
    if (info.isSymbolicLink() || b.path !== await realpath(b.path) ||
        (b.kind === "file" ? !info.isFile() : !info.isDirectory())) throw Error("existing_state_binding_invalid");
    let contentDigest: string | undefined;
    if (b.kind === "file") {
      // Small launch configuration only. Hash through a no-follow descriptor;
      // directory contents stay mutable and are never copied or walked here.
      const file = await open(b.path,constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
      try {
        const before = await file.stat({bigint:true});
        if (!before.isFile() || before.size > 1048576n || before.dev !== info.dev || before.ino !== info.ino)
          throw Error("existing_state_binding_invalid");
        const buffer = Buffer.alloc(Number(before.size) + 1);
        let total = 0;
        while (total < buffer.length) {
          const read = await file.read(buffer,total,buffer.length-total,null);
          if (!read.bytesRead) break;
          total += read.bytesRead;
        }
        const after = await file.stat({bigint:true}), current = await lstat(b.path,{bigint:true});
        if (BigInt(total) !== before.size || before.size !== after.size || before.mtimeNs !== after.mtimeNs ||
            before.ctimeNs !== after.ctimeNs || current.dev !== before.dev || current.ino !== before.ino || current.isSymbolicLink())
          throw Error("existing_state_bindings_changed");
        contentDigest = createHash("sha256").update(buffer.subarray(0,total)).digest("hex");
      } finally { await file.close(); }
    }
    return {id,path:b.path,kind:b.kind,device:String(info.dev),inode:String(info.ino),contentDigest};
  }));
  rows.sort((a,b)=>a.id.localeCompare(b.id));
  return createHash("sha256").update(JSON.stringify(rows)).digest("hex");
}
/** First-party source adapter. It obtains a real saved stop plus the source's
 * retained actual-child exit proof, and retires that source's resume authority.
 * Other manual launchers need a separately authenticated/qualified source port. */
export function createOwnedServiceHandoffSource(options: {
  service: ServiceLifecycleOwner;
  bindings: ExistingStateBinding[];
}): ExistingStateHandoffSource {
  const bindings = structuredClone(options.bindings);
  return {claim:async request=>{
    if (await inspectExistingStateBindings(bindings) !== request.dataBindingDigest)
      throw Error("existing_state_bindings_changed");
    return options.service.claimExistingStateHandoff(request);
  }};
}
/** Trusted local operator entrypoint, deliberately absent from remote RPC. The
 * destination is a fresh service owner over a fresh POSIX lifecycle. Its resolver
 * and release verifier must qualify the actual launch using these exact bindings.
 * Returning accepts a durable operation; waitFor/notifications observe completion. */
export async function launchExistingStateHandoff(options: {
  destination: ServiceLifecycleOwner;
  source: ExistingStateHandoffSource;
  command: Omit<ExistingStateHandoffCommand,"dataBindingDigest">;
  bindings: ExistingStateBinding[];
}) {
  const bindings = structuredClone(options.bindings), digest = await inspectExistingStateBindings(bindings);
  return options.destination.handoff({...options.command,dataBindingDigest:digest},{claim:async request=>{
    if (await inspectExistingStateBindings(bindings) !== digest) throw Error("existing_state_bindings_changed");
    const proof = await options.source.claim(request);
    if (await inspectExistingStateBindings(bindings) !== digest) throw Error("existing_state_bindings_changed");
    return proof;
  }});
}
