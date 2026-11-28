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
finish() {
    local rc=$? cleaned=not-started
    trap - EXIT
    if [[ $started == true ]]; then
        cleaned=failed
        if timeout 90s "${compose[@]}" down --volumes --remove-orphans --rmi local >"$work/down.log" 2>&1; then
            local c n v i
            if c=$(docker ps -aq --filter "label=com.docker.compose.project=$project") &&
               n=$(docker network ls -q --filter "label=com.docker.compose.project=$project") &&
               v=$(docker volume ls -q --filter "label=com.docker.compose.project=$project") &&
               i=$(docker image ls -q --filter "reference=$project-*") && [[ -z $c$n$v$i ]]; then cleaned=confirmed; fi
        fi
        if [[ $cleaned != confirmed ]]; then rc=1; fi
    fi
    printf '{"phase":"%s","exitCode":%d,"cleanup":"%s","host":"%s","client":"%s","fixture":"%s","workflow":"%s","run":"%s","attempt":"%s"}\n' "$phase" "$rc" "$cleaned" "$host" "$client" "$fixture" "$GITHUB_SHA" "$GITHUB_RUN_ID" "$GITHUB_RUN_ATTEMPT" > "$evidence/hosted-result.json"
    (cd "$evidence" && sha256sum -- *.json > SHA256SUMS)
    printf 'Pairing stage=%s exit=%d cleanup=%s\n' "$phase" "$rc" "$cleaned"
    exit "$rc"
}
trap finish EXIT
[[ $(git rev-parse HEAD) == "$GITHUB_SHA" && $(git rev-parse "$host^{tree}") == 8ab9f7b7c0e10329f19412d031b2f55405238c41 ]]
[[ $(git rev-parse "$fixture^{tree}") == 25b7fee36c66f104df43ee4c6654faafd278f2e6 && $(git rev-parse "$fixture^1") == "$host" ]]
web="$PWD/.build/qualification-client"
[[ $(git -C "$web" rev-parse HEAD) == "$client" && $(git -C "$web" rev-parse HEAD^{tree}) == 077b7f593aae2b499936ef58c26f9c04ccf2037c ]]
git archive "$fixture" | tar -x -C "$work"
# Preserve exactly the public fixture blobs; the workflow and its driver are not app inputs.
stage="$work/tests/web/linked-client-candidate"
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
python3 - "$work" "$web" <<'PY'
import json, hashlib, shutil, sys
from pathlib import Path
root, web = map(Path, sys.argv[1:]); export = root / 'exported-client-inputs'
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
python3 - "$work" <<'PY'
import runpy, sys
from pathlib import Path
root = Path(sys.argv[1]); fixture = runpy.run_path(str(root / 'tests/web/linked-fresh-install.py'))
fixture['verify_static_package'](root / 'client-package')
PY
git -C "$web" archive "$client" | gzip -n > "$stage/client-source.tar.gz"
tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner -cf - -C "$work/client-package" component.json static | gzip -n > "$stage/static-component.tar.gz"
printf 'ready\n' > "$stage/fixture-mode"
(cd "$stage" && sha256sum fixture-mode selection.json client-source.tar.gz static-component.tar.gz > SHA256SUMS)
manifest=$(sha256sum "$stage/SHA256SUMS" | cut -d ' ' -f 1)
compose=(docker compose --project-name "$project" -f "$work/tests/web/linked-fresh-install.compose.yaml")
phase=compose_preflight
"${compose[@]}" config --format json | python3 -c 'import json,sys; v=json.load(sys.stdin); n=v["networks"]["peer"]; assert list(v["networks"])==["peer"] and n["internal"] and n["driver_opts"]["com.docker.network.bridge.gateway_mode_ipv4"]=="isolated"; assert v["services"]["twin"]["network_mode"]=="none"; assert all(not s.get("ports") and not s.get("privileged") and s.get("network_mode")!="host" and all(m["type"]=="volume" for m in s.get("volumes",[])) for s in v["services"].values())'
[[ -z $(docker ps -aq --filter "label=com.docker.compose.project=$project") ]]
phase=compose_launch
started=true
if ! timeout 900s "${compose[@]}" up -d --build > "$work/launch.log" 2>&1; then
    # Emit only coarse classifications, never arbitrary image/build/daemon logs.
    if grep -qi 'overlap\|address pool' "$work/launch.log"; then phase=compose_address_pool; fi
    exit 1
fi
phase=browser
set +e
timeout 300s "${compose[@]}" exec -T \
  -e "AMPLIFIER_PAIRED_HOST_REVISION=$host" -e "AMPLIFIER_PAIRED_FIXTURE_REVISION=$fixture" \
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
