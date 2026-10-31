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
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib
import importlib.machinery
import importlib.metadata as metadata
import json
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
import time
import tomllib
import xml.etree.ElementTree as ET

# -I deliberately removes the script directory as well as PYTHONPATH. Restore
# only this frozen helper directory, never the checkout in an installed child.
sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path('/workspace')
OUT = Path('/artifacts/files1318-qualified')
PREPARED = Path('/opt/unified-prepared')
PYTHON = PREPARED / 'venv/bin/python'
BUILD_PYTHON = PREPARED / 'build-venv/bin/python'
NODE = PREPARED / 'node-runtime/bin/node'
NPM = PREPARED / 'node-runtime/bin/npm'
UV = PREPARED / 'bootstrap/bin/uv'
CONTRACT = PREPARED / 'execution-contract.json'
MANIFEST = ROOT / 'output/files1318-frozen-manifest.json'
HARNESS = OUT / 'harness'
VENV = OUT / 'installed'
DRIVER = 'scripts/files1318_integrated_checks.py'
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
                and str(Path(name)) == name and path != MANIFEST, 'Unsafe frozen path')
        require(path.parent.resolve().is_relative_to(ROOT), 'Frozen path escapes snapshot')
        require(expected['mode'] in ('100644', '100755', '120000')
                and re.fullmatch('[a-f0-9]{64}', expected['sha256']), 'Invalid frozen identity')
        if built and name.startswith('amplifier_web/static/'):
            continue  # Canonical npm build replaces this entire generated directory.
        actual[name] = file_identity(path)
        require(actual[name] == expected, f'Frozen input changed: {name}')
    return actual


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

    args = ['-c', '/dev/null', '--rootdir=/workspace', '--import-mode=importlib',
            '-p', 'no:cacheprovider', '-p', 'pytest_asyncio.plugin', '-p', 'pytest_aiohttp.plugin',
            '--asyncio-mode=auto', '-x', '-v', '--tb=short',
            '--junitxml=' + str(OUT / 'python.xml'), *[str(ROOT / n) for n in PYTEST_NODES]]
    save('pytest-call.json', {'call': 'pytest.main', 'args': args})
    try:
        code = pytest.main(args, plugins=[Collection()])
    finally:
        save('python-origins-after.json', origins(ROOT))
    return int(code)


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


def junit():
    root = ET.parse(OUT / 'python.xml').getroot()
    cases = root.findall('.//testcase')
    counts = {'tests': len(cases), **{tag: len(root.findall('.//' + tag))
                                    for tag in ('failure', 'error', 'skipped')}}
    save('python-counts.json', counts)
    require(counts == {'tests': len(PYTEST_NODES), 'failure': 0, 'error': 0, 'skipped': 0},
            'JUnit must contain exactly the selection, with zero skip/error/fail')
    require(Counter(read(OUT / 'python-collection.json')) == Counter(PYTEST_NODES), 'Collection changed')
    require(Counter(c.attrib['name'] for c in cases) ==
            Counter(n.split('::')[-1] for n in PYTEST_NODES), 'JUnit identities differ')


def unit_proof():
    text = (OUT / 'frontend-units/frontend-units.log').read_text()
    expected = []
    for name in UNIT_FILES:
        expected += re.findall(r"^test\('([^']+)',", (ROOT / name).read_text(), re.M)
    require(len(expected) == 19 and Counter(re.findall(r'^ok \d+ - (.+)$', text, re.M)) ==
            Counter(expected), 'Frontend exact test identities differ')
    for key in ('tests', 'pass', 'fail', 'cancelled', 'skipped', 'todo'):
        require(re.findall(rf'^# {key} (\d+)$', text, re.M) ==
                [str(19 if key in ('tests', 'pass') else 0)], f'Frontend {key} mismatch')
    require(not re.search(r'^not ok\b|^Bail out!', text, re.M), 'Frontend failure')


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
    counts = Counter(re.findall(r"checks\.push\(\{check:'([^']+)'", source))
    require(len(counts) == 15 and set(counts.values()) == {1}, 'Journey check definitions changed')
    taps = [g for g in proof['geometry'] if g['label'] == 'corner-tap-before']
    require(len(taps) == 14, 'Missing corner-tap measurements')
    counts.update({'real clipboard + Copied.': 16, 'deliberate saved-v1 preview setup, not a download': 1,
                   'right-rail paths/download and primary Download preserve running saved-v1 HTML': 2})
    counts['old padding-origin conversion rejected by same exact-corner oracle'] = sum(
        bool(g['borders']['left'] or g['borders']['top']) for g in taps)
    require(Counter(c['check'] for c in proof['checks']) == counts, 'Journey check coverage/counts changed')
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


