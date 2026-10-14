"""Semantic public-fact revisions, updated at each directory owner's boundary.

Only changed/admitted rows are decoded. Native revisions come from its scalar
journal; global pins/read receipts join indexed identities and fingerprints.
"""
import hashlib
import json

COLLECTIONS = ('sessions', 'attention', 'activity', 'navigation')


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS history_observation_rows '
               '(id TEXT PRIMARY KEY,sessions TEXT,attention TEXT,activity TEXT,navigation TEXT)')
    if 'navigation' not in {row[1] for row in db.execute('PRAGMA table_info(history_observation_rows)')}:
        db.execute('ALTER TABLE history_observation_rows ADD COLUMN navigation TEXT')
    db.execute('CREATE TABLE IF NOT EXISTS history_observation_revisions '
               '(collection TEXT PRIMARY KEY,revision INTEGER NOT NULL)')
    db.executemany('INSERT OR IGNORE INTO history_observation_revisions VALUES(?,0)',
                   [(key,) for key in COLLECTIONS])


def encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'))


def parts(row, *, blocked=False):
    from .client_observations import Observations
    facts = Observations.row_facts(None,row,{},frozenset())
    session,workspace,notices = facts
    from .chat_navigation import navigation_activity, timestamp
    from .navigation_summary import activity
    fields = ('id','title','description','status','workspace','workspaceId','location',
              'titleSource','nativeNameSource','autoName','naming','configurationBusy',
              'runtimeSessionId','nativeIdentity','createdAt','sessionKind','parentId',
              'nativeParentId','historyManaged','workspaceAvailable','nativeAvailable')
    defaults = {'description':'','status':'idle','sessionKind':
                'internal' if session['role']=='internal' else 'root' if session['role']=='root' else 'worker'}
    navigation = ([bool(row.get(key,True)) if key=='workspaceAvailable' else
                   timestamp(row.get(key)) if key=='createdAt' and row.get(key) is not None else
                   row.get(key) if row.get(key) is not None else defaults.get(key)
                   for key in fields],session['agentCreated'],
                  navigation_activity(row),activity(row,False,blocked=blocked))
    return tuple(encoded(value) for value in ((session,workspace),
        (session['role'],notices),Observations.count_facts(facts),navigation))


def native_parts(directory, row):
    """The prior native public row for an overlay, without admitting its body."""
    if not directory._native_path:
        return None
    from .session_identity import native_id, project
    saved = directory.db.execute('SELECT identity,project,public_id,kind,title,workspace_path '
        'FROM history_native.native_rows WHERE namespace=? AND project=? AND identity=?',
        (directory._native_namespace,project(row),native_id(row))).fetchone()
    if saved is None:
        return None
    identity,project_id,public,kind,title,workspace = saved
    duplicated = directory.db.execute('SELECT COUNT(*) FROM history_native.native_rows '
        'WHERE namespace=? AND identity=?',(directory._native_namespace,identity)).fetchone()[0]>1
    effective_id = public if duplicated else identity
    if row['id'] != effective_id:
        return None
    return parts({'id':effective_id,'title':title,'sessionKind':kind,'status':'idle','workspace':workspace})


def saved_parts(directory,row):
    saved = directory.db.execute('SELECT sessions,attention,activity,navigation FROM history_observation_rows WHERE id=?',
                                  (row['id'],)).fetchone()
    return tuple(saved) if saved else native_parts(directory,row)


