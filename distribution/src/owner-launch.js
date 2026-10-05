/** Construct a built-in installed Python owner or the configured executable. */
export function configuredOwnerLaunch(config,module,configuration,{cwd}={}){
 // Isolated Python (-I) ignores PYTHONDONTWRITEBYTECODE. Imports must not add
 // bytecode to the signed runtime: doing so invalidates later readiness and
 // update verification. Keep -B on the interpreter, including on cold starts.
 // An explicit executable is authoritative and may not be Python; preserve its
 // existing argument contract rather than inferring or rewriting its flags.
 return {command:config.command??config.python,
  args:config.command?['--config',configuration]:['-I','-B','-m',module,'--config',configuration],
  env:config.env,...(cwd===undefined?{}:{cwd})};
}
