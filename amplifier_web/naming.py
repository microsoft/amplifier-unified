"""Native session names with a disposable Unified presentation cache."""
import json
from pathlib import Path

from amplifier_foundation.session.metadata import NAMING_FIELDS, SessionMetadataStore

FIELDS = (*NAMING_FIELDS, 'naming_completed_inputs', 'name_auto', 'name_auto_revision', 'name_policy_revision')
PLACEHOLDERS = {'New chat', 'New conversation', 'A new conversation', 'Untitled conversation'}


def automatic(session):
    if 'autoName' in session:
        return bool(session['autoName'])
    return session.get('titleSource') != 'manual' and session.get('nativeNameSource') != 'manual'


def automatic_metadata(metadata):
    if 'name_auto' in metadata:
        # An explicit rename by another host takes precedence over a previous
        # opt-in. Generated writes advance this marker with the name revision.
        return bool(metadata['name_auto']) and not (metadata.get('name_source') == 'manual'
            and metadata.get('name_revision', 0) != metadata.get('name_auto_revision', 0))
    return not metadata.get('name') or metadata.get('name_source') in {'fallback', 'generated'}


def set_automatic(home, session, enabled):
    from amplifier_foundation.session.metadata import metadata_lock
    store = SessionMetadataStore(directory_for(home, session))
    if store.history.exists():
        with metadata_lock(store.history.session_dir):
            current = store.read()
            current.update(name_auto=enabled, name_auto_revision=current.get('name_revision', 0),
                           name_policy_revision=current.get('name_policy_revision', 0) + 1)
            # Foundation's lock and existing atomic metadata writer are shared
            # with its name writer; no separate name store or transcript write.
            store.history._save_metadata_unlocked(current)
    session['autoName'] = enabled


def accept_generated(directory, candidate, *, explicit=False):
    """CAS both the name and its policy; a late result never wins over edits."""
    from amplifier_foundation.session.metadata import metadata_lock
    from datetime import datetime, UTC
    store = SessionMetadataStore(directory)
    with metadata_lock(store.history.session_dir):
        current = store.read()
        revision = current.get('name_revision', 0)
        if candidate.get('name_revision', 0) != revision or candidate.get('name_policy_revision', 0) != current.get('name_policy_revision', 0):
            return current, False
        enabled = automatic_metadata(current)
        if not explicit and not enabled:
            return current, False
        title = candidate.get('name')
        if not isinstance(title, str) or not title.strip():
            raise ValueError('The naming model did not return a chat name.')
        now = datetime.now(UTC).isoformat()
        current.update(name=title.strip()[:200], name_source='generated' if enabled else 'manual',
                       name_revision=revision + 1, name_updated_at=now, name_generated_at=now)
        if 'name_auto' in current:
            current['name_auto_revision'] = revision + 1
        if isinstance(candidate.get('description'), str):
            current.update(description=candidate['description'][:1000], description_updated_at=now)
        store.history._save_metadata_unlocked(current)
        return current, True


def legacy(directory):
    try:
        value = json.loads((Path(directory) / 'naming.json').read_text())
        return {k: v for k, v in value.items() if k in FIELDS} if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def read(directory):
    metadata = SessionMetadataStore(directory).read()
    old = legacy(directory)
    # Legacy data is a read-only fallback, never an override of a native name.
    value = old if not metadata.get('name') else {k: v for k, v in old.items() if k == 'naming_completed_inputs'}
    return {**value, **{k: v for k, v in metadata.items() if k in FIELDS}}


def directory_for(home, session):
    if session.get('nativeProject'):
        from .automatic_history import directory
        return directory(session)
    from .host.storage import SessionStore
    return SessionStore.for_app(home, session.get('workspace') or Path.cwd()).directory(session.get('runtimeSessionId') or session['id'])


def initial_name(directory, session=None):
    old = legacy(directory)
    if session is None:
        try:
            session = json.loads((Path(directory) / 'unified' / 'view.json').read_text())
        except (OSError, ValueError):
            session = {}
    if not isinstance(session, dict):
        session = {}
    title = old.get('name') or session.get('title')
    source = old.get('name_source') or session.get('titleSource')
    source = source if source in {'manual', 'generated'} else 'fallback'
    return title, source, old, session


def adopt(directory, session=None):
    """Fill missing native names without replacing an existing host's choice."""
    store = SessionMetadataStore(directory)
    if not store.history.exists():
        return {}  # An empty composer is not a native execution session.
    current = store.read()
    if current.get('name'):
        return current
    title, source, old, session = initial_name(directory, session)
    if isinstance(title, str) and title.strip():
        return store.set_name(title.strip()[:200], source=source,
                              description=old.get('description') or session.get('description'),
                              only_if_missing=True)
    return store.read()


def refresh(home, session, *, migrate=False):
    directory = directory_for(home, session)
    metadata = adopt(directory, session) if migrate else SessionMetadataStore(directory).read()
    if metadata.get('name'):
        source = metadata.get('name_source', 'manual')
        display_source = ('manual' if session.get('titleSource') == 'manual' else 'native') if session.get('historyManaged') else source
        session.update(title=metadata['name'], titleSource=display_source, nativeNameSource=source)
        session['autoName'] = automatic_metadata(metadata)
    if 'description' in metadata:
        session['description'] = metadata['description']
    return metadata


def persist(home, session, *, shared_rename=False, expected_revision=None):
    directory = directory_for(home, session)
    store = SessionMetadataStore(directory)
    if not store.history.exists():
        return  # Unified's view retains the title until the first native save.
    source = session.get('titleSource', 'automatic')
    title = session.get('title', '')
    if title and (shared_rename or title not in PLACEHOLDERS):
        store.set_name(title[:200], source='manual' if shared_rename else
                       ('generated' if source == 'generated' else 'fallback'),
                       description=session.get('description') if source == 'generated' and not shared_rename else None,
                       expected_revision=expected_revision)
    refresh(home, session)
