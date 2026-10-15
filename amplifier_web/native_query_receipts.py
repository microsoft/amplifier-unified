"""Effective query epochs for native recency hidden by admitted local owners.

The native writer records structural changes separately from root recency.
Readers subtract only exact owner counters whose recency the canonical join
cannot expose. Unaccounted native generations always use the broad receipt.
"""
import uuid
import sqlite3

MAX_MASK = 128


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS native_query_epochs ('
        'namespace TEXT PRIMARY KEY, epoch TEXT, root_raw INTEGER, workspace_raw INTEGER,'
        'root_content INTEGER, workspace_content INTEGER, recent INTEGER)')
    db.execute('CREATE TABLE IF NOT EXISTS native_query_recency ('
        'namespace TEXT, project TEXT, identity TEXT, epoch TEXT, revision INTEGER,'
        'PRIMARY KEY(namespace,project,identity))')


class Writer:
    def __init__(self, db, namespace):
        self.db, self.namespace = db, namespace
        raw = db.execute('SELECT root_navigation,workspace FROM native_revisions WHERE namespace=?',
                         (namespace,)).fetchone()
        saved = db.execute('SELECT epoch,root_raw,workspace_raw,root_content,workspace_content,recent '
                           'FROM native_query_epochs WHERE namespace=?', (namespace,)).fetchone()
        self.saved = saved
        if saved is None or tuple(saved[1:3]) != tuple(raw):
            # A different writer/old cache was observed. Start a new receipt;
            # never infer which historical changes might have been masked.
            self.epoch, self.root, self.workspace, self.recent = uuid.uuid4().hex, 0, 0, 0
        else:
            self.epoch, self.root, self.workspace, self.recent = saved[0], *saved[3:]
        self.root_changed = self.workspace_changed = False

    def structure(self):
        self.root_changed = self.workspace_changed = True

    def remove(self, project, identity):
        self.db.execute('DELETE FROM native_query_recency WHERE namespace=? AND project=? AND identity=?',
                        (self.namespace, project, identity))

    def row(self, row, prior):
        from .native_catalog import MEMBERSHIP_FIELDS, NAVIGATION_FIELDS
        membership = prior is None or any(row.get(key) != prior.get(key) for key in MEMBERSHIP_FIELDS)
        root = row.get('sessionKind', 'root') == 'root'
        self.root_changed |= membership or root and any(
            row.get(key) != prior.get(key) for key in NAVIGATION_FIELDS if key != 'recentActivityAt')
        self.workspace_changed |= membership
        if prior is not None and root and row.get('recentActivityAt') != prior.get('recentActivityAt'):
            self.recent += 1
            self.db.execute('INSERT INTO native_query_recency VALUES(?,?,?,?,1) '
                'ON CONFLICT(namespace,project,identity) DO UPDATE SET '
                'revision=CASE WHEN epoch=excluded.epoch THEN revision+1 ELSE 1 END,epoch=excluded.epoch',
                (self.namespace, row['nativeProject'], row['nativeIdentity'], self.epoch))

    def commit(self):
        raw = self.db.execute('SELECT root_navigation,workspace FROM native_revisions WHERE namespace=?',
                              (self.namespace,)).fetchone()
        current = (self.epoch, *raw, self.root + int(self.root_changed),
                   self.workspace + int(self.workspace_changed), self.recent)
        if self.saved != current:
            self.db.execute('INSERT OR REPLACE INTO native_query_epochs VALUES(?,?,?,?,?,?,?)',
                            (self.namespace, *current))


def masked_owners(live):
    """Only admitted owners; dormant history remains conservatively invalidated."""
    if len(live) > MAX_MASK:
        return []
    from .history_directory import COLUMNS
    positions = {name: COLUMNS.index(name) for name in ('native_project', 'native_id', 'status', 'history_managed')}
    owners = {}
    for row in live.values():
        key = tuple(row[positions[name]] for name in ('native_project', 'native_id'))
        if not all(key):
            continue
        frozen = not row[positions['history_managed']] or row[positions['status']] in ('working', 'starting', 'stopping', 'ready')
        owners[key] = owners.get(key, True) and frozen
    return [list(key) for key, frozen in sorted(owners.items()) if frozen]


def receipt(db, namespace, collection, live):
    raw = db.execute('SELECT root_navigation,workspace FROM history_native.native_revisions WHERE namespace=?',
                     (namespace,)).fetchone() or (0, 0)
    broad = ['raw', raw[0 if collection == 'root_navigation' else 1]]
    try:
        saved = db.execute('SELECT epoch,root_raw,workspace_raw,root_content,workspace_content,recent '
            'FROM history_native.native_query_epochs WHERE namespace=?', (namespace,)).fetchone()
    except sqlite3.OperationalError:
        return broad
    if saved is None or tuple(saved[1:3]) != tuple(raw):
        return broad
    # Indexed point lookups only, bounded by the admitted working set. Any other
    # stored overlay for the same native owner must also freeze native recency.
    masked = masked_owners(live)
    discount = 0
    for project, identity in masked:
        stored = db.execute('SELECT id,status,history_managed FROM history_local_rows '
            'WHERE native_project=? AND native_id=? LIMIT ?', (project, identity, MAX_MASK + 1)).fetchall()
        if len(stored) > MAX_MASK or any(sid not in live and managed and status not in ('working', 'starting', 'stopping', 'ready')
               for sid, status, managed in stored):
            continue
        row = db.execute('SELECT revision FROM history_native.native_query_recency '
            'WHERE namespace=? AND project=? AND identity=? AND epoch=?', (namespace, project, identity, saved[0])).fetchone()
        discount += row[0] if row else 0
    return ['effective', saved[0], saved[3 if collection == 'root_navigation' else 4], saved[5] - discount]
