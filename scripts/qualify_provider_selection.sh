#!/bin/bash
# Existing focused #1201 entry point. Run ONLY in the prepared browser-test guest.
# Source and installed journeys share the newly built bytes, never stale Git assets.
set -eu
cd /workspace
OUT=/artifacts/provider1201
mkdir -p "$OUT"
# Preserve the first failing phase and all earlier logs; there are no implicit retries.
run() {
  name=$1; shift
  printf 'starting %s\n' "$name"
  if "$@" > "$OUT/$name.log" 2>&1; then code=0; else code=$?; fi
  printf '%s=%s\n' "$name" "$code" | tee -a "$OUT/results.txt"
  if [ "$code" -ne 0 ]; then tail -80 "$OUT/$name.log"; fi
  return "$code"
}
trap 'code=$?; printf "qualifier_exit=%s\n" "$code" >> "$OUT/results.txt"' EXIT
run readiness python - <<'PY'
import os, sys, json, hashlib, importlib.metadata as m
from pathlib import Path
import amplifier_core, amplifier_core._engine, amplifier_foundation
import amplifier_module_loop_live.orchestrator, amplifier_module_loop_streaming
assert sys.version_info >= (3, 13)
contract=json.loads(Path('/opt/unified-prepared/execution-contract.json').read_text())
build=contract['pythonBuild']
assert build.get('available') and Path(build['python']).is_file(), 'Prepared wheel builder unavailable'
assert build['python']=='/opt/unified-prepared/build-venv/bin/python'
input_path=Path(os.environ['PROVIDER_SELECTION_INPUT_MANIFEST'])
inputs=json.loads(input_path.read_text())
assert inputs['commit'] and inputs['files']
for name,sha in inputs['files'].items():
    path=Path(name)
    assert not path.is_absolute() and '..' not in path.parts and not path.is_symlink()
    assert hashlib.sha256(path.read_bytes()).hexdigest()==sha, name
Path('/artifacts/provider1201/candidate-inputs.json').write_bytes(input_path.read_bytes())
modules={name:{'path':str(Path(module.__file__).resolve()),'sha256':hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()}
 for name,module in sys.modules.copy().items() if name in ('amplifier_core._engine','amplifier_foundation','amplifier_module_loop_live.orchestrator','amplifier_module_loop_streaming')}
packages={d.metadata['Name']: {'version':d.version,'source':json.loads(d.read_text('direct_url.json') or 'null')}
 for d in m.distributions() if d.metadata['Name']}
# Check actual prepared pins, not a matching loop __init__.py alone.
assert json.loads(m.distribution('amplifier-module-loop-live').read_text('direct_url.json'))['vcs_info']['commit_id']=='ccc4aa8a0bb0efdbc9e4023c2feab47d19c4bcaf'
assert json.loads(m.distribution('amplifier-module-loop-streaming').read_text('direct_url.json'))['vcs_info']['commit_id']=='270a998503c689b4b6185df817d3f6249c32e956'
Path('/artifacts/provider1201/readiness.json').write_text(json.dumps({'python':sys.version,'modules':modules,'packages':packages,'contract':contract},indent=2)+'\n')
PY
run chromium node --input-type=module - <<'JS'
import {chromium} from './frontend/node_modules/@playwright/test/index.mjs';
const browser=await chromium.launch({headless:true});
try {const page=await browser.newPage();await page.setContent('<button>Ready</button>');await page.getByRole('button',{name:'Ready'}).click();console.log('Chromium',browser.version(),'Node',process.version);} finally {await browser.close();}
JS
cp /opt/unified-prepared/execution-contract.json "$OUT/execution-contract.json"
# This remains a small affected set, not the Python/frontend full suites. Changed
# runtime/control and loop inputs mean their previous results are not blanket proof.
run python python -m pytest -q --tb=short tests/test_draft_defaults.py tests/test_provider_selection_lifecycle.py tests/test_selected_provider.py tests/test_new_chat.py tests/test_provider_recovery.py tests/test_provider_isolation.py tests/test_browser_check_runner.py::test_existing_scenario_commands_environments_and_distinct_modes_are_preserved tests/test_bundle_selection.py::test_scoped_defaults_persist_and_do_not_retarget_existing_chats
run frontend bash -c 'cd frontend && node --test tests/chat-controls.test.mjs'
# Discriminating old-behavior control; restore exact source even on runner failure.
cp frontend/src/chat-controls.jsx "$OUT/chat-controls-candidate.jsx"
python - <<'PY'
from pathlib import Path
path=Path('frontend/src/chat-controls.jsx');text=path.read_text()
old=' const resolvedDefaults=defaults;';assert text.count(old)==1
path.write_text(text.replace(old, ''' const defaultsContext=JSON.stringify([setup.location?.kind||'workspace',setup.workspace,configurationRevision]);
 const previousDefaults=useRef(null);
 if(isDraft&&defaults.phase==='ready')previousDefaults.current={context:defaultsContext,value:defaults};
 const resolvedDefaults=defaults.phase==='ready'||defaults.phase==='error'?defaults:previousDefaults.current?.context===defaultsContext?previousDefaults.current.value:defaults;'''))
