"""Conversation navigation distinguishes worker ancestry from independent forks."""


def is_top_level(session):
    kind = session.get('sessionKind')
    if kind in {'root', 'worker', 'internal'}:
        return kind == 'root'
    # Older web forks keep a parent link for navigation but execute as roots.
    if session.get('forkTranscript') or session.get('editOrigin'):
        return True
    # Compatibility while an existing native index receives classification.
    # UI parentId alone denotes fork lineage and must never hide a conversation.
    return not bool(session.get('nativeParentId'))