def sync(directory,row,prior_summary_or_none=None,state=None,*,deleted=False):
    identity = row if isinstance(row,str) else row['id']
    previous = directory.db.execute('SELECT sessions,attention,activity,navigation FROM history_observation_rows WHERE id=?',
                                     (identity,)).fetchone()
    if previous is None and not isinstance(row,str):
        previous = native_parts(directory,row)
    from .navigation_summary import task_blocked
    blocked = False
    if not deleted:
        blocked = task_blocked(state or {}, identity)
        if identity not in (state or {}).get('runtimeControl', {}):
            saved = directory.db.execute("SELECT json_extract(value, '$.\"task.get\".task.status') FROM state_records WHERE kind='runtime' AND id=?", (identity,)).fetchone()
            blocked = bool(saved and saved[0] == 'blocked')
    current = None if deleted else parts(row, blocked=blocked)
    if deleted:
        directory.db.execute('DELETE FROM history_observation_rows WHERE id=?',(identity,))
    else:
        directory.db.execute('INSERT OR REPLACE INTO history_observation_rows VALUES(?,?,?,?,?)',
                             (identity,*current))
    for index,key in enumerate(COLLECTIONS):
        if (previous[index] if previous else None)!=(current[index] if current else None):
            directory.db.execute('UPDATE history_observation_revisions SET revision=revision+1 WHERE collection=?',(key,))


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def revision(directory,state):
    # effective freezes each admitted row once; a snapshot uses its already
    # frozen overlay and pinned DB views, never mutable service state.
    sql,params = directory.effective(state)
    native = (0,0,0)
    if directory._native_path:
        saved = directory.db.execute('SELECT observation_sessions,observation_membership,navigation '
            'FROM history_native.native_revisions WHERE namespace=?',(directory._native_namespace,)).fetchone()
        native = tuple(saved) if saved else native
    local = dict(directory.db.execute('SELECT collection,revision FROM history_observation_revisions'))
    overlays = {key:[] for key in COLLECTIONS}
    from .history_directory import COLUMNS
    cached = getattr(directory, '_observation_parts', {})
    retained = {}
    for identity,record in directory._live_values.items():
        raw = (record[COLUMNS.index('summary')],record[COLUMNS.index('base_activity_label')])
        prior = cached.get(identity)
        if prior is not None and prior[0] == raw:
            current = prior[1]
            # Identity fields suffice for an indexed native baseline lookup.
            row = {'id':identity,'nativeProject':record[COLUMNS.index('native_project')],
                   'nativeIdentity':record[COLUMNS.index('native_id')]}
        else:
            row = directory.decode(record[:len(COLUMNS)])
            current = parts(row, blocked=raw[1]=='Work blocked')
        retained[identity] = (raw,current)
        previous = saved_parts(directory,row)
        for index,key in enumerate(COLLECTIONS):
            if previous is None or current[index]!=previous[index]:
                overlays[key].append((row['id'],current[index]))
    directory._observation_parts = retained
    pins = [] if not state.get('pinnedSessionIds') else [row[0] for row in directory.db.execute(sql+
        ' SELECT id FROM directory_rows WHERE id IN (SELECT value FROM json_each(?)) ORDER BY id',
        [*params,json.dumps(state.get('pinnedSessionIds',[]))])]
    read = [] if not state.get('attentionRead') else [tuple(row) for row in directory.db.execute(sql+
        " SELECT n.owner,n.key,n.fingerprint FROM directory_notices n JOIN json_each(?) r ON r.key=n.key AND r.value=n.fingerprint "
        "WHERE EXISTS(SELECT 1 FROM directory_rows d WHERE d.id=n.owner AND d.session_kind!='internal') ORDER BY n.owner,n.key,n.fingerprint",
        [*params,json.dumps(state.get('attentionRead',{}))])]
    from .managed_deletion import tombstones
    suppression = digest([sorted(set(state.get('hiddenNativeSessions',[]))),
                          sorted(set(state.get('hiddenNativeWorkspaces',[]))),
                          sorted(encoded((row.get('id'),row.get('project'),sorted(row.get('ids') or [])))
                                 for row in tombstones(directory.db))])
    return {key:(directory._native_namespace,native[2 if key=='navigation' else 0 if key=='sessions' else 1],local.get(key,0),
                 digest(sorted(overlays[key])),digest(pins if key=='sessions' else read),suppression)
            for key in COLLECTIONS}
