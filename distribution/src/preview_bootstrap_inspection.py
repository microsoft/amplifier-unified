"""Exact-artifact preview recovery inspection, not a generic owner contract.

Runs only inside the operator-owned offline window. The operator holds every
reviewed service start gate through successor acquisition. This module never
masks/stops services, opens an owner constructor, migrates a DB, or replays work.
The policy is hash-bound by the authenticated composition, names an exhaustive
reviewed inventory, and carries baseline files, never copied safety assertions.
"""
import hashlib,json,os,pathlib,sqlite3,stat,subprocess,sys

def fail(reason): raise RuntimeError(reason)
def digest(b): return hashlib.sha256(b).hexdigest()
def canonical(v): return json.dumps(v,sort_keys=True,separators=(',',':')).encode()
def encoded(v):
    if isinstance(v,bytes): return {'blob':v.hex()}
    return v
def file_bytes(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        s=os.fstat(fd)
        if not stat.S_ISREG(s.st_mode): fail('inspection_regular_file_required')
        with os.fdopen(fd,'rb',closefd=False) as f:return f.read()
    finally:os.close(fd)
def checked_file(item):
    b=file_bytes(item['path'])
    if digest(b)!=item['sha256']: fail('inspection_artifact_changed')
    return b
def ctl(unit):
    if not unit.endswith('.service') or any(x not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.@:-' for x in unit):fail('inspection_unit_invalid')
    r=subprocess.run(['systemctl','--user','show',unit,'-p','LoadState,ActiveState,SubState,MainPID,FragmentPath,UnitFileState,ControlGroup'],capture_output=True,text=True,timeout=15)
    if r.returncode:fail('inspection_systemd_failed')
    return dict(x.split('=',1) for x in r.stdout.splitlines() if '=' in x)
def inside(path,root):
    return path==root or path.startswith(root.rstrip('/')+'/')
def process_identity(pid):
    p=pathlib.Path('/proc')/str(pid)
    s=(p/'stat').read_text();rest=s[s.rfind(')')+2:].split()
    return {'pid':pid,'startTicks':rest[19],'cgroup':(p/'cgroup').read_text().strip(),'exe':os.readlink(p/'exe')}
def exclusion(policy,allowed_pid=None):
    facts=[]
    for row in policy['maskedUnits']:
        checked_file(row['originalDefinition'])
        path=pathlib.Path(row['maskPath'])
        if not path.is_symlink() or os.readlink(path)!='/dev/null':fail('inspection_start_gate_not_held')
        s=ctl(row['unit'])
        if s.get('LoadState')!='masked' or s.get('UnitFileState') not in ('masked','masked-runtime') or s.get('ActiveState')!='inactive' or s.get('SubState')!='dead' or s.get('MainPID')!='0' or s.get('FragmentPath') not in (str(path),'/dev/null'):fail('inspection_start_gate_not_effective')
        cg=s.get('ControlGroup')
        if cg:
            root=pathlib.Path('/sys/fs/cgroup')/cg.lstrip('/')
            if root.exists():
                for f in root.rglob('cgroup.procs'):
                    if f.read_text().strip():fail('inspection_start_gate_has_descendants')
        facts.append({'unit':row['unit'],'state':s,'maskInode':path.lstat().st_ino,'definition':row['originalDefinition']['sha256']})
    # The failed source keeps its original unit/witness. Its reviewed launch code
    # refuses the retained one-shot authority before constructing application owners.
    for row in policy['guardedFiles']: checked_file(row)
    for row in policy['artifacts']: checked_file(row)
    if pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()!=policy['bootId']:fail('inspection_boot_changed')
    for path in policy['absentPaths']:
        if os.path.lexists(path):fail('inspection_unexpected_authority')
    if not policy.get('admissionTables'):fail('inspection_admission_coverage_incomplete')
    for requirement in policy['admissionTables']:
        matches=[row for row in policy['databases'] if row['path']==requirement['path']]
        if len(matches)!=1 or not (matches[0].get('absent') or matches[0]['tables'].get(requirement['table'])=='empty-effect-journal'):
            fail('inspection_admission_coverage_incomplete')
    roots=[r['path'] for r in policy['roots']]
    excluded=policy.get('unreadableProcessExceptions',[])
    allowed=None
    if allowed_pid:
        allowed=process_identity(allowed_pid)
        unit=ctl(policy['successorUnit'])
        if unit.get('MainPID')!=str(allowed_pid) or unit.get('ActiveState') not in ('activating','active'):fail('inspection_successor_process_unbound')
        if not unit.get('ControlGroup') or allowed['cgroup']!='0::'+unit['ControlGroup']:fail('inspection_successor_cgroup_unbound')
    exception_facts=[]
    for p in pathlib.Path('/proc').iterdir():
        if not p.name.isdigit():continue
        try:
            if p.stat().st_uid!=os.getuid():continue
            pid=int(p.name)
            if pid==os.getpid():continue
            identity=process_identity(pid)
            # Native children created by the one permitted successor are within
            # its exact systemd cgroup. Independent launches never qualify here.
            if allowed and identity['cgroup']==allowed['cgroup']:continue
            fdpaths=list((p/'fd').iterdir())
            for fd in fdpaths:
                try:
                    target=os.readlink(fd).removesuffix(' (deleted)')
                    if not any(inside(target,r) for r in roots):continue
                    info=(p/'fdinfo'/fd.name).read_text()
                    flags=int(next(x.split()[1] for x in info.splitlines() if x.startswith('flags:')),8)
                    if flags&3:fail('inspection_independent_writer')
                except FileNotFoundError:continue
            for line in (p/'maps').read_text().splitlines():
                parts=line.split(None,5)
                if len(parts)==6 and 'w' in parts[1] and any(inside(parts[5],r) for r in roots):fail('inspection_independent_mapping')
            # A process with its cwd in an owned root or a launch argument naming
            # one may open it later; a clean fd snapshot alone is insufficient.
            cwd=os.readlink(p/'cwd')
            args=(p/'cmdline').read_bytes().split(b'\0')
            if any(inside(cwd,r) for r in roots) or any(any(inside(a.decode(errors='replace'),r) for r in roots) for a in args if a.startswith(b'/')):
                fail('inspection_independent_start_path')
        except FileNotFoundError:continue
        except PermissionError:
            # Only independently reviewed exact OS process identities may be
            # excluded. Their presence is a limit of the operator boundary,
            # never evidence excluding privileged/non-cooperating writers.
            matches=[x for x in excluded if x.get('pid')==int(p.name)]
            if len(matches)!=1:fail('inspection_process_coverage_unknown')
            x=matches[0]
            try:
                s=(p/'stat').read_text();rest=s[s.rfind(')')+2:].split()
                cg=(p/'cgroup').read_text().strip()
                if rest[19]!=x['startTicks'] or cg!=x['cgroup']:fail('inspection_process_exception_changed')
            except PermissionError:fail('inspection_process_exception_unverifiable')
            exception_facts.append(x)
    return {'schema':'stopped-writer-exclusion-v1','coverage':'complete','activeWriters':0,
            'publicReadyObserved':False,'admissionObserved':False,'digest':digest(canonical({'gates':facts,'exceptions':sorted(exception_facts,key=lambda x:x['pid']),'absentAuthorities':policy['absentPaths'],'admissionTables':policy['admissionTables']}))}
def readonly_db(path):
    p=pathlib.Path(path)
    if not p.is_file() or p.is_symlink():fail('inspection_database_missing')
    wal=pathlib.Path(str(p)+'-wal')
    if wal.exists() and wal.stat().st_size:fail('inspection_nonempty_wal')
    db=sqlite3.connect(p.as_uri()+'?mode=ro&immutable=1',uri=True)
    db.execute('PRAGMA query_only=ON')
    if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':fail('inspection_database_invalid')
    return db
def quote(s):return '"'+s.replace('"','""')+'"'
def db_rows(db):
    tables={}
    for name,sql in db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        # Typed multiset preserves duplicate rows. EXCEPT alone loses multiplicity.
        rows=sorted(canonical([encoded(v) for v in row]).decode() for row in db.execute('SELECT * FROM '+quote(name)))
        tables[name]={'schema':sql,'columns':[list(r) for r in db.execute('PRAGMA table_info('+quote(name)+')')],'rows':rows}
    return tables
def databases(policy):
    result=[];unknown=[]
    for row in policy['databases']:
        if row.get('absent'):
            if any(os.path.lexists(row['path']+suffix) for suffix in ('','-wal','-shm','-journal')):fail('inspection_absent_owner_state_created')
            continue
        checked_file(row['baseline'])
        with readonly_db(row['baseline']['path']) as before,readonly_db(row['path']) as after:
            original=db_rows(before);current=db_rows(after)
            if current!=original:fail('inspection_database_state_changed')
            # The reviewed incident profile declares every table. Nonempty
            # tables must be explicitly classified as immutable history or
            # bootstrap defaults; no new uncertain/pending journal is tolerated.
            if set(current)!=set(row['tables']):fail('inspection_database_schema_uncovered')
            for name,table in current.items():
                classification=row['tables'][name]
                if classification=='empty-effect-journal':
                    if table['rows']:fail('inspection_effect_journal_nonempty')
                elif classification not in ('preserved-history','startup-default'):
                    fail('inspection_table_classification_unknown')
            if row.get('hostReceipts'):
                states={}
                for command,payload in after.execute('SELECT command_id,payload FROM receipts ORDER BY command_id'):
                    state=json.loads(payload).get('status')
                    if state not in ('completed','unknown'):fail('inspection_host_effect_unaccounted')
                    states[state]=states.get(state,0)+1
                    if state=='unknown':unknown.append(digest(canonical([command,payload])))
                if states!=row['hostReceipts']:fail('inspection_historical_receipts_changed')
            result.append({'path':row['path'],'digest':digest(canonical(current)),'tables':len(current)})
    return {'databases':result,'historicalUnknown':{'count':len(unknown),'digest':digest(canonical(sorted(unknown)))}}
def inventory(policy):
    dbs={r['path'] for r in policy['databases'] if not r.get('absent')}
    declared=policy['entries'];seen={};db_seen=set()
    def visit(p):
        s=p.lstat();key=str(p)
        # SQLite transient sidecars must be absent or empty after the held stop.
        if any(key==x+suffix for x in dbs for suffix in ('-wal','-shm','-journal')):
            if not stat.S_ISREG(s.st_mode) or s.st_size:fail('inspection_nonempty_sqlite_sidecar')
            return
        if key not in declared:fail('inspection_unreviewed_file')
        expected=declared[key]
        kind='directory' if stat.S_ISDIR(s.st_mode) else 'file' if stat.S_ISREG(s.st_mode) else 'symlink' if stat.S_ISLNK(s.st_mode) else 'unsupported'
        v={'kind':kind,'mode':stat.S_IMODE(s.st_mode),'uid':s.st_uid}
        if kind=='symlink':v['target']=os.readlink(p)
        elif kind=='file':
            if key in dbs:db_seen.add(key);v['database']=True
            else:v['sha256']=digest(file_bytes(p))
        elif kind!='directory':fail('inspection_special_file')
        if v!=expected:fail('inspection_file_changed')
        seen[key]=v
        if kind=='directory':
            for child in sorted(p.iterdir()):visit(child)
    for r in policy['roots']:visit(pathlib.Path(r['path']))
    if set(seen)!=set(declared) or db_seen!=dbs:fail('inspection_inventory_incomplete')
    return {'schema':'stopped-storage-inventory-v1','coverage':'complete','roots':policy['roots'],'digest':digest(canonical(seen))}
def inspect(policy,mode,allowed_pid=None):
    if policy.get('schema')!='exact-preview-recovery-policy-v1' or policy.get('scope')!='operator-held-offline-window':fail('inspection_policy_invalid')
    if len(policy['owners'])!=20 or len(set(policy['owners']))!=20:fail('inspection_owner_coverage_incomplete')
    if set(policy['ownerCoverage'])!=set(policy['owners']):fail('inspection_owner_coverage_incomplete')
    for paths in policy['ownerCoverage'].values():
        if not paths or any(not any(inside(path,r['path']) for r in policy['roots']) for path in paths):fail('inspection_owner_roots_incomplete')
    # This is a hash-bound exact-candidate profile, not a claim that arbitrary
    # twenty-owner configurations share private SQL/storage contracts.
    before=exclusion(policy,allowed_pid)
    if mode=='exclusion':return before
    if mode!='stopped':fail('inspection_mode_invalid')
    i=inventory(policy);d=databases(policy)
    if inventory(policy)!=i or exclusion(policy,allowed_pid)!=before:fail('inspection_state_changed_during_read')
    return {'inventory':i,'exclusion':before,'effects':d,'owners':policy['owners'],
            'evidenceDigest':digest(canonical({'inventory':i,'exclusion':before,'effects':d,'profile':policy['profileDigest']}))}
if __name__=='__main__':
    try:
        args=json.load(sys.stdin)
        result=inspect(args['policy'],args['mode'],args.get('allowedPid'))
        print(json.dumps(result,separators=(',',':')))
    except Exception as error:
        # Never echo paths, commands, credential or database row bodies.
        reason=str(error) if isinstance(error,RuntimeError) and str(error).startswith('inspection_') else 'inspection_failed'
        print(json.dumps({'error':reason}));sys.exit(1)
