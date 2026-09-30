"""Rebuildable native metadata cache. Canonical history is never written here."""
from pathlib import Path
import hashlib
import json
import sqlite3
from contextlib import contextmanager


def tuples(value):
    if isinstance(value, list):
        return tuple(tuples(item) for item in value)
    return value


class NativeCatalog:
    VERSION = 1

    def __init__(self, path, home):
        self.path = Path(path)
        self.home = Path(home)
        self.namespace = hashlib.sha256(str(self.home).encode()).hexdigest()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS native_cache_version(version INTEGER);
                CREATE TABLE IF NOT EXISTS native_projects(namespace TEXT, project TEXT, value TEXT,
                    PRIMARY KEY(namespace,project));
                CREATE TABLE IF NOT EXISTS native_rows(namespace TEXT, project TEXT, identity TEXT, value TEXT,
                    PRIMARY KEY(namespace,project,identity));
                CREATE TABLE IF NOT EXISTS native_files(namespace TEXT, path TEXT, value TEXT,
                    PRIMARY KEY(namespace,path));
            ''')
            row = db.execute('SELECT version FROM native_cache_version').fetchone()
            if row is None:
                db.execute('INSERT INTO native_cache_version VALUES(?)', (self.VERSION,))
            elif row[0] != self.VERSION:
                raise ValueError('Unsupported native metadata cache; original files were preserved')
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def load(self):
        projects, files = {}, {}
        with self.connect() as db:
            for project, raw in db.execute('SELECT project,value FROM native_projects WHERE namespace=?', (self.namespace,)):
                value = json.loads(raw)
                if not isinstance(value, dict) or value.get('workspace', {}).get('nativeProject') != project:
                    raise ValueError('Invalid native metadata cache')
                projects[project] = {'workspace': value['workspace'], 'sessions': []}
            for project, identity, raw in db.execute('SELECT project,identity,value FROM native_rows WHERE namespace=?', (self.namespace,)):
                value = json.loads(raw)
                if (project not in projects or not isinstance(value, dict)
                        or value.get('nativeProject') != project or value.get('nativeIdentity') != identity):
                    raise ValueError('Invalid native metadata cache')
                projects[project]['sessions'].append(value)
            for raw_path, raw in db.execute('SELECT path,value FROM native_files WHERE namespace=?', (self.namespace,)):
                path = Path(raw_path)
                if not path.is_relative_to(self.home / 'projects'):
                    raise ValueError('Invalid native metadata cache path')
                value = json.loads(raw)
                if (not isinstance(value, list) or len(value) != 3
                        or not isinstance(value[1], dict) or type(value[2]) is not bool):
                    raise ValueError('Invalid native metadata cache file')
                files[path] = (tuples(value[0]), value[1], value[2])
        return projects, files

    def save(self, projects, previous, files, dirty_files):
        """Commit only changed metadata; no transcript, event or app view bodies."""
        with self.connect() as db:
            for name in previous.keys() - projects.keys():
                db.execute('DELETE FROM native_rows WHERE namespace=? AND project=?', (self.namespace, name))
                db.execute('DELETE FROM native_projects WHERE namespace=? AND project=?', (self.namespace, name))
                prefix = str(self.home / 'projects' / name) + '/'
                db.execute('DELETE FROM native_files WHERE namespace=? AND substr(path,1,?)=?',
                           (self.namespace, len(prefix), prefix))
            for name, project in projects.items():
                old = previous.get(name)
                if project is old:
                    continue
                if old is None or old['workspace'] != project['workspace']:
                    db.execute('INSERT OR REPLACE INTO native_projects VALUES(?,?,?)',
                               (self.namespace, name, json.dumps({'workspace': project['workspace']})))
                old_rows = {row['nativeIdentity']: row for row in old['sessions']} if old else {}
                new_rows = {row['nativeIdentity']: row for row in project['sessions']}
                for identity in old_rows.keys() - new_rows.keys():
                    db.execute('DELETE FROM native_rows WHERE namespace=? AND project=? AND identity=?',
                               (self.namespace, name, identity))
                for identity, row in new_rows.items():
                    if row != old_rows.get(identity):
                        db.execute('INSERT OR REPLACE INTO native_rows VALUES(?,?,?,?)',
                                   (self.namespace, name, identity, json.dumps(row)))
            for path in dirty_files:
                value = files.get(path)
                if value is not None:
                    db.execute('INSERT OR REPLACE INTO native_files VALUES(?,?,?)',
                               (self.namespace, str(path), json.dumps(value)))
