"""Safe diagnostics survive worker IPC and persisted application state."""
import json
import sys

import pytest

from amplifier_web.module_failures import ConfiguredModuleError, REMEDIATION, persist_failures, safe_failures
from amplifier_web.runtime import RuntimeManager
from amplifier_web.service import AppService


@pytest.mark.parametrize('reason',list(REMEDIATION))
def test_diagnostics_allowlist_persistence(tmp_path,reason):
    original=[{'module':'tool-fixture','type':'tool','reason_code':reason,'error':'private-token','path':'/private-token','source':'https://private-token','guidance':'private-token'}]
    error=persist_failures(tmp_path,original)
    saved=json.loads((tmp_path/'module-load-failures.json').read_text())
    assert saved==error.failures==safe_failures(original)
    assert saved[0]['guidance']==REMEDIATION[reason]
    assert 'private-token' not in str(error)+json.dumps(saved)
    assert (tmp_path/'module-load-failures.json').stat().st_mode & 0o077 == 0


def test_unknown_and_legacy_data_never_echo_untrusted_fields():
    assert safe_failures([{'module':'https://private-token','type':{},'reason_code':{'token':'private-token'}}])==[
        {'module':'unknown','type':'unknown','reason_code':'unknown','guidance':REMEDIATION['unknown']}]
    assert safe_failures([{'module':'tool-fixture','type':'tool'}])[0]['reason_code']=='unknown'


@pytest.mark.asyncio
async def test_real_worker_protocol_preserves_only_safe_diagnostics(tmp_path):
    failures=[{'module':'tool-fixture','type':'tool','reason_code':'invalid_entry_point','error':'private-token'}]
    script='import sys,json\njson.loads(sys.stdin.readline())\nprint('+repr(json.dumps({'type':'runtime.error','code':'module_load_failed','moduleFailures':failures,'error':'private-token'}))+',flush=True)\n'
    runtime=RuntimeManager(command=[sys.executable,'-c',script],startup_timeout=3)
    events=[]
    async def emit(kind,data):events.append((kind,data))
    try:
        with pytest.raises(ConfiguredModuleError):
            await runtime.start({'id':'fixture','workspace':str(tmp_path),'bundle':'work'},emit)
        payload=next(data for kind,data in events if kind=='runtime.error')
        assert payload['moduleFailures'][0]['reason_code']=='invalid_entry_point'
        assert 'private-token' not in json.dumps(events)
    finally:await runtime.close()


@pytest.mark.asyncio
async def test_service_retains_diagnostic_on_reload_and_clears_after_recovery(tmp_path):
    app=AppService(tmp_path,workspace=tmp_path)
    await app.dispatch('session.create',{})
    sid=app.state['selectedSessionId']
    await app.on_runtime_event('runtime.error',{'sessionId':sid,'error':'private-token',
        'moduleFailures':[{'module':'tool-fixture','type':'tool','reason_code':'invalid_package_layout','error':'private-token'}]})
    assert 'private-token' not in json.dumps(app._session(sid))
    await app.close()
    restored=AppService(tmp_path,workspace=tmp_path)
    try:
        assert restored._session(sid)['moduleFailures'][0]['reason_code']=='invalid_package_layout'
        assert 'package layout' in restored._session(sid)['error']
        restored._session(sid)['health'] = {'moduleFailures': restored._session(sid)['moduleFailures']}
        await restored.on_runtime_event('runtime.status',{'sessionId':sid,'status':'ready'})
        assert 'moduleFailures' not in restored._session(sid)
        assert 'moduleFailures' not in restored._session(sid)['health']
    finally:await restored.close()


def test_native_history_inspection_reads_only_safe_current_failure(tmp_path):
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.session_health import inspect_session
    from amplifier_web.module_failures import clear_failures
    session={'id':'native-fixture','runtimeSessionId':'native-fixture','workspace':str(tmp_path),'status':'idle'}
    directory=SessionStore.for_app(tmp_path,tmp_path).directory(session['id'])
    persist_failures(directory,[{'module':'tool-fixture','type':'tool','reason_code':'missing_source','error':'private-token'}])
    report=inspect_session(tmp_path,session)
    assert report['moduleFailures'][0]['reason_code']=='missing_source'
    assert 'private-token' not in json.dumps(report)
    clear_failures(directory)
    assert inspect_session(tmp_path,session)['moduleFailures']==[]


def test_current_worker_report_wins_over_native_history_and_clears(tmp_path):
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.session_health import inspect_session
    from amplifier_web.module_failures import clear_failures
    session = {"id": "native-fixture", "workspace": str(tmp_path), "status": "error"}
    legacy = SessionStore.for_app(tmp_path, tmp_path).directory(session["id"])
    current = tmp_path / "runtime-reports" / session["id"]
    persist_failures(legacy, [{"module": "old-tool", "type": "tool", "reason_code": "missing_source"}])
    failure = persist_failures(current, [{"module": "current-hook", "type": "hook", "reason_code": "invalid_module_metadata"}])
    assert inspect_session(tmp_path, session)["moduleFailures"] == failure.failures
    clear_failures(current)
    assert inspect_session(tmp_path, session)["moduleFailures"] == []
