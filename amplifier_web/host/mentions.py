"""CLI-compatible input mentions using Foundation's shared expansion mechanism."""
from copy import copy
from pathlib import Path

from amplifier_foundation.mentions import BaseMentionResolver, expand_mentions_in_instruction
from amplifier_foundation.paths.resolution import get_amplifier_home


def include_instruction_files(bundle):
    """Include the CLI's optional instruction files without freezing their contents.

    Call after composition and runtime overrides, before preparing an ordinary
    root. Foundation resolves these mentions afresh for the session workspace
    on each request. Exported snapshot bundles retain their frozen instructions.
    """
    from amplifier_foundation.mentions import parse_mentions
    instruction = getattr(bundle, 'instruction', None) or ''
    declared = set(parse_mentions(instruction))
    missing = [path for path in ('@~/.amplifier/AGENTS.md', '@.amplifier/AGENTS.md') if path not in declared]
    result = copy(bundle)
    if missing:
        result.instruction = '\n\n'.join(part for part in (instruction, '\n'.join(missing)) if part)
    return result


class AppMentionResolver:
    """Add app shortcuts without composing additional bundles or their tools."""

    def __init__(self, foundation, workspace):
        self.foundation = foundation
        self.workspace = Path(workspace)

    def resolve(self, mention):
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
            # filesystem tools, scoped to this session's workspace.
            return BaseMentionResolver(base_path=self.workspace).resolve(mention)
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
