"""Credential-free update receipts; never retain subprocess text or arguments."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid

PROBE_PREFIX = 'AMPLIFIER_UPDATE_PROBE='
ERROR_TYPES = {'Exception','CommandFailure','CommandTimeout','AssertionError','ImportError','ModuleNotFoundError','FileNotFoundError','PermissionError',
               'OSError','RuntimeError','ValueError','TimeoutError','CancelledError'}
PROBE_STAGES = {'imports','package','assets','login','complete','prepare','capabilities','cleanup'}


def exception_type(error):
    name=type(error).__name__
    return name if name in ERROR_TYPES else 'Exception'


def probe_record(output):
    if isinstance(output,bytes):output=output.decode(errors='replace')
    records=[]
    for line in output.splitlines():
        if not line.startswith(PROBE_PREFIX) or len(line)>8192:continue
        try:value=json.loads(line[len(PROBE_PREFIX):])
        except ValueError:continue
        if not isinstance(value,dict):continue
        safe={key:value[key] for key in ('ok','isolated','packageInEnvironment','frontendPresent','loginAvailable','standalone','providersPresent','cliAbsent') if type(value.get(key)) is bool}
        if isinstance(value.get('stage'),str) and value['stage'] in PROBE_STAGES:safe['stage']=value['stage']
        if isinstance(value.get('errorType'),str) and value['errorType'] in ERROR_TYPES:safe['errorType']=value['errorType']
        for key in ('version','pythonVersion'):
            if isinstance(value.get(key),str) and re.fullmatch(r'\d+\.\d+\.\d+',value[key]):safe[key]=value[key]
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

    def begin(self,kind,revision=None,attempt_id=None):
        identity=attempt_id if isinstance(attempt_id,str) and re.fullmatch(r'[a-f0-9]{32}',attempt_id) else uuid.uuid4().hex
        self.state.update(attemptId=identity,kind=kind)
        if revision and re.fullmatch(r'[a-f0-9]{32,40}',revision):self.state['revision']=revision
        else:self.state.pop('revision',None)
        return identity

    def record(self,phase,status,**facts):
        event={'id':uuid.uuid4().hex,'at':time.time(),'attemptId':self.state.get('attemptId'),
               'kind':self.state.get('kind'),'phase':phase,'status':status}
        if self.state.get('revision'):event['revision']=self.state['revision']
        for key in ('durationMs','exitCode','stdoutBytes','stderrBytes'):
            if type(facts.get(key)) is int:event[key]=facts[key]
        if isinstance(facts.get('errorType'),str) and facts['errorType'] in ERROR_TYPES:event['errorType']=facts['errorType']
        if type(facts.get('timedOut')) is bool:event['timedOut']=facts['timedOut']
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
        if status in {'failed','interrupted'}:self.state['lastFailure']=event
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
        try:self.manager.service._save()
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
            self.record(phase,'failed',commandId=command_id,durationMs=round((time.monotonic()-started)*1000),errorType=exception_type(error))
            raise
        self.record(phase,'succeeded',commandId=command_id,durationMs=round((time.monotonic()-started)*1000))
        return result

    async def run(self,phase,operation,*args,**kwargs):
        command_id=uuid.uuid4().hex
        self.record(phase,'started',commandId=command_id)
        started=time.monotonic()
        try:result=await operation(*args,**kwargs)
        except BaseException as error:
            facts={'durationMs':round((time.monotonic()-started)*1000),'errorType':exception_type(error)}
            facts.update(getattr(error,'diagnostic_facts',{}))
            self.record(phase,'interrupted' if type(error).__name__=='CancelledError' else 'failed',commandId=command_id,**facts)
            raise
        facts={'durationMs':round((time.monotonic()-started)*1000)}
        facts.update(getattr(result,'diagnostic_facts',{}))
        if isinstance(result,(str,bytes)):
            probe=probe_record(result)
            if probe:facts['probe']=probe
        self.record(phase,'succeeded',commandId=command_id,**facts)
        return result
