# Artifact runtime discovery

Use the shared `runtime.dependencies` action to inspect the host and an optional
`sessionId`. Agent calls default to the calling conversation. UI calls default
to the selected conversation. This read does not select a chat, change a draft,
warm a worker, acquire execution ownership or install dependencies.
The same inspection is available under Settings → Advanced → Session controls →
Document and data tools.

The response separates `host` from `worker`. Each available environment reports
its exact Python executable, prefix, platform and installed public package
versions. A stopped, retired or unprepared worker remains `unavailable`; a failed
inspection is `unknown`. Use the Python executable from the matching record.
Installed metadata does not prove a package imports or works in another venv.

Node, LibreOffice and Poppler's `pdftoppm` paths and version probes are bounded.
Set `WORK_SOFFICE` and `WORK_PDFTOPPM` to absolute executable paths when the host
uses custom installations. Invalid overrides do not silently fall back. Relative
PATH entries and the current directory are excluded. Default discovery does not import Python packages. With `verifyImports: true`,
it probes the six public authoring libraries using each environment's own Python
in an isolated child process with a clean temporary working directory, bounded
time/output, and no workspace or PYTHONPATH imports. It reports per-package
`importStatus` and `importVerified`, plus `validation.imports`. Probe failures
never return package output or exception messages. It does not load Node packages,
return environment variables or render files.

For an optional host installation with public office/PDF libraries, use
`uv sync --group artifacts`. The group includes python-docx, python-pptx,
openpyxl, reportlab, pypdf and Pillow. Node, LibreOffice and Poppler remain optional
system tools. No proprietary Codex artifact runtime is required or bundled.
The managed worker declares these same six public libraries as runtime
dependencies, so normal worker preparation provisions them in its actual Python.
Existing qualified generations retain their recorded dependencies until the
normal staged update is activated; discovery never changes them. A custom worker
command remains responsible for its own environment.

Artifact workflows must still distinguish generation, rendering, visual review
and spreadsheet recalculation. Validation fields start as `not_run`; explicit import verification changes only
the imports field.
For example, openpyxl writes formulas but does not calculate them. Check saved
outputs and cached results with a calculation-capable application when required.
Keep artifacts within the task's permitted workspace. Runtime discovery does
not grant file access or add a persistent notebook kernel.
