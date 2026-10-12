"""Trusted, process-local download policy; never edit personal Git settings."""
import os
import json
from pathlib import Path


def repository_environment(environment=None, *, anonymous=False):
    result = dict(os.environ if environment is None else environment)
    if anonymous:
        result['AMPLIFIER_GITHUB_ACCESS'] = 'anonymous'
    if result.get('AMPLIFIER_GITHUB_ACCESS') != 'anonymous':
        return result
    for key in list(result):
        if key.startswith('GIT_CONFIG_') or key in {
            'GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN',
            'GIT_ASKPASS', 'SSH_ASKPASS',
        }:
            result.pop(key, None)
    result.update(GIT_TERMINAL_PROMPT='0', GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    settings = [('credential.helper', ''), ('http.extraHeader', ''),
                ('http.cookieFile', ''), ('core.askPass', '')]
    result['GIT_CONFIG_COUNT'] = str(len(settings))
    for index, (key, value) in enumerate(settings):
        result[f'GIT_CONFIG_KEY_{index}'] = key
        result[f'GIT_CONFIG_VALUE_{index}'] = value
    return result


def repository_auth_failure(*details):
    """Classify transiently; never retain raw repository errors in receipts."""
    text = "\n".join(value.decode(errors="replace") if isinstance(value, bytes) else str(value or "") for value in details).lower()
    return any(marker in text for marker in (
        "saml", "single sign-on", "sso enforcement", "authentication failed", "could not read username",
        "could not read password", "invalid username or token", "permission denied (publickey)",
        "repository not found", "requested url returned error: 403", "requested url returned error: 401",
        "http 403", "http 401", "oauth token access", "terminal prompts disabled",
    ))


def anonymous_retry_environment(argv, env, stdout=b"", stderr=b""):
    """One anonymous retry for a download; never retry a remote mutation."""
    current = os.environ if env is None else env
    if current.get("AMPLIFIER_GITHUB_ACCESS") == "anonymous" or not argv:
        return None
    args = [str(arg) for arg in argv]
    executable = Path(args[0]).name.removesuffix('.exe')
    command = args[1:]
    if executable == 'git':
        while command and command[0] in {'-c', '-C', '--git-dir', '--work-tree'}:
            command = command[2:]
    read = executable == 'git' and command and command[0] in {'ls-remote', 'fetch', 'clone'}
    install = executable == 'uv' and command and (command[0] in {'sync', 'lock'} or command[:2] in [['pip', 'install'], ['tool', 'install']])
    probe = executable in {'uv', 'python', 'python3', 'python3.13'} and any(Path(arg).name == 'update_probe.py' for arg in command)
    if not (read or install or probe):
        return None
    details = b"\n".join((value if isinstance(value, bytes) else str(value or "").encode())[-65536:] for value in (stdout, stderr))
    if probe:
        for line in details.splitlines():
            if line.startswith(b"AMPLIFIER_UPDATE_PROBE=") and len(line) <= 8192:
                try:
                    facts = json.loads(line.split(b"=", 1)[1])
                    if isinstance(facts, dict) and facts.get("repositoryAccessFailure") is True:
                        return repository_environment(current, anonymous=True)
                except ValueError:
                    pass
    github = 'github.com/' in ' '.join(args).lower() or b'github.com' in details.lower()
    if github and (read or install) and repository_auth_failure(details):
        return repository_environment(current, anonymous=True)
    return None
