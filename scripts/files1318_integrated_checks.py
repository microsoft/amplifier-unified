"""ONE Files1318 cycle in the prepared browser-test runner; never run on the host.

Admission (parent-owned, not performed here): commit the reviewed final source,
including this driver and the other workers' final files, then freeze ONE job:
  /opt/unified-prepared/venv/bin/python -B scripts/files1318_integrated_checks.py
    --manifest /workspace/output/files1318-frozen-manifest.json
Use the normal 1200-second check budget and separate 1800-second preparation.
The 1200 seconds include verification and finalization: stop work at 1140,
reserve 15 seconds per process for runner cleanup, and stop evidence hashing at
1195. SIGALRM bounds synchronous work; a 1200-second hard guard exits nonzero.
SIGTERM/SIGINT remain owned by run_scenarios while its child is active.
No Git is available in the snapshot. Required manifest schemaVersion=2:
baselineRevision/full40 starting 826f6c84; baselineTree/full40 starting 2ca2a7c5;
revision/tree = the reviewed candidate's full40 commit/tree; reviewed=true;
changedPaths = exact baseline-to-candidate changed paths within REVIEWED_CHANGES;
files={relative_posix_path: {sha256: hex, mode: Git mode string}}.
Generate AFTER committing: every source file must match the candidate commit;
no dirty authored bytes. Include ALL tracked files, including committed helpers.
Modes are 100644, 100755 or 120000 (hash the link text). Exclude this generated
manifest itself and prepared dependencies. Parent attests review, clean source,
baseline diff and tree; runtime checks candidate sourceHead plus every frozen byte.
Retain the manifest hash with admission. This driver never commits or invokes Git.

The v1 execution contract selects browser-test; its companion inventory/plan own
Node/Playwright versions and the prepared dependency directory (v1 has no invented
nodeExecutable field). Missing prerequisites fail, never install or follow main.
The sole application wheel is built offline with the advertised build interpreter.
After a fresh --no-deps --no-index wheel install, dependency.pth appends ONLY the
prepared purelib directory. It does not call site.addsitedir or execute its .pth
files. This is locked development-dependency reuse, NOT a minimal-deps proof.

All evidence is under /artifacts/files1318-qualified. Maintained run_scenarios
owns processes, always cwd=root/frontend; one call per phase supplies fail-stop.
The copied fixture invokes its existing package_identity guard, then records
origins and verifies payload/static/RECORD in that SAME process before and after.
No selectors, environment overrides, retries, preparation or publication CLI.

--corner-diagnostic is a separate, source-only checkpoint, NOT qualification.
It uses /artifacts/files1318-corner-diagnostic and the fixed retained inputs in
output/files1318-corner-inputs/{reuse.json,static.tar}. The schema-2 manifest must
also bind diagnosticReuse={metadataSha256,archiveSha256,sourceRevision,sourceTree}.
Only this driver and the generated-file producer may differ from the retained
8ca6ac6d manifest. No build, unit suite, full journey, wheel or install runs.
Exit 0 means checkpoint observed with cleanup/integrity verified; cause and
visual judgment remain unknown/pending. An original false assertion stays exit 1.

--contour-qualification uses /workspace/output/files1318-computed-round-manifest.json
and /artifacts/files1318-computed-round-qualified. This compatibility repair is
one separately authorized execution; all prior failed checks remain consumed.
It runs the full remaining flow,
but never reruns frontend-build/python-tests/frontend-units. Those three phases
are REUSED only after exact old source, 311 static files, 70 JUnit/collection
identities, 19 TAP identities, process cleanup and current dependency identities
are verified. The prior integrated run remains FAILED, not a qualification pass.
Bind contourReuse=REUSE_BINDING and reuseEvidence={relative_path: sha256} for
every REUSE_EVIDENCE_FILES entry (relative to REUSE_MANIFEST.parent). Raw old
proofs are copied unchanged to prior-evidence; no old proof is rewritten.

--touch-event-diagnostic uses /workspace/output/files1318-touch-event-manifest.json
and /artifacts/files1318-touch-event-diagnostic. Bind touchEventReuse=REUSE_BINDING
and the SAME 23 reuseEvidence files. The final producer SHA is checked below.
Reuse the old 70/19/311 proofs, normal runtime/browser readiness and topology;
run generated-source ONLY through the first 390px primary Download tap. No
build, unit suite, renderer journey, wheel or install runs. Raw exit 1 remains
failed even when its event pair was captured. Missing events/capture loss are
UNKNOWN, not a retry. Exit 0 means checkpoint observed, never product acceptance.
The private FILES1318_TOUCH_EVENT_DIAGNOSTIC flag keeps fixture child OUT aligned;
FILE_CONTROLS_TOUCH_EVENT_DIAGNOSTIC enables only the producer's passive recorder.

--coarse-toolbar-qualification uses /workspace/output/files1318-coarse-toolbar-manifest.json
and /artifacts/files1318-coarse-toolbar-qualified. This is ONE prospective 16th
check; 15 prior checks remain consumed. Bind coarseToolbarReuse=REUSE_BINDING
and the same 23 reuseEvidence files. ONLY the 70 Python and 19 Node tests may be
REUSED after exact source/dependency/runtime validation. The old 311-file build
is immutable before-context, never restored or qualified as current product.
A fresh canonical npm run build feeds all source, wheel and installed checks.
coarseToolbarReview binds the exact CSS/driver/producer identities and reviewed
14px external coarse-pointer separation below the visible saved-file toolbar.
coarseToolbarBefore=COARSE_TOOLBAR_BEFORE binds the raw failed 8ac observation,
not a newly invented geometry baseline. Private FILES1318_COARSE_TOOLBAR_QUALIFICATION
keeps children aligned; FILE_CONTROLS_COARSE_TOOLBAR_QUALIFICATION selects the
producer's full journey. The pinned coarse-toolbar-v1 interface is checked
independently against raw 8ac geometry, all six layout records, the single
trusted event sequence, exact download and retained running preview.

--native-state-diagnostic uses /workspace/output/files1318-native-state-manifest.json
and /artifacts/files1318-native-state-diagnostic. Bind nativeStateReuse=REUSE_BINDING
and the SAME 23 reuseEvidence files. This source-only checkpoint restores only
the verified pre-gap 8ca 311-file build: every tracked input except DRIVER and
PRODUCER must match that retained manifest, including the rolled-back CSS.
It preserves all 16 prior outcomes. No CSS candidate, build, units, installed
journey or retry. One bounded CDP DOM snapshot/current quads and gesture-window
trace accompany the original first-390 tap and passive events. A successful
gesture must pass the original byte/preservation checks, then stop. A wrong
target remains exit 1. Captured evidence is not a product or root-cause verdict.

--portal-host-experiment uses /workspace/output/files1318-portal-host-manifest.json
and /artifacts/files1318-portal-host-experiment. This distinct topology falsifier
preserves all 17 outcomes. Bind portalHostReuse=REUSE_BINDING, the same 23
reuseEvidence files, and portalHostBefore=PORTAL_HOST_BEFORE. Only 70 Python/19
Node tests may be reused after exact input/environment checks. A fresh canonical
frontend build is required; old static is before-context, never restored.
The original first-390 tap is admitted only after exact check-17 Download/Details
geometry and a real portal-host box agree. No CDP trace, extra gesture, full
journey, wheel or installed phase follows. A successful checkpoint supports
topology dependence, not candidate pruning or product acceptance.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib
import importlib.machinery
import importlib.metadata as metadata
import json
import math
import os
from pathlib import Path
import re
import runpy
import shlex
import shutil
import signal
import stat
import sys
import sysconfig
import tarfile
import time
import tomllib
import xml.etree.ElementTree as ET

# -I deliberately removes the script directory as well as PYTHONPATH. Restore
# only this frozen helper directory, never the checkout in an installed child.
sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path('/workspace')
QUALIFIED_OUT = Path('/artifacts/files1318-qualified')
OUT = QUALIFIED_OUT
PREPARED = Path('/opt/unified-prepared')
PYTHON = PREPARED / 'venv/bin/python'
BUILD_PYTHON = PREPARED / 'build-venv/bin/python'
NODE = PREPARED / 'node-runtime/bin/node'
NPM = PREPARED / 'node-runtime/bin/npm'
UV = PREPARED / 'bootstrap/bin/uv'
CONTRACT = PREPARED / 'execution-contract.json'
MANIFEST = ROOT / 'output/files1318-frozen-manifest.json'
CORNER_MANIFEST = ROOT / 'output/files1318-corner-manifest.json'
CONTOUR_MANIFEST = ROOT / 'output/files1318-computed-round-manifest.json'
TOUCH_EVENT_MANIFEST = ROOT / 'output/files1318-touch-event-manifest.json'
COARSE_TOOLBAR_MANIFEST = ROOT / 'output/files1318-coarse-toolbar-manifest.json'
NATIVE_STATE_MANIFEST = ROOT / 'output/files1318-native-state-manifest.json'
PORTAL_HOST_MANIFEST = ROOT / 'output/files1318-portal-host-manifest.json'
HARNESS = OUT / 'harness'
VENV = OUT / 'installed'
DRIVER = 'scripts/files1318_integrated_checks.py'
PRODUCER = 'frontend/tests/generated-file-controls-browser.mjs'
CORNER_OUT = Path('/artifacts/files1318-corner-diagnostic')
CONTOUR_OUT = Path('/artifacts/files1318-computed-round-qualified')
TOUCH_EVENT_OUT = Path('/artifacts/files1318-touch-event-diagnostic')
COARSE_TOOLBAR_OUT = Path('/artifacts/files1318-coarse-toolbar-qualified')
NATIVE_STATE_OUT = Path('/artifacts/files1318-native-state-diagnostic')
PORTAL_HOST_OUT = Path('/artifacts/files1318-portal-host-experiment')
PORTAL_HOST_PRODUCER_SHA = '31dc5d7895fea5223b4b7b32de767e384ebc923cb1c28da644c764af832827ae'
PORTAL_HOST_CSS_SHA = 'a3ca24cd47c7df134cc32d6f096dc071684dc438203ce4109f1fe0d21632d13b'
PORTAL_HOST_BEFORE_ROOT = ROOT / 'output/files1318-native-state-v1-result'
PORTAL_HOST_BEFORE = {
    'sourceRevision': '2c66c2c3be0fe9c33cfd04ccf0ded85ff1dc9cd4',
    'sourceTree': '2885cde3365d400d3a58d99e5fc6fb3ae84f592e',
    'files': {
        'parent-manifest.json': 'ea03e6a682224857404bc9d56f6cfcad4aa927d172123e3e43875e7a94440c00',
        'generated-source/evidence/generated-file-controls-gUIeI4/observations.json':
            'eedecc305886fd1d31b5a7c864876b95d4e4d2c0a9fab1145eb9a5106637bcce',
    },
}
NATIVE_STATE_PRODUCER_SHA = '447e829658c586a0c63bde131affe2dfff5858b6639e497ed3d64ca54cf14c21'
NATIVE_STATE_CSS_SHA = '82010a69d3fb1e27f5b5e6d35c5c2be9191006d52e804963e21fb3e58685dbb0'
COARSE_TOOLBAR_CSS = 'frontend/src/canvas-workspace.css'
COARSE_TOOLBAR_CHANGES = frozenset((DRIVER, PRODUCER, COARSE_TOOLBAR_CSS))
COARSE_TOOLBAR_REUSED_PHASES = ('python-tests', 'frontend-units')
# Reviewed producer/mitigation pair; a manifest cannot substitute different bytes.
COARSE_TOOLBAR_PRODUCER_SHA = 'f1e1abf17be39edec5841b74e59c1e1985083048699e5f117d0085aa2539f8bc'
COARSE_TOOLBAR_CSS_SHA = 'aa28e10156f52e0c692643a0a8830be850b91bc1e3d7a9ca246d96184e124b66'
COARSE_TOOLBAR_BEFORE_ROOT = ROOT / 'output/files1318-touch-event-v1-result'
COARSE_TOOLBAR_BEFORE = {
    'sourceRevision': '8ac98b80826b136432aa45e263bebd98e0a04407',
    'sourceTree': 'f1cb13af2c6d0281e589f7703ab1368d3e0835c3',
    'files': {
        'parent-manifest.json': '59301f84d07bc9f5d7a781676b1cb3422c49ebf2b47072cf807443e1b579c023',
        'generated-source/evidence/generated-file-controls-3W6LLV/observations.json':
            '365b1b690f2735f799186bd5170ce82d93b5014eb18c55e0f745af4a8bf8e8bb',
    },
}
# Same reviewed producer; the diagnostic flag is off for full qualification.
CONTOUR_PRODUCER_SHA = 'f7a8eb18f75ac6f7791cf81edf20861312b7b188404774efc18430f0189f104e'
TOUCH_EVENT_PRODUCER_SHA = 'f7a8eb18f75ac6f7791cf81edf20861312b7b188404774efc18430f0189f104e'
REUSE_INPUT = ROOT / 'output/files1318-corner-inputs'
REUSE_BINDING = {
    'metadataSha256': '56123cca65eb88bb8ddbcf4fc66e2eed47fb102bb6c7d663ed39cf8798d32b8e',
    'archiveSha256': '4eedcfb31fc612490c83641b04cedab8851f6d75cb3a4131042fd989e35695a7',
    'sourceRevision': '8ca6ac6dff5ca75c4ca9cc944c40ae04296b2bdf',
    'sourceTree': '5a2b4dfe20402463ec153579b4bf5985cad801f7',
}
REUSE_MANIFEST = ROOT / 'output/files1318-integrated-v1-result/parent-manifest.json'
REUSE_MANIFEST_SHA = 'b5c2e56b5e868a9b986eeb140f490ac85a6554c309f7cb8dd2d2930d1036eb79'
# run_scenarios hashes the ORIGINAL prepared contract, whereas save() serializes
# its parsed value into the retained execution-contract.json. They are not the
# same byte representation; keep both identities rather than conflating them.
REUSE_CONTRACT_SHA = '955658f45c5089592fde90dcfddf28fb08094c84198ee8f0487840c886ced916'
REUSE_ARCHIVE_BYTES = 23603200
REUSE_WORKSPACE_SHA = '5500763b6c47aa1ee3f6054c29e293fee30b86033e831a12dcac8f13fc0737c4'
REUSED_PHASES = ('frontend-build', 'python-tests', 'frontend-units')
REUSE_EVIDENCE_FILES = (
    'parent-manifest.json', 'report.json', 'evidence-manifest.json',
    'execution-contract.json', 'runtime-readiness.json', 'browser-readiness.json',
    'executables.json', 'prepared-inventory.json', 'prepared-plan.json',
    'candidate.json', 'static.json', 'python.xml', 'python-collection.json',
    'python-counts.json', 'pytest-call.json',
    'python-origins-before.json', 'python-origins-after.json',
    *(f'{phase}/{name}' for phase in REUSED_PHASES
      for name in ('results.json', f'{phase}.log')),
)
REVIEWED_CHANGES = frozenset((
    DRIVER, 'frontend/tests/canvas-renderers-browser.mjs',
    'tests/fixtures/empty_host_ui_server.py',
    'frontend/tests/generated-file-controls-browser.mjs',
    'frontend/tests/standard-ux-canvas-files-browser.mjs',
    'tests/fixtures/outputs_ui_server.py',
))
CLEAN = {'remaining': [], 'terminated': 0}
BUDGET, FINALIZATION_RESERVE, CLEANUP_RESERVE, RECEIPT_RESERVE = 1200, 60, 15, 5
PHASES = (
    'snapshot', 'runtime-readiness', 'build-readiness', 'browser-readiness',
    'frontend-build', 'source-freeze', 'python-tests', 'frontend-units',
    'topology-source', 'generated-source', 'canvas-renderers',
    'renderer-stalled-response', 'canvas-usability', 'standard-ux-canvas-files',
    'wheel-build', 'wheel-verify', 'fresh-venv', 'install-wheel',
    'dependency-link', 'installed-readiness', 'topology-installed',
    'generated-installed', 'final-integrity',
)
COPY = (
    DRIVER, 'scripts/cold_restore_checks.py',
    'frontend/tests/generated-file-controls-browser.mjs',
    'frontend/tests/file-controls-topology-preflight.mjs',
    'frontend/tests/file-controls-browser-topology.mjs',
    'frontend/tests/browser-chat-pages.mjs', 'frontend/tests/fixture-lifecycle.mjs',
    'tests/fixtures/generated_file_controls_server.py',
    'tests/fixtures/configured_browser.py',
)
DEPS = ('amplifier_core._engine', 'amplifier_foundation', 'pytest',
        'pytest_asyncio.plugin', 'pytest_aiohttp.plugin', 'aiohttp',
        'amplifier_module_loop_live.orchestrator', 'amplifier_module_context_simple')


def nodes(file, *names):
    return tuple('tests/' + file + '.py::test_' + name for name in names)


# Explicit collected identities, including every parameter case. No -k filters.
PYTEST_NODES = (
    *nodes('test_canvas_paths',
        'copy_paths_retains_original_workspace_after_restart_and_deleted_file',
        'agent_copy_uses_shared_target_and_only_intended_client',
        'unknown_or_outside_workspace_does_not_invent_a_relative_path',
        'paths_use_current_owner_working_folder_without_reading_files',
        'paths_fallback_uses_owner_workspace_then_recorded_workspace_only_without_owner',
        *(f'relative_paths_refuse_lexical_escape_and_sibling_prefixes[{p}]' for p in
          ('/workspace/../outside.txt', '/workspace-old/file.txt', '/elsewhere/file.txt')),
        'generated_file_preview_uses_working_directory_and_keeps_boundary',
        'workspace_files_keeps_workspace_boundary_with_worktree',
        'reveal_binds_exact_view_and_owning_host_without_browser_path_guess'),
    *nodes('test_canvas_downloads',
        *(f'safe_source_filename[canvas{i}-{name.encode("unicode_escape").decode()}]'
          for i, name in enumerate((
              'Plan été.md', 'run.py', '.gitignore', 'bad___name.md', 'canvas.md',
              'Design notes.md', 'Design notes.md', 'Team report.html',
              '_unsafe_title.md', 'canvas.md', 'actual.md', 'canvas-3d.html', 'canvas.txt'))),
        'inspect_exact_metadata_preserves_history_and_never_loads_body',
        'inspect_historical_path_and_filename_do_not_inherit_latest_metadata',
        'inspect_historical_app_metadata_does_not_load_body_or_state',
        *(f'inspect_download_metadata_for_supported_saved_text[{kind}]' for kind in
          ('text', 'markdown', 'code', 'html', 'mermaid', 'dot', 'json', 'jsonl', 'babylon')),
        *(f'inspect_does_not_offer_unsupported_downloads[{kind}]' for kind in
          ('browser', 'settings', 'mcp-app', 'gif', 'a2ui', 'image', 'pdf', 'canvas-app', 'unknown')),
        'inspect_missing_version_and_other_chat_refuse_before_exposing_metadata',
        'public_inspect_keeps_preview_selection_and_history_unchanged',
        'agent_inspect_cannot_read_another_chats_download_metadata',
        'both_download_actions_keep_original_name_and_snapshot', 'name_survives_restart',
        'image_download_is_exact_binary_version',
        'http_download_name_and_body_survive_source_deletion',
        'inspected_download_survives_later_revision_and_source_deletion[False]',
        'inspected_download_survives_later_revision_and_source_deletion[True]',
        'exact_download_and_source_urls_do_not_follow_later_revisions',
        'source_and_download_preserve_small_large_small_file_versions'),
    *nodes('test_worktrees',
        *(f'canvas_file_capture_follows_execution_while_files_keep_workspace[{absolute}-{origin}]'
          for absolute in ('False', 'True') for origin in ('ui', 'agent'))),
    *nodes('test_canvas_files',
        'exact_file_preview_refuses_changed_bytes_without_replacing_view',
        'exact_peer_artifact_uses_retained_execution_folder[False]',
        'exact_peer_artifact_uses_retained_execution_folder[True]',
        'legacy_peer_file_rejects_moved_producer_before_path_read',
        'legacy_file_open_does_not_change_browser_presentation',
        'missing_escape_and_symlink_do_not_replace_canvas',
        'named_file_preview_missing_dirty_and_changed_chat_do_not_navigate',
        'dirty_view_guard_and_agent_scope'),
    *nodes('test_artifact_presentation',
        'show_inline_preserves_unrelated_dirty_canvas_and_draft',
        'canvas_transition_refuses_dirty_inline_before_changing_selection'),
)
UNIT_FILES = ('frontend/tests/clipboard-paste.test.mjs', 'tests/block-copy.test.mjs',
              'frontend/tests/artifact-placement.test.mjs')


def require(value, message):
    if not value:
        raise RuntimeError(message)


def select_output(folder):
    """Keep the main process and every private child on the same fixed paths."""
    global OUT, HARNESS, VENV
    require(folder in (QUALIFIED_OUT, CORNER_OUT, CONTOUR_OUT, TOUCH_EVENT_OUT, COARSE_TOOLBAR_OUT,
                      NATIVE_STATE_OUT, PORTAL_HOST_OUT),
            'Unknown evidence destination')
    OUT = folder
    HARNESS, VENV = OUT / 'harness', OUT / 'installed'


def read(path):
    return json.loads(Path(path).read_text())


def save(name, value):
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    temporary.replace(path)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def file_identity(path):
    mode = path.lstat().st_mode
    if stat.S_ISLNK(mode):
        return {'mode': '120000', 'sha256': hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest()}
    require(stat.S_ISREG(mode), f'Not an ordinary file: {path}')
    return {'mode': '100755' if mode & 0o111 else '100644', 'sha256': sha(path)}


def tree(folder):
    from cold_restore_checks import manifest
    return manifest(folder)


def frozen_inputs(frozen, *, built=False):
    actual = {}
    for name, expected in frozen['files'].items():
        path = ROOT / name
        require(not Path(name).is_absolute() and '..' not in Path(name).parts
                and str(Path(name)) == name
                and path not in (MANIFEST, CORNER_MANIFEST, CONTOUR_MANIFEST, TOUCH_EVENT_MANIFEST,
                                 COARSE_TOOLBAR_MANIFEST, NATIVE_STATE_MANIFEST, PORTAL_HOST_MANIFEST), 'Unsafe frozen path')
        require(path.parent.resolve().is_relative_to(ROOT), 'Frozen path escapes snapshot')
        require(expected['mode'] in ('100644', '100755', '120000')
                and re.fullmatch('[a-f0-9]{64}', expected['sha256']), 'Invalid frozen identity')
        if built and name.startswith('amplifier_web/static/'):
            continue  # Canonical npm build replaces this entire generated directory.
        actual[name] = file_identity(path)
        require(actual[name] == expected, f'Frozen input changed: {name}')
    return actual


def diagnostic_static(frozen, *, binding='diagnosticReuse'):
    """Validate old static; coarse-toolbar keeps it as context WITHOUT restoring."""
    require(ROOT == Path('/workspace') and Path.cwd().resolve() == ROOT
            and Path(__file__).resolve() == ROOT / DRIVER and not (ROOT / '.git').exists(),
            'Static reuse is guest-only')
    unit_only = binding in ('coarseToolbarReuse', 'portalHostReuse')
    require(not unit_only or OUT in (COARSE_TOOLBAR_OUT, PORTAL_HOST_OUT), 'Unexpected unit-only reuse mode')
    require(binding in ('diagnosticReuse', 'contourReuse', 'touchEventReuse', 'coarseToolbarReuse', 'nativeStateReuse',
                        'portalHostReuse')
            and frozen.get(binding) == REUSE_BINDING, 'Missing exact static reuse binding')
    archive, metadata_path = REUSE_INPUT / 'static.tar', REUSE_INPUT / 'reuse.json'
    for path, digest in ((archive, REUSE_BINDING['archiveSha256']),
                         (metadata_path, REUSE_BINDING['metadataSha256']),
                         (REUSE_MANIFEST, REUSE_MANIFEST_SHA)):
        require(path.resolve() == path and stat.S_ISREG(path.lstat().st_mode)
                and sha(path) == digest, f'Reuse input identity mismatch: {path}')
    reuse, previous = read(metadata_path), read(REUSE_MANIFEST)
    require(reuse['schemaVersion'] == 1 and reuse['archiveSha256'] == REUSE_BINDING['archiveSha256']
            and reuse['archiveBytes'] == archive.stat().st_size == REUSE_ARCHIVE_BYTES
            and reuse['sourceWorkspaceArchiveSha256'] == REUSE_WORKSPACE_SHA
            and reuse['build'] == {'version': '0.41.0', 'id': '3e60a9007f094b31'}
            and reuse['sourceRevision'] == previous['revision'] == REUSE_BINDING['sourceRevision']
            and reuse['sourceTree'] == previous['tree'] == REUSE_BINDING['sourceTree'],
            'Wrong retained build provenance')
    require(previous['schemaVersion'] == 2 and previous['reviewed'] is True
            and all(previous[key] == frozen[key] for key in ('baselineRevision', 'baselineTree'))
            and set(previous['changedPaths']) == REVIEWED_CHANGES
            and len(previous['files']) == len(frozen['files']) == 3187
            and previous['files'].keys() == frozen['files'].keys(), 'Old/new source inventory mismatch')
    changed = {name for name in frozen['files'] if previous['files'][name] != frozen['files'][name]}
    require(changed == (COARSE_TOOLBAR_CHANGES if unit_only else {DRIVER, PRODUCER}),
            'Reuse requires all other tracked inputs unchanged')
    expected = reuse['files']
    require(isinstance(expected, dict) and len(expected) == 311
            and 'amplifier_web/static/index.html' in expected, 'Wrong retained static inventory')
    for name, digest in expected.items():
        require(name.startswith('amplifier_web/static/') and str(Path(name)) == name
                and not Path(name).is_absolute() and '..' not in Path(name).parts
                and '\\' not in name and re.fullmatch('[a-f0-9]{64}', digest),
                'Unsafe retained static path/hash')
    # Every member must be a unique ordinary file, with exactly the retained
    # canonical path set and content hashes. No extractall, links or path repair.
    with tarfile.open(archive, 'r:') as bundle:
        members = {}
        for member in bundle:
            require(member.isfile() and not member.issparse() and member.name in expected
                    and member.name not in members and 0 <= member.size <= REUSE_ARCHIVE_BYTES,
                    'Unexpected/duplicate/nonregular static archive member')
            with bundle.extractfile(member) as stream:
                require(hashlib.file_digest(stream, 'sha256').hexdigest() == expected[member.name],
                        f'Retained static bytes differ: {member.name}')
            members[member.name] = member
        require(members.keys() == expected.keys(), 'Static archive file set differs')
        require(all(parent.as_posix() not in members for name in members for parent in Path(name).parents),
                'Static archive file/directory collision')
        if unit_only:
            # Validate the old build as historical context, not candidate product.
            # No copy into /workspace, no extraction, no REUSED build status.
            save('reuse-metadata.json', reuse)
            save('reuse-parent-manifest.json', previous)
            save('reuse-validation.json', {
                **REUSE_BINDING, 'parentManifestSha256': REUSE_MANIFEST_SHA,
                'sourceWorkspaceArchiveSha256': REUSE_WORKSPACE_SHA,
                'changedPaths': sorted(changed), 'priorStaticFiles': len(members),
                'archiveBytes': REUSE_ARCHIVE_BYTES, 'staticRestored': False,
                'buildReusable': False, 'purpose': 'immutable_before_context_only'})
            return
        static = ROOT / 'amplifier_web/static'
        require(static.resolve() == static and static.is_dir(), 'Unsafe source static directory')
        require(all(not path.is_symlink() and (path.is_dir() or path.is_file())
                    for path in static.rglob('*')), 'Unexpected original static entry')
        before = tree(static)
        save('static-before.json', before)
        shutil.copytree(static, OUT / 'static-before')
        require(tree(OUT / 'static-before') == before, 'Original static preservation failed')
        save('reuse-metadata.json', reuse)
        save('reuse-parent-manifest.json', previous)
        save('reuse-validation.json', {
            **REUSE_BINDING, 'parentManifestSha256': REUSE_MANIFEST_SHA,
            'sourceWorkspaceArchiveSha256': REUSE_WORKSPACE_SHA,
            'changedPaths': sorted(changed), 'fileCount': len(members),
            'archiveBytes': REUSE_ARCHIVE_BYTES, 'validatedBeforeRestore': True})
        shutil.rmtree(static)  # Only the guest directory, after verified preservation.
        static.mkdir()
        for name, member in members.items():
            destination = ROOT / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            require(destination.parent.resolve().is_relative_to(static), 'Static destination escape')
            with bundle.extractfile(member) as source, destination.open('xb') as target:
                shutil.copyfileobj(source, target)
            destination.chmod(0o644)  # Never apply archived permissions/owners.
        require({f'amplifier_web/static/{name}': digest for name, digest in tree(static).items()} == expected,
                'Restored static differs from retained build')
    require(sha(archive) == REUSE_BINDING['archiveSha256'], 'Reuse archive changed during restore')


def retained_proofs(frozen, *, unit_only=False):
    """Read/copy old proofs only; never collect or execute either test suite."""
    require(not unit_only or OUT in (COARSE_TOOLBAR_OUT, PORTAL_HOST_OUT), 'Unexpected unit-only proof reuse mode')
    bindings = frozen.get('reuseEvidence')
    require(isinstance(bindings, dict) and set(bindings) == set(REUSE_EVIDENCE_FILES),
            'Final manifest must bind the exact retained proof inventory')
    prior = OUT / 'prior-evidence'
    prior.mkdir()
    for name in REUSE_EVIDENCE_FILES:
        source = REUSE_MANIFEST.parent / name
        digest = bindings[name]
        require(isinstance(digest, str) and re.fullmatch('[a-f0-9]{64}', digest)
                and source.resolve() == source and stat.S_ISREG(source.lstat().st_mode)
                and sha(source) == digest, f'Retained proof identity mismatch: {name}')
        target = prior / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        require(sha(target) == digest, f'Retained proof copy mismatch: {name}')
    require(bindings['parent-manifest.json'] == REUSE_MANIFEST_SHA, 'Wrong old parent manifest')
    old = read(prior / 'report.json')
    evidence = read(prior / 'evidence-manifest.json')
    require(old['schemaVersion'] == 1 and old['passed'] is False and old['complete'] is False
            and old['finalization'] == {'status': 'passed'}
            and old['manifestSha256'] == REUSE_MANIFEST_SHA
            and old['evidenceManifestSha256'] == bindings['evidence-manifest.json']
            and old['phases']['generated-source']['status'] == 'failed',
            'Expected retained partial run with later generated-source failure, not a prior full pass')
    for name, digest in bindings.items():
        if name not in ('report.json', 'evidence-manifest.json'):
            require(evidence[name] == digest, f'Old evidence manifest does not bind {name}')
    previous = read(prior / 'parent-manifest.json')
    contract = read(prior / 'execution-contract.json')
    require(previous['revision'] == contract['source']['sourceHead'] == REUSE_BINDING['sourceRevision']
            and previous['tree'] == REUSE_BINDING['sourceTree'], 'Retained run source mismatch')
    commands = {
        'frontend-build': [str(NPM), 'run', 'build'],
        'python-tests': [str(PYTHON), '-I', '-B', str(ROOT / DRIVER), '--python-tests'],
        'frontend-units': [str(NODE), '--test', '--test-reporter=tap',
                           *(str(ROOT / name) for name in UNIT_FILES)],
    }
    provenance = {
        'manifestSha256': REUSE_MANIFEST_SHA, 'revision': previous['revision'], 'tree': previous['tree'],
        'baselineRevision': previous['baselineRevision'], 'baselineTree': previous['baselineTree'],
        'contractSha256': REUSE_CONTRACT_SHA,
    }
    for phase, command in commands.items():
        row = old['phases'][phase]
        result = read(prior / phase / 'results.json')
        raw, = result['scenarios']
        require(result['schemaVersion'] == 1 and result['source'] == provenance
                and result['complete'] is True and result['passed'] is True
                and row['status'] == row['verdict'] == raw['status'] == 'passed'
                and row['reason'] is None and raw['reason'] is None
                and raw['id'] == phase and raw['log'] == f'{phase}.log'
                and raw['cleanup'] == CLEAN and type(raw['exitCode']) is int and raw['exitCode'] == 0
                and row['process'] == raw and row['command'] == raw['command'] == command
                and row['cwd'] == str(ROOT / 'frontend') and row['env'] == raw['env'],
                f'Old {phase} process/pass/cleanup proof differs')
    require(read(prior / 'pytest-call.json') ==
            {'call': 'pytest.main', 'args': pytest_arguments(QUALIFIED_OUT)},
            'Old pytest command/selection differs')
    counts = junit(prior)
    require(len(PYTEST_NODES) == 70 and counts == read(prior / 'python-counts.json'),
            'Retained Python count mismatch')
    units = unit_proof(prior)
    require(read(prior / 'candidate.json')['static'] == read(prior / 'static.json')
            == read(REUSE_INPUT / 'reuse.json')['files'], 'Old build/static proof mismatch')
    retained_environment(prior)
    result = {
        'sourceRevision': previous['revision'], 'sourceTree': previous['tree'],
        'priorIntegratedPassed': False, 'priorIntegratedComplete': False,
        'proofFiles': bindings, 'python': counts, 'frontendUnits': units,
        'staticFiles': 311, 'processCleanup': CLEAN,
        'status': 'verified_prior_evidence_only_pending_static_source_validation',
    }
    if unit_only:
        result.pop('staticFiles')
        result.update(priorStaticFiles=311, buildReusable=False,
                      reusablePhases=list(COARSE_TOOLBAR_REUSED_PHASES))
    save('reuse-evidence-validation.json', result)
    return result


def retained_environment(prior):
    """Do not reuse a pass across a changed runtime or resolved dependency graph."""
    old, current = read(prior / 'runtime-readiness.json'), read(OUT / 'runtime-readiness.json')
    require(set(old) == set(current) == {'python', 'executable', 'purelib', 'modules', 'distributions'}
            and old == current, 'Runtime/version/module bytes/dependency version or direct-source graph changed')
    require(read(prior / 'browser-readiness.json') == read(OUT / 'browser-readiness.json'),
            'Node/Playwright/browser readiness identity changed')
    before, after = read(prior / 'executables.json'), read(OUT / 'executables.json')
    require(before == after, 'Prepared executable bytes/locations changed')
    before, after = read(prior / 'prepared-inventory.json'), read(OUT / 'prepared-inventory.json')
    keys = ('profileId', 'python', 'pythonPackages', 'node', 'lockfiles')
    require(all(before[key] == after[key] for key in keys)
            and before['preparedFiles']['node'] == after['preparedFiles']['node'],
            'Prepared dependency inventory or locked Node tree changed')
    before, after = read(prior / 'prepared-plan.json'), read(OUT / 'prepared-plan.json')
    keys = ('profileId', 'baseImage', 'lockfiles', 'nodeDirectory', 'nodeKey',
            'playwrightVersion', 'pythonProject')
    require(all(before[key] == after[key] for key in keys), 'Prepared dependency inputs changed')
    save('reuse-environment-validation.json', {
        'runtimeExact': True, 'dependencyVersionsAndDirectSourcesExact': True,
        'preparedExecutablesExact': True, 'preparedNodeTreeExact': True,
        'browserReadinessExact': True, 'planInputsExact': True,
        'oldRuntimeSha256': sha(prior / 'runtime-readiness.json'),
        'currentRuntimeSha256': sha(OUT / 'runtime-readiness.json'),
        'distributionCount': len(current['distributions']),
    })


def portal_host_inputs(frozen):
    """Derive immutable geometry from failed check 17; never repair its report."""
    require(OUT == PORTAL_HOST_OUT and frozen.get('portalHostReuse') == REUSE_BINDING
            and frozen.get('portalHostBefore') == PORTAL_HOST_BEFORE
            and frozen['files'][PRODUCER]['sha256'] == PORTAL_HOST_PRODUCER_SHA
            and frozen['files'][COARSE_TOOLBAR_CSS]['sha256'] == PORTAL_HOST_CSS_SHA,
            'Missing exact portal-host source/before bindings')
    require(set(frozen['changedPaths']) == REVIEWED_CHANGES | {COARSE_TOOLBAR_CSS},
            'Unexpected baseline-to-portal-host changes')
    for name, digest in PORTAL_HOST_BEFORE['files'].items():
        source, destination = PORTAL_HOST_BEFORE_ROOT / name, OUT / 'before-evidence' / name
        require(source.resolve() == source and source.is_file() and sha(source) == digest,
                'Check-17 before evidence changed')
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        require(sha(destination) == digest, 'Check-17 before copy differs')
    previous = read(OUT / 'before-evidence/parent-manifest.json')
    require(previous['revision'] == PORTAL_HOST_BEFORE['sourceRevision']
            and previous['tree'] == PORTAL_HOST_BEFORE['sourceTree']
            and previous['files'].keys() == frozen['files'].keys()
            and {name for name in frozen['files'] if previous['files'][name] != frozen['files'][name]}
                == COARSE_TOOLBAR_CHANGES, 'Only the portal CSS/producer/driver may differ from check 17')
    name, = [name for name in PORTAL_HOST_BEFORE['files'] if name.endswith('/observations.json')]
    proof = read(OUT / 'before-evidence' / name)
    gesture = proof['touchEventDiagnostic']
    require(proof['passed'] is False and proof['mode'] == 'native-state-diagnostic'
            and gesture['tapAttempts'] == 1 and gesture['delivered']['event']['sameNode'] is False,
            'Require original failed check-17 gesture')
    host, = [row for row in gesture['nativeState']['candidates']
             if row.get('attributes', {}).get('class') == 'a-canvas-primary-actions']
    require(host['isClickable'] is True and host['layout'] == [] and host['backendNodeId'] == 102,
            'Check-17 portal-host topology differs')
    fields = ('rect', 'radii', 'borders', 'viewport', 'transforms', 'cornerShapes', 'computed')
    def geometry(label):
        row, = [row for row in proof['geometry']
                if row.get('label') == 'touch-390-preview-primary-before' and row['name'] == label]
        return {**{key: row[key] for key in fields},
                'scroll': {'x': row['scroll']['x'], 'y': row['scroll']['y'],
                           'ancestors': [{key: a[key] for key in ('x', 'y')} for a in row['scroll']['ancestors']]}}
    pointer, = [event for event in gesture['capture']['events'] if event['type'] == 'pointerdown']
    body, = [node for node in pointer['ancestors'] if node['className'] == 'a-canvas-body']
    roots = {row['kind']: row for row in gesture['nativeState']['roots']}
    copy_quad, download_quad = host['quads']
    require(copy_quad == [244, 207.5, 288, 207.5, 288, 251.5, 244, 251.5]
            and download_quad == [292, 207.5, 336, 207.5, 336, 251.5, 292, 251.5],
            'Check-17 Copy/Download quads differ')
    baseline = {
        'source': PORTAL_HOST_BEFORE, 'oldHost': host,
        'download': geometry('Download canvas source'), 'details': geometry('Canvas details'),
        'toolbarRect': roots['toolbar']['rect'], 'bodyRect': body['rect'],
        'copyRect': {'x': 244, 'y': 207.5, 'left': 244, 'top': 207.5, 'right': 288, 'bottom': 251.5,
                     'width': 44, 'height': 44},
        'tap': {key: gesture['before'][key] for key in ('rect', 'radii', 'borders', 'viewport', 'intended', 'position')},
        'html': {'bytes': proof['seed']['artifacts']['html']['byteLength'],
                 'sha256': proof['seed']['artifacts']['html']['sha256']},
    }
    save('portal-host-baseline.json', baseline)


def coarse_toolbar_inputs(frozen):
    """Bind the one reviewed product exception and immutable raw 8ac baseline."""
    require(OUT == COARSE_TOOLBAR_OUT and frozen.get('coarseToolbarReuse') == REUSE_BINDING
            and frozen.get('coarseToolbarBefore') == COARSE_TOOLBAR_BEFORE,
            'Missing exact coarse-toolbar reuse/before binding')
    require(set(frozen['changedPaths']) == REVIEWED_CHANGES | {COARSE_TOOLBAR_CSS},
            'Coarse-toolbar candidate must have the exact baseline-to-candidate paths')
    require(frozen['files'][COARSE_TOOLBAR_CSS] == {'mode': '100644', 'sha256': COARSE_TOOLBAR_CSS_SHA}
            and frozen['files'][PRODUCER] == {'mode': '100644', 'sha256': COARSE_TOOLBAR_PRODUCER_SHA},
            'Coarse-toolbar reviewed CSS/producer identity changed')
    review = {
        'reviewed': True,
        'baseRevision': COARSE_TOOLBAR_BEFORE['sourceRevision'],
        'baseTree': COARSE_TOOLBAR_BEFORE['sourceTree'],
        'changedPaths': sorted(COARSE_TOOLBAR_CHANGES),
        'files': {name: frozen['files'][name] for name in sorted(COARSE_TOOLBAR_CHANGES)},
        'productChange': '14px external gap below visible saved-file toolbar to whole following body on coarse pointer',
    }
    require(frozen.get('coarseToolbarReview') == review, 'Missing exact changed-file review attestation')
    prior = OUT / 'before-evidence'
    prior.mkdir()
    for name, digest in COARSE_TOOLBAR_BEFORE['files'].items():
        source, target = COARSE_TOOLBAR_BEFORE_ROOT / name, prior / name
        require(source.resolve() == source and stat.S_ISREG(source.lstat().st_mode)
                and sha(source) == digest, f'Raw 8ac baseline identity mismatch: {name}')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        require(sha(target) == digest, f'Raw 8ac baseline copy mismatch: {name}')
    previous = read(prior / 'parent-manifest.json')
    require(previous['schemaVersion'] == 2 and previous['reviewed'] is True
            and previous['revision'] == review['baseRevision'] and previous['tree'] == review['baseTree']
            and all(previous[key] == frozen[key] for key in ('baselineRevision', 'baselineTree'))
            and previous['touchEventReuse'] == REUSE_BINDING
            and previous['files'][PRODUCER]['sha256'] == TOUCH_EVENT_PRODUCER_SHA
            and previous['files'].keys() == frozen['files'].keys()
            and {name for name in frozen['files'] if previous['files'][name] != frozen['files'][name]}
                == COARSE_TOOLBAR_CHANGES, 'Only the reviewed CSS/driver/producer may differ from 8ac')
    observation_name, = [name for name in COARSE_TOOLBAR_BEFORE['files'] if name.endswith('/observations.json')]
    proof = read(prior / observation_name)
    diagnostic = proof['touchEventDiagnostic']
    require(proof['mode'] == 'touch-event-diagnostic' and proof['passed'] is False
            and diagnostic['checkpoint'] == 'first-390-primary-download'
            and diagnostic['label'] == 'touch-390-preview' and diagnostic['tapAttempts'] == 1
            and diagnostic['originalOraclePassed'] is False
            and diagnostic['checkpointObserved'] is True, 'Wrong before gesture/failure baseline')
    before = diagnostic['before']
    contour_sample(before)
    require(before['name'] == 'Download canvas source' and before['corner'] == 'bottom-right'
            and before['viewport']['width'] == 390 and before['viewport']['height'] == 844
            and before['intended'] == {axis: before['corners'][3][axis] for axis in ('x', 'y')},
            'Raw before contour baseline differs')
    pointer, = [event for event in diagnostic['capture']['events'] if event['type'] == 'pointerdown']
    toolbar, = [node for node in pointer['atIntended']['elements']
                if 'a-canvas-artifact-toolbar' in (node.get('className') or '').split()]
    body, = [node for node in pointer['ancestors']
             if 'a-canvas-body' in (node.get('className') or '').split()]
    html = proof['seed']['artifacts']['html']
    require(html['byteLength'] == len(html['body'].encode()) == 426
            and html['sha256'] == hashlib.sha256(html['body'].encode()).hexdigest()
                == '93229c6d39952ff4ab7b89ec66ae6d92e49da7a0d4da4269b736b8430caabfb0',
            'Raw before HTML identity differs')
    baseline = {
        'source': {'head': previous['revision'], 'tree': previous['tree'],
                   'report': (COARSE_TOOLBAR_BEFORE_ROOT / observation_name).relative_to(ROOT).as_posix(),
                   'sha256': COARSE_TOOLBAR_BEFORE['files'][observation_name],
                   'checkpoint': diagnostic['checkpoint'], 'path': 'touchEventDiagnostic.before'},
        'before': {key: before[key] for key in ('rect', 'radii', 'borders', 'viewport', 'intended', 'position')},
        'bodyTop': body['rect']['top'], 'toolbarRect': toolbar['rect'],
        'html': {'bytes': html['byteLength'], 'sha256': html['sha256']},
    }
    save('coarse-toolbar-review.json', review)
    save('coarse-toolbar-before.json', {
        'reference': COARSE_TOOLBAR_BEFORE, 'observationPath': str(prior / observation_name),
        'browser': diagnostic['browser'], 'options': diagnostic['options'],
        'before': before, 'retained': diagnostic['retained'],
        'baseline': baseline, 'html': html,
        'originalOraclePassed': False, 'productAcceptance': False,
    })


def coarse_toolbar_layout(layout, *, coarse, width):
    """Recompute separation/clipping from raw geometry, never its passed flag."""
    def rect(value):
        require(all(type(value[key]) in (int, float) and math.isfinite(value[key])
                    for key in ('x', 'y', 'left', 'right', 'top', 'bottom', 'width', 'height'))
                and value['x'] == value['left'] and value['y'] == value['top']
                and value['right'] - value['left'] == value['width'] > 0
                and value['bottom'] - value['top'] == value['height'] > 0, 'Invalid toolbar layout rectangle')

    def contained(inner, outer):
        return (inner['left'] >= outer['left'] and inner['right'] <= outer['right']
                and inner['top'] >= outer['top'] and inner['bottom'] <= outer['bottom'])

    viewport, reader = layout['viewport'], layout['reader']
    require(layout['queries'] == {'coarse': coarse, 'fine': not coarse}
            and (coarse, width) in ((False, 1440), (True, 390), (True, 320))
            and viewport['width'] == width and viewport['height'] == {1440: 1000, 390: 844, 320: 740}[width]
            and viewport['scale'] == 1 and viewport['offsetLeft'] == viewport['offsetTop'] == 0
            and layout['adjacent'] is True and layout['panelMode'] == 'artifact' and layout['settings'] is False
            and reader['connected'] is True and 'a-canvas-reader' in reader['className'].split()
            and reader['computed']['display'] == 'flex' and reader['computed']['flexDirection'] == 'column',
            'Wrong fine/coarse reader/layout context')
    rect(reader['rect'])
    toolbar, body, button = (layout[key] for key in ('toolbar', 'body', 'button'))
    require('a-canvas-artifact-toolbar' in toolbar['className'].split()
            and 'a-canvas-body' in body['className'].split() and button['tag'] == 'BUTTON'
            and len({box['nodeId'] for box in (toolbar, body, button, reader)}) == 4,
            'Toolbar/body/button identity is not distinct')
    for box in (toolbar, body, button):
        rect(box['rect'])
        require(type(box['nodeId']) is int and box['nodeId'] > 0
                and box['connected'] is True and box['visible'] is True and box['hidden'] is False
                and contained(box['rect'], {'left': 0, 'top': 0, 'right': width, 'bottom': viewport['height']})
                and contained(box['rect'], reader['rect'])
                and type(box['scrollWidth']) is int and type(box['clientWidth']) is int
                and 0 < box['clientWidth'] >= box['scrollWidth']
                and box['computed']['pointerEvents'] != 'none' and box['computed']['touchAction'] != 'none',
                'Toolbar/body/button clipping, overflow or hit suppression')
    margin = 14 if coarse else 0
    require(body['computed']['marginTop'] == f'{margin}px'
            and body['rect']['top'] == toolbar['rect']['bottom'] + margin
            and contained(button['rect'], toolbar['rect'])
            and type(layout['documentWidth']) is int and 0 < layout['documentWidth'] <= width
            and button['computed']['minWidth'] == button['computed']['minHeight'] == '44px'
            and button['rect']['width'] >= 44 and button['rect']['height'] >= 44
            and layout['radii'] == [button['computed'][key] for key in
                ('borderTopLeftRadius', 'borderTopRightRadius', 'borderBottomLeftRadius', 'borderBottomRightRadius')],
            'External separation/native button geometry differs')
    require([row['selector'] for row in layout['wrappers']] ==
            ['.a-canvas-primary-actions', '.a-canvas-common-actions', '.a-saved-file-download']
            and all(row['node']['connected'] is True and row['node']['computed']['display'] == 'contents'
                    for row in layout['wrappers']), 'Toolbar wrappers changed layout')
    notices = layout['notices']
    require(layout['noticesTruncated'] is False and 1 <= len(notices) <= 4
            and any(notice['visible'] is True for notice in notices), 'Missing visible saved-version notice')
    for notice in notices:
        if notice['visible'] is True:
            rect(notice['rect'])
            require(notice['connected'] is True and 'a-renderer-notice' in notice['className'].split()
                    and contained(notice['rect'], body['rect'])
                    and notice['computed']['userSelect'] != 'none'
                    and notice['computed']['pointerEvents'] != 'none', 'Notice clipping/selectability changed')
    require(layout['contactEnvelope'] == {
        'radiusDip': 16, 'radiusCssPx': 16, 'bottom': button['rect']['bottom'] + 16,
        'basis': 'Maximum 16 DIP contact radius; qualified only at DPR=1 and visual scale=1.'},
        'Contact envelope identity differs')
    if coarse:
        require(viewport['dpr'] == 1 and body['rect']['top'] > button['rect']['bottom'] + 16,
                'Whole following body intersects the contact envelope')


def coarse_toolbar_proof(proof, mode):
    """Independent raw-baseline, layout, native-event and saved-preview acceptance."""
    require(OUT == COARSE_TOOLBAR_OUT and mode in ('source', 'installed'), 'Wrong coarse-toolbar proof mode')
    prior = read(OUT / 'coarse-toolbar-before.json')
    qualification = proof['coarseToolbarQualification']
    baseline = prior['baseline']
    require(proof['mode'] == 'coarse-toolbar-qualification' and proof['qualificationComplete'] is True
            and qualification['version'] == 'coarse-toolbar-v1' and qualification['complete'] is True
            and qualification['baseline'] == baseline
            and qualification['separationCssPx'] == 14 and qualification['contactRadiusDip'] == 16,
            'Coarse-toolbar envelope/bound raw baseline differs')
    layouts = qualification['layoutChecks']
    expected_layouts = [(label, phase) for label in
                        ('desktop-preview', 'touch-390-preview', 'touch-320-preview') for phase in ('before', 'after')]
    require([(row['label'], row['phase']) for row in layouts] == expected_layouts
            and all(row['check'] == 'saved-file toolbar external separation and clipping'
                    and row['passed'] is True for row in layouts), 'Require the exact six layout records')
    for row in layouts:
        layout = row['snapshot']
        coarse = row['label'] != 'desktop-preview'
        width = 320 if row['label'] == 'touch-320-preview' else 390 if coarse else 1440
        coarse_toolbar_layout(layout, coarse=coarse, width=width)
        # Bind every layout to the existing independently checked contour sample.
        measured, = [sample for sample in proof['geometry']
                     if sample['label'] == row['label'] + '-primary-' + row['phase']
                     and sample['name'] == 'Download canvas source']
        require(layout['button']['nodeId'] == measured['nodeId']
                and layout['button']['rect'] == measured['rect'] and layout['radii'] == measured['radii'],
                'Layout does not describe the original measured control')
        if row['phase'] == 'before':
            require('feedback' not in row, 'Before-layout unexpectedly contains post-download feedback')
            continue
        feedback = row['feedback']
        recorded, = [sample for sample in proof['geometry'] if sample['label'] == row['label'] + '-visible-feedback']
        require(recorded == {'label': row['label'] + '-visible-feedback', **feedback}
                and feedback['bar'] == layout['toolbar']['rect'] and feedback['contained'] is True
                and feedback['lines'], 'Feedback not bound to the measured toolbar')
        box, bar = feedback['rect'], feedback['bar']
        require(box['width'] > 0 and box['height'] > 0 and box['left'] >= bar['left']
                and box['right'] <= min(bar['right'], width) and box['top'] >= bar['top']
                and box['bottom'] <= min(bar['bottom'], layout['viewport']['height'])
                and all(line['width'] > 0 and line['height'] > 0 and line['left'] >= box['left'] - 1
                        and line['right'] <= box['right'] + 1 and line['bottom'] <= box['bottom'] + 1
                        for line in feedback['lines']), 'Download feedback clipped/empty')
    gesture = qualification['gesture']
    require(gesture['qualification'] is True and gesture['checkpoint'] == 'first-390-primary-download'
            and gesture['label'] == 'touch-390-preview' and gesture['control'] == 'Download canvas source'
            and gesture['browser'] == prior['browser'] == read(OUT / 'browser-readiness.json')['browser']
            and gesture['options'] == prior['options'] and gesture['tapAttempts'] == 1
            and gesture['checkpointObserved'] is True and gesture['originalOraclePassed'] is True
            and gesture['outcome'] == 'qualified-gesture-download-and-preservation'
            and gesture['originalListenerRemoved'] is True and gesture['handleDisposed'] is True
            and gesture['captureErrors'] == gesture['cleanupErrors'] == []
            and not gesture.get('failure'), 'Wrong/incomplete first-390 gesture or cleanup')
    before, delivered = gesture['before'], gesture['delivered']
    for sample in (before, delivered):
        contour_sample(sample)
        require(all(sample[key] == value for key, value in baseline['before'].items())
                and sample['corner'] == 'bottom-right' and sample['name'] == 'Download canvas source',
                'Button geometry or intended point changed from raw 8ac failure')
    taps = [sample for sample in proof['geometry'] if sample['label'] == 'corner-tap-before']
    deliveries = [sample for sample in proof['geometry'] if sample['label'] == 'corner-tap-delivered']
    # The retained full journey has six card touches before this seventh tap.
    for raw, sample in ((before, taps[6]), (delivered, deliveries[6])):
        require(sample == {'label': sample['label'], **{key: value for key, value in raw.items() if key != 'corners'},
                           'acceptance': {'basis': raw['basis'], 'corners': raw['corners']}},
                'First-390 record does not match the seventh original journey touch')
    layout = gesture['beforeLayout']
    coarse_toolbar_layout(layout, coarse=True, width=390)
    require(layout['button']['nodeId'] == before['nodeId'] and layout['button']['rect'] == before['rect']
            and layout['radii'] == before['radii'] and layout['toolbar']['rect'] == baseline['toolbarRect']
            and layout['body']['rect']['top'] == baseline['bodyTop'] + 14
            and gesture['baselineCheck'] == {
                'check': 'first-390 baseline geometry and contour point unchanged', 'passed': True,
                'fields': ['rect', 'radii', 'borders', 'viewport', 'intended', 'position'],
                'bodyGap': layout['body']['rect']['top'] - before['rect']['bottom']}
            and gesture['baselineCheck']['bodyGap'] == 16.5, 'Before-layout/baseline discriminator differs')
    capture, node = gesture['capture'], before['nodeId']
    events = capture['events']
    types = ['pointerover', 'pointerenter', 'pointerdown', 'pointermove', 'pointerup', 'pointercancel',
             'pointerout', 'pointerleave', 'gotpointercapture', 'lostpointercapture',
             'touchstart', 'touchmove', 'touchend', 'touchcancel', 'mouseover', 'mouseenter', 'mousemove',
             'mousedown', 'mouseup', 'click', 'mouseout', 'mouseleave', 'auxclick', 'contextmenu']
    require(capture['limits'] == {'windowMs': 2000, 'records': 64, 'stack': 16, 'path': 24,
                                 'ancestors': 16, 'notices': 4, 'touches': 4, 'errors': 8}
            and capture['types'] == types and 5 <= len(events) <= 64 and capture['errors'] == []
            and capture['truncations'] == {key: 0 for key in
                ('records', 'stack', 'path', 'ancestors', 'notices', 'touches', 'errors')}
            and capture['deadline'] == capture['armedAt'] + 2000
            and capture['finished']['listenersRemoved'] is True and capture['finished']['timerCleared'] is True
            and capture['finished']['reason'] in ('deadline', 'deadline-observed')
            and all(type(value) in (int, float) and math.isfinite(value) for value in
                    (capture['armedAt'], capture['deadline'], capture['finished']['at']))
            and capture['finished']['at'] >= capture['armedAt'], 'Incomplete/lossy passive capture')
    def untruncated(value):
        if isinstance(value, dict):
            return all(not (key.endswith('Truncated') and item is not False) and untruncated(item)
                       for key, item in value.items())
        return not isinstance(value, list) or all(untruncated(item) for item in value)
    require(untruncated(events), 'Truncated nested event descriptor/layout')
    for index, event in enumerate(events):
        require(event['index'] == index and event['type'] in types and not event.get('captureError')
                and all(type(event[key]) in (int, float) and math.isfinite(event[key])
                        for key in ('at', 'wallTime', 'timeStamp'))
                and capture['armedAt'] <= event['at'] < capture['deadline']
                and event['at'] <= capture['finished']['at']
                and (index == 0 or event['at'] >= events[index - 1]['at'])
                and event['intended'] == before['intended'] and event['intendedButton']['nodeId'] == node
                and len(event['composedPath']) <= 24 and len(event['ancestors']) <= 16
                and len(event['notices']) <= 4 and len(event['capture']['queried']) <= 26
                and len(event.get('changedTouches') or []) <= 4
                and event['atIntended']['point'] == before['intended']
                and len(event['atIntended']['elements']) <= 16
                and (event['actual'] is None or (event['atActual']['point'] == event['actual']
                     and len(event['atActual']['elements']) <= 16)), 'Malformed synchronous event record')
    sequence = [event['type'] for event in events]
    kinds = ('pointerdown', 'pointerup', 'mousedown', 'mouseup', 'click')
    require(all(sequence.count(kind) == 1 for kind in kinds)
            and not any(kind in sequence for kind in ('pointercancel', 'touchcancel')),
            'Missing, repeated or cancelled gesture')
    pair = [events[sequence.index(kind)] for kind in kinds]
    require([event['index'] for event in pair] == sorted(event['index'] for event in pair)
            and gesture['sequence'] == sequence and gesture['pairPresence'] ==
                {'pointerdown': pair[0]['index'], 'firstMousedown': pair[2]['index']}
            and pair[0]['pointerType'] == pair[1]['pointerType'] == 'touch'
            and pair[2]['sourceCapabilities'] == {'firesTouchEvents': True},
            'Wrong gesture order/type/summary')
    for event in pair:
        actual = event['actual']
        require(event['isTrusted'] is True and event['sameNode'] is True
                and event['target']['nodeId'] == node and event['target']['tag'] == 'BUTTON'
                and event['target']['connected'] is True and event['target']['rect'] == before['rect']
                and event['intendedButton']['tag'] == 'BUTTON' and event['intendedButton']['connected'] is True
                and event['intendedButton']['rect'] == before['rect']
                and {'nodeId': node, 'tag': 'BUTTON'} in event['composedPath']
                and all(type(actual[axis]) in (int, float) and math.isfinite(actual[axis]) for axis in ('x', 'y'))
                and before['rect']['left'] <= actual['x'] < before['rect']['right']
                and before['rect']['top'] <= actual['y'] < before['rect']['bottom']
                and event['capture']['phase'] in (1, 2)
                and event['scroll'] == {'x': 0, 'y': 0}, 'Trusted event did not reach the original native button')
        event_layout = event['toolbarLayout']
        coarse_toolbar_layout(event_layout, coarse=True, width=390)
        require(event_layout['button']['nodeId'] == node and event_layout['button']['rect'] == before['rect']
                and event_layout['radii'] == before['radii'], 'Event-time button geometry changed')
    pointer, mouse, click = pair[0], pair[2], pair[4]
    require(pointer['actual'] == {axis: delivered['event'][axis] for axis in ('x', 'y')}
            and delivered['event']['sameNode'] is True
            and gesture['eventCheck'] == {
                'check': 'first-390 trusted pointerdown and click on original button', 'passed': True,
                'buttonNodeId': node, 'pointerdownIndex': pointer['index'], 'firstMousedownIndex': mouse['index'],
                'clickIndex': click['index'], 'actual': {name: event['actual'] for name, event in
                    (('pointerdown', pointer), ('mousedown', mouse), ('click', click))},
                'mouseDelta': {axis: mouse['actual'][axis] - pointer['actual'][axis] for axis in ('x', 'y')}},
            'Original pointer observation or independently computed event summary differs')
    retained, post = gesture['retained'], gesture['postGesture']
    setup, = [row for row in proof['checks'] if row['check'] ==
              'deliberate saved-v1 preview setup, not a download' and row['label'] == 'touch']
    journey, = [row for row in proof['checks'] if row['check'] ==
                'right-rail paths/download and primary Download preserve running saved-v1 HTML'
                and row['label'] == 'touch-390-preview']
    html = proof['seed']['artifacts']['html']
    require(all(html[key] == prior['html'][key] for key in ('body', 'byteLength', 'sha256', 'filename', 'version'))
            and all(retained[key] == setup[key] == journey[key] for key in ('marker', 'state', 'documents'))
            and journey['mode'] == 'touch' and retained['unfinished'] == journey['unfinished']
            and re.fullmatch(r'Unsubmitted touch note — [a-f0-9-]{36}', retained['unfinished'])
            and retained['marker']['count'] == 1 and retained['marker']['executed'] == '1'
            and re.fullmatch(r'\d+-\d+-\d+-\d+', retained['marker']['token']),
            'Fresh running HTML/input/token setup binding differs')
    state = retained['state']
    require(state['selected'] == proof['seed']['sessionId']
            and state['draft'] == 'Keep touch draft — unsubmitted.'
            and state['canvas'] == {'id': html['id'], 'version': 1, 'readOnly': True}
            and len(state['views']) == len(state['iframeSightings']) == len(retained['documents']) == 1
            and state['views'][0]['viewId'] == 'primary' and state['views'][0]['resourceId'] == html['id']
            and state['views'][0]['selectedVersion'] == 1 and state['views'][0]['generation'] > 0
            and re.fullmatch('[a-f0-9]{64}', state['views'][0]['resourceRevision'])
            and retained['documents'] == [request for request in proof['requests']
                                        if request['client'] == 'touch' and request['path'].endswith('/document')]
            and state['iframeSightings'] == [retained['documents'][0]['path'] + retained['documents'][0]['query']],
            'Selected saved-v1 lease/draft/document binding differs')
    download, = [row for row in proof['downloads'] if row['label'] == 'touch-390-preview-primary-v1']
    require(download == gesture['download'] == post['download']
            == {'label': 'touch-390-preview-primary-v1', 'filename': html['filename'], **baseline['html']}
            and post['check'] == 'first-390 exact saved HTML download and running preview preserved'
            and post['passed'] is True and post['preservation'] == {
                'iframeConnected': True, 'sameIframe': True, 'iframeCount': 1,
                'marker': retained['marker'], 'unfinished': retained['unfinished'], 'state': state,
                'documents': retained['documents'], 'prohibitedCommands': []},
            'Exact saved download or running-preview preservation differs')
    save(f'generated-{mode}/coarse-toolbar-validation.json', {
        'baseline': baseline, 'layoutRecords': 6, 'gestureChecks': 3, 'tapAttempts': 1,
        'eventIndices': {kind: event['index'] for kind, event in zip(kinds, pair)},
        'bodyGap': gesture['baselineCheck']['bodyGap'], 'download': download,
        'passed': True, 'visualJudgment': 'pending'})


def candidate(frozen):
    """All wheel-source bytes, including forced resources and example dist output."""
    build = tomllib.loads((ROOT / 'pyproject.toml').read_text())['tool']['hatch']['build']['targets']['wheel']
    files = {p: v['sha256'] for p, v in frozen['files'].items()
             if not p.startswith('amplifier_web/static/')}
    for folder in (*build['packages'], *build.get('force-include', {})):
        path = ROOT / folder
        if path.is_dir():
            files.update({folder + '/' + p: h for p, h in tree(path).items()})
        else:
            files[folder] = sha(path)
    require(all(p in frozen['files'] or p.startswith(('amplifier_web/static/',
                'examples/shell-reader/dist/')) for p in files), 'Unfrozen wheel-source input')
    return {'root': str(ROOT), 'files': files, 'packages': build['packages'],
            'static': {p: h for p, h in files.items() if p.startswith('amplifier_web/static/')}}


def origins(package_root):
    expected = read(OUT / 'candidate.json')
    installed = package_root != ROOT
    result = {}
    if installed:
        require(not os.environ.get('PYTHONPATH'), 'Installed fixture inherited PYTHONPATH')
        require(not Path.cwd().resolve().is_relative_to(ROOT), 'Installed cwd is source')
        require(all(not Path(p or '.').resolve().is_relative_to(ROOT) for p in sys.path),
                'Installed sys.path contains checkout')
    for name, module in tuple(sys.modules.items()):
        file = getattr(module, '__file__', None)
        if not file or not Path(file).is_file():
            continue
        path = Path(file).resolve()
        require(not installed or not path.is_relative_to(ROOT), f'Source import: {name}')
        if name.split('.')[0] in expected['packages']:
            relative = path.relative_to(package_root).as_posix()
            require(sha(path) == expected['files'][relative], f'Wrong product import: {name}')
        result[name] = {'path': str(path), 'sha256': sha(path)}
    return {'pid': os.getpid(), 'executable': sys.executable, 'prefix': sys.prefix,
            'cwd': str(Path.cwd()), 'sysPath': sys.path, 'modules': result}


def wheel_proof(package_root=None):
    from cold_restore_checks import verify_wheel
    value = read(OUT / 'candidate.json')
    result = verify_wheel(Path(value['wheel']), value, package_root)
    require(result['sha256'] == value['wheelSha256'], 'Wheel bytes changed')
    if package_root:
        record, = package_root.glob('amplifier_unified-*.dist-info/RECORD')
        result['installedRecordSha256'] = sha(record)
        result['installedStatic'] = tree(package_root / 'amplifier_web/static')
    return result


def runtime_readiness():
    require(sys.version_info >= (3, 13) and not sys.flags.optimize, 'Python >=3.13 without -O required')
    modules = {name: importlib.import_module(name) for name in DEPS}
    engine = Path(modules['amplifier_core._engine'].__file__).resolve()
    require(any(str(engine).endswith(s) for s in importlib.machinery.EXTENSION_SUFFIXES),
            'Core _engine must be the actual native extension')
    require(all(Path(module.__file__).resolve().is_relative_to(PREPARED)
                for module in modules.values()), 'Dependency import escaped prepared environment')
    import venv  # Availability only; no environment creation here.
    save('runtime-readiness.json', {
        'python': sys.version, 'executable': sys.executable,
        'purelib': sysconfig.get_path('purelib'),
        'modules': {name: {'path': str(module.__file__), 'sha256': sha(module.__file__)}
                    for name, module in modules.items()},
        'distributions': sorted([{'name': d.metadata['Name'], 'version': d.version,
                                  'direct': json.loads(d.read_text('direct_url.json') or 'null')}
                                 for d in metadata.distributions()], key=lambda d: d['name']),
    })


def pytest_process():
    sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
    import pytest
    import amplifier_web.server
    save('python-origins-before.json', origins(ROOT))

    class Collection:
        def pytest_collection_finish(self, session):
            collected = [item.nodeid for item in session.items]
            save('python-collection.json', collected)
            if Counter(collected) != Counter(PYTEST_NODES):
                raise pytest.UsageError('Files1318 exact collection mismatch')

    args = pytest_arguments(OUT)
    save('pytest-call.json', {'call': 'pytest.main', 'args': args})
    try:
        code = pytest.main(args, plugins=[Collection()])
    finally:
        save('python-origins-after.json', origins(ROOT))
    return int(code)


def pytest_arguments(folder):
    return ['-c', '/dev/null', '--rootdir=/workspace', '--import-mode=importlib',
            '-p', 'no:cacheprovider', '-p', 'pytest_asyncio.plugin', '-p', 'pytest_aiohttp.plugin',
            '--asyncio-mode=auto', '-x', '-v', '--tb=short',
            '--junitxml=' + str(folder / 'python.xml'), *[str(ROOT / n) for n in PYTEST_NODES]]


def fixture_process(mode):
    """Thin wrapper: run the unchanged maintained fixture, not a second server."""
    base = HARNESS if mode == 'installed' else ROOT
    fixture = base / 'tests/fixtures/generated_file_controls_server.py'
    sys.path.insert(0, str(fixture.parent))
    namespace = runpy.run_path(str(fixture))
    identity = namespace['package_identity']()  # Existing fail-closed guard.
    package_root = Path(identity['packageRoot']).parent
    import amplifier_web.server
    save(f'{mode}-fixture-origins-before.json', origins(package_root))
    if mode == 'installed':
        save('installed-wheel-before.json', wheel_proof(package_root))
    else:
        save('source-fixture-payload-before.json', {
            name: tree(ROOT / name) for name in read(OUT / 'candidate.json')['packages']})
    try:
        runpy.run_path(str(fixture), run_name='__main__')
    finally:
        save(f'{mode}-fixture-origins-after.json', origins(package_root))
        if mode == 'installed':
            save('installed-wheel-after.json', wheel_proof(package_root))
        else:
            save('source-fixture-payload-after.json', {
                name: tree(ROOT / name) for name in read(OUT / 'candidate.json')['packages']})


def installed_readiness():
    import amplifier_web.server
    package = Path(importlib.import_module('amplifier_web').__file__).resolve().parent
    require(package.parent == Path(sysconfig.get_path('purelib')), 'Not the fresh environment package')
    dist = metadata.distribution('amplifier-unified')
    require(Path(dist.locate_file('')).resolve() == package.parent, 'Wrong distribution origin')
    direct = json.loads(dist.read_text('direct_url.json'))
    require(direct['url'] == Path(read(OUT / 'candidate.json')['wheel']).as_uri()
            and not direct.get('dir_info', {}).get('editable'), 'Not the fresh noneditable wheel')
    for name in DEPS:
        importlib.import_module(name)
    save('installed-identity.json', {'packageRoot': str(package), 'direct': direct,
                                   'process': origins(package.parent),
                                   'wheel': wheel_proof(package.parent)})


def junit(folder=None):
    root = ET.parse((folder or OUT) / 'python.xml').getroot()
    cases = root.findall('.//testcase')
    counts = {'tests': len(cases), **{tag: len(root.findall('.//' + tag))
                                    for tag in ('failure', 'error', 'skipped')}}
    if folder is None:
        save('python-counts.json', counts)
    require(counts == {'tests': len(PYTEST_NODES), 'failure': 0, 'error': 0, 'skipped': 0},
            'JUnit must contain exactly the selection, with zero skip/error/fail')
    require(Counter(read((folder or OUT) / 'python-collection.json')) == Counter(PYTEST_NODES),
            'Collection changed')
    require(Counter(c.attrib['classname'].replace('.', '/') + '.py::' + c.attrib['name'] for c in cases)
            == Counter(PYTEST_NODES), 'JUnit full node identities differ')
    return counts


def unit_proof(folder=None):
    text = ((folder or OUT) / 'frontend-units/frontend-units.log').read_text()
    expected = []
    for name in UNIT_FILES:
        expected += re.findall(r"^test\('([^']+)',", (ROOT / name).read_text(), re.M)
    require(len(expected) == 19 and Counter(re.findall(r'^ok \d+ - (.+)$', text, re.M)) ==
            Counter(expected), 'Frontend exact test identities differ')
    for key in ('tests', 'pass', 'fail', 'cancelled', 'skipped', 'todo'):
        require(re.findall(rf'^# {key} (\d+)$', text, re.M) ==
                [str(19 if key in ('tests', 'pass') else 0)], f'Frontend {key} mismatch')
    require(not re.search(r'^not ok\b|^Bail out!', text, re.M), 'Frontend failure')
    return {'tests': 19, 'pass': 19, 'fail': 0, 'cancelled': 0, 'skipped': 0, 'todo': 0,
            'names': expected}


def topology_proof(mode):
    proof = read(OUT / f'topology-{mode}/evidence/result.json')
    require(proof['passed'] is True and proof['attempts'] == 1 and proof['noFallback'] is True
            and proof['serverClosed'] is True and not proof.get('error'), 'Topology did not pass')
    require(Counter(r['owner'] for r in proof['checks']) == Counter(('desktop', 'touch')), 'Missing topology')
    body = 'Topology download α\r\n'.encode()
    for row in proof['checks']:
        path = OUT / f'topology-{mode}/evidence' / (row['owner'] + '-download.txt')
        require(row['touch'] is (row['owner'] == 'touch') and row['browser']
                and row['clipboard'] == f"preflight-{row['owner']}-clipboard-α"
                and row['bytes'] == len(body) and path.read_bytes() == body
                and row['downloadSha256'] == sha(path), 'Topology evidence mismatch')
    require(proof['owners'] == [{'label': label, 'closed': True, 'connected': False}
                                for label in ('desktop', 'touch')], 'Topology did not close')


def file_frame_proof(proof, folder):
    """Bracket evidence only. Raw sibling/header occlusion is NOT visual clearance."""
    captures = proof['fileFrameCaptures']
    labels = [f'{base}-{suffix}' for base in ('touch-320', 'touch-320-preview-rail')
              for suffix in ('controls', 'paths', 'path-0', 'path-1', 'path-2', 'path-3')]
    require(Counter(c['label'] for c in captures) == Counter([*labels, 'touch-320-preview-primary']),
            'Require all 13 existing 320px PNG brackets, not empty/partial captures')
    for capture in captures:
        png, binding = capture['png'], capture['binding']
        path = Path(png['path'])
        require(path.parent.resolve() == folder.resolve() and not path.is_symlink()
                and path.name == capture['label'] + '.png', 'Capture path escaped evidence')
        data = path.read_bytes()
        require(data.startswith(b'\x89PNG\r\n\x1a\n') and len(data) == png['bytes']
                and sha(path) == png['sha256'] and int.from_bytes(data[16:20], 'big') == 320,
                'Capture PNG identity/width mismatch')
        before, after = capture['before']['state'], capture['after']['state']
        require(not capture.get('error') and capture['visualJudgment'] == 'pending'
                and binding['atomic'] is False and binding['basis']
                and before['viewport']['width'] == after['viewport']['width'] == 320
                and before['elements'] and after['elements'], 'Incomplete frame samples')
        def scroll(state):
            return (state['scroll']['x'], state['scroll']['y'],
                    [(c['nodeId'], c['x'], c['y']) for c in state['scroll']['containers']])
        equal = {
            'domIdentityUnchanged': before['anchorConnected'] and after['anchorConnected']
                and before['documentId'] == after['documentId'] and before['anchorId'] == after['anchorId']
                and [e['nodeId'] for e in before['elements']] == [e['nodeId'] for e in after['elements']],
            'viewportUnchanged': before['viewport'] == after['viewport'],
            'scrollUnchanged': scroll(before) == scroll(after), 'layoutUnchanged': before == after,
        }
        require(all(binding[key] is value for key, value in equal.items())
                and binding['status'] == ('matched-before-after-only' if all(equal.values()) else 'not-matched'),
                'Frame bracket status does not match retained samples')
        # Keep raw intersections, including offscreen siblings, regardless of status.
        require(all(isinstance(s[key], list) for s in (before, after)
                    for key in ('intersections', 'headerIntersections')), 'Occlusion evidence missing')


def generated_coverage(proof, folder):
    source = (ROOT / 'frontend/tests/generated-file-controls-browser.mjs').read_text()
    taps = [g for g in proof['geometry'] if g['label'] == 'corner-tap-before']
    require(len(taps) == 14, 'Missing corner-tap measurements')
    padding_count = sum(
        bool(g['borders']['left'] or g['borders']['top']) for g in taps)
    counts = Counter({
        'real clipboard + Copied.': 17,
        'contour formula and unsupported geometry logic checks': 1,
        'negative control: native size below 44px rejected': 2,
        'negative control: individual contour region rejected': 4,
        'negative control: real one-probe pointer overlay rejected by same contour oracle': 1,
        'computed corner-shape exact-value predicate checks': 1,
        'actual browser computed round accepted and noncircular corner rejected by same sampler': 1,
        'old padding-origin conversion rejected by same exact-contour oracle': padding_count,
        'deliberate saved-v1 preview setup, not a download': 2,
        'right-rail paths/download and primary Download preserve running saved-v1 HTML': 3,
        'negative control: missing buttons rejected by the same oracle': 1,
        'v1 browser download remains original after v2 is saved before URL resolution': 1,
        'clipboard refusal reports failure and leaves real clipboard unchanged': 1,
        '403 has visible feedback and zero downloads, then a legitimate click succeeds': 1,
        'negative control: real wrong-byte download rejected by the same SHA-256 oracle': 1,
        'reload after source deletion retains paths, exact saved v1 download and unrelated read-only Canvas/draft': 1,
        'adjacent Markdown stays distinct; direct saved-only has no paths; outside-current-base has no relative-copy control': 1,
        'open details refreshes current working folder and rejects delayed prior-placement metadata': 1,
        '390px/320px emulated touch, actual contour taps, 44px targets, four contour-relative native hit probes and saved PNGs': 1,
        'card-only phase completed with zero document requests and unrelated historical Canvas retained': 1,
        'negative controls: lost input and changed execution token rejected by the same retained-preview oracle': 1,
    })
    require(Counter(re.findall(r"checks\.push\(\{check:'([^']+)'", source)) ==
            Counter({name: 1 for name in counts}), 'Journey check definitions changed')
    require(Counter(c['check'] for c in proof['checks']) == counts, 'Journey check coverage/counts changed')
    contour_proof(proof, taps, padding_count)
    fresh = [r for r in proof['detailsReadiness'] if r.get('kind') != 'atomic-oracle']
    require(len(fresh) == 10 and len(proof['detailsReadiness']) == 23
            and all(r.get('passed') is True and len(r['matches']) == 1
                    and not any(r.get(k) for k in ('error', 'captureError', 'disposeError'))
                    for r in fresh), 'Fresh metadata/render readiness incomplete')
    require(proof['metadataExchanges'] and all(not r.get('error') and r.get('completedAt')
            and r['httpStatus'] == 200 and r['response']['accepted'] is True
            and r['target']['id'] == r['response']['id'] and r['target']['version'] == r['response']['version']
            for r in proof['metadataExchanges']), 'Metadata response failure')
    # Preserve the producer's strict positive geometry oracle; no occlusion waiver.
    controls = [g for g in proof['geometry'] if 'corners' in g]
    paths = [g for g in proof['geometry'] if g['label'].endswith('-path-text')]
    require(len(controls) == 36 and len(paths) == 24
            and all(g['rect']['width'] >= 44 and g['rect']['height'] >= 44 and len(g['corners']) == 4
                    and all(c['reachable'] is True for c in g['corners']) for g in controls)
            and all(g['lines'] and g['scrollWidth'] <= g['clientWidth']
                    and all(len(line['points']) == 3 and all(p['reachable'] is True for p in line['points'])
                            for line in g['lines']) for g in paths), 'Positive reachability/geometry failed')
    labels = ['desktop-race-v1', 'desktop-after-403', 'reload-deleted-source-v1',
              'touch-390-deleted-v1', 'touch-320-deleted-v1',
              *(f'{p}-{s}-v1' for p in ('desktop-preview', 'touch-390-preview', 'touch-320-preview')
                for s in ('primary', 'rail')), 'adjacent-markdown', 'saved-only', 'negative-wrong-bytes']
    require(Counter(d['label'] for d in proof['downloads']) == Counter(labels), 'Download coverage/counts')
    for download in proof['downloads']:
        row = proof['seed']['artifacts'][{'adjacent-markdown': 'adjacent', 'saved-only': 'saved'}.get(download['label'], 'html')]
        body = row['body'].replace('\r\n', '\n') if download['label'] == 'negative-wrong-bytes' else row['body']
        path = folder / (download['label'] + '-' + row['filename'])
        require(path.parent.resolve() == folder.resolve() and not path.is_symlink()
                and download['filename'] == row['filename'] and path.read_bytes() == body.encode()
                and download['bytes'] == path.stat().st_size and download['sha256'] == sha(path),
                'Download bytes/name/hash mismatch')
    require(Counter(r['client'] for r in proof['requests'] if r['path'].endswith('/document')) ==
            Counter(('desktop', 'touch')), 'Unexpected preview document requests')
    file_frame_proof(proof, folder)


def contour_sample(sample, *, reachable=(True, True, True, True)):
    """Independently bind the retained contour points; legacy misses are diagnostic."""
    labels = ('top-left', 'top-right', 'bottom-left', 'bottom-right')
    rect, viewport = sample['rect'], sample['viewport']
    finite = lambda value: type(value) in (int, float) and math.isfinite(value)
    require(sample['tag'] in ('BUTTON', 'SUMMARY')
            and type(sample['nodeId']) is int and sample['nodeId'] > 0
            and sample['basis'] == 'four contour-relative native hit probes'
            and all(finite(rect[key]) for key in ('left', 'top', 'right', 'bottom', 'width', 'height'))
            and rect['width'] >= 44 and rect['height'] >= 44
            and rect['right'] - rect['left'] == rect['width']
            and rect['bottom'] - rect['top'] == rect['height'], 'Unsupported native contour rectangle')
    require(all(finite(viewport[key]) and viewport[key] > 0 for key in ('width', 'height'))
            and viewport['scale'] == 1 and viewport['offsetLeft'] == viewport['offsetTop'] == 0
            and sample['transforms'] and all(
                all(style[key] == 'none' for key in
                    ('transform', 'perspective', 'translate', 'rotate', 'scale', 'offsetPath'))
                and style['transformStyle'] == 'flat' and style['zoom'] in ('1', 'normal')
                for style in sample['transforms']), 'Unsupported contour viewport/transform')
    shapes = sample['cornerShapes']
    require(isinstance(shapes, list) and len(shapes) == 4
            and [row['corner'] for row in shapes] == list(labels)
            and all(row['raw'] in ('', 'round', 'superellipse(1)')
                    and row['quoted'] == json.dumps(row['raw']) for row in shapes),
            'Unsupported or unrecorded computed circular corner shape')
    radii = []
    require(len(sample['radii']) == 4, 'Four contour radii required')
    for text in sample['radii']:
        parts = text.split()
        require(len(parts) in (1, 2)
                and all(re.fullmatch(r'(?:\d+(?:\.\d+)?|\.\d+)px', part) for part in parts),
                'Contour radius must be finite computed px')
        values = [float(part[:-2]) for part in parts]
        require(all(finite(value) and value >= 0 and value == values[0] for value in values),
                'Unsupported nonfinite/elliptical contour radius')
        radii.append(values[0])
    tl, tr, bl, br = radii
    require(tl + tr <= rect['width'] and bl + br <= rect['width']
            and tl + bl <= rect['height'] and tr + br <= rect['height'],
            'Overlapping radii require unsupported used-radius scaling')
    borders = sample['borders']
    require(set(borders) == {'left', 'top', 'right', 'bottom'}
            and all(finite(value) and value >= 0 for value in borders.values())
            and borders['left'] + borders['right'] < rect['width']
            and borders['top'] + borders['bottom'] < rect['height'], 'Unsupported contour borders')
    require(len(sample['corners']) == len(sample['legacyCorners']) == 4, 'Four contour/legacy points required')
    for index, (label, radius, point, legacy) in enumerate(zip(
            labels, radii, sample['corners'], sample['legacyCorners'])):
        inward = max(3, radius - (radius - 3) / math.sqrt(2))
        require(inward < rect['width'] / 2 and inward < rect['height'] / 2,
                'Contour regions are not distinct')
        x = rect['right'] - inward if index % 2 else rect['left'] + inward
        y = rect['bottom'] - inward if index >= 2 else rect['top'] + inward
        require(point['corner'] == legacy['corner'] == label and point['radius'] == radius
                and all(finite(point[key]) and math.isclose(point[key], expected, rel_tol=0, abs_tol=1e-9)
                        for key, expected in (('inward', inward), ('x', x), ('y', y)))
                and 0 <= point['x'] < viewport['width'] and 0 <= point['y'] < viewport['height']
                and point['reachable'] is reachable[index] and isinstance(point['hit'], dict)
                and type(point['hit']['nodeId']) is int and point['hit']['nodeId'] > 0,
                'Contour formula/region/native hit evidence differs')
        # Float tolerance above only compares independent Python/JS formula
        # evaluation. Padding reconstruction and target identity stay exact.
        require(legacy['x'] == (rect['right'] - 3 if index % 2 else rect['left'] + 3)
                and legacy['y'] == (rect['bottom'] - 3 if index >= 2 else rect['top'] + 3)
                and type(legacy['reachable']) is bool and 'hit' in legacy,
                'Legacy rectangular probe diagnostic missing/changed')


def computed_shape_proof(proof):
    oracle = proof['contourOracle']
    inputs = ['', 'round', 'superellipse(1)', 'bevel', 'scoop', 'superellipse(0)',
              'superellipse(2)', 'unknown', 'Round', ' round', 'round ',
              'superellipse(1.0)', None]
    require(oracle['cornerShapeChecks'] == [
        {'raw': raw, 'quoted': json.dumps(raw), 'expected': index < 3,
         'actual': index < 3, 'passed': True} for index, raw in enumerate(inputs)],
        'Exact computed-shape predicate cases differ')
    require([row for row in proof['checks'] if row['check'] ==
             'computed corner-shape exact-value predicate checks'] ==
            [{'check': 'computed corner-shape exact-value predicate checks', 'count': 13}],
            'Computed-shape predicate count differs')
    preflight = oracle['browserPreflight']
    result, observations = preflight['result'], oracle['cornerShapeObservations']
    require(preflight['passed'] is True and not preflight.get('error')
            and preflight['client'] == 'desktop' and preflight['timeoutMs'] == 2000
            and preflight['browser'] == {'name': 'chromium',
                'version': read(OUT / 'browser-readiness.json')['browser']}
            and preflight['observationRange'] == {'start': 0, 'end': 3}
            and result['removed'] is True and result['error'] is None
            and result['supported'] == {'round': True, 'bevel': True}
            and isinstance(result['userAgent'], str) and result['userAgent']
            and isinstance(result['platform'], str) and result['platform'],
            'Actual browser computed-shape preflight incomplete')
    contour_sample(result['positive'])
    require(result['positive']['tag'] == 'BUTTON'
            and all(row['raw'] in ('round', 'superellipse(1)')
                    for row in result['positive']['cornerShapes']),
            'Preflight must observe computed round, not unsupported empty property')
    negative = result['negative']
    require(negative['corner'] == 'top-left' and negative['authored'] == 'bevel'
            and negative['raw'] in ('bevel', 'superellipse(0)')
            and negative['quoted'] == json.dumps(negative['raw'])
            and negative['accepted'] is False and 'sample' not in negative
            and 'Unsupported contour geometry: noncircular corner shape top-left='
                in negative['error']['message'], 'Real noncircular guard discriminator failed')
    require(oracle['cornerShapeCaptureErrors'] == [] and 3 <= len(observations) <= 512
            and [row['stage'] for row in observations[:3]] ==
                ['before-validation', 'before-validation', 'rejected']
            and all(row['stage'] == 'before-validation' for row in observations[3:]),
            'Computed shapes missing before validation or unexpected rejection')
    for index, row in enumerate(observations):
        require(row['client'] in ('desktop', 'touch')
                and type(row['nodeId']) is int and row['nodeId'] > 0
                and row['tag'] in ('BUTTON', 'SUMMARY')
                and type(row['at']) in (int, float) and math.isfinite(row['at'])
                and [shape['corner'] for shape in row['cornerShapes']] ==
                    ['top-left', 'top-right', 'bottom-left', 'bottom-right']
                and all(isinstance(shape['raw'], str)
                        and shape['quoted'] == json.dumps(shape['raw'])
                        and (shape['raw'] in ('', 'round', 'superellipse(1)')
                             or (index in (1, 2) and shape['corner'] == 'top-left'
                                 and shape['raw'] == negative['raw']))
                        for shape in row['cornerShapes']), 'Invalid raw computed-shape evidence')
    require(all(row['client'] == 'desktop' and row['nodeId'] == result['positive']['nodeId']
                for row in observations[:3])
            and observations[0]['cornerShapes'] == result['positive']['cornerShapes']
            and observations[1]['cornerShapes'] == observations[2]['cornerShapes']
            and observations[2]['cornerShapes'][0]['raw'] == negative['raw'],
            'Preflight capture does not bind the same native control')


def contour_proof(proof, taps, padding_count):
    computed_shape_proof(proof)
    oracle = proof['contourOracle']
    logic = ['square radius 0 uses 3px', 'circular radius 10 uses 5.050252531694167px',
             *('reject ' + name for name in (
                 'nonfinite radius', 'nonfinite rect', 'elliptical radius', 'percentage radius',
                 'negative radius', 'overlapping radii', 'transform', 'perspective',
                 'fragmented box', 'inconsistent box', 'coincident regions', 'scaled viewport'))]
    require(oracle['version'] == 'circular-contour-v1'
            and oracle['formula'] == 'd=max(3,r-(r-3)/sqrt(2))'
            and oracle['logicChecks'] == [{'check': name, 'passed': True} for name in logic]
            and padding_count > 0
            and oracle['negativeCounts'] ==
            {'size': 2, 'regions': 4, 'obstruction': 1, 'paddingOrigin': padding_count},
            'Contour logic/negative evidence incomplete')
    rows = lambda name: [row for row in proof['checks'] if row['check'] == name]
    require(rows('contour formula and unsupported geometry logic checks') ==
            [{'check': 'contour formula and unsupported geometry logic checks', 'count': 14}]
            and Counter(row['dimension'] for row in rows('negative control: native size below 44px rejected'))
            == Counter(('width', 'height'))
            and Counter(row['corner'] for row in rows('negative control: individual contour region rejected'))
            == Counter(('top-left', 'top-right', 'bottom-left', 'bottom-right'))
            and rows('negative control: real one-probe pointer overlay rejected by same contour oracle') ==
            [{'check': 'negative control: real one-probe pointer overlay rejected by same contour oracle',
              'corner': 'top-left'}], 'Contour discriminator identities differ')
    for sample in proof['geometry']:
        if 'corners' in sample:
            contour_sample(sample)
    negative, = [row for row in proof['geometry'] if row['label'] == 'negative-contour-overlay']
    require('corners' not in negative and negative['hitOverlay'] is True
            and negative['rejected'] is True and negative['removed'] is True, 'Overlay negative incomplete')
    before, blocked, restored = (negative[key] for key in ('before', 'sample', 'restored'))
    contour_sample(before)
    contour_sample(blocked, reachable=(False, True, True, True))
    contour_sample(restored)
    require(before['nodeId'] == blocked['nodeId'] == restored['nodeId']
            and before['rect'] == blocked['rect'] == restored['rect']
            and before['corners'] == restored['corners']
            and [{key: point[key] for key in ('corner', 'radius', 'inward', 'x', 'y')}
                 for point in blocked['corners']] ==
                [{key: point[key] for key in ('corner', 'radius', 'inward', 'x', 'y')}
                 for point in before['corners']]
            and blocked['corners'][0]['hit']['nodeId'] != before['corners'][0]['hit']['nodeId'],
            'Overlay failed to isolate/restore the same first contour region')
    delivered = [row for row in proof['geometry'] if row['label'] == 'corner-tap-delivered']
    require(len(delivered) == 14, 'Missing delivered contour touch evidence')
    old_negatives = []
    for before, after in zip(taps, delivered):
        require(not after.get('captureError') and before['nodeId'] == after['nodeId']
                and before['corner'] == after['corner'], 'Touch target/capture identity changed')
        for key in ('rect', 'radii', 'borders', 'transforms', 'viewport', 'scroll', 'intended', 'position'):
            require(before[key] == after[key], f'Touch {key} changed between measurement and delivery')
        for sample in (before, after):
            require('corners' not in sample and sample['acceptance']['basis'] == sample['basis'],
                    'Touch contour acceptance missing/flattened')
            contour_sample({**sample, 'corners': sample['acceptance']['corners']})
            point, = [p for p in sample['acceptance']['corners'] if p['corner'] == sample['corner']]
            require(sample['intended'] == {'x': point['x'], 'y': point['y']}
                    and sample['corrected']['point'] == sample['intended']
                    and sample['corrected']['reachable'] is True, 'Exact contour touch coordinate mismatch')
            for axis, side in (('x', 'left'), ('y', 'top')):
                old_position = sample['intended'][axis] - sample['rect'][side]
                require(sample['oldPosition'][axis] == old_position
                        and sample['position'][axis] == old_position - sample['borders'][side]
                        and sample['corrected']['point'][axis] ==
                        sample['rect'][side] + sample['borders'][side] + sample['position'][axis]
                        and sample['old']['point'][axis] ==
                        sample['rect'][side] + sample['borders'][side] + sample['oldPosition'][axis],
                        'Padding-box conversion evidence differs')
        event = after['event']
        require(event['sameNode'] is True and event['isTrusted'] is True and event['pointerType'] == 'touch'
                and all(type(event[axis]) in (int, float) and math.isfinite(event[axis])
                        and abs(event[axis] - before['intended'][axis]) < 1 for axis in ('x', 'y')),
                'Delivered event missed the native contour target')
        if before['borders']['left'] or before['borders']['top']:
            require(before['old']['point'] != before['intended'] and type(before['old']['reachable']) is bool,
                    'Old padding conversion did not differ')
            old_negatives.append({
                'check': 'old padding-origin conversion rejected by same exact-contour oracle',
                'corner': before['corner'], 'oldPoint': before['old']['point'],
                'intended': before['intended'], 'oldReachable': before['old']['reachable']})
        else:
            require(before['old']['point'] == before['corrected']['point'], 'Zero-border conversion differs')
    require(rows('old padding-origin conversion rejected by same exact-contour oracle') == old_negatives,
            'Missing/changed per-touch padding conversion negatives')


def generated_proof(mode, *, coarse_toolbar=False):
    reports = list((OUT / f'generated-{mode}/evidence').glob('generated-file-controls-*/observations.json'))
    require(len(reports) == 1, 'Require one generated-file journey report')
    proof = read(reports[0])
    require((proof.get('mode') == 'coarse-toolbar-qualification' if coarse_toolbar else 'mode' not in proof)
            and not any(key in proof for key in ('diagnosticComplete', 'stoppedAfter', 'cornerDiagnostics',
                                               'touchEventDiagnostic'))
            and proof['passed'] is True and proof['errors'] == [] and proof['screenshotErrors'] == []
            and not proof.get('error') and not proof.get('failure') and proof['preCleanupSnapshots'] == [],
            'Generated-file journey did not pass')
    # ownFixture returns {pid,code,signal}; the Python fixture separately emits its pid.
    cleanup = proof['cleanup']
    require(set(cleanup) == {'pid', 'code', 'signal'} and type(cleanup['pid']) is int and cleanup['pid'] > 0
            and type(cleanup['code']) is int and cleanup['code'] == 0 and cleanup['signal'] is None
            and proof['fixtureCleanup'] == {'pid': cleanup['pid'], 'homeRemoved': True}, 'Fixture exit/identity')
    require(proof['browserCleanup'] == [{'label': label, 'closed': True, 'connected': False}
                                       for label in ('desktop', 'touch')], 'Browser cleanup')
    generated_coverage(proof, reports[0].parent)
    expected = read(OUT / 'candidate.json')
    static = {p.removeprefix('amplifier_web/static/'): h for p, h in expected['static'].items()}
    package = (read(OUT / 'installed-identity.json')['packageRoot']
               if mode == 'installed' else str(ROOT / 'amplifier_web'))
    require(proof['installed'] == {'packageRoot': package, 'verified': mode == 'installed',
                                   'staticSha256': static}, 'Journey used wrong package/static')
    for when in ('before', 'after'):
        identity = read(OUT / f'{mode}-fixture-origins-{when}.json')
        require(identity['pid'] == proof['cleanup']['pid'], 'Origin proof is not the fixture process')
    if mode == 'installed':
        require(read(OUT / 'installed-wheel-before.json') == read(OUT / 'installed-wheel-after.json'),
                'Installed payload/static/RECORD changed')
    else:
        require(read(OUT / 'source-fixture-payload-before.json') ==
                read(OUT / 'source-fixture-payload-after.json'), 'Source payload/static changed')
    if coarse_toolbar:
        build_identity = read(OUT / 'frontend-build-identity.json')
        require(build_identity['reusedBuild'] is False and expected['static'] == build_identity['static']
                and read(Path(package) / 'static/build.json') == build_identity['build']
                and tree(Path(package) / 'static') == static, 'Journey serving package is not the fresh build')
        server_origins = {}
        for when in ('before', 'after'):
            process = read(OUT / f'{mode}-fixture-origins-{when}.json')
            origin = process['modules']['amplifier_web.server']
            require(origin == {'path': str(Path(package) / 'server.py'),
                               'sha256': expected['files']['amplifier_web/server.py']},
                    'Serving module is not from the verified source/installed package')
            server_origins[when] = origin
        # The frozen server's index/static routes resolve beside server.py.
        # This proves the same fixture's serving package/assets, not a packet trace.
        save(f'generated-{mode}/serving-assets.json', {
            'fixturePid': proof['cleanup']['pid'], 'packageRoot': package,
            'serverOrigins': server_origins, 'build': build_identity['build'],
            'static': static, 'reusedBuild': False,
            'basis': 'same-fixture server module origins and adjacent static bytes; not browser response capture'})
        coarse_toolbar_proof(proof, mode)
    save(f'generated-{mode}/accepted-report.json', {
        'path': str(reports[0]), 'sha256': sha(reports[0]), 'visualJudgment': 'pending',
        'frameBindingCounts': dict(Counter(c['binding']['status'] for c in proof['fileFrameCaptures']))})


def corner_diagnostic_proof(exit_code):
    """Validate checkpoint evidence even for exit 1; never turn that exit into PASS."""
    reports = list((OUT / 'generated-source/evidence').glob('generated-file-controls-*/observations.json'))
    require(len(reports) == 1, 'Require one corner diagnostic producer report')
    proof = read(reports[0])
    require(exit_code in (0, 1) and proof['mode'] == 'corner-diagnostic'
            and proof['passed'] is (exit_code == 0), 'Wrong diagnostic mode/exit')
    captures = proof['cornerDiagnostics']
    controls = [g for g in proof['geometry'] if 'corners' in g]
    require(len(captures) == len(controls) == 4
            and [g['name'] for g in controls[1:]] ==
            ['Copy full path', 'Copy relative path', 'Download saved file'],
            'First desktop checkpoint did not reach all controls')
    for index, (capture, control) in enumerate(zip(captures, controls)):
        require(capture['label'] == control['label'] == 'desktop'
                and capture['controlIndex'] == index and capture['complete'] is True
                and not capture.get('captureError') and not capture.get('disposeError'),
                'Incomplete first desktop corner capture')
        first, rect = capture['first'], control['rect']
        require(first['rect'] == rect and len(first['corners']) == len(control['corners']) == 4,
                'Diagnostic must bind the original rectangle and all four points')
        points = [(rect['left'] + 3, rect['top'] + 3), (rect['right'] - 3, rect['top'] + 3),
                  (rect['left'] + 3, rect['bottom'] - 3), (rect['right'] - 3, rect['bottom'] - 3)]
        for label, point, raw, observed in zip(
                ('top-left', 'top-right', 'bottom-left', 'bottom-right'),
                points, control['corners'], first['corners']):
            in_view = 0 <= point[0] < control['viewport']['width'] and 0 <= point[1] < control['viewport']['height']
            require((raw['x'], raw['y']) == point and type(raw['reachable']) is bool
                    and {key: observed[key] for key in ('x', 'y', 'reachable')} == raw
                    and observed['corner'] == label and observed['hitTested'] is in_view
                    and 'hit' in observed and isinstance(observed['stack'], list)
                    and len(observed['stack']) <= 8, 'Original 3px corner hit evidence missing/changed')
        require(all(key in first['computed'] for key in (
                    'borderTopLeftRadius', 'borderTopRightRadius', 'borderBottomLeftRadius',
                    'borderBottomRightRadius', 'pointerEvents', 'clipPath', 'overflowX', 'overflowY'))
                and first['node']['connected'] is True and first['viewport']['dpr'] > 0
                and all(first['viewport'][k] == control['viewport'][k] for k in ('width', 'height'))
                and isinstance(first['ancestors'], list) and len(first['ancestors']) <= 16
                and 'scroll' in first and 'fonts' in first, 'Diagnostic context incomplete')
    if exit_code == 0:
        require(proof['diagnosticComplete'] is True and proof['stoppedAfter'] == 'first-desktop-measure'
                and not proof.get('failure') and not proof.get('error') and proof['preCleanupSnapshots'] == []
                and all(g['rect']['width'] >= 44 and g['rect']['height'] >= 44
                        and all(c['reachable'] is True for c in g['corners']) for g in controls),
                'Exit zero without the complete original checkpoint oracle')
    else:
        require(proof['diagnosticComplete'] is False and proof['stoppedAfter'] is None
                and proof.get('failure') and proof.get('error'), 'Missing original diagnostic failure')
    require(proof['errors'] == [] and proof['screenshotErrors'] == []
            and Counter(c['check'] for c in proof['checks']) == Counter({
                'negative control: missing buttons rejected by the same oracle': 1,
                'real clipboard + Copied.': 2,
                'v1 browser download remains original after v2 is saved before URL resolution': 1})
            and [d['label'] for d in proof['downloads']] == ['desktop-race-v1']
            and proof['fileFrameCaptures'] == []
            and not any(r['path'].endswith('/document') for r in proof['requests']),
            'Diagnostic exceeded checkpoint scope or failed before it')
    cleanup = proof['cleanup']
    require(set(cleanup) == {'pid', 'code', 'signal'} and type(cleanup['pid']) is int and cleanup['pid'] > 0
            and type(cleanup['code']) is int and cleanup['code'] == 0 and cleanup['signal'] is None
            and proof['fixtureCleanup'] == {'pid': cleanup['pid'], 'homeRemoved': True}
            and proof['browserCleanup'] == [{'label': 'desktop', 'closed': True, 'connected': False}],
            'Diagnostic fixture/browser cleanup failed')
    expected = read(OUT / 'candidate.json')
    require(proof['installed'] == {
                'packageRoot': str(ROOT / 'amplifier_web'), 'verified': False,
                'staticSha256': {p.removeprefix('amplifier_web/static/'): h for p, h in expected['static'].items()}}
            and all(read(OUT / f'source-fixture-origins-{when}.json')['pid'] == cleanup['pid']
                    for when in ('before', 'after'))
            and read(OUT / 'source-fixture-payload-before.json') == read(OUT / 'source-fixture-payload-after.json'),
            'Diagnostic fixture package/static identity changed')
    result = {'path': str(reports[0]), 'sha256': sha(reports[0]), 'exitCode': exit_code,
              'checkpointObserved': True, 'producerDiagnosticComplete': proof['diagnosticComplete'],
              'originalCornerFalse': any(not c['reachable'] for g in controls for c in g['corners']),
              'productAcceptance': False, 'cause': 'unknown', 'visualJudgment': 'pending'}
    save('generated-source/diagnostic-report.json', result)
    return result


def touch_event_diagnostic_proof(exit_code, *, native_state=False, portal_host=False):
    """Inspect raw exit-1 evidence before fail-stop; never bless a missing pair."""
    result = {'exitCode': exit_code, 'checkpointObserved': False, 'outcome': 'unknown',
              'productAcceptance': False, 'cause': 'unknown', 'visualJudgment': 'pending'}
    try:
        reports = list((OUT / 'generated-source/evidence').glob('generated-file-controls-*/observations.json'))
        require(len(reports) == 1, 'Require one touch event diagnostic producer report')
        path, = reports
        result.update(path=str(path), sha256=sha(path))
        proof = read(path)
        require(type(exit_code) is int and exit_code in (0, 1)
                and proof['mode'] == ('portal-host-experiment' if portal_host
                                     else 'native-state-diagnostic' if native_state else 'touch-event-diagnostic')
                and proof['passed'] is (exit_code == 0)
                and proof['productAcceptance'] is False, 'Wrong touch diagnostic mode/exit')
        diagnostic = proof['touchEventDiagnostic']
        require(isinstance(diagnostic, dict) and diagnostic['checkpoint'] == 'first-390-primary-download'
                and diagnostic['label'] == 'touch-390-preview'
                and diagnostic['control'] == 'Download canvas source' and diagnostic['tapAttempts'] == 1
                and diagnostic['productAcceptance'] is False and diagnostic['cause'] == 'unknown'
                and diagnostic['browser'] == read(OUT / 'browser-readiness.json')['browser']
                and diagnostic['options'] == {'viewport': {'width': 390, 'height': 844},
                    'isMobile': True, 'hasTouch': True, 'deviceScaleFactor': 1}, 'Wrong/missing touch checkpoint')
        computed_shape_proof(proof)
        taps = [g for g in proof['geometry'] if g['label'] == 'corner-tap-before']
        delivered = [g for g in proof['geometry'] if g['label'] == 'corner-tap-delivered']
        require(len(taps) == len(delivered) == 7
                and [t['node']['label'] for t in taps] ==
                    ['Copy full path', 'Copy relative path', 'Download saved file'] * 2 + ['Download canvas source']
                and all(t['event']['sameNode'] is True for t in delivered[:-1]),
                'Preceding card taps missing or checkpoint exceeded')
        before = diagnostic['before']
        contour_sample(before)
        point = before['corners'][3]
        require(before['corner'] == 'bottom-right' and before['name'] == 'Download canvas source'
                and before['viewport']['width'] == 390 and before['viewport']['height'] == 844
                and before['intended'] == {'x': point['x'], 'y': point['y']}
                and before['position'] == {
                    'x': point['x'] - before['rect']['left'] - before['borders']['left'],
                    'y': point['y'] - before['rect']['top'] - before['borders']['top']}
                and all(taps[-1][key] == before[key] for key in
                    ('nodeId', 'rect', 'radii', 'borders', 'intended', 'position', 'cornerShapes'))
                and all(delivered[-1][key] == diagnostic['delivered'][key] for key in
                    ('nodeId', 'rect', 'intended', 'event')), 'Measured/delivered contour identity changed')
        # This is the existing journey prefix, not a new product acceptance rule.
        counts = Counter({
            'real clipboard + Copied.': 13,
            'contour formula and unsupported geometry logic checks': 1,
            'negative control: native size below 44px rejected': 2,
            'negative control: individual contour region rejected': 4,
            'negative control: real one-probe pointer overlay rejected by same contour oracle': 1,
            'computed corner-shape exact-value predicate checks': 1,
            'actual browser computed round accepted and noncircular corner rejected by same sampler': 1,
            'old padding-origin conversion rejected by same exact-contour oracle':
                sum(bool(g['borders']['left'] or g['borders']['top']) for g in taps),
            'deliberate saved-v1 preview setup, not a download': 2,
            'right-rail paths/download and primary Download preserve running saved-v1 HTML': 1,
            'negative control: missing buttons rejected by the same oracle': 1,
            'v1 browser download remains original after v2 is saved before URL resolution': 1,
            'clipboard refusal reports failure and leaves real clipboard unchanged': 1,
            '403 has visible feedback and zero downloads, then a legitimate click succeeds': 1,
            'negative control: real wrong-byte download rejected by the same SHA-256 oracle': 1,
            'reload after source deletion retains paths, exact saved v1 download and unrelated read-only Canvas/draft': 1,
            'adjacent Markdown stays distinct; direct saved-only has no paths; outside-current-base has no relative-copy control': 1,
            'open details refreshes current working folder and rejects delayed prior-placement metadata': 1,
            '390px/320px emulated touch, actual contour taps, 44px targets, four contour-relative native hit probes and saved PNGs': 1,
            'card-only phase completed with zero document requests and unrelated historical Canvas retained': 1,
            'negative controls: lost input and changed execution token rejected by the same retained-preview oracle': 1,
        })
        require(Counter(c['check'] for c in proof['checks']) == counts
                and [d['label'] for d in proof['downloads']] == [
                    'desktop-race-v1', 'desktop-after-403', 'negative-wrong-bytes', 'reload-deleted-source-v1',
                    'adjacent-markdown', 'saved-only', 'touch-390-deleted-v1', 'touch-320-deleted-v1',
                    'desktop-preview-primary-v1', 'desktop-preview-rail-v1'] +
                    (['touch-390-preview-primary-v1'] if (native_state or portal_host) and diagnostic.get('download') else [])
                and len([r for r in proof['requests'] if r['path'].endswith('/document')]) == 2
                and [c['label'] for c in proof['fileFrameCaptures']] ==
                    [f'touch-320-{suffix}' for suffix in ('controls', 'paths', 'path-0', 'path-1', 'path-2', 'path-3')],
                'Missing preceding flow or later journey ran after checkpoint')
        setup, = [c for c in proof['checks'] if c['check'] == 'deliberate saved-v1 preview setup, not a download'
                  and c['label'] == 'touch']
        require(all(diagnostic['retained'][key] == setup[key] for key in ('marker', 'state', 'documents'))
                and diagnostic['retained']['state']['draft'] == 'Keep touch draft — unsubmitted.'
                and diagnostic['retained']['state']['canvas'] ==
                    {'id': proof['seed']['artifacts']['html']['id'], 'version': 1, 'readOnly': True},
                'Saved-v1 setup/draft binding differs')
        capture = diagnostic.get('capture') or {}
        events = capture.get('events', [])
        sequence = [event['type'] for event in events]
        pair = {key: next((i for i, event in enumerate(events) if event['type'] == kind), -1)
                for key, kind in (('pointerdown', 'pointerdown'), ('firstMousedown', 'mousedown'))}
        result.update(sequence=sequence, pairPresence=pair, originalOraclePassed=diagnostic['originalOraclePassed'],
                      originalFailure=diagnostic.get('failure'), cleanup={
                          'capture': capture.get('finished'), 'handleDisposed': diagnostic.get('handleDisposed'),
                          'originalListenerRemoved': diagnostic.get('originalListenerRemoved'),
                          'errors': diagnostic.get('cleanupErrors')},
                      captureErrors=diagnostic.get('captureErrors'), truncations=capture.get('truncations'))
        require(capture.get('limits') == {'windowMs': 2000, 'records': 64, 'stack': 16, 'path': 24,
                'ancestors': 16, 'notices': 4, 'touches': 4, 'errors': 8}
                and len(events) <= 64 and diagnostic['sequence'] == sequence and diagnostic['pairPresence'] == pair,
                'Capture bounds/sequence/pair summary differs')
        finite = lambda value: type(value) in (int, float) and math.isfinite(value)
        for index, event in enumerate(events):
            require(event['index'] == index and event['type'] in capture['types']
                    and all(finite(event[k]) for k in ('at', 'timeStamp', 'wallTime'))
                    and capture['armedAt'] <= event['at'] < capture['deadline'],
                    'Invalid synchronous event index/time')
            if event.get('captureError'):
                continue  # Retain the partial event, but never mark it observed.
            require(all(key in event for key in ('pointerType', 'pointerId', 'isTrusted', 'actual',
                    'target', 'intendedButton', 'notices', 'capture', 'scroll', 'viewport', 'atActual', 'atIntended'))
                    and event['intended'] == before['intended']
                    and event['intendedButton']['nodeId'] == before['nodeId']
                    and len(event['composedPath']) <= 24
                    and all(set(node) == {'nodeId', 'tag'} for node in event['composedPath'])
                    and len(event['ancestors']) <= 16 and len(event['notices']) <= 4
                    and event['capture']['phase'] in (1, 2)
                    and len(event['capture']['queried']) <= 26
                    and event['atIntended']['point'] == before['intended']
                    and len(event['atIntended']['elements']) <= 16
                    and (event['actual'] is None or (
                        all(finite(event['actual'][k]) for k in ('x', 'y'))
                        and event['atActual']['point'] == event['actual']
                        and len(event['atActual']['elements']) <= 16)), 'Incomplete synchronous event snapshot')
        pair_present = pair['pointerdown'] >= 0 and pair['firstMousedown'] > pair['pointerdown']
        if pair_present:
            pointer, mouse = (events[pair[key]] for key in ('pointerdown', 'firstMousedown'))
            require(pointer['actual'] == {k: diagnostic['delivered']['event'][k] for k in ('x', 'y')}
                    and pointer['sameNode'] == diagnostic['delivered']['event']['sameNode'],
                    'Original pointer sampler and passive record disagree')
            pair_present = pointer.get('pointerType') == 'touch' and all(
                e.get('isTrusted') and e.get('actual') and e.get('atActual') for e in (pointer, mouse))
        observed = bool(pair_present and capture.get('finished', {}).get('listenersRemoved')
                        and capture['finished']['timerCleared'] and diagnostic.get('handleDisposed')
                        and diagnostic.get('captureErrors') == [] and capture.get('errors') == []
                        and capture.get('truncations') and all(n == 0 for n in capture['truncations'].values()))
        require(diagnostic['checkpointObserved'] is observed, 'Unsupported checkpoint observation claim')
        result.update(checkpointObserved=observed,
                      outcome='checkpoint_observed_cause_unknown' if observed else 'unknown',
                      unknownReason=None if observed else 'missing_event_or_capture_loss_or_truncation')
        require(diagnostic.get('originalListenerRemoved') is True and diagnostic.get('cleanupErrors') == []
                and proof['errors'] == [] and proof['screenshotErrors'] == [], 'Diagnostic cleanup/page errors')
        cleanup = proof['cleanup']
        require(type(cleanup['pid']) is int and cleanup['pid'] > 0 and cleanup['code'] == 0
                and cleanup['signal'] is None and proof['fixtureCleanup'] == {'pid': cleanup['pid'], 'homeRemoved': True}
                and proof['browserCleanup'] == [{'label': label, 'closed': True, 'connected': False}
                                               for label in ('desktop', 'touch')], 'Diagnostic owner cleanup failed')
        expected = read(OUT / 'candidate.json')
        require(proof['installed'] == {'packageRoot': str(ROOT / 'amplifier_web'), 'verified': False,
                'staticSha256': {p.removeprefix('amplifier_web/static/'): h for p, h in expected['static'].items()}}
                and all(read(OUT / f'source-fixture-origins-{when}.json')['pid'] == cleanup['pid']
                        for when in ('before', 'after'))
                and read(OUT / 'source-fixture-payload-before.json') == read(OUT / 'source-fixture-payload-after.json'),
                'Diagnostic fixture identity/payload differs')
        if native_state:
            native = diagnostic.get('nativeState', {})
            result['nativeStateCapture'] = {key: native.get(key) for key in
                                            ('version', 'complete', 'errors', 'snapshot', 'detached')}
            result['nativeState'] = native_state_proof(proof, path.parent, exit_code)
        if portal_host:
            result['portalHost'] = portal_host_proof(proof, path.parent, exit_code)
        if exit_code == 0:
            require(observed and diagnostic['originalOraclePassed'] is True
                    and proof['diagnosticComplete'] is True and proof['stoppedAfter'] == 'first-390-primary-download'
                    and not proof.get('failure') and not proof.get('error'), 'Exit zero without observed checkpoint')
        else:
            require(proof['diagnosticComplete'] is False and proof['stoppedAfter'] is None
                    and proof.get('failure') and proof.get('error'), 'Missing original failure')
            if diagnostic['delivered']['event']['sameNode'] is False:
                require(diagnostic['originalOraclePassed'] is False
                        and 'Touch must reach the native control' in json.dumps(proof['failure']),
                        'Original sameNode assertion was suppressed')
        return result
    except Exception as error:
        result.update(checkpointObserved=False, outcome='unknown',
                      validationError={'type': type(error).__name__, 'message': str(error)})
        raise
    finally:
        # Always retain the raw report reference and any partially read sequence,
        # even when exit 1, malformed capture or a missing mousedown prevents proof.
        save('generated-source/portal-host-experiment-report.json' if portal_host
             else 'generated-source/native-state-diagnostic-report.json' if native_state
             else 'generated-source/touch-event-diagnostic-report.json', result)


def portal_host_proof(proof, folder, exit_code):
    gesture = proof['touchEventDiagnostic']
    experiment, baseline = gesture['portalHost'], read(OUT / 'portal-host-baseline.json')
    require(gesture['portalHostRequested'] is True and gesture['nativeStateRequested'] is False
            and 'nativeState' not in gesture and experiment['version'] == 'portal-host-v1'
            and experiment['baseline'] == baseline and experiment['baselinePassed'] is True,
            'Portal experiment must use check-17 geometry, not a new trace/cap recovery')
    for key, expected in baseline['tap'].items():
        require(gesture['before'][key] == expected, 'Original contour/position changed')

    def layout(snapshot):
        require(snapshot['coarse'] is True and snapshot['dpr'] == 1, 'Wrong portal pointer/scale')
        for key in ('download', 'details'):
            button = snapshot[key]
            require(button['connected'] is True and button['geometry'] == baseline[key]
                    and len(button['corners']) == 4 and all(c['reachable'] for c in button['corners'])
                    and all(button['geometry']['rect'][k] >= 44 for k in ('width', 'height')),
                    f'Portal {key} geometry or native hit target changed')
        host, copy, download = snapshot['host'], snapshot['copy'], snapshot['download']['geometry']['rect']
        require(snapshot['toolbar']['rect'] == baseline['toolbarRect']
                and snapshot['body']['rect'] == baseline['bodyRect'] and snapshot['body']['computed']['margin'] == '0px'
                and copy['rect'] == baseline['copyRect'] and host['connected'] is True
                and host['rectCount'] == 1 and host['computed']['display'] == 'grid'
                and host['computed']['position'] == 'static'
                and all(host['computed'][k] == '0px' for k in ('margin', 'padding', 'borderWidth'))
                and host['computed']['pointerEvents'] != 'none' and host['computed']['touchAction'] != 'none'
                and host['rect'] == {**copy['rect'], 'right': download['right'],
                                     'width': download['right'] - copy['rect']['left']}
                and download['left'] - copy['rect']['right'] == 4
                and snapshot['details']['geometry']['rect']['left'] - download['right'] == 4
                and all(w['connected'] and w['computed']['display'] == 'contents' for w in snapshot['innerWrappers']),
                'Portal box must occupy only the existing action tracks, without body gap or shield')

    layout(experiment['before'])
    events = [e for e in gesture['capture']['events']
              if e['type'] in ('pointerdown', 'pointerup', 'mousedown', 'mouseup', 'click')]
    require([e['type'] for e in events] == ['pointerdown', 'pointerup', 'mousedown', 'mouseup', 'click']
            and all(e['isTrusted'] is True for e in events), 'Missing/duplicate/untrusted portal gesture events')
    for event in events:
        layout(event['portalHost'])
        require(all(event['portalHost'][key]['nodeId'] == experiment['before'][key]['nodeId']
                    for key in ('download', 'details', 'host')), 'Portal identity changed during gesture')
    require(proof['tapDrain'].get('settled') is True and proof['tapDrain']['pendingAfter'] == 0
            and len(proof['tapSettlements']) == 7 and all(
                row['settled'] and row['listenerCleanup']['state'] == 'removed' and row['handleDisposed']
                and not row['otherFailures'] and all(op['state'] == 'fulfilled' for op in row['operations'])
                for row in proof['tapSettlements'])
            and len(proof['downloadSettlements']) == 11 and all(
                row['settled'] and row['observation']['listenerRemoved'] and row['observation']['timerCleared']
                for row in proof['downloadSettlements']), 'Portal branches/listeners not settled')
    last = proof['tapSettlements'][-1]
    require(last == gesture['settlement'] and last['retained']['connected'] is True
            and last['retained']['nodeId'] == experiment['before']['download']['nodeId'], 'Original Download not retained')
    wrong = next((e for e in events if e['sameNode'] is False), None)
    if wrong:
        require(exit_code == 1 and last.get('primaryFailure') and
                ('Touch must reach the native control' if wrong['type'] == 'pointerdown'
                 else 'click must reach the native control' if wrong['type'] == 'click'
                 else wrong['type'] + ' must reach the native control') in json.dumps(last['primaryFailure']),
                'Portal wrong target suppressed')
    if exit_code == 0:
        download, preserved = gesture['download'], gesture['preservation']
        html = proof['seed']['artifacts']['html']
        path = folder / (download['label'] + '-' + download['filename'])
        require(not wrong and gesture['strictEventsPassed'] is True
                and download['label'] == 'touch-390-preview-primary-v1' and download['filename'] == html['filename']
                and download['bytes'] == baseline['html']['bytes'] == 426
                and download['sha256'] == baseline['html']['sha256'] == sha(path)
                and path.read_bytes() == html['body'].encode()
                and preserved['iframeConnected'] is True and preserved['sameIframe'] is True
                and preserved['iframeCount'] == 1 and preserved['prohibitedCommands'] == []
                and all(preserved[k] == gesture['retained'][k] for k in ('marker', 'unfinished', 'state', 'documents')),
                'Portal success requires bytes and retained input/token/lease/draft')
    return {'sameGeometry': True, 'realHostBox': True, 'wrongTarget': bool(wrong),
            'checkpointPassed': exit_code == 0, 'productAcceptance': False,
            'interpretation': 'topology-dependence-supported-not-pruning-proof' if exit_code == 0
                              else 'topology-hypothesis-not-supported-by-this-gesture'}


def native_state_proof(proof, folder, exit_code):
    """Validate native observations, not candidate membership/pruning/scoring."""
    diagnostic = proof['touchEventDiagnostic']
    state = diagnostic['nativeState']
    limits = {'snapshotBytes': 4 * 1024 * 1024, 'snapshotNodes': 12000, 'documents': 8,
              'candidateNodes': 64, 'descendantDepth': 8, 'ancestorDepth': 24, 'quadsPerNode': 32,
              'traceBytes': 4 * 1024 * 1024, 'traceRecords': 10000, 'hitTests': 128, 'traceWindowMs': 3000}
    require(diagnostic['nativeStateRequested'] is True and state['version'] == 'native-state-v1'
            and state['limits'] == limits and state['complete'] is True and state['prepared'] is True
            and state['errors'] == [] and all(state.get(key) is True for key in
                ('timerCleared', 'listenersRemoved', 'objectsReleased', 'detachAttempted', 'detached')),
            'Native-state capture incomplete or cleanup unconfirmed')

    def artifact(binding, name, cap):
        path = folder / name
        require(binding['file'] == name and path.resolve() == path and path.is_file()
                and 0 < path.stat().st_size == binding['bytes'] <= cap and sha(path) == binding['sha256'],
                'Native artifact identity/size mismatch')
        return read(path)

    snapshot = artifact(state['snapshot'], 'native-dom-snapshot.json', limits['snapshotBytes'])
    documents, strings = snapshot['documents'], snapshot['strings']
    require(0 < len(documents) <= limits['documents']
            and sum(len(d['nodes']['nodeName']) for d in documents) <= limits['snapshotNodes'],
            'Native snapshot bound differs')
    roots, candidates = state['roots'], state['candidates']
    require([r['kind'] for r in roots] == ['download', 'details', 'notice', 'toolbar']
            and all(r['connected'] is True for r in roots)
            and roots[0]['nodeId'] == diagnostic['before']['nodeId']
            and roots[0]['rect'] == diagnostic['before']['rect']
            and 0 < len(candidates) <= limits['candidateNodes'], 'Native roots/identity/subset mismatch')
    doc_index = candidates[0]['documentIndex']
    nodes, layout = documents[doc_index]['nodes'], documents[doc_index]['layout']
    root_indexes = [nodes['backendNodeId'].index(r['backendNodeId']) for r in roots]
    expected_indexes = []
    for index in range(len(nodes['nodeName'])):
        parent, depth = index, 0
        while parent >= 0 and parent not in root_indexes:
            parent, depth = nodes['parentIndex'][parent], depth + 1
        if parent >= 0:
            require(depth <= limits['descendantDepth'], 'Omitted native descendants')
            expected_indexes.append(index)
    require([r['index'] for r in candidates] == expected_indexes, 'Native candidate subset incomplete')
    for row in candidates:
        index = row['index']
        require(row['documentIndex'] == doc_index and row['backendNodeId'] == nodes['backendNodeId'][index]
                and row['parentIndex'] == nodes['parentIndex'][index] and row['nodeType'] == nodes['nodeType'][index]
                and row['name'] == strings[nodes['nodeName'][index]]
                and row['isClickable'] is (index in nodes.get('isClickable', {}).get('index', [])),
                'Native snapshot-derived node differs')
        parents, parent = [], nodes['parentIndex'][index]
        while parent >= 0:
            parents.append({'index': parent, 'backendNodeId': nodes['backendNodeId'][parent],
                            'name': strings[nodes['nodeName'][parent]]})
            parent = nodes['parentIndex'][parent]
        require(row['ancestry'] == parents and len(parents) <= limits['ancestorDepth'], 'Native ancestry differs')
        expected_layout = [{'index': i, 'bounds': layout['bounds'][i],
                            'styles': {name: strings[layout['styles'][i][j]]
                                       if j < len(layout['styles'][i]) and layout['styles'][i][j] >= 0 else None
                                       for j, name in enumerate(state['styles'])}}
                           for i, node_index in enumerate(layout['nodeIndex']) if node_index == index]
        require(row['layout'] == expected_layout, 'Native snapshot layout/styles differ')
        raw_attrs = nodes['attributes'][index]
        attrs = {strings[raw_attrs[i]]: strings[raw_attrs[i + 1]] for i in range(0, len(raw_attrs), 2)}
        require(row['attributes'] == {key: attrs[key] for key in ('id', 'class', 'role', 'aria-label') if key in attrs},
                'Native structural attributes differ')
        wrapper = bool(set(attrs.get('class', '').split()) &
                       {'a-canvas-primary-actions', 'a-canvas-common-actions', 'a-saved-file-download'})
        if 'quads' in row:
            require(row['nodeType'] == 1 and len(row['quads']) <= limits['quadsPerNode']
                    and all(len(q) == 8 and all(type(v) in (int, float) and math.isfinite(v) for v in q)
                            for q in row['quads']), 'Invalid current native quads')
        else:
            require(row['quadsNotRequested'] == 'non-element or no snapshot layout and not an action wrapper/root'
                    and (row['nodeType'] != 1 or (not row['layout'] and not wrapper and index not in root_indexes)),
                    'Missing requested quads')
    require(all('quads' in next(row for row in candidates if row['index'] == index)
                for index in root_indexes), 'Native root quads missing')
    trace = state['trace']
    events = artifact(trace['artifact'], 'native-gesture-trace.json', limits['traceBytes'])
    hits = [{'index': i, 'event': event} for i, event in enumerate(events) if event.get('name') == 'HitTest']
    require(trace['recordCount'] == len(events) <= limits['traceRecords']
            and trace['bytes'] <= limits['traceBytes'] and trace['overflow'] is False
            and trace['config'] == {'transferMode': 'ReportEvents', 'traceConfig': {
                'recordMode': 'recordUntilFull', 'traceBufferSizeInKb': 1024,
                'includedCategories': ['devtools.timeline', 'input']}}
            and trace['windowExpired'] is False and trace['completion']['dataLossOccurred'] is False
            and trace['endReason'] == 'tap-settled' and trace['endRequestedAt'] >= trace['startedAt']
            and trace['endRequestedAt'] - trace['startedAt'] <= limits['traceWindowMs']
            and 0 < len(hits) <= limits['hitTests'] and trace['hitTests'] == hits,
            'Native trace missing/truncated/lost/outside gesture window')
    clock = state['clock']
    require(all(math.isfinite(clock[k]) for k in ('before', 'after')) and clock['after'] >= clock['before']
            and clock['offsetUs'] == {'min': clock['before'] * 1e6 - clock['page']['now'] * 1000,
                                     'max': clock['after'] * 1e6 - clock['page']['now'] * 1000},
            'Native clock interval differs')
    passive = [e for e in diagnostic['capture']['events']
               if e['type'] in ('pointerdown', 'pointerup', 'mousedown', 'mouseup', 'click')]
    require([e['type'] for e in passive] == ['pointerdown', 'pointerup', 'mousedown', 'mouseup', 'click']
            and all(e['isTrusted'] is True for e in passive)
            and state['correlation'] == [{
                'eventIndex': e['index'], 'type': e['type'], 'at': e['at'], 'timeStamp': e['timeStamp'],
                'wallTime': e['wallTime'], 'traceCallbackIntervalUs': {
                    k: e['at'] * 1000 + clock['offsetUs'][k] for k in ('min', 'max')}} for e in passive],
            'Native passive sequence/correlation differs')
    require(proof['tapDrain'].get('settled') is True and proof['tapDrain']['pendingAfter'] == 0
            and len(proof['tapSettlements']) == 7 and all(
                row['settled'] and row['listenerCleanup']['state'] == 'removed' and row['handleDisposed']
                and not row['otherFailures'] and all(op['state'] == 'fulfilled' for op in row['operations'])
                for row in proof['tapSettlements'])
            and len(proof['downloadSettlements']) == 11 and all(
                row['settled'] and row['observation']['listenerRemoved'] and row['observation']['timerCleared']
                for row in proof['downloadSettlements']), 'Native gesture/download branches did not settle cleanly')
    last = proof['tapSettlements'][-1]
    require(last == diagnostic['settlement'] and last['retained']['connected'] is True
            and last['retained']['nodeId'] == roots[0]['nodeId'], 'Retained native handle identity/connectedness differs')
    if any(e['sameNode'] is False for e in passive):
        wrong = next((e for e in passive if e['type'] in ('pointerdown', 'click') and e['sameNode'] is False),
                     next(e for e in passive if e['sameNode'] is False))
        expected_failure = ('Touch must reach the native control' if wrong['type'] == 'pointerdown'
                            else 'Trusted click must reach the native control' if wrong['type'] == 'click'
                            else wrong['type'] + ' must reach the native control')
        require(exit_code == 1 and proof['downloadSettlements'][-1]['trigger']['state'] == 'rejected'
                and last.get('primaryFailure')
                and expected_failure in json.dumps(last['primaryFailure']),
                'Wrong native target was not the primary failure')
    if exit_code == 0:
        download, preservation = diagnostic['download'], diagnostic['preservation']
        html = proof['seed']['artifacts']['html']
        saved = folder / (download['label'] + '-' + download['filename'])
        require(diagnostic['strictEventsPassed'] is True and all(e['sameNode'] is True for e in passive)
                and download['label'] == 'touch-390-preview-primary-v1' and download['bytes'] == html['byteLength'] == 426
                and download['sha256'] == html['sha256'] == sha(saved)
                and download['filename'] == html['filename'] and saved.read_bytes() == html['body'].encode()
                and preservation['iframeConnected'] is True and preservation['sameIframe'] is True
                and preservation['iframeCount'] == 1 and preservation['prohibitedCommands'] == []
                and all(preservation[k] == diagnostic['retained'][k] for k in ('marker', 'unfinished', 'state', 'documents')),
                'Successful native diagnostic skipped bytes/preview preservation')
    return {'complete': True, 'snapshot': state['snapshot'], 'trace': trace['artifact'],
            'candidateCount': len(candidates), 'hitTestCount': len(hits),
            'rootResponsiveness': {r['kind']: next(c['isClickable'] for c in candidates
                                    if c['backendNodeId'] == r['backendNodeId']) for r in roots},
            'wrongTarget': any(e['sameNode'] is False for e in passive),
            'cause': 'unknown', 'candidateMembership': 'not_exposed', 'productAcceptance': False}


def renderer_proof(negative=False):
    phase = 'renderer-stalled-response' if negative else 'canvas-renderers'
    mode = 'stalled-body' if negative else 'normal'
    proof = read(OUT / phase / 'evidence' / f'canvas-renderers-{mode}.json')
    require(proof['schemaVersion'] == 1 and proof['mode'] == mode, 'Wrong renderer report')
    require(type(proof['fixtureExit']['pid']) is int and proof['fixtureExit']['pid'] > 0
            and proof['fixtureExit']['code'] == 0 and proof['fixtureExit']['signal'] is None
            and proof['fixtureExit']['settled'] is True and proof['fixtureStopSucceeded'] is True,
            'Renderer fixture exit')
    require(proof['browserErrors'] == [] and proof['unhandledRejectionCount'] == 0
            and proof['unhandledRejections'] == [] and not proof.get('reportWriteFailure')
            and not proof.get('negativeVerificationFailure')
            and not proof.get('finalCleanupFailure') and proof['cleanup']['failures'] == []
            and proof['cleanup']['drained'] is True, 'Renderer cleanup/errors')
    if not negative:
        require(proof['outcome'] == 'passed' and proof['failure'] is None
                and proof['expectedFailureVerified'] is False and proof['watchdog']['fired'] is False
                and proof['bodyWaitStarted'] is True and proof['bodyWaitCompleted'] is True
                and proof['optimisticPanel'] is True and proof['reloadNavigationCount'] == 1
                and proof['acceptedCompleteResponse'] is True and proof['reloadStarted'] is True,
                'Renderer normal response barrier failed')
        return
    require(proof['outcome'] == 'expected-failure' and proof['expectedFailureVerified'] is True
            and proof['watchdog']['fired'] is True and proof['watchdog']['phase'] == 'response.finished'
            and proof['timeoutCause']['code'] == 'CANVAS_DETAILS_LIFECYCLE_TIMEOUT',
            'Not the intended stalled response failure')
    request, headers, acceptance = proof['request'], proof['observedHeaders'], proof['hostAcceptance']
    require(request['id'] and request['action'] == 'view.update' and request['method'] == 'POST'
            and request['args']['patch'] == {'canvasControlsExpanded': True}
            and request['id'] == headers['stallRequestId'] == headers['requestId'] == acceptance['id']
            and headers['status'] == 200 and acceptance['accepted'] is True
            and all(request[k] == acceptance[k] for k in ('clientId', 'hostInstanceId', 'args')),
            'Stall not correlated with accepted request')
    require(proof['optimisticPanel'] is True and proof['bodyWaitStarted'] is True
            and proof['bodyWaitCompleted'] is False and proof['acceptedCompleteResponse'] is False
            and proof['reloadStarted'] is False and proof['reloadNavigationCount'] == 0
            and proof['noReload'] is True and proof['cleanup']['pageClosed'] is True
            and proof['cleanup']['pageStopRequested'] is True, 'Wrong stall/reload boundary')
    require(Counter(e['event'] for e in proof['fixtureEvents']) ==
            Counter(('accepted', 'headers-sent', 'cleanup-complete'))
            and any(e['event'] == 'headers-sent' and e['id'] == request['id']
                and e['bodyBytesSent'] == 1 and e['bodyBytesTotal'] > 1 for e in proof['fixtureEvents'])
            and any(e['event'] == 'cleanup-complete' and e['stalledResponses'] == 1
                    for e in proof['fixtureEvents']), 'Incomplete server stall evidence')


def usability_proof():
    lines = (OUT / 'canvas-usability/canvas-usability.log').read_text().splitlines()
    expected = {key: True for key in ('activeTabs', 'narrowTabs', 'filenameDetails', 'titledDownloads',
                                     'markdownAttachment', 'reload', 'graphvizDecoded', 'tallMermaidScrollable')}
    require([json.loads(line) for line in lines if line.startswith('{"activeTabs":')] ==
            [{**expected, 'browserErrors': 0}], 'Canvas usability completion report missing/changed')


def standard_files_proof():
    proof = read(OUT / 'standard-ux-canvas-files/evidence/observations.json')
    source = (ROOT / 'frontend/tests/standard-ux-canvas-files-browser.mjs').read_text()
    expected = re.findall(r"checks\.push\('([^']+)'\)", source)
    require(proof['schemaVersion'] == 1 and len(expected) == 4 and proof['checks'] == expected
            and all(proof[key] is True for key in
                    ('passed', 'uiPassed', 'browserClosed', 'fixtureStopSucceeded', 'homeRemoved'))
            and proof['uiFailure'] is None and proof['failure'] is None
            and proof['cleanupFailures'] == [] and not proof.get('reportWriteFailure'),
            'Standard canvas/files exact coverage failed')
    exit_state, cleanup = proof['fixtureExit'], proof['fixtureCleanup']
    require(set(exit_state) == {'pid', 'code', 'signal', 'settled'}
            and type(exit_state['pid']) is int and exit_state['pid'] > 0
            and type(exit_state['code']) is int and exit_state['code'] == 0
            and exit_state['signal'] is None and exit_state['settled'] is True,
            'Standard canvas/files fixture did not exit normally')
    home = Path(cleanup['home'])
    require(home.is_absolute() and home.parent == OUT / 'tmp'
            and cleanup == {'pid': exit_state['pid'], 'home': str(home), 'signal': 'SIGTERM',
                            'runnerCleanup': True, 'homeRemoved': True}
            and not home.exists() and not home.is_symlink(),
            'Standard canvas/files fixture cleanup identity/removal mismatch')


BROWSER_READY = r"""
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {chromium} from '@playwright/test';
const require=createRequire(import.meta.url);
const inventory=JSON.parse(await readFile('/opt/unified-prepared/inventory.json'));
const lock=JSON.parse(await readFile('package-lock.json'));
const version=require('@playwright/test/package.json').version;
assert.equal(process.version,inventory.node.version);
assert.equal(version,inventory.node.playwright);
assert.equal(version,lock.packages['node_modules/@playwright/test'].version);
const executable=chromium.executablePath();
assert.ok(executable.startsWith('/opt/unified-prepared/browsers/'));
const browser=await chromium.launch({headless:true});
try {
 const page=await browser.newPage();await page.setContent('<button>Ready</button>');
 await page.getByRole('button',{name:'Ready'}).click();
 await writeFile('/artifacts/files1318-qualified/browser-readiness.json',
   JSON.stringify({node:process.execPath,version:process.version,playwright:version,
                   executable,browser:browser.version()}));
} finally {await browser.close();}
"""


def evidence_manifest(deadline):
    """Prune before descent; do not enumerate installed dependencies or caches."""
    result, total_bytes = {}, 0
    for directory, folders, files in os.walk(OUT, followlinks=False):
        require(time.monotonic() < deadline, 'Evidence finalization budget exhausted')
        base = Path(directory)
        folders[:] = sorted(name for name in folders
                            if not (base / name).is_symlink()
                            and not (base == OUT and name in ('installed', 'harness', 'home', 'tmp', 'cache')))
        for name in sorted(files):
            path = base / name
            # The final report hashes this manifest, not the other way around.
            if path.is_symlink() or path in (OUT / 'report.json', OUT / 'evidence-manifest.json'):
                continue
            require(path.is_file(), f'Unexpected evidence file type: {path}')
            total_bytes += path.stat().st_size
            require(len(result) < 10000 and total_bytes <= 1024 ** 3, 'Evidence exceeds 10000 files/1 GiB')
            require(time.monotonic() < deadline, 'Evidence hashing budget exhausted')
            result[path.relative_to(OUT).as_posix()] = sha(path)
            require(time.monotonic() < deadline, 'Evidence hashing budget exhausted')
    return result


def main():
    started = time.monotonic()
    hard_deadline = started + BUDGET
    work_deadline = hard_deadline - FINALIZATION_RESERVE
    evidence_deadline = hard_deadline - RECEIPT_RESERVE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path,
                        choices=[MANIFEST, CORNER_MANIFEST, CONTOUR_MANIFEST, TOUCH_EVENT_MANIFEST,
                                 COARSE_TOOLBAR_MANIFEST, NATIVE_STATE_MANIFEST, PORTAL_HOST_MANIFEST])
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--corner-diagnostic', action='store_true')
    modes.add_argument('--contour-qualification', action='store_true')
    modes.add_argument('--touch-event-diagnostic', action='store_true')
    modes.add_argument('--native-state-diagnostic', action='store_true')
    modes.add_argument('--portal-host-experiment', action='store_true')
    modes.add_argument('--coarse-toolbar-qualification', action='store_true')
    args = parser.parse_args()
    touch_checkpoint = args.touch_event_diagnostic or args.native_state_diagnostic or args.portal_host_experiment
    checkpoint_mode = args.corner_diagnostic or touch_checkpoint
    reuse_mode = args.contour_qualification or args.touch_event_diagnostic or args.native_state_diagnostic
    fresh_product = args.coarse_toolbar_qualification or args.portal_host_experiment
    reused_phases = COARSE_TOOLBAR_REUSED_PHASES if fresh_product else REUSED_PHASES
    manifest_path = (CORNER_MANIFEST if args.corner_diagnostic
                     else PORTAL_HOST_MANIFEST if args.portal_host_experiment
                     else NATIVE_STATE_MANIFEST if args.native_state_diagnostic
                     else TOUCH_EVENT_MANIFEST if args.touch_event_diagnostic
                     else COARSE_TOOLBAR_MANIFEST if args.coarse_toolbar_qualification
                     else CONTOUR_MANIFEST if args.contour_qualification else MANIFEST)
    if args.manifest != manifest_path:
        parser.error('The manifest must match the selected qualification mode')
    select_output(CORNER_OUT if args.corner_diagnostic
                  else PORTAL_HOST_OUT if args.portal_host_experiment
                  else NATIVE_STATE_OUT if args.native_state_diagnostic
                  else TOUCH_EVENT_OUT if args.touch_event_diagnostic
                  else COARSE_TOOLBAR_OUT if args.coarse_toolbar_qualification
                  else CONTOUR_OUT if args.contour_qualification else QUALIFIED_OUT)
    OUT.mkdir(parents=True, exist_ok=False)  # No replay or overwrite of a prior cycle.
    report = {'schemaVersion': 1, 'passed': False, 'complete': False,
              'budgetSeconds': BUDGET, 'preparationBudgetSeconds': 1800,
              'finalizationReserveSeconds': FINALIZATION_RESERVE, 'cleanupReserveSeconds': CLEANUP_RESERVE,
              'receiptReserveSeconds': RECEIPT_RESERVE, 'finalization': {'status': 'unrun'},
              'phases': {name: {'status': 'unrun', 'reason': 'not_started'} for name in PHASES},
              'limits': ['Synthetic fixtures, Chromium desktop/touch emulation; not physical devices.',
                         'Locked prepared test dependencies reused, not a minimal dependency install.',
                         'No publication, full suite, provider, OS reveal or independent pixel acceptance.']}
    if args.corner_diagnostic:
        selected = {'snapshot', 'runtime-readiness', 'browser-readiness', 'static-reuse',
                    'source-freeze', 'topology-source', 'generated-source', 'final-integrity'}
        report['phases']['static-reuse'] = {'status': 'unrun', 'reason': 'not_started'}
        for name, row in report['phases'].items():
            if name not in selected:
                row['reason'] = 'outside_corner_diagnostic_scope'
        report.update(mode='corner-diagnostic', outcome='not_started', cause='unknown',
                      productAcceptance=False, checkpointObserved=False)
        report['limits'].append(
            'Checkpoint only: reused static; no build, unit suites, later journeys, wheel or install. '
            'Topology includes touch readiness, not a touch product journey. Prior integrated failure unchanged.')
    if touch_checkpoint:
        selected = {'snapshot', 'runtime-readiness', 'browser-readiness', 'source-freeze',
                    'topology-source', 'generated-source', 'final-integrity', *REUSED_PHASES}
        for name, row in report['phases'].items():
            if name not in selected:
                row['reason'] = 'outside_touch_event_diagnostic_scope'
        report.update(mode='portal-host-experiment' if args.portal_host_experiment
                      else 'native-state-diagnostic' if args.native_state_diagnostic else 'touch-event-diagnostic',
                      outcome='not_started', cause='unknown',
                      productAcceptance=False, checkpointObserved=False)
        report['limits'].append(
            'First 390px primary Download event pair only; missing compatibility mousedown or capture loss '
            'is UNKNOWN. Original touch assertion remains binding. No later gesture, renderer or installed run.')
        if args.native_state_diagnostic:
            report.update(priorChecksConsumed=16)
            report['limits'].append(
                'Bounded current DOM responsiveness/quads and gesture-window HitTest trace only. '
                'Candidate membership, pruning and scoring are not exposed. Native source/bytes checks are not a full pass.')
    if reuse_mode:
        if args.contour_qualification:
            report['mode'] = 'contour-qualification'
        report['priorIntegratedPassed'] = False
        for name in ('reuse-proof', 'static-reuse'):
            report['phases'][name] = {'status': 'unrun', 'reason': 'not_started'}
        for name in REUSED_PHASES:
            report['phases'][name]['reason'] = 'reuse_pending_exact_evidence_and_inputs'
        report['limits'].append(
            'Only the earlier 70 Python tests, 19 Node tests and 311 built static files may be REUSED. '
            'The prior integrated run failed later; no prior full qualification pass is claimed.')
    if fresh_product:
        report.update(mode='portal-host-experiment' if args.portal_host_experiment else 'coarse-toolbar-qualification',
                      priorChecksConsumed=17 if args.portal_host_experiment else 15,
                      prospectiveCheckOrdinal=18 if args.portal_host_experiment else 16,
                      priorIntegratedPassed=False, reusedBuild=False)
        for name in ('reuse-proof', 'prior-build-context'):
            report['phases'][name] = {'status': 'unrun', 'reason': 'not_started'}
        for name in reused_phases:
            report['phases'][name]['reason'] = 'reuse_pending_exact_evidence_and_inputs'
        report['limits'].append(
            'Only the exact earlier 70 Python and 19 Node tests may be REUSED. '
            'The old build remains immutable before-context; fresh canonical frontend build is required. ' +
            ('First-390 same-geometry topology falsifier only; no CDP trace, later gesture, wheel or install.'
             if args.portal_host_experiment else
             'One full source/installed qualification; no spacing sweep, repair or retry.'))
    save('report.json', report)
    def expired(_signum, _frame):
        remaining = hard_deadline - time.monotonic()
        if remaining <= 0:
            os._exit(124)  # No successful receipt if Python/cleanup exceeded the absolute limit.
        signal.setitimer(signal.ITIMER_REAL, remaining)
        raise TimeoutError('Files1318 absolute budget boundary reached')

    old_alarm = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, max(.001, work_deadline - time.monotonic()))
    active = None
    checks_finished = False
    try:
        require(Path(__file__).resolve() == ROOT / DRIVER and Path.cwd().resolve() == ROOT,
                'Run only the admitted /workspace driver from /workspace')
        require(sys.executable == str(PYTHON) and not sys.flags.optimize, 'Use the prepared Python, without -O')
        require(not (ROOT / '.git').exists(), 'Expected a Git-free managed snapshot')

        def step(name, work):
            nonlocal active
            require(time.monotonic() < work_deadline - CLEANUP_RESERVE,
                    'Insufficient budget after reserving cleanup and finalization')
            active = name
            report['phases'][name].update(status='running', reason=None)
            save('report.json', report)
            value = work()
            require(time.monotonic() < work_deadline, 'Verification exceeded work deadline')
            report['phases'][name].update(status='passed', reason=None)
            save('report.json', report)
            active = None
            return value

        def snapshot():
            frozen = read(args.manifest)
            require(frozen['schemaVersion'] == 2
                    and re.fullmatch('826f6c84[a-f0-9]{32}', frozen['baselineRevision'])
                    and re.fullmatch('2ca2a7c5[a-f0-9]{32}', frozen['baselineTree']), 'Wrong parent baseline')
            require(all(re.fullmatch('[a-f0-9]{40}', frozen[key]) for key in ('revision', 'tree'))
                    and frozen['revision'] != frozen['baselineRevision']
                    and frozen['tree'] != frozen['baselineTree'] and frozen['reviewed'] is True,
                    'Require the committed, parent-reviewed candidate, not dirty baseline bytes')
            changed = frozen['changedPaths']
            allowed_changes = (REVIEWED_CHANGES | {COARSE_TOOLBAR_CSS}
                               if fresh_product else REVIEWED_CHANGES)
            require(isinstance(changed, list) and len(changed) == len(set(changed))
                    and DRIVER in changed and set(changed) <= allowed_changes
                    and set(changed) <= frozen['files'].keys(), 'Candidate changes exceed selected fixed scope')
            save('parent-manifest.json', frozen)
            report['manifestSha256'] = sha(args.manifest)
            save('source-before.json', frozen_inputs(frozen))
            required = {*COPY, 'scripts/browser_check_runner.py', 'pyproject.toml', 'uv.lock',
                        'frontend/package-lock.json', 'frontend/package.json',
                        *(n.split('::')[0] for n in PYTEST_NODES),
                        *UNIT_FILES, 'tests/fixtures/empty_host_ui_server.py',
                        'tests/fixtures/outputs_ui_server.py',
                        'frontend/tests/canvas-renderers-browser.mjs',
                        'frontend/tests/canvas-usability-browser.mjs',
                        'frontend/tests/standard-ux-canvas-files-browser.mjs'}
            require(required <= frozen['files'].keys(), 'Parent manifest omits required source')
            if args.contour_qualification:
                require(frozen['files'][PRODUCER]['sha256'] == CONTOUR_PRODUCER_SHA,
                        'Contour producer differs from the reviewed verifier interface')
            if args.touch_event_diagnostic:
                require(frozen['files'][PRODUCER]['sha256'] == TOUCH_EVENT_PRODUCER_SHA,
                        'Touch event producer differs from the reviewed verifier interface')
            if args.native_state_diagnostic:
                require(frozen.get('nativeStateReuse') == REUSE_BINDING
                        and frozen['files'][PRODUCER]['sha256'] == NATIVE_STATE_PRODUCER_SHA
                        and frozen['files'][COARSE_TOOLBAR_CSS]['sha256'] == NATIVE_STATE_CSS_SHA,
                        'Native-state producer/reuse/rolled-back CSS differs from reviewed inputs')
            if args.portal_host_experiment:
                portal_host_inputs(frozen)
            if args.coarse_toolbar_qualification:
                require(isinstance(COARSE_TOOLBAR_PRODUCER_SHA, str)
                        and re.fullmatch('[a-f0-9]{64}', COARSE_TOOLBAR_PRODUCER_SHA)
                        and frozen['files'][PRODUCER]['sha256'] == COARSE_TOOLBAR_PRODUCER_SHA,
                        'Coarse-toolbar producer differs from the reviewed verifier interface')
                coarse_toolbar_inputs(frozen)
            renderer_source = (ROOT / 'frontend/tests/canvas-renderers-browser.mjs').read_text()
            if not checkpoint_mode and ('CANVAS_RENDERER_STALLED_BODY' not in renderer_source
                    or 'expectedFailureVerified' not in renderer_source):
                report['phases']['renderer-stalled-response']['reason'] = 'renderer_negative_interface_unavailable'
                raise RuntimeError('Renderer interface unavailable; negative explicitly unrun')
            contract, inventory, plan = read(CONTRACT), read(PREPARED / 'inventory.json'), read(PREPARED / 'plan.json')
            require(contract['version'] == 1 and contract['profileId'] == 'browser-test'
                    and contract['workspace'] == str(ROOT) and contract['artifacts'] == '/artifacts'
                    and contract['python'] == str(PYTHON), 'Wrong execution contract')
            require(inventory['executionContract'] == contract
                    and plan['executionContract']['source'] == contract['source']
                    and plan['nodeDirectory'] == 'frontend', 'Prepared source identity mismatch')
            require(contract['source']['gitMetadataAvailable'] is False
                    and contract['source']['sourceHead'] == frozen['revision'], 'Wrong admitted HEAD')
            expected = None
            executables = (PYTHON, NODE)
            if not checkpoint_mode:
                build = contract['pythonBuild']
                require(build['available'] is True and build['python'] == str(BUILD_PYTHON),
                        'Prepared build prerequisites unavailable')
                expected = [str(BUILD_PYTHON), '-m', 'build', '--wheel', '--no-isolation',
                            '--outdir', '/artifacts/dist', '/workspace']
                require(shlex.split(build['command']) == expected, 'Unexpected configured build command')
                require(not Path('/artifacts/dist').exists(), 'Wheel output must be fresh')
                require(read(ROOT / 'frontend/package.json')['scripts']['test:canvas-renderers-browser']
                        == 'node tests/canvas-renderers-browser.mjs', 'Configured renderer command changed')
                require(read(ROOT / 'frontend/package.json')['scripts']['build'] == 'vite build',
                        'Canonical frontend build command changed')
                executables = (PYTHON, BUILD_PYTHON, NODE, NPM, UV)
            elif touch_checkpoint:
                # Compare all old executable identities for retained_proofs;
                # checking their bytes is NOT executing build/install readiness.
                executables = (PYTHON, BUILD_PYTHON, NODE, NPM, UV)
            if args.portal_host_experiment:
                require(read(ROOT / 'frontend/package.json')['scripts']['build'] == 'vite build',
                        'Portal experiment requires the canonical frontend build')
            for path in executables:
                require(path.is_file() and os.access(path, os.X_OK), f'Missing executable: {path}')
            save('executables.json', {str(path): {'resolved': str(path.resolve()), 'sha256': sha(path)}
                                     for path in executables})
            save('execution-contract.json', contract)
            save('prepared-inventory.json', inventory)
            save('prepared-plan.json', plan)
            return frozen, expected

        frozen, wheel_command = step('snapshot', snapshot)
        # Import maintained stdlib helpers only after frozen bytes are checked.
        from browser_check_runner import run_scenarios
        from cold_restore_checks import verify_wheel
        env = {
            'PATH': str(NODE.parent) + ':' + str(PYTHON.parent) + ':/usr/local/bin:/usr/bin:/bin',
            'HOME': str(OUT / 'home'), 'TMPDIR': str(OUT / 'tmp'),
            'XDG_CACHE_HOME': str(OUT / 'cache'), 'LANG': 'C.UTF-8',
            'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
            'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1',
            'AMPLIFIER_TEST_PYTHON': str(PYTHON), 'AMPLIFIER_TEST_CONTRACT': str(CONTRACT),
            'PLAYWRIGHT_BROWSERS_PATH': str(PREPARED / 'browsers'),
            'UV_OFFLINE': '1', 'UV_NO_CONFIG': '1', 'PIP_NO_INDEX': '1',
            'PIP_CONFIG_FILE': '/dev/null', 'npm_config_offline': 'true',
            'npm_config_audit': 'false', 'npm_config_fund': 'false',
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
            'GIT_TERMINAL_PROMPT': '0',
        }
        if args.corner_diagnostic:
            env['FILES1318_CORNER_DIAGNOSTIC'] = '1'  # Fixed child evidence destination only.
        elif args.portal_host_experiment:
            env['FILES1318_PORTAL_HOST_EXPERIMENT'] = '1'
        elif args.native_state_diagnostic:
            env['FILES1318_NATIVE_STATE_DIAGNOSTIC'] = '1'
        elif args.touch_event_diagnostic:
            env['FILES1318_TOUCH_EVENT_DIAGNOSTIC'] = '1'
        elif args.contour_qualification:
            env['FILES1318_CONTOUR_QUALIFICATION'] = '1'
        elif args.coarse_toolbar_qualification:
            env['FILES1318_COARSE_TOOLBAR_QUALIFICATION'] = '1'
        for key in ('HOME', 'TMPDIR', 'XDG_CACHE_HOME'):
            Path(env[key]).mkdir()
        os.environ.clear()
        os.environ.update(env)  # In particular no inherited PYTHONPATH/PYTHONHOME.

        def run(name, command, *, root=ROOT, extra=None, cap=150, verify=None, negative=False):
            def execute():
                row = report['phases'][name]
                selected_env = {**env, 'AMPLIFIER_TEST_ARTIFACTS': str(OUT / name / 'evidence'), **(extra or {})}
                row.update(command=list(map(str, command)), cwd=str(root / 'frontend'), env=selected_env)
                save('report.json', report)
                remaining = work_deadline - CLEANUP_RESERVE - time.monotonic()
                require(remaining > 0, 'Combined budget exhausted; cleanup/finalization reserved')
                if fresh_product:
                    row['executed'] = True
                code = run_scenarios(root, {'schemaVersion': 1, 'scenarios': [
                    {'id': name, 'command': row['command'], 'env': selected_env}]},
                    OUT / name, budget=min(cap, remaining), case_timeout=min(cap, remaining),
                    provenance={'manifestSha256': report['manifestSha256'], 'revision': frozen['revision'],
                                'tree': frozen['tree'], 'baselineRevision': frozen['baselineRevision'],
                                'baselineTree': frozen['baselineTree'], 'contractSha256': sha(CONTRACT)})
                result = read(OUT / name / 'results.json')
                raw, = result['scenarios']
                row['process'] = raw  # Raw negative remains failed/nonzero.
                save('report.json', report)
                if args.corner_diagnostic and name == 'generated-source':
                    # Preserve/validate the producer's first false sample before
                    # the ordinary fail-stop exit check. This is NOT a negative test.
                    row['diagnostic'] = corner_diagnostic_proof(raw['exitCode'])
                    report['checkpointObserved'] = row['diagnostic']['checkpointObserved']
                    save('report.json', report)
                if touch_checkpoint and name == 'generated-source':
                    row['diagnostic'] = touch_event_diagnostic_proof(
                        raw['exitCode'], native_state=args.native_state_diagnostic, portal_host=args.portal_host_experiment)
                    report['checkpointObserved'] = row['diagnostic']['checkpointObserved']
                    report['observationOutcome'] = row['diagnostic']['outcome']
                    save('report.json', report)
                require(raw['cleanup'] == CLEAN and raw['reason'] is None, f'{name}: process/cleanup failure')
                require(result['complete'] is True and result['passed'] is (not negative)
                        and raw['status'] == ('failed' if negative else 'passed')
                        and type(raw['exitCode']) is int and raw['exitCode'] == (1 if negative else 0)
                        and code == (1 if negative else 0), f'{name}: unexpected exit')
                if verify:
                    verify()
                require(time.monotonic() < work_deadline, f'{name}: verification exceeded work deadline')
                row['verdict'] = ('checkpoint_observed_cause_unknown'
                                  if checkpoint_mode and name == 'generated-source'
                                  else 'expected_stalled_response_failure' if negative else 'passed')
            step(name, execute)

        run('runtime-readiness', [PYTHON, '-I', '-B', ROOT / DRIVER, '--runtime-ready'], cap=45)
        if not checkpoint_mode:
            run('build-readiness', [BUILD_PYTHON, '-I', '-B', '-c',
                "import build,hatchling,sys; assert sys.version_info >= (3,13); "
                "assert not build.ProjectBuilder('/workspace').check_dependencies('wheel')"], cap=30)
        run('browser-readiness', [NODE, '--input-type=module', '-e',
            BROWSER_READY.replace('/artifacts/files1318-qualified', str(OUT))], cap=45)
        if args.corner_diagnostic:
            step('static-reuse', lambda: diagnostic_static(frozen))
        elif reuse_mode or fresh_product:
            reused = step('reuse-proof', lambda: retained_proofs(
                frozen, unit_only=fresh_product))
            if fresh_product:
                step('prior-build-context', lambda: diagnostic_static(
                    frozen, binding='portalHostReuse' if args.portal_host_experiment else 'coarseToolbarReuse'))
            else:
                step('static-reuse', lambda: diagnostic_static(
                    frozen, binding='nativeStateReuse' if args.native_state_diagnostic
                    else 'touchEventReuse' if args.touch_event_diagnostic else 'contourReuse'))
            # No status changes until every original proof, dependency identity,
            # tracked input and archived static byte has passed the reuse guard.
            for name in reused_phases:
                report['phases'][name].update(
                    status='REUSED', reason='exact_prior_pass_and_unchanged_inputs_verified',
                    executed=False, sourceRevision=REUSE_BINDING['sourceRevision'],
                    sourceTree=REUSE_BINDING['sourceTree'],
                    priorEvidence=f'prior-evidence/{name}/results.json',
                    priorEvidenceSha256=frozen['reuseEvidence'][f'{name}/results.json'])
            reused['status'] = 'REUSED'
            save('reuse-evidence-validation.json', reused)
            report['reuseEvidence'] = reused
            save('report.json', report)
        if args.portal_host_experiment:
            # Preserve committed static as before-context. Never restore either
            # the failed gap build or native17's reused build for this variant.
            before = tree(ROOT / 'amplifier_web/static')
            save('static-before.json', before)
            shutil.copytree(ROOT / 'amplifier_web/static', OUT / 'static-before')
            require(tree(OUT / 'static-before') == before, 'Pre-build static preservation differs')
        if (not checkpoint_mode and not reuse_mode) or args.portal_host_experiment:
            run('frontend-build', [NPM, 'run', 'build'], cap=240)

        def source_freeze():
            save('source-after-reuse.json' if (checkpoint_mode or reuse_mode) and not args.portal_host_experiment
                 else 'source-after-build.json',
                 frozen_inputs(frozen, built=True))
            value = candidate(frozen)
            require(value['static'] and 'amplifier_web/static/index.html' in value['static'], 'No built static')
            if (checkpoint_mode or reuse_mode) and not args.portal_host_experiment:
                require(value['static'] == read(OUT / 'reuse-metadata.json')['files'], 'Reused static changed')
            if fresh_product:
                old_build = read(OUT / 'reuse-metadata.json')
                build_identity = read(ROOT / 'amplifier_web/static/build.json')
                require(set(build_identity) == {'id', 'version'}
                        and re.fullmatch('[a-f0-9]{16}', build_identity['id'])
                        and build_identity['id'] != old_build['build']['id']
                        and build_identity['version'] ==
                            tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
                        and value['static'] != old_build['files']
                        and report['phases']['frontend-build']['status'] == 'passed'
                        and report['phases']['frontend-build']['executed'] is True,
                        'Product variant requires a fresh changed canonical build')
                save('frontend-build-identity.json', {
                    'build': build_identity, 'sourceRevision': frozen['revision'], 'sourceTree': frozen['tree'],
                    'manifestSha256': report['manifestSha256'], 'css': frozen['files'][COARSE_TOOLBAR_CSS],
                    'static': value['static'], 'reusedBuild': False})
            save('candidate.json', value)
            save('static.json', value['static'])
        step('source-freeze', source_freeze)
        if not checkpoint_mode and not reuse_mode and not args.coarse_toolbar_qualification:
            run('python-tests', [PYTHON, '-I', '-B', ROOT / DRIVER, '--python-tests'], cap=180, verify=junit)
            run('frontend-units', [NODE, '--test', '--test-reporter=tap',
                                  *(ROOT / name for name in UNIT_FILES)], cap=60, verify=unit_proof)

        def generated(mode):
            root = HARNESS if mode == 'installed' else ROOT
            extra = {'FILE_CONTROLS_FIXTURE': str(root / DRIVER), 'FILES1318_FIXTURE': mode}
            if mode == 'installed':
                extra.update(AMPLIFIER_TEST_PYTHON=str(VENV / 'bin/python'), AMPLIFIER_TEST_INSTALLED='1',
                             AMPLIFIER_TEST_PACKAGE_ROOT=read(OUT / 'installed-identity.json')['packageRoot'])
            if args.corner_diagnostic:
                require(mode == 'source', 'Diagnostic cannot exercise installed mode')
                extra['FILE_CONTROLS_CORNER_DIAGNOSTIC'] = '1'
            if args.touch_event_diagnostic:
                require(mode == 'source', 'Touch event diagnostic cannot exercise installed mode')
                extra['FILE_CONTROLS_TOUCH_EVENT_DIAGNOSTIC'] = '1'
            if args.native_state_diagnostic:
                require(mode == 'source', 'Native-state diagnostic cannot exercise installed mode')
                extra['FILE_CONTROLS_NATIVE_STATE_DIAGNOSTIC'] = '1'
            if args.portal_host_experiment:
                require(mode == 'source', 'Portal experiment cannot exercise installed mode')
                extra['FILE_CONTROLS_PORTAL_HOST_EXPERIMENT'] = '1'
            if args.coarse_toolbar_qualification:
                extra['FILE_CONTROLS_COARSE_TOOLBAR_QUALIFICATION'] = '1'
            run('topology-' + mode, [NODE, 'tests/file-controls-topology-preflight.mjs'], root=root,
                extra={'FILE_CONTROLS_PREFLIGHT_EVIDENCE': str(OUT / f'topology-{mode}/evidence')},
                cap=80, verify=lambda: topology_proof(mode))
            run('generated-' + mode, [NODE, 'tests/generated-file-controls-browser.mjs'],
                root=root, extra=extra, cap=160,
                verify=None if checkpoint_mode else lambda: generated_proof(
                    mode, coarse_toolbar=args.coarse_toolbar_qualification))

        generated('source')
        if not checkpoint_mode:
            run('canvas-renderers', [NPM, 'run', 'test:canvas-renderers-browser'],
                verify=renderer_proof)
            # Raw nonzero is retained; only the exact correlated negative qualifies.
            run('renderer-stalled-response', [NPM, 'run', 'test:canvas-renderers-browser'],
                extra={'CANVAS_RENDERER_STALLED_BODY': '1'}, cap=120,
                verify=lambda: renderer_proof(True), negative=True)
            run('canvas-usability', [NODE, 'tests/canvas-usability-browser.mjs'], verify=usability_proof)
            run('standard-ux-canvas-files', [NODE, 'tests/standard-ux-canvas-files-browser.mjs'],
                verify=standard_files_proof)
            # The renderer test builds its independent example, NOT the bundled app.
            # Freeze those forced-included bytes before the ONE application wheel.
            frozen_inputs(frozen, built=True)
            built = read(OUT / 'candidate.json')
            final_source = candidate(frozen)
            require(final_source['static'] == built['static'], 'Source journeys changed static')
            save('candidate.json', final_source)
            run('wheel-build', wheel_command, cap=120)

            def verify_package():
                wheel, = Path('/artifacts/dist').glob('*.whl')
                value = read(OUT / 'candidate.json')
                value.update(wheel=str(wheel), wheelSha256=sha(wheel))
                save('candidate.json', value)
                save('wheel.json', verify_wheel(wheel, value))
                for name in COPY:
                    destination = HARNESS / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(ROOT / name, destination)
                    require(file_identity(destination) == frozen['files'][name], f'Copied harness differs: {name}')
                save('harness.json', {name: file_identity(HARNESS / name) for name in COPY})
                plan = read(PREPARED / 'plan.json')
                dependency = (PREPARED / 'node' / plan['nodeKey']).resolve()
                require(dependency.is_relative_to(PREPARED / 'node') and dependency.is_dir(), 'Missing locked Node tree')
                (HARNESS / 'frontend/node_modules').symlink_to(dependency, target_is_directory=True)
            step('wheel-verify', verify_package)
            run('fresh-venv', [PYTHON, '-I', '-B', '-m', 'venv', '--without-pip', VENV], root=HARNESS, cap=30)
            run('install-wheel', [UV, 'pip', 'install', '--python', VENV / 'bin/python',
                                  '--no-deps', '--no-index', read(OUT / 'candidate.json')['wheel']],
                root=HARNESS, cap=45)

            def link_dependencies():
                purelib = Path(read(OUT / 'runtime-readiness.json')['purelib']).resolve()
                require(purelib.is_relative_to(PREPARED / 'venv') and purelib.is_dir(), 'Wrong prepared purelib')
                site, = VENV.glob('lib/python*/site-packages')
                require(not list(site.glob('*.pth')), 'Fresh venv contains unexpected path configuration')
                require((site / 'amplifier_web/__init__.py').is_file(), 'Wheel must be installed BEFORE pth')
                pth = site / 'dependency.pth'
                pth.write_text(str(purelib) + '\n')
                save('dependency-reuse.json', {'pth': str(pth), 'sha256': sha(pth), 'content': pth.read_text(),
                                              'site': str(site), 'installedBeforeLink': True})
            step('dependency-link', link_dependencies)
            run('installed-readiness', [VENV / 'bin/python', '-I', '-B', HARNESS / DRIVER, '--installed-ready'],
                root=HARNESS, cap=45)
            generated('installed')

            def final_integrity():
                save('source-after.json', frozen_inputs(frozen, built=True))
                expected = read(OUT / 'candidate.json')
                require(candidate(frozen)['files'] == expected['files'], 'Source payload changed')
                require({name: file_identity(HARNESS / name) for name in COPY} == read(OUT / 'harness.json'),
                        'Copied harness changed')
                package = Path(read(OUT / 'installed-identity.json')['packageRoot']).parent
                require(wheel_proof(package) == read(OUT / 'installed-wheel-after.json'), 'Final installed integrity')
                save('source-payload-final.json', expected['files'])
                save('static-final.json', tree(package / 'amplifier_web/static'))
                if args.contour_qualification:
                    require(expected['static'] == read(OUT / 'reuse-metadata.json')['files']
                            and tree(OUT / 'static-before') == read(OUT / 'static-before.json'),
                            'Reused or preserved original static changed')
                    require(all(sha(OUT / 'prior-evidence' / name) == digest
                                for name, digest in frozen['reuseEvidence'].items()),
                            'Copied prior raw evidence changed')
                    require(all(report['phases'][name]['status'] == 'REUSED'
                                and report['phases'][name]['executed'] is False for name in REUSED_PHASES),
                            'Reused phases were rerun or lost their evidence')
                if args.coarse_toolbar_qualification:
                    built_identity = read(OUT / 'frontend-build-identity.json')
                    require(expected['static'] == built_identity['static']
                            and built_identity['reusedBuild'] is False
                            and read(ROOT / 'amplifier_web/static/build.json') == built_identity['build']
                            and report['phases']['frontend-build']['status'] == 'passed'
                            and report['phases']['frontend-build']['executed'] is True
                            and {name for name, row in report['phases'].items() if row['status'] == 'REUSED'}
                                == set(COARSE_TOOLBAR_REUSED_PHASES)
                            and all(report['phases'][name]['executed'] is False
                                    for name in COARSE_TOOLBAR_REUSED_PHASES),
                            'Fresh-build identity or unit-only reuse boundary changed')
                    require(all(sha(path) == digest for path, digest in (
                                (REUSE_INPUT / 'reuse.json', REUSE_BINDING['metadataSha256']),
                                (REUSE_INPUT / 'static.tar', REUSE_BINDING['archiveSha256']),
                                (REUSE_MANIFEST, REUSE_MANIFEST_SHA)))
                            and all(sha(folder / name) == digest
                                    for folder in (REUSE_MANIFEST.parent, OUT / 'prior-evidence')
                                    for name, digest in frozen['reuseEvidence'].items())
                            and all(sha(folder / name) == digest
                                    for folder in (COARSE_TOOLBAR_BEFORE_ROOT, OUT / 'before-evidence')
                                    for name, digest in COARSE_TOOLBAR_BEFORE['files'].items()),
                            'Immutable old build/proof/before context changed')
            step('final-integrity', final_integrity)
        checks_finished = True  # Not qualified until bounded evidence finalization also finishes.
    except BaseException as error:
        report['failure'] = {'type': type(error).__name__, 'message': str(error)}
        if active:
            report['phases'][active].update(status='failed', reason=str(error))
        for row in report['phases'].values():
            if row['status'] == 'unrun' and row['reason'] == 'not_started':
                row['reason'] = 'stopped_after_first_unexpected_failure'
    finally:
        # Keep the original absolute deadline, even after a failed or timed-out phase.
        signal.setitimer(signal.ITIMER_REAL, max(.001, evidence_deadline - time.monotonic()))
        try:
            report['finalization'] = {'status': 'running'}
            save('report.json', report)  # A killed finalizer cannot leave an overall pass.
            if checkpoint_mode and report['phases']['source-freeze']['status'] == 'passed':
                # Failure at an original corner still gets source preservation
                # checks. Keep its raw failed phase and producer report intact.
                integrity = report['phases']['final-integrity']
                integrity.update(status='running', reason=None)
                save('report.json', report)
                try:
                    save('source-after.json', frozen_inputs(frozen, built=True))
                    expected = read(OUT / 'candidate.json')
                    require(candidate(frozen)['files'] == expected['files'], 'Diagnostic source payload changed')
                    if args.portal_host_experiment:
                        built = read(OUT / 'frontend-build-identity.json')
                        require(expected['static'] == built['static'] and built['reusedBuild'] is False
                                and read(ROOT / 'amplifier_web/static/build.json') == built['build']
                                and report['phases']['frontend-build']['status'] == 'passed'
                                and report['phases']['frontend-build']['executed'] is True,
                                'Portal experiment fresh-build identity changed')
                        require(all(sha(folder / name) == digest
                                    for folder in (PORTAL_HOST_BEFORE_ROOT, OUT / 'before-evidence')
                                    for name, digest in PORTAL_HOST_BEFORE['files'].items()),
                                'Immutable check-17 before evidence changed')
                    else:
                        require(expected['static'] == read(OUT / 'reuse-metadata.json')['files'],
                                'Diagnostic static binding changed')
                    require(tree(OUT / 'static-before') == read(OUT / 'static-before.json'),
                            'Preserved original static changed')
                    if touch_checkpoint:
                        require(all(sha(OUT / 'prior-evidence' / name) == digest
                                    for name, digest in frozen['reuseEvidence'].items()),
                                'Copied prior raw evidence changed')
                        require(all(report['phases'][name]['status'] == 'REUSED'
                                    and report['phases'][name]['executed'] is False for name in reused_phases),
                                'Reused phases were rerun or lost their evidence')
                    save('source-payload-final.json', expected['files'])
                    save('static-final.json', tree(ROOT / 'amplifier_web/static'))
                    integrity.update(status='passed', reason=None)
                except BaseException as error:
                    checks_finished = False
                    integrity.update(status='failed', reason=str(error))
                    report['integrityFailure'] = {'type': type(error).__name__, 'message': str(error)}
                save('report.json', report)
            save('evidence-manifest.json', evidence_manifest(evidence_deadline))
            report['evidenceManifestSha256'] = sha(OUT / 'evidence-manifest.json')
            require(time.monotonic() < evidence_deadline, 'Evidence finalization exceeded deadline')
            report['finalization'] = {'status': 'passed'}
            report.update(passed=checks_finished and not checkpoint_mode, complete=checks_finished)
        except BaseException as error:
            report.update(passed=False, complete=False)
            report['finalization'] = {'status': 'failed', 'type': type(error).__name__, 'message': str(error)}
        finally:
            # Only the bounded final receipt gets the last five seconds. Never
            # replace the browser runner's SIGTERM/SIGINT cleanup policy.
            signal.setitimer(signal.ITIMER_REAL, max(.001, hard_deadline - time.monotonic()))
            if checkpoint_mode:
                report['outcome'] = 'checkpoint_observed_cause_unknown' if report['complete'] else 'failed'
                if touch_checkpoint and not report['checkpointObserved']:
                    report['outcome'] = 'unknown'
            report['durationSeconds'] = round(time.monotonic() - started, 3)
            save('report.json', report)
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_alarm)
    return 0 if report['passed'] or (checkpoint_mode and report['complete']) else 1


if __name__ == '__main__':
    # Private, fixed child entrypoints. No arbitrary command or selector input.
    corner = os.environ.get('FILES1318_CORNER_DIAGNOSTIC') == '1'
    contour = os.environ.get('FILES1318_CONTOUR_QUALIFICATION') == '1'
    touch_event = os.environ.get('FILES1318_TOUCH_EVENT_DIAGNOSTIC') == '1'
    native_state = os.environ.get('FILES1318_NATIVE_STATE_DIAGNOSTIC') == '1'
    portal_host = os.environ.get('FILES1318_PORTAL_HOST_EXPERIMENT') == '1'
    coarse_toolbar = os.environ.get('FILES1318_COARSE_TOOLBAR_QUALIFICATION') == '1'
    require(sum((corner, contour, touch_event, native_state, portal_host, coarse_toolbar)) <= 1, 'Conflicting child qualification modes')
    select_output(CORNER_OUT if corner else PORTAL_HOST_OUT if portal_host
                  else NATIVE_STATE_OUT if native_state else TOUCH_EVENT_OUT if touch_event
                  else COARSE_TOOLBAR_OUT if coarse_toolbar
                  else CONTOUR_OUT if contour else QUALIFIED_OUT)
    if touch_event or native_state or portal_host:
        require(os.environ.get('FILES1318_FIXTURE') != 'installed'
                and sys.argv[1:] not in (['--python-tests'], ['--installed-ready']),
                'Touch event diagnostic cannot run units or installed children')
    if os.environ.get('FILES1318_FIXTURE') in ('source', 'installed'):
        fixture_process(os.environ['FILES1318_FIXTURE'])
    elif sys.argv[1:] == ['--runtime-ready']:
        runtime_readiness()
    elif sys.argv[1:] == ['--python-tests']:
        raise SystemExit(pytest_process())
    elif sys.argv[1:] == ['--installed-ready']:
        installed_readiness()
    else:
        raise SystemExit(main())