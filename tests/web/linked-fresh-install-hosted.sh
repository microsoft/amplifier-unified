#!/usr/bin/env bash
set -euo pipefail
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux ]]
[[ ${GITHUB_REPOSITORY:-} == microsoft/amplifier-unified && ${GITHUB_REF:-} == refs/heads/ci/2525-fresh-lan ]]
[[ ${GITHUB_EVENT_NAME:-} == workflow_dispatch && ${GITHUB_RUN_ID:-} =~ ^[0-9]+$ && ${GITHUB_RUN_ATTEMPT:-} =~ ^[0-9]+$ ]]
readonly host=de5c5bf93f88a64c8cd9297f3cb8cd31dc3c22e5 client=1176e683897e1f4fb472d68c2d722b613faa0eec inputs=c8576e108d0a0fd0b306c522826ec3135eab040dbdc596511babf974fe590f10
readonly fixture=50b9b631464e790015cd865e19c6e8dd3bcfdf1f
readonly component=78b52a9cbfc9d69ff631542eb80be08202f6af9c1af04928be2aa6109185b438
readonly project="fresh-lan-2525-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
readonly evidence="$RUNNER_TEMP/fresh-lan-evidence" work="$RUNNER_TEMP/fresh-lan-source"
mkdir "$evidence" "$work"
phase=immutable_source
compose=()
started=false
probe_started=false
probe_cleanup_attempted=false
readonly probe_project="$project-probe"
probe=(docker compose --project-name "$probe_project" -f "$work/probe.json")
reason=not-observed
stage_status=0
clean_project() {
    local name=$1; shift
    local c n v i
    timeout 90s "$@" down --volumes --remove-orphans --rmi local >"$work/down-$name.log" 2>&1 &&
        c=$(docker ps -aq --filter "label=com.docker.compose.project=$name") &&
        n=$(docker network ls -q --filter "label=com.docker.compose.project=$name") &&
        v=$(docker volume ls -q --filter "label=com.docker.compose.project=$name") &&
        i=$(docker image ls -q --filter "reference=$name-*") &&
        [[ -z $c$n$v$i ]]
}
classify_launch() {
    local log=$1
    reason=unclassified
    if (( stage_status == 124 )); then reason=timeout
    elif grep -qiE 'overlap|address pool|could not find an available.*address' "$log"; then reason=address-pool-conflict
    elif grep -qiE 'gateway mode|gateway_mode_ipv4' "$log"; then reason=bridge-gateway-mode
    elif grep -Fqi 'interface_name requires Docker Engine v28.1 or later' "$log"; then reason=engine-interface-name-unsupported
    elif grep -qiE 'interface_name|rename.*interface|veth' "$log"; then reason=network-interface
    elif grep -qiE 'conflict.*already in use|container name.*already' "$log"; then reason=container-name-conflict
    elif grep -qiE 'failed to (create|set up).*network|Error.*(iptables|nftables)' "$log"; then reason=network-driver
    elif grep -qiE 'tmpfs|mount.*(invalid|denied)|invalid mount' "$log"; then reason=container-mount
    elif grep -qiE 'oci runtime|failed to (create|start).*task|failed to create shim' "$log"; then reason=container-runtime
    elif grep -qiE 'permission denied|operation not permitted' "$log"; then reason=permission
    elif grep -qiE 'no space left|disk quota' "$log"; then reason=storage
    elif grep -qiE 'executable file not found|exec format error' "$log"; then reason=container-entrypoint
    elif grep -qiE 'no such image|pull access denied|manifest unknown' "$log"; then reason=image-unavailable
    fi
}
observe_launch() {
    local phase_name=$1 project_name=$2
    # Inspect only booleans and counts for our exact project, never inspect env or log payloads.
    local networks=unavailable created=unavailable running=unavailable
    local rows
    if rows=$(docker network ls -q --filter "label=com.docker.compose.project=$project_name"); then
        networks=$(printf '%s\n' "$rows" | grep -c . || true)
    fi
    if rows=$(docker ps -aq --filter "label=com.docker.compose.project=$project_name"); then
        created=$(printf '%s\n' "$rows" | grep -c . || true)
    fi
    if rows=$(docker ps -q --filter "label=com.docker.compose.project=$project_name"); then
        running=$(printf '%s\n' "$rows" | grep -c . || true)
    fi
    printf '{"stage":"%s","exitCode":%s,"reason":"%s","networkCount":"%s","containerCount":"%s","runningCount":"%s"}\n' \
        "$phase_name" "$stage_status" "$reason" "$networks" "$created" "$running" > "$evidence/$phase_name.json"
}
launch_step() {
    local name=$1 project_name=$2; shift 2
    phase=$name
    stage_status=0
    reason=none
    timeout 90s "$@" > "$work/$name.log" 2>&1 || stage_status=$?
    if (( stage_status != 0 )); then classify_launch "$work/$name.log"; fi
    observe_launch "$name" "$project_name"
    return "$stage_status"
}
finish() {
    local rc=$? cleaned=not-started probe_cleaned=not-started
    trap - EXIT
    if [[ $started == true ]]; then
        cleaned=failed
        if clean_project "$project" "${compose[@]}"; then cleaned=confirmed; fi
        if [[ $cleaned != confirmed ]]; then rc=1; fi
    fi
    if [[ $probe_started == true && $probe_cleanup_attempted == false ]]; then
        probe_cleaned=failed
        if clean_project "$probe_project" "${probe[@]}"; then probe_cleaned=confirmed; else rc=1; fi
    elif [[ $probe_started == true ]]; then
        probe_cleaned=failed
        rc=1
    elif [[ -f $evidence/probe-cleanup.json ]]; then
        probe_cleaned=confirmed
    fi
    printf '{"phase":"%s","exitCode":%d,"cleanup":"%s","probeCleanup":"%s","host":"%s","client":"%s","fixture":"%s","workflow":"%s","run":"%s","attempt":"%s"}\n' "$phase" "$rc" "$cleaned" "$probe_cleaned" "$host" "$client" "$fixture" "$GITHUB_SHA" "$GITHUB_RUN_ID" "$GITHUB_RUN_ATTEMPT" > "$evidence/hosted-result.json"
    (cd "$evidence" && sha256sum -- *.json > SHA256SUMS)
    printf 'Pairing stage=%s exit=%d cleanup=%s\n' "$phase" "$rc" "$cleaned"
    exit "$rc"
}
trap finish EXIT
[[ $(git rev-parse HEAD) == "$GITHUB_SHA" && $(git rev-parse "$host^{tree}") == 8ab9f7b7c0e10329f19412d031b2f55405238c41 ]]
[[ $(git rev-parse "$fixture^{tree}") == 25b7fee36c66f104df43ee4c6654faafd278f2e6 && $(git rev-parse "$fixture^1") == "$host" ]]
web="$PWD/.build/qualification-client"
[[ $(git -C "$web" rev-parse HEAD) == "$client" && $(git -C "$web" rev-parse HEAD^{tree}) == 077b7f593aae2b499936ef58c26f9c04ccf2037c ]]
context="$work/source"
mkdir "$context"
# Use only tracked source plus the reviewed fixture-only changes on this branch.
git archive "$GITHUB_SHA" | tar -x -C "$context"
compose=(docker compose --project-name "$project" -f "$context/tests/web/linked-fresh-install.compose.yaml")
phase=compose_preflight
engine=$(docker version --format '{{.Server.Version}}' 2>"$work/engine.log")
compose_version=$(docker compose version --short 2>"$work/compose-version.log")
version_pattern='^v?[0-9]+[.][0-9]+[.][0-9]+([+._-][a-zA-Z0-9+._-]+)?$'
[[ $engine =~ $version_pattern && $compose_version =~ $version_pattern ]]
printf '{"dockerEngine":"%s","dockerCompose":"%s"}\n' "$engine" "$compose_version" > "$evidence/platform.json"
"${compose[@]}" config --format json > "$work/resolved.json" 2>"$work/config.log"
python3 - "$work/resolved.json" "$work/probe.json" "$probe_project" <<'PY'
import json, sys
from pathlib import Path
source, target = map(Path, sys.argv[1:3]); project = sys.argv[3]
v = json.loads(source.read_bytes()); n = v['networks']['peer']; services = v['services']
assert set(services) == {'twin', 'fresh-source', 'fresh-destination'}
assert list(v['networks']) == ['peer'] and n['internal']
assert n['driver_opts']['com.docker.network.bridge.gateway_mode_ipv4'] == 'isolated'
assert services['twin']['network_mode'] == 'none'
for name, service in services.items():
    assert not service.get('ports') and not service.get('privileged') and service.get('network_mode') != 'host'
    assert all(m['type'] == 'volume' for m in service.get('volumes', []))
    if name != 'twin':
        # Compose 2.38 rejects custom interface names on Docker Engine 28.0.
        assert set(service['networks']) == {'peer'} and not service['networks']['peer'].get('interface_name')
    service.pop('build')
    service.pop('entrypoint', None)
    service['image'] = 'python:3.13-slim-bookworm'
    service['command'] = ['sleep', '180']
v['name'] = project
n['name'] = project + '_peer'
for name, volume in v['volumes'].items():
    volume['name'] = project + '_' + name
target.write_text(json.dumps(v))
PY
phase=probe_create
probe_started=true
launch_step probe_create "$probe_project" "${probe[@]}" create
launch_step probe_start "$probe_project" "${probe[@]}" start
phase=probe_readiness
stage_status=0
reason=none
for guest in twin fresh-source fresh-destination; do
    timeout 15s "${probe[@]}" exec -T "$guest" python -c '
import socket, sys
from pathlib import Path
assert {name for _, name in socket.if_nameindex()} == ({"lo"} if sys.argv[1] == "twin" else {"lo", "eth0"})
assert all(line.split()[1] != "00000000" for line in Path("/proc/net/route").read_text().splitlines()[1:])
' "$guest" >> "$work/probe-readiness.log" 2>&1 || { stage_status=$?; reason=interface-or-route-not-confirmed; break; }
done
observe_launch probe_readiness "$probe_project"
(( stage_status == 0 ))
phase=probe_cleanup
probe_cleanup_attempted=true
clean_project "$probe_project" "${probe[@]}"
probe_started=false
printf '{"cleanup":"confirmed"}\n' > "$evidence/probe-cleanup.json"
stage="$context/tests/web/linked-client-candidate"
mkdir "$stage"
node "$web/scripts/client-source.mjs" record "$stage/selection.json"
python3 - "$stage/selection.json" "$inputs" <<'PY'
from pathlib import Path
import json, sys
p = Path(sys.argv[1]); record = json.loads(p.read_bytes())
assert record['inputsSha256'] == sys.argv[2]
record['delivery'] = {'type': 'directory', 'path': '.build/paired-candidate-source'}
p.write_text(json.dumps(record) + '\n')
PY
phase=canonical_package
export CI=true TZ=UTC LC_ALL=C.UTF-8
python3 - "$context" "$web" "$work" <<'PY'
import json, hashlib, shutil, sys
from pathlib import Path
root, web, work = map(Path, sys.argv[1:]); export = work / 'exported-client-inputs'
export.mkdir()
lock = web / 'build-inputs.lock.json'; shutil.copyfile(lock, export / lock.name)
for name, check in json.loads(lock.read_bytes())['inputs'].items():
    b = (root / name).read_bytes()
    assert check == {'bytes': len(b), 'sha256': hashlib.sha256(b).hexdigest()}, name
    p = export / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b)
PY
node "$web/scripts/prepare-inputs.mjs" "$work/exported-client-inputs"
(cd "$web" && npm ci && npm run build && node scripts/package-client.mjs "$work/client-package" "$host" --source-record "$stage/selection.json") > "$work/client.log" 2>&1
actual=$(sha256sum "$work/client-package/component.json" | cut -d ' ' -f 1)
printf '{"expected":"%s","actual":"%s","node":"%s"}\n' "$component" "$actual" "$(node --version)" > "$evidence/static-stamp.json"
[[ $actual == "$component" ]]
python3 - "$context" "$work" <<'PY'
import runpy, sys
from pathlib import Path
root, work = map(Path, sys.argv[1:]); fixture = runpy.run_path(str(root / 'tests/web/linked-fresh-install.py'))
fixture['verify_static_package'](work / 'client-package')
PY
git -C "$web" archive "$client" | gzip -n > "$stage/client-source.tar.gz"
tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner -cf - -C "$work/client-package" component.json static | gzip -n > "$stage/static-component.tar.gz"
printf 'ready\n' > "$stage/fixture-mode"
(cd "$stage" && sha256sum fixture-mode selection.json client-source.tar.gz static-component.tar.gz > SHA256SUMS)
manifest=$(sha256sum "$stage/SHA256SUMS" | cut -d ' ' -f 1)
[[ -z $(docker ps -aq --filter "label=com.docker.compose.project=$project") ]]
phase=image_build
started=true
if ! timeout 900s "${compose[@]}" build > "$work/build.log" 2>&1; then
    if grep -qiE 'could not read Username|authentication failed|repository not found' "$work/build.log"; then phase=image_public_dependency_unavailable
    elif grep -qi 'no space left on device' "$work/build.log"; then phase=image_storage_exhausted
    elif grep -qiE 'failed to resolve source metadata|pull access denied|manifest unknown' "$work/build.log"; then phase=image_base_unavailable
    elif grep -qi 'no installation candidate\\|unable to locate package' "$work/build.log"; then phase=image_system_dependency_unavailable
    elif grep -qiE 'could not import|ModuleNotFoundError|ImportError' "$work/build.log"; then phase=image_import_failed
    fi
    exit 1