def generated_proof(mode):
    reports = list((OUT / f'generated-{mode}/evidence').glob('generated-file-controls-*/observations.json'))
    require(len(reports) == 1, 'Require one generated-file journey report')
    proof = read(reports[0])
    require(proof['passed'] is True and proof['errors'] == [] and proof['screenshotErrors'] == []
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
    save(f'generated-{mode}/accepted-report.json', {
        'path': str(reports[0]), 'sha256': sha(reports[0]), 'visualJudgment': 'pending',
        'frameBindingCounts': dict(Counter(c['binding']['status'] for c in proof['fileFrameCaptures']))})


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
    parser.add_argument('--manifest', required=True, type=Path, choices=[MANIFEST])
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=False)  # No replay or overwrite of a prior cycle.
    report = {'schemaVersion': 1, 'passed': False, 'complete': False,
              'budgetSeconds': BUDGET, 'preparationBudgetSeconds': 1800,
              'finalizationReserveSeconds': FINALIZATION_RESERVE, 'cleanupReserveSeconds': CLEANUP_RESERVE,
              'receiptReserveSeconds': RECEIPT_RESERVE, 'finalization': {'status': 'unrun'},
              'phases': {name: {'status': 'unrun', 'reason': 'not_started'} for name in PHASES},
              'limits': ['Synthetic fixtures, Chromium desktop/touch emulation; not physical devices.',
                         'Locked prepared test dependencies reused, not a minimal dependency install.',
                         'No publication, full suite, provider, OS reveal or independent pixel acceptance.']}
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
            require(isinstance(changed, list) and len(changed) == len(set(changed))
                    and DRIVER in changed and set(changed) <= REVIEWED_CHANGES
                    and set(changed) <= frozen['files'].keys(), 'Candidate changes exceed fixed test-only scope')
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
            renderer_source = (ROOT / 'frontend/tests/canvas-renderers-browser.mjs').read_text()
            if ('CANVAS_RENDERER_STALLED_BODY' not in renderer_source
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
            for path in (PYTHON, BUILD_PYTHON, NODE, NPM, UV):
                require(path.is_file() and os.access(path, os.X_OK), f'Missing executable: {path}')
            save('executables.json', {str(path): {'resolved': str(path.resolve()), 'sha256': sha(path)}
                                     for path in (PYTHON, BUILD_PYTHON, NODE, NPM, UV)})
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
                require(raw['cleanup'] == CLEAN and raw['reason'] is None, f'{name}: process/cleanup failure')
                require(result['complete'] is True and result['passed'] is (not negative)
                        and raw['status'] == ('failed' if negative else 'passed')
                        and type(raw['exitCode']) is int and raw['exitCode'] == (1 if negative else 0)
                        and code == (1 if negative else 0), f'{name}: unexpected exit')
                if verify:
                    verify()
                require(time.monotonic() < work_deadline, f'{name}: verification exceeded work deadline')
                row['verdict'] = 'expected_stalled_response_failure' if negative else 'passed'
            step(name, execute)

        run('runtime-readiness', [PYTHON, '-I', '-B', ROOT / DRIVER, '--runtime-ready'], cap=45)
        run('build-readiness', [BUILD_PYTHON, '-I', '-B', '-c',
            "import build,hatchling,sys; assert sys.version_info >= (3,13); "
            "assert not build.ProjectBuilder('/workspace').check_dependencies('wheel')"], cap=30)
        run('browser-readiness', [NODE, '--input-type=module', '-e', BROWSER_READY], cap=45)
        run('frontend-build', [NPM, 'run', 'build'], cap=240)

        def source_freeze():
            save('source-after-build.json', frozen_inputs(frozen, built=True))
            value = candidate(frozen)
            require(value['static'] and 'amplifier_web/static/index.html' in value['static'], 'No built static')
            save('candidate.json', value)
            save('static.json', value['static'])
        step('source-freeze', source_freeze)
        run('python-tests', [PYTHON, '-I', '-B', ROOT / DRIVER, '--python-tests'], cap=180, verify=junit)
        run('frontend-units', [NODE, '--test', '--test-reporter=tap',
                              *(ROOT / name for name in UNIT_FILES)], cap=60, verify=unit_proof)

        def generated(mode):
            root = HARNESS if mode == 'installed' else ROOT
            extra = {'FILE_CONTROLS_FIXTURE': str(root / DRIVER), 'FILES1318_FIXTURE': mode}
            if mode == 'installed':
                extra.update(AMPLIFIER_TEST_PYTHON=str(VENV / 'bin/python'), AMPLIFIER_TEST_INSTALLED='1',
                             AMPLIFIER_TEST_PACKAGE_ROOT=read(OUT / 'installed-identity.json')['packageRoot'])
            run('topology-' + mode, [NODE, 'tests/file-controls-topology-preflight.mjs'], root=root,
                extra={'FILE_CONTROLS_PREFLIGHT_EVIDENCE': str(OUT / f'topology-{mode}/evidence')},
                cap=80, verify=lambda: topology_proof(mode))
            run('generated-' + mode, [NODE, 'tests/generated-file-controls-browser.mjs'],
                root=root, extra=extra, cap=160, verify=lambda: generated_proof(mode))

        generated('source')
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
            save('evidence-manifest.json', evidence_manifest(evidence_deadline))
            report['evidenceManifestSha256'] = sha(OUT / 'evidence-manifest.json')
            require(time.monotonic() < evidence_deadline, 'Evidence finalization exceeded deadline')
            report['finalization'] = {'status': 'passed'}
            report.update(passed=checks_finished, complete=checks_finished)
        except BaseException as error:
            report.update(passed=False, complete=False)
            report['finalization'] = {'status': 'failed', 'type': type(error).__name__, 'message': str(error)}
        finally:
            # Only the bounded final receipt gets the last five seconds. Never
            # replace the browser runner's SIGTERM/SIGINT cleanup policy.
            signal.setitimer(signal.ITIMER_REAL, max(.001, hard_deadline - time.monotonic()))
            report['durationSeconds'] = round(time.monotonic() - started, 3)
            save('report.json', report)
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_alarm)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    # Private, fixed child entrypoints. No arbitrary command or selector input.
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