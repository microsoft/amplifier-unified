"""Explicit consent for one qualified application restart, never a replay policy."""
import re
import sys


class RestartRejected(ValueError):
    pass


def target(manager):
    state = manager.service.state['updates']
    release = state.get('pendingApp') or {}
    if (manager.closed or manager.awaiting_restart() or state.get('phase') != 'app-staged'
            or state.get('application', {}).get('canInstall') is False
            or not re.fullmatch('[a-f0-9]{40}', str(release.get('revision', '')))
            or not re.fullmatch('[a-f0-9]{32}', str(release.get('generation', '')))
            or not isinstance(release.get('attemptId'), str) or not release['attemptId']):
        return None
    return {'hostInstanceId': manager.service.instance_id,
            **{key: release[key] for key in ('revision', 'generation', 'attemptId')}}


WINDOWS_REFUSAL = ('Amplifier on Windows cannot interrupt work to restart. '
                   'The update continues by itself when work has finished.')


def require(manager, args):
    if sys.platform == 'win32':
        # Windows activation refuses every forced restart (app_updates._activate),
        # and that refusal ends the automatic update with an error. Refuse here,
        # before the request is recorded: the prepared update then stays as it is.
        raise RestartRejected(WINDOWS_REFUSAL)
    expected = target(manager)
    if (not expected or args.get('confirm') is not True
            or any(args.get(key) != value for key, value in expected.items())):
        raise RestartRejected('The staged update or host changed. Review the current update and confirm again.')
    return expected
