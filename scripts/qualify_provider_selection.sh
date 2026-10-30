#!/bin/bash
# Run only in the prepared managed browser-test guest, never on the development host.
set -eu
cd /workspace
mkdir -p /artifacts/provider1201
python - <<'PY' > /artifacts/provider1201/readiness.json
import sys, json, importlib.metadata as m
from pathlib import Path
import amplifier_core, amplifier_foundation, amplifier_module_loop_live.orchestrator
assert sys.version_info >= (3, 13)
build=json.loads(Path('/opt/unified-prepared/execution-contract.json').read_text())['pythonBuild']
assert build.get('available') and Path(build['python']).is_file(), 'Installed qualification requires prepared build runtime'
print(json.dumps({'python': sys.version, 'core': m.version('amplifier-core'), 'foundation': m.version('amplifier-foundation'), 'loop': m.version('amplifier-module-loop-live')}))
PY
node --input-type=module - <<'JS'
import {chromium} from './frontend/node_modules/@playwright/test/index.mjs';
const browser=await chromium.launch({headless:true});
const page=await browser.newPage();await page.setContent('<button>Ready</button>');await page.getByRole('button',{name:'Ready'}).click();console.log('Chromium',browser.version());await browser.close();
JS
cp /opt/unified-prepared/execution-contract.json /artifacts/provider1201/execution-contract.json
sha256sum uv.lock frontend/package-lock.json frontend/src/chat-controls.jsx tests/test_draft_defaults.py frontend/tests/chat-controls.test.mjs frontend/tests/provider-selection-browser.mjs tests/fixtures/provider_selection_scenario.py tests/fixtures/empty_host_ui_server.py scripts/qualify_provider_selection.sh > /artifacts/provider1201/source-sha256.txt
python - <<'PY'
import hashlib,json
from pathlib import Path
root=Path('amplifier_web/static')
assert not any(path.is_symlink() for path in root.rglob('*'))
files={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(root.rglob('*')) if path.is_file()}
Path('/artifacts/provider1201/committed-static.json').write_text(json.dumps(files,indent=2)+'\n')
PY
export PROVIDER_SELECTION_STATIC_MANIFEST=/artifacts/provider1201/committed-static.json
# Keep independent results even when a check fails. Never silently retry it.
set +e
python -m pytest -q --tb=short tests/test_draft_defaults.py tests/test_provider_selection_lifecycle.py tests/test_selected_provider.py tests/test_new_chat.py tests/test_provider_recovery.py tests/test_provider_isolation.py tests/test_bundle_selection.py::test_scoped_defaults_persist_and_do_not_retarget_existing_chats > /artifacts/provider1201/python.log 2>&1
python_result=$?
(cd frontend && node --test tests/chat-controls.test.mjs) > /artifacts/provider1201/frontend.log 2>&1
frontend_result=$?
set -e
# Old-behavior negative control: restore the precise obsolete fallback, run only
# the discriminating test, then restore the candidate before building it.
cp frontend/src/chat-controls.jsx /artifacts/provider1201/chat-controls-candidate.jsx
python - <<'PY'
from pathlib import Path
path=Path('frontend/src/chat-controls.jsx')
text=path.read_text()
old=' const resolvedDefaults=defaults;'
assert text.count(old)==1
path.write_text(text.replace(old, ''' const defaultsContext=JSON.stringify([setup.location?.kind||'workspace',setup.workspace,configurationRevision]);
 const previousDefaults=useRef(null);
 if(isDraft&&defaults.phase==='ready')previousDefaults.current={context:defaultsContext,value:defaults};
 const resolvedDefaults=defaults.phase==='ready'||defaults.phase==='error'?defaults:previousDefaults.current?.context===defaultsContext?previousDefaults.current.value:defaults;'''))
PY
set +e
(cd frontend && node --test --test-name-pattern='inherited choices are displayed' tests/chat-controls.test.mjs) > /artifacts/provider1201/negative.log 2>&1
negative_result=$?
set -e
cp /artifacts/provider1201/chat-controls-candidate.jsx frontend/src/chat-controls.jsx
sha256sum -c /artifacts/provider1201/source-sha256.txt
python - <<'PY'
from pathlib import Path
log=Path('/artifacts/provider1201/negative.log').read_text()
assert 'ERR_ASSERTION' in log and 'Loading model' in log and 'not ok' in log, log
assert 'ERR_LOAD_URL' not in log and 'ERR_MODULE_NOT_FOUND' not in log, log
PY
set +e
npm run build --prefix frontend > /artifacts/provider1201/build.log 2>&1
build_result=$?
if [ "$build_result" -eq 0 ]; then
  python - <<'PY' > /artifacts/provider1201/static-parity.json
