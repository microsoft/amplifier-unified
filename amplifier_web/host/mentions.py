"""CLI-compatible input mentions using Foundation's shared expansion mechanism."""
from copy import copy
from pathlib import Path
import re

from amplifier_foundation.mentions import BaseMentionResolver, expand_mentions_in_instruction
from amplifier_foundation.paths.resolution import get_amplifier_home


def include_instruction_files(bundle, config_home=None, execution_workspace=None):
    """Bind optional host instruction files without freezing their contents.

    Call after composition and runtime overrides, before preparing an ordinary
    root. Path aliases avoid parsing absolute paths (which may contain spaces)
    and do not activate namespaces through source_base_paths. Foundation owns
    fresh reads, nested mentions and deduplication on each request. Authored
    mentions, including literal tilde paths, retain their original meaning.
    Exported snapshot bundles retain their frozen instructions.
    """
    from amplifier_foundation.mentions import parse_mentions
    name = getattr(bundle, 'name', None)
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', name):
        raise ValueError('Host instruction defaults require a root bundle name usable as a mention namespace (letters, digits, underscores, dots or hyphens).')
    instruction = getattr(bundle, 'instruction', None) or ''
    declared = set(parse_mentions(instruction))
    result = copy(bundle)
    result.context = dict(getattr(bundle, 'context', None) or {})
    config_home = Path(config_home if config_home is not None else Path.home() / '.amplifier').expanduser().resolve()
    workspace = Path(execution_workspace if execution_workspace is not None else Path.cwd()).expanduser().resolve()
    pending = getattr(bundle, '_pending_context', None) or {}
    # Context keys are shared by the prepared namespace views. Do not retarget
    # an author's namespaced reference even if its context is currently absent.
    reserved = {mention[1:].split(':', 1)[1] for mention in declared if ':' in mention}
    missing = []
    for stem, path in (
        ('__unified_instruction_global', config_home / 'AGENTS.md'),
        ('__unified_instruction_project', workspace / '.amplifier/AGENTS.md'),
        ('__unified_instruction_workspace', workspace / 'AGENTS.md'),
    ):
        alias, suffix = stem, 1
        while True:
            pending_collision = alias in pending or f'{name}:{alias}' in pending
            if not pending_collision and alias in result.context and result.context[alias] == path:
                break
            if not pending_collision and alias not in result.context and alias not in reserved:
                break
            suffix += 1
            alias = f'{stem}_{suffix}'
        result.context[alias] = path
        mention = f'@{name}:{alias}'
        if mention not in declared:
            missing.append(mention)
            declared.add(mention)
    if missing:
        result.instruction = '\n\n'.join(part for part in (instruction, '\n'.join(missing)) if part)
    return result


class AppMentionResolver:
    """Add app shortcuts without composing additional bundles or their tools."""

    def __init__(self, foundation, workspace):
        self.foundation = foundation
        self.workspace = Path(workspace)

    def resolve(self, mention):
        return self.resolve_relative(mention, self.workspace)

    def resolve_relative(self, mention, relative_to):
        """Scope local references without retargeting project/user shortcuts."""
        if not mention.startswith('@') or '..' in mention:
            return None
        body = mention[1:]
        if body.startswith('user:'):
            root, relative = get_amplifier_home(), body[5:]
        elif body.startswith('project:'):
            root, relative = self.workspace / '.amplifier', body[8:]
        elif body.startswith('~/'):
            root, relative = Path.home(), body[2:]
        elif ':' in body:
            # Only the prepared composition owns bundle namespaces. Never
            # reinterpret a missing namespace as a local filename.
            return self.foundation.resolve(mention) if self.foundation else None
        else:
            # Retain Foundation's plain-path and omitted-.md behavior used by
            # filesystem tools, scoped to the workspace or referring file.
            return BaseMentionResolver(base_path=relative_to).resolve(mention)
        if not relative:
            return None
        path = root / relative
        return path.resolve() if path.exists() else None


def install(coordinator):
    resolver = coordinator.get_capability('mention_resolver')
    if not isinstance(resolver, AppMentionResolver):
        workspace = coordinator.get_capability('session.working_dir') or Path.cwd()
        coordinator.register_capability('mention_resolver', AppMentionResolver(resolver, workspace))


async def expand_input(coordinator, text, *, max_chars=None):
    if '@' not in text:
        return text
    resolver = coordinator.get_capability('mention_resolver')
    if resolver is None:
        return text
    # A fresh deduplicator includes only this input's references and sees file
    # edits on later turns. Do not prepend a session's entire cached context.
    expanded = await expand_mentions_in_instruction(text, resolver=resolver,
        relative_to=Path(coordinator.get_capability('session.working_dir') or Path.cwd()))
    if max_chars is not None and len(expanded) > max_chars:
        raise ValueError('The message and referenced files exceed this session input limit. Reference smaller files.')
    return expanded
