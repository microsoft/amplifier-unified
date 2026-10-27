"""Credential-free update receipts; never retain subprocess text or arguments."""
from __future__ import annotations

import json
import errno
import os
import re
import sqlite3
import time
import uuid

PROBE_PREFIX = 'AMPLIFIER_UPDATE_PROBE='
ERROR_TYPES = {'Exception','CommandFailure','CommandTimeout','AssertionError','ImportError','ModuleNotFoundError','FileNotFoundError','PermissionError',
               'OSError','RuntimeError','ValueError','TimeoutError','CancelledError','ModuleActivationError',
               'BundleNotFoundError','BundleLoadError','BundleValidationError','BundleDependencyError','HostComponentConflict','AmbiguousBundleReferenceError'}
PREPARATION_REASONS = {'ModuleActivationError':'module-prepare-failed','BundleNotFoundError':'bundle-not-found',
                       'BundleLoadError':'bundle-load-failed','BundleValidationError':'bundle-validation-failed',
                       'BundleDependencyError':'bundle-dependency-failed', 'HostComponentConflict':'host-component-conflict'}
STORAGE_REASONS = {getattr(errno, name): reason for name, reason in {
    'ENOSPC': 'disk-full', 'EDQUOT': 'disk-quota-exceeded',
    'EACCES': 'storage-permission-denied', 'EPERM': 'storage-permission-denied',
    'EROFS': 'read-only-storage'}.items() if hasattr(errno, name)}
RECOVERY_REASONS = {'smart-tool-startup-failed', 'smart-tool-update-failed', 'missing-configured-environment', 'incompatible-conversation-loop', 'protected-runtime-source', 'runtime-source-constraint', 'runtime-source-changed', 'runtime-resolution-mismatch', 'runtime-installed-source-mismatch', *PREPARATION_REASONS.values(), *STORAGE_REASONS.values()}
RESOLUTION_FIELDS = {'source', 'repository', 'ref', 'subdirectory', 'revision'}
PROBE_STAGES = {'imports','package','assets','login','terminal','complete','prepare','prepared','capabilities','cleanup','worker-imports'}


def exception_type(error):
    name=type(error).__name__
    return name if name in ERROR_TYPES else 'Exception'


def failure_location(value):
    # Code identities only: never filenames, source lines, locals or exception text.
    return isinstance(value, str) and len(value) <= 240 and re.fullmatch(
        r'(?:amplifier_web|amplifier_foundation|amplifier_core)(?:\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*:[0-9]{1,7}', value)


def exception_facts(error):
    """Fixed OS codes and code location; no error messages or file paths."""
    facts = {'errorType': exception_type(error)}
    current, seen = error, set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, OSError) and current.errno in errno.errorcode:
            facts['osError'] = errno.errorcode[current.errno]
            if current.errno in STORAGE_REASONS:
                facts['reason'] = STORAGE_REASONS[current.errno]
        traceback = current.__traceback__
        while traceback:
            frame = traceback.tb_frame
            location = f"{frame.f_globals.get('__name__', '')}:{frame.f_code.co_name}:{traceback.tb_lineno}"
            if failure_location(location):
                facts['failureLocation'] = location
            traceback = traceback.tb_next
        current = current.__cause__ or current.__context__
    return facts


def failure_guidance(event):
    return {
        'runtime-source-constraint': 'A package source declaration could not be reconciled with the selected component. The current components remain active. This does not establish local edits. Share the update diagnostics for review; do not remove pins or caches to bypass this check.',
        'runtime-installed-source-mismatch': 'A component changed to a different source during update preparation. Amplifier kept the current components active. Check for an app update before retrying; if this happens again, share the update details.',
        'disk-full': 'The update stopped because its destination disk is full. Free space on the computer running Amplifier, then try again.',
        'disk-quota-exceeded': 'The update stopped because its storage quota was reached. Increase the quota or free space, then try again.',
        'storage-permission-denied': 'The update could not access a required file. Review the installation’s file permissions before trying again.',
        'read-only-storage': 'The update destination is read-only. Make the installation writable before trying again.',
    }.get(event.get('reason') or event.get('probe', {}).get('reason'))