PY
negative=0
run old_behavior bash -c "cd frontend && node --test --test-name-pattern='inherited choices are displayed' tests/chat-controls.test.mjs" || negative=$?
cp "$OUT/chat-controls-candidate.jsx" frontend/src/chat-controls.jsx
[ "$negative" -ne 0 ]
run negative_oracle python - <<'PY'
from pathlib import Path
log=Path('/artifacts/provider1201/old_behavior.log').read_text()
assert 'ERR_ASSERTION' in log and 'Loading model' in log and 'not ok' in log, log
assert 'ERR_LOAD_URL' not in log and 'ERR_MODULE_NOT_FOUND' not in log, log
PY
run build npm run build --prefix frontend
python - <<'PY'
import hashlib,json,tarfile
from pathlib import Path
root=Path('amplifier_web/static');assert not any(p.is_symlink() for p in root.rglob('*'))
files={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob('*')) if p.is_file()}
Path('/artifacts/provider1201/rebuilt-static.json').write_text(json.dumps(files,indent=2)+'\n')
with tarfile.open('/artifacts/provider1201/rebuilt-static.tar','w') as archive:
    for name in files: archive.add(name,arcname=name,recursive=False)
# Bind every frozen non-static tracked input; the managed guest has no .git.
inputs=json.loads(Path('/artifacts/provider1201/candidate-inputs.json').read_text())
for name,sha in inputs['files'].items():
    if not name.startswith('amplifier_web/static/'):
        assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==sha, name
assert not list(Path('.').glob('.env*')) and not list(Path('frontend').glob('.env*'))
PY
export PROVIDER_SELECTION_STATIC_MANIFEST="$OUT/rebuilt-static.json"
run wheel /opt/unified-prepared/build-venv/bin/python -m build --wheel --no-isolation --outdir /artifacts/dist /workspace
run install python - <<'PY'
import hashlib,json,pathlib,shutil,subprocess,sysconfig,venv,zipfile
out=pathlib.Path('/artifacts/provider1201');wheels=list(pathlib.Path('/artifacts/dist').glob('*.whl'));assert len(wheels)==1
wheel=wheels[0];venv.EnvBuilder(with_pip=True).create('/artifacts/installed');python='/artifacts/installed/bin/python'
subprocess.run([python,'-m','pip','install','--no-index','--no-deps',str(wheel)],check=True)
site=pathlib.Path(subprocess.check_output([python,'-c','import sysconfig; print(sysconfig.get_path("purelib"))'],text=True).strip())
# A plain path does not execute the prepared environment's editable-app .pth.
(site/'prepared-dependencies.pth').write_text(sysconfig.get_path('purelib')+'\n')
with zipfile.ZipFile(wheel) as archive:
    assert not any('.data/' in n for n in archive.namelist()), 'Unexpected relocated wheel payload; retain it and review installation mapping'
    files={n:hashlib.sha256(archive.read(n)).hexdigest() for n in archive.namelist() if not n.endswith('/') and not n.endswith('.dist-info/RECORD')}
identity={'wheelSha256':hashlib.sha256(wheel.read_bytes()).hexdigest(),'files':files,'static':{n:s for n,s in files.items() if n.startswith('amplifier_web/static/')}}
assert identity['static']==json.loads((out/'rebuilt-static.json').read_text())
for name,sha in files.items(): assert hashlib.sha256((site/name).read_bytes()).hexdigest()==sha, name
(out/'installed-identity.json').write_text(json.dumps(identity,indent=2)+'\n')
fixtures=pathlib.Path('/artifacts/installed-fixtures');fixtures.mkdir()
for name in ('empty_host_ui_server.py','configured_browser.py','provider_selection_scenario.py','provider_native_ui_server.py'):
    shutil.copyfile(pathlib.Path('/workspace/tests/fixtures')/name,fixtures/name)
PY
mkdir -p /artifacts/source-native /artifacts/installed-native /artifacts/source-synthetic /artifacts/installed-synthetic
run native_source env AMPLIFIER_TEST_ARTIFACTS=/artifacts/source-native node frontend/tests/provider-native-browser.mjs
run native_installed bash -c 'cd /artifacts && env -u PYTHONPATH AMPLIFIER_TEST_INSTALLED=1 AMPLIFIER_TEST_INSTALLED_IDENTITY=/artifacts/provider1201/installed-identity.json AMPLIFIER_TEST_DEPENDENCY_PREFIX=/opt/unified-prepared/venv AMPLIFIER_TEST_PYTHON=/artifacts/installed/bin/python AMPLIFIER_TEST_ARTIFACTS=/artifacts/installed-native PROVIDER_NATIVE_FIXTURE=/artifacts/installed-fixtures/provider_native_ui_server.py node /workspace/frontend/tests/provider-native-browser.mjs'
run synthetic_source env AMPLIFIER_TEST_ARTIFACTS=/artifacts/source-synthetic node frontend/tests/provider-selection-browser.mjs
run synthetic_installed bash -c 'cd /artifacts && env -u PYTHONPATH AMPLIFIER_TEST_INSTALLED=1 AMPLIFIER_TEST_INSTALLED_IDENTITY=/artifacts/provider1201/installed-identity.json AMPLIFIER_TEST_PYTHON=/artifacts/installed/bin/python AMPLIFIER_TEST_ARTIFACTS=/artifacts/installed-synthetic PROVIDER_SELECTION_FIXTURE=/artifacts/installed-fixtures/empty_host_ui_server.py node /workspace/frontend/tests/provider-selection-browser.mjs'
run adjacent node frontend/tests/draft-provider-refresh-browser.mjs
# Host imports this exact successful build after verifying unchanged inputs. It
# must compare final Git static bytes to rebuilt-static.json before publication.
printf 'source_and_installed_complete=0\n' >> "$OUT/results.txt"
cat "$OUT/results.txt"
