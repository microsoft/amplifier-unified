"""Pure, deliberately small voice observation and pacing policy.

No model text, transcript excerpts, tool arguments, labels or identifiers enter
spoken progress. A status observation is never a completion receipt.
"""
from dataclasses import dataclass
import re
from .resident_activity import ActivityFacts

# Whole utterances only. Compound requests and paraphrased model arguments are
# deliberately not interpreted here; they retain the ordinary Amplifier path.
_STATUS = frozenset({
    'status', 'status please', 'what is the status', "what's the status",
    'what is the status of my task', "what's the status of my task",
    'are you still working', 'are you still working on it',
    'what are you doing', 'how is it going', "how's it going",
    'any progress', 'any update', 'any updates', 'is it still running',
})


def normalize_utterance(text):
    if not isinstance(text, str) or len(text) > 160:
        return ''
    return re.sub(r'\s+', ' ', text.strip().lower().replace('’', "'")).rstrip('?.!')


def status_utterance(text):
    return normalize_utterance(text) in _STATUS


# Exact builtin names only; arbitrary identifiers cannot become speech.
_TOOLS = {'bash': 'running a command', 'read_file': 'reading a file',
          'write_file': 'updating a file', 'edit_file': 'updating a file',
          'web_search': 'searching the web', 'web_fetch': 'reading a web page'}


def public_status(facts: ActivityFacts):
    """Return a host enum and sentence; never infer success from idle/stopped."""
    if facts.status == 'unavailable':
        return 'unavailable', 'Current task status is unavailable.'
    if facts.configuring:
        return 'preparing', 'Amplifier is preparing the task configuration.'
    if facts.approvals:
        return 'approval', 'An approval is waiting in the chat. Other work may still be running.'
    if facts.questions:
        return 'question', 'A question is waiting in the chat. Other work may still be running.'
    if facts.status in {'error', 'failed'}:
        return 'error', 'The task reports an error. Check the chat for details.'
    if facts.status in {'stopped', 'interrupted', 'stopping'}:
        return 'stopped', 'The task is stopped or stopping. That does not confirm completion.'
    if facts.tools:
        phrase = _TOOLS.get(facts.tools[0], 'using a tool') if len(facts.tools) == 1 else 'using tools'
        return 'tool:' + phrase, 'Amplifier is ' + phrase + '.'
    if facts.status in {'starting', 'preparing'}:
        return 'preparing', 'Amplifier is preparing the task.'
    if facts.status in {'working', 'running'}:
        return 'working', 'Amplifier is still working.'
    if facts.workers:
        return 'workers', 'Background workers are still running.'
    if facts.truncated:
        return 'partial', 'Only part of the current activity is available. Check the chat for current status.'
    if facts.status in {'idle', 'ready', 'complete', 'completed'}:
        return 'idle', 'No active work is reported for this conversation. That is not a completion confirmation.'
    return 'unknown', 'Current task progress is not available.'


@dataclass(frozen=True)
class ProgressNotice:
    key: str
    text: str
    revision: int
    threshold: int = -1


class PacingPolicy:
    """Work-span grace and phase cooldown, with finite elapsed observations.

    Elapsed notices mean time observed by this call, never estimated task age.
    Phase chatter cannot restart the work-span grace. User speech does reset it.
    """
    intervals = (5.0, 20.0, 60.0, 120.0)
    elapsed = ('', 'twenty seconds', 'one minute', 'two minutes')

    def __init__(self):
        self.key = None
        self.active = False
        self.since = 0.0
        self.last_sent = float('-inf')
        self.last_key = None
        self.stage = 0
        self.revision = 0

    def interrupt(self, now):
        self.since = now
        self.stage = 0
        self.last_key = None
        self.revision += 1

    def observe(self, facts, now):
        key, text = public_status(facts)
        active = key in {'working', 'workers', 'preparing', 'approval', 'question'} or key.startswith('tool:')
        if active and not self.active:
            self.since, self.stage, self.last_key = now, 0, None
        self.active = active
        if key != self.key:
            self.key = key
            self.revision += 1
        if not active:
            return None, None
        changed = self.last_key is not None and self.last_key != key
        threshold = self.stage
        if changed:
            deadline = max(self.since + 5, self.last_sent + 5)
            threshold = -1
        elif self.stage < len(self.intervals):
            deadline = max(self.since + self.intervals[self.stage], self.last_sent + 5)
        else:
            return None, None
        if now < deadline:
            return None, deadline
        if threshold > 0:
            text = 'This call has observed ongoing activity for at least ' + self.elapsed[threshold] + '. ' + text
        return ProgressNotice(key, text, self.revision, threshold), None

    def delivered(self, notice, now):
        if notice.revision == self.revision:
            if notice.threshold >= 0:
                self.stage = notice.threshold + 1
            self.last_key = notice.key
            self.last_sent = now