import hashlib,json
from pathlib import Path
expected=json.loads(Path('/artifacts/provider1201/committed-static.json').read_text())
actual={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in Path('amplifier_web/static').rglob('*') if path.is_file()}
assert actual==expected, {'missing': sorted(expected.keys()-actual.keys()), 'extra': sorted(actual.keys()-expected.keys()), 'changed': [name for name in expected if name in actual and expected[name]!=actual[name]]}
print(json.dumps({'committedEqualsRebuild':True,'fileCount':len(actual),'manifestSha256':hashlib.sha256(Path('/artifacts/provider1201/committed-static.json').read_bytes()).hexdigest()}))
PY
  parity_result=$?
  node frontend/tests/provider-selection-browser.mjs > /artifacts/provider1201/browser.log 2>&1
  browser_result=$?
  node frontend/tests/draft-provider-refresh-browser.mjs > /artifacts/provider1201/adjacent-browser.log 2>&1
  adjacent_result=$?
else
  browser_result=125
  adjacent_result=125
  parity_result=125
fi
set -e
# Installed package boundary: fresh venv, exact wheel, prepared dependency paths
# without processing their editable-app .pth; leave source and unset PYTHONPATH.
/opt/unified-prepared/build-venv/bin/python -m build --wheel --no-isolation --outdir /artifacts/dist /workspace > /artifacts/provider1201/wheel.log 2>&1
python - <<'PY'
import hashlib,json,pathlib,shutil,subprocess,sys,sysconfig,venv,zipfile
out=pathlib.Path('/artifacts/provider1201')
wheel=next(pathlib.Path('/artifacts/dist').glob('*.whl'))
venv.EnvBuilder(with_pip=True).create('/artifacts/installed')
python='/artifacts/installed/bin/python'
subprocess.run([python,'-m','pip','install','--no-index','--no-deps',str(wheel)],check=True)
site=pathlib.Path(subprocess.check_output([python,'-c','import sysconfig; print(sysconfig.get_path("purelib"))'],text=True).strip())
# Referencing a site directory in .pth adds imports, not its nested .pth hooks.
(site/'prepared-dependencies.pth').write_text(sysconfig.get_path('purelib')+'\n')
with zipfile.ZipFile(wheel) as archive:
    files={name:hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist() if not name.endswith('/') and not name.endswith('.dist-info/RECORD')}
identity={'wheelSha256':hashlib.sha256(wheel.read_bytes()).hexdigest(),'files':files,'static':{name:sha for name,sha in files.items() if name.startswith('amplifier_web/static/')}}
committed=json.loads((out/'committed-static.json').read_text())
assert identity['static']==committed, 'Wheel static payload differs from committed candidate'
identity['committedStaticParity']=True
(out/'installed-identity.json').write_text(json.dumps(identity,indent=2))
fixtures=pathlib.Path('/artifacts/installed-fixtures');fixtures.mkdir()
for name in ['empty_host_ui_server.py','configured_browser.py','provider_selection_scenario.py']:
    shutil.copyfile(pathlib.Path('/workspace/tests/fixtures')/name,fixtures/name)
PY
mkdir -p /artifacts/installed-proof
set +e
(cd /artifacts && env -u PYTHONPATH AMPLIFIER_TEST_INSTALLED=1 AMPLIFIER_TEST_INSTALLED_IDENTITY=/artifacts/provider1201/installed-identity.json AMPLIFIER_TEST_PYTHON=/artifacts/installed/bin/python AMPLIFIER_TEST_ARTIFACTS=/artifacts/installed-proof PROVIDER_SELECTION_FIXTURE=/artifacts/installed-fixtures/empty_host_ui_server.py node /workspace/frontend/tests/provider-selection-browser.mjs) > /artifacts/provider1201/installed-browser.log 2>&1
installed_result=$?
set -e
printf 'python=%s frontend=%s negative_expected_nonzero=%s build=%s static_parity=%s browser=%s adjacent=%s installed=%s\n' "$python_result" "$frontend_result" "$negative_result" "$build_result" "$parity_result" "$browser_result" "$adjacent_result" "$installed_result" | tee /artifacts/provider1201/results.txt
cat /artifacts/provider1201/python.log /artifacts/provider1201/frontend.log /artifacts/provider1201/browser.log /artifacts/provider1201/installed-browser.log
[ "$python_result" -eq 0 ] && [ "$frontend_result" -eq 0 ] && [ "$negative_result" -ne 0 ] && [ "$build_result" -eq 0 ] && [ "$parity_result" -eq 0 ] && [ "$browser_result" -eq 0 ] && [ "$adjacent_result" -eq 0 ] && [ "$installed_result" -eq 0 ]
