"""Native session references with compatibility aliases for existing app rows."""
import uuid
from .session_files import project_slug


ID_ACTIONS = {'feedback.excerpt.review', 'feedback.excerpt.stage', 'session.select', 'session.warm', 'session.takeover', 'session.rename', 'session.naming',
              'session.pin', 'session.deletePreview', 'session.delete', 'session.export', 'session.exportDeliver', 'session.history',
              'session.inspect', 'session.recover', 'session.fork', 'session.archive', 'session.restore'}


def native_id(row):
    return row.get('nativeIdentity') or row.get('runtimeSessionId') or row['id']


def project(row):
    return row.get('nativeProject') or (project_slug(row['workspace']) if row.get('workspace') else None)


def aliases(row):
    result = {row['id'], native_id(row)}
    scope = project(row)
    if scope:
        result.add(uuid.uuid5(uuid.NAMESPACE_URL, f'amplifier-session:{scope}/{native_id(row)}').hex)
    return result


def resolve(rows, identity, native_project=None):
    if not isinstance(identity, str):
        return None  # Let the action schema report malformed caller input.
    matches = [row for row in rows if (identity in {row['id'], native_id(row)}
               or isinstance(identity, str) and len(identity) == 32 and identity in aliases(row))
               and (not native_project or project(row) == native_project)]
    if len(matches) > 1:
        raise ValueError('This native session ID exists in more than one project. Supply nativeProject.')
    return matches[0] if matches else None


def reference(row):
    from .host_identity import local_host_identity
    return {'sessionId': native_id(row), 'nativeProject': project(row),
            'hostId': local_host_identity()['id'], 'accountScope': 'current'}