def probe_failure(error, stage):
    facts={'ok':False, **exception_facts(error)}
    detail = getattr(error, 'diagnostic_facts', {})
    if detail.get('reason') in RECOVERY_REASONS:
        facts['reason'] = detail['reason']
    if (isinstance(detail.get('package'), str) and len(detail['package']) <= 128
            and re.fullmatch(r'amplifier-[a-z0-9]+(?:-[a-z0-9]+)*', detail['package'])):
        facts['package'] = detail['package']
    from .reset import repository_auth_failure
    current, seen = error, set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        detail = "\n".join(str(value or "") for value in (current, getattr(current, "stderr", None), getattr(current, "stdout", None)))
        if 'github.com' in detail.lower() and repository_auth_failure(detail):
            facts['repositoryAccessFailure'] = True
        current = current.__cause__ or current.__context__
    reason=PREPARATION_REASONS.get(type(error).__name__) if stage=='prepare' else None
    traceback = error.__traceback__
    while traceback:
        frame = traceback.tb_frame
        module = frame.f_globals.get('__name__', '')
        function = frame.f_code.co_name
        location = f'{module}:{function}:{traceback.tb_lineno}'
        if failure_location(location):
            facts['failureLocation'] = location
        if stage == 'prepare' and isinstance(error, ValueError):
            if (module, function) == ('amplifier_web.host.config', 'substitute'):
                reason = 'missing-configured-environment'
            elif (module, function) == ('amplifier_web.host.session', 'live_plan'):
                reason = 'incompatible-conversation-loop'
        traceback = traceback.tb_next
    if reason and 'reason' not in facts:facts['reason']=reason
    return facts


def probe_record(output):
    if isinstance(output,bytes):output=output.decode(errors='replace')
    records=[]
    for line in output.splitlines():
        if not line.startswith(PROBE_PREFIX) or len(line)>8192:continue
        try:value=json.loads(line[len(PROBE_PREFIX):])
        except ValueError:continue
        if not isinstance(value,dict):continue
        safe={key:value[key] for key in ('ok','isolated','packageInEnvironment','frontendPresent','loginAvailable','standalone','providersPresent','cliAbsent','dependenciesPrepared','workerImportsAvailable','repositoryAccessFailure') if type(value.get(key)) is bool}
        if isinstance(value.get('stage'),str) and value['stage'] in PROBE_STAGES:safe['stage']=value['stage']
        if isinstance(value.get('errorType'),str) and value['errorType'] in ERROR_TYPES:safe['errorType']=value['errorType']
        if isinstance(value.get('reason'),str) and value['reason'] in RECOVERY_REASONS:safe['reason']=value['reason']
        if isinstance(value.get('package'),str) and len(value['package']) <= 128 and re.fullmatch(r'amplifier-[a-z0-9]+(?:-[a-z0-9]+)*',value['package']):safe['package']=value['package']
        if failure_location(value.get('failureLocation')):safe['failureLocation']=value['failureLocation']
        if value.get('osError') in errno.errorcode.values():safe['osError']=value['osError']
        for key in ('version','pythonVersion'):
            if isinstance(value.get(key),str) and re.fullmatch(r'\d+\.\d+\.\d+',value[key]):safe[key]=value[key]
        for key in ('elapsedMs', 'sources', 'packages', 'resolverTransactions', 'profileIndex'):
            if type(value.get(key)) is int and value[key]>=0:safe[key]=value[key]
        if 'ok' in safe:records.append(safe)
    return records[-1] if records else None


def command_facts(stdout,stderr,returncode,duration):
    facts={'exitCode':returncode,'durationMs':round(duration*1000),'stdoutBytes':len(stdout),'stderrBytes':len(stderr)}
    probe=probe_record(stdout)
    if probe:facts['probe']=probe
    # Only a fixed exception class, never the following message or traceback.
    classes=re.findall(rb'^([A-Za-z]+Error):',stderr,re.MULTILINE)
    if classes:
        name=classes[-1].decode('ascii')
        if name in ERROR_TYPES:facts['errorType']=name
    return facts


