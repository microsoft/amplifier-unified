# Workspace starters, setup and resource inventory v1

The workspace owner is the sole replacement authority for starter definitions,
reviewed plans, setup receipts and workspace resource records. UI and agent
callers use the same advertised workspace actions and durable command IDs.

A trusted assembly callback supplies the native owner's configured standalone
bundle catalog. Without that callback the starter actions are not advertised.
No hard-coded fallback bundle is presented as a discovered configuration.
`workspace.prepare` snapshots the chosen definition and selected bundle source;
subsequent edits cannot change a reviewed plan. Existing folders require explicit
attachment and never receive starter scaffolding or repository imports.

Creation records allocation and registration before admitting background setup.
The owner counts all setup work in its quiescence background gate. Setup cannot
be resumed automatically on restart. Reads report retained pending/running work
as interrupted without writing. Explicit retry checks the observed revision,
original directory identity, and every uncertain import. Completed steps are not
repeated; unknown Git outcomes require inspection. Reconciliation performs local
Git inspection only. Partial work and unrelated existing files are retained.

Native Git runs from inherited directory handles on both macOS and Linux;
credentials, child repository source, and root note history remain separate.
Scaffolding never overwrites a file. Repository sources and refs are bounded and
validated, imports are noninteractive, hooks/filters are disabled, and output
and process lifetime are bounded. No starter can supply executable setup hooks.

Resource actions retain identifiers, owners and evidence outside the workspace.
`reaped` means a caller reports confirmed teardown; `observed_absent` records an
independent observation. Neither action performs or authorizes resource deletion.

Starter, setup and inventory files reside under the workspace owner's existing
state root and travel with that owner in complete backups. Migration of existing
legacy starter files is a separate adoption step and must preserve the originals.

Source behavior was ported from Amplifier Unified commit
12056a077540fec3cee62f329f4093f24422ebb2 (workspace_starters.py,
workspace_provisioning.py, workspace_resources.py and placement identity checks).
The legacy service retains its prior implementation while the replacement owns
this extracted implementation. No legacy service dependency is imported.
