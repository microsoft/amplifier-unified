"""Scoped reads and locked writes to the shared Amplifier settings files."""
from pathlib import Path
from .shared_settings import settings_paths, read_yaml, update_settings
from .session_files import amplifier_home


class SettingsStore:
    def __init__(self, home, *, shared_home=None):
        self.home = Path(home)  # Application state, never the settings authority.
        self.shared_home = Path(shared_home or amplifier_home()).expanduser().resolve()

    def path(self, workspace, scope, *, session_id=None):
        paths = settings_paths(workspace, shared_home=self.shared_home, session_id=session_id)
        if scope not in paths:
            raise ValueError("Choose global, project, or local scope (session requires an ID)")
        return paths[scope]

    def read(self, workspace, scope="global", *, session_id=None):
        return read_yaml(self.path(workspace, scope, session_id=session_id))

    def update(self, workspace, scope, mutator, *, session_id=None):
        from .configuration_authority import check_current
        def checked_mutator(value):
            check_current(workspace=workspace, scope=scope)
            from .decision_namespace import read_scopes, translate
            from .decision_settings import catalogs
            paths = settings_paths(workspace, shared_home=self.shared_home, session_id=session_id)
            prior = []
            for level, path in paths.items():
                if level == scope:
                    break
                prior.append(path)
            definitions = catalogs()
            inherited = read_scopes(prior, definitions=definitions)
            value = translate(value, definitions, inherited=inherited)
            result = mutator(value)
            return translate(value if result is None else result, definitions, inherited=inherited)
        check_current(workspace=workspace, scope=scope)
        return update_settings(self.path(workspace, scope, session_id=session_id), checked_mutator)
