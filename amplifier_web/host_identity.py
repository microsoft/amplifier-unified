"""The existing Foundation owner hostname, explicitly scoped to this local host.

A hostname is not a cryptographic or globally unique machine identity. Callers
requiring a particular running server must bind its instance_id separately.
"""
import socket


def local_host_identity():
    hostname = socket.gethostname()
    return {'scope': 'local', 'id': hostname, 'label': hostname,
            'identitySource': 'foundation-owner-hostname'}


def require_local_host(expected_id):
    current = local_host_identity()
    if not isinstance(expected_id, str) or expected_id != current['id']:
        raise ValueError('This target belongs to a different local host; cross-host execution is unavailable.')
    return current