fi
launch_step compose_create "$project" "${compose[@]}" create --no-build
launch_step compose_start "$project" "${compose[@]}" start
phase=guest_readiness
stage_status=0
reason=none
timeout 75s "${compose[@]}" exec -T twin .venv/bin/python -c '
import time
from pathlib import Path
for _ in range(280):
    roots = (Path("/run/link-source"), Path("/run/link-destination"))
    if any((root / "fresh-failed.json").is_file() for root in roots):
        raise SystemExit(3)
    if all((root / "fresh-ready.json").is_file() for root in roots):
        break
    time.sleep(.25)
else:
    raise SystemExit(4)
' > "$work/guest-readiness.log" 2>&1 || stage_status=$?
case $stage_status in
    0) ;;
    3) reason=guest-reported-startup-failure ;;
    4|124) reason=guest-readiness-timeout ;;
    *) classify_launch "$work/guest-readiness.log" ;;
esac
observe_launch guest_readiness "$project"
(( stage_status == 0 ))
phase=browser
set +e
timeout 300s "${compose[@]}" exec -T \
  -e "AMPLIFIER_PAIRED_HOST_REVISION=$host" -e "AMPLIFIER_PAIRED_FIXTURE_REVISION=$GITHUB_SHA" \
  -e "AMPLIFIER_PAIRED_CLIENT_REVISION=$client" -e "AMPLIFIER_PAIRED_INPUTS_SHA256=$inputs" \
  -e "AMPLIFIER_PAIRED_MANIFEST_SHA256=$manifest" -e AMPLIFIER_TEST_ARTIFACTS=/run/link-control \
  twin .venv/bin/python tests/web/linked-fresh-install.py coordinate > "$work/check.log" 2>&1
status=$?
set -e
for name in linked-fresh-install-result.json verified-client.json; do
    if timeout 10s "${compose[@]}" cp "twin:/run/link-control/$name" "$work/$name" >/dev/null 2>&1; then
        # Retain only bounded fixture summaries or the already-public package descriptor.
        if [[ $name == verified-client.json ]]; then
            [[ $(sha256sum "$work/$name" | cut -d ' ' -f 1) == "$component" ]]
        else
            python3 -m json.tool "$work/$name" > /dev/null
            [[ $(wc -c < "$work/$name") -lt 32768 ]]
        fi
        cp "$work/$name" "$evidence/$name"
    fi
done
[[ $status == 0 ]]
python3 - "$evidence/linked-fresh-install-result.json" <<'PY'
import json, sys
r=json.load(open(sys.argv[1])); assert r['checkPassed'] is True
assert set(r['guests'])=={'source','destination'} and all(g['checkPassed'] for g in r['guests'].values())
assert 'native-avahi-and-owner-browse-empty-after-explicit-withdrawal' in r['guests']['destination']['checkpoints']
assert 'real-post-persistence-503-preserved-and-one-explicit-identical-retry-confirmed' in r['guests']['destination']['checkpoints']
assert 'owner-revokes-exact-saved-grant-locally-while-peer-socket-stays-closed' in r['guests']['source']['checkpoints']
PY
phase=verified_browser