class CommandOutput(str):
    def __new__(cls,value,facts=None):
        result=super().__new__(cls,value);result.diagnostic_facts=facts or {};return result
    def __deepcopy__(self,memo):return str(self)


class CommandFailure(RuntimeError):
    def __init__(self,facts):
        super().__init__('Update command failed; see its sanitized diagnostic receipt')
        self.diagnostic_facts=facts


class CommandTimeout(TimeoutError):
    def __init__(self,duration):
        super().__init__('Update command exceeded its time limit')
        self.diagnostic_facts={'durationMs':round(duration*1000),'errorType':'TimeoutError','timedOut':True}


class UpdateDiagnostics:
    """Small state overview plus bounded private JSONL, with a collector seam."""
    def __init__(self,manager):
        self.manager=manager
        self.state=manager.service.state['updates'].setdefault('diagnostics',{'events':[]})
        self.state.setdefault('events',[])
        self.path=manager.directory/'diagnostics.jsonl'

    def begin(self,kind,revision=None,attempt_id=None,*,tier=None,components=()):
        identity=attempt_id if isinstance(attempt_id,str) and re.fullmatch(r'[a-f0-9]{32}',attempt_id) else uuid.uuid4().hex
        self.state.update(attemptId=identity,kind=kind)
        if revision and re.fullmatch(r'[a-f0-9]{32,40}',revision):self.state['revision']=revision
        else:self.state.pop('revision',None)
        # Public batch names are repository/package slugs, never arguments,
        # workspaces, credential-bearing URLs or exception messages.
        names=sorted({name for name in components if isinstance(name,str) and
                      re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',name)})
        self.state['batch']={'attemptId':identity,'kind':kind,'tier':tier if tier in {'included','other'} else kind,
                             'startedAt':time.time(),'components':names[:5],'componentCount':len(names)}
        self.manager.service.state['updates'].pop('probeProgress',None)
        return identity

    def record(self,phase,status,*,preserve_last_failure=False,**facts):
        prior_failure=self.state.get('lastFailure') if preserve_last_failure else None
        event={'id':uuid.uuid4().hex,'at':time.time(),'attemptId':self.state.get('attemptId'),
               'kind':self.state.get('kind'),'phase':phase,'status':status}
        if self.state.get('revision'):event['revision']=self.state['revision']
        if isinstance(facts.get('reason'),str) and facts['reason'] in RECOVERY_REASONS:event['reason']=facts['reason']
        if isinstance(facts.get('resolutionMismatches'), list):
            fields = sorted({value for value in facts['resolutionMismatches']
                             if isinstance(value, str) and value in RESOLUTION_FIELDS})
            if fields:event['resolutionMismatches'] = fields
        for key in ('component', 'installationId', 'targetInstallationId'):
            value = facts.get(key)
            if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', value):event[key] = value
        if isinstance(facts.get('package'),str) and re.fullmatch(r'amplifier-[a-z0-9]+(?:-[a-z0-9]+)*',facts['package']) and len(facts['package'])<=128:
            event['package']=facts['package']
        for key in ('durationMs','exitCode','stdoutBytes','stderrBytes'):
            if type(facts.get(key)) is int:event[key]=facts[key]
        if isinstance(facts.get('errorType'),str) and facts['errorType'] in ERROR_TYPES:event['errorType']=facts['errorType']
        if facts.get('osError') in errno.errorcode.values():event['osError']=facts['osError']
        if failure_location(facts.get('failureLocation')):event['failureLocation']=facts['failureLocation']
        if type(facts.get('timedOut')) is bool:event['timedOut']=facts['timedOut']
        if type(facts.get('anonymousRetry')) is bool:event['anonymousRetry']=facts['anonymousRetry']
        if isinstance(facts.get('commandId'),str) and re.fullmatch(r'[a-f0-9]{32}',facts['commandId']):event['commandId']=facts['commandId']
        for key in ('expectedVersion','observedVersion'):
            if isinstance(facts.get(key),str) and re.fullmatch(r'\d+\.\d+\.\d+',facts[key]):event[key]=facts[key]
        for key in ('expectedRevision','observedRevision'):
            if isinstance(facts.get(key),str) and re.fullmatch(r'[a-f0-9]{40}',facts[key]):event[key]=facts[key]
        if isinstance(facts.get('probe'),dict):
            probe=probe_record(PROBE_PREFIX+json.dumps(facts['probe']))
            if probe:event['probe']=probe
        self.state['events']=(self.state['events']+[event])[-50:]
        self.state['latest']=event
        if status=='succeeded' and phase in {'ecosystem-activation','smart-tools-complete','smart-tools-partial','restart-ack'}:
            batch=self.state.get('batch',{})
            if batch.get('attemptId')==event['attemptId']:
                completed={**batch,'completedAt':event['at'],'phase':phase}
                if phase in {'smart-tools-complete','smart-tools-partial'}:
                    activated=batch.get('activatedComponents',[])
                    completed.update(components=activated[:5],componentCount=len(activated))
                # Smart Tools may activate separately within one attempt.
                prior=[row for row in self.state.get('completedBatches',[]) if row.get('attemptId')!=event['attemptId']]
                self.state['completedBatches']=(prior+[completed])[-8:]
        if status in {'failed','interrupted'}:
            if status=='interrupted' and self.state.get('lastFailure',{}).get('attemptId')==event['attemptId'] and self.state.get('lastFailure',{}).get('status')=='failed':
                prior_failure=self.state['lastFailure']
            self.state['lastFailure']=prior_failure if prior_failure is not None else event
        collector=getattr(self.manager.service,'diagnostics',None)
        delivered=False
        if collector:
            try:
                collector.record('updates',{'event':'update:'+phase,'data':event})
                delivered=True
            except Exception:pass
        if not delivered:
            try:
                if self.path.exists() and self.path.stat().st_size>1_000_000:
                    old=self.path.with_suffix('.previous.jsonl');os.replace(self.path,old)
                fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
                with os.fdopen(fd,'a') as stream:
                    os.fchmod(stream.fileno(),0o600)
                    stream.write(json.dumps(event,separators=(',',':'))+'\n')
            except OSError:
                self.state['storageUnavailable']=True
        try:self.manager.service._save_changes(globals={'updates'})
        except (OSError,sqlite3.Error):self.state['storageUnavailable']=True
        return event

    def clear_failure(self):
        self.state.pop('lastFailure',None)
        self.manager.service.state['updates']['error']=None

    def sync(self,phase,operation,*args,**kwargs):
        command_id=uuid.uuid4().hex
        self.record(phase,'started',commandId=command_id)
        started=time.monotonic()
        try:result=operation(*args,**kwargs)
        except Exception as error:
            self.record(phase,'failed',commandId=command_id,**{**exception_facts(error),'durationMs':round((time.monotonic()-started)*1000)})
            raise
        self.record(phase,'succeeded',commandId=command_id,durationMs=round((time.monotonic()-started)*1000))
        return result

    async def run(self,phase,operation,*args,_context=None,**kwargs):
        command_id=uuid.uuid4().hex
        context = _context or {}
        self.record(phase,'started',commandId=command_id,**context)
        started=time.monotonic()
        try:result=await operation(*args,**kwargs)
        except BaseException as error:
            facts={'durationMs':round((time.monotonic()-started)*1000),
                   **getattr(error,'diagnostic_facts',{}), **exception_facts(error)}
            self.record(phase,'interrupted' if type(error).__name__=='CancelledError' else 'failed',commandId=command_id,**{**context,**facts})
            raise
        facts={'durationMs':round((time.monotonic()-started)*1000)}
        facts.update(getattr(result,'diagnostic_facts',{}))
        if isinstance(result,(str,bytes)):
            probe=probe_record(result)
            if probe:facts['probe']=probe
        self.record(phase,'succeeded',commandId=command_id,**{**context,**facts})
        return result
