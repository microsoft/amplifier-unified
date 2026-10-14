"""Pure attributed evidence checks, extracted from amplifier-memory. See PROVENANCE.json."""
from __future__ import annotations
import json
import re
from collections.abc import Sequence

REPLY_SHAPE = 'a JSON list of {"text": "…", "quote": "…"} objects'

_CLAIM_OPENING_RE = re.compile(
    r"^\W*claim\s+\S+\s+from\s+the\s+\S+\s+work[-_ ]?tracker\b", re.IGNORECASE
)

LANE_BRIEF_CHARS = 1500

_LANE_MARKERS: tuple[str, ...] = (
    "work-tracker project",
    "work_claim(",
    "done.json",
    "worker session",
    "lane brief",
)

_REMINDER_BLOCK_RE = re.compile(
    r"<system-reminder\b[^>]*>.*?</system-reminder\s*>", re.DOTALL | re.IGNORECASE
)

_REMINDER_TAG_RE = re.compile(r"</?system-reminders?\b[^>]*>", re.IGNORECASE)

def _without_reminders(turn: str) -> str:
    """The turn with the harness's own reminder blocks removed — for judging it only."""
    return _REMINDER_TAG_RE.sub("", _REMINDER_BLOCK_RE.sub("", turn))

def looks_like_a_lane_brief(turn: str) -> bool:
    """Core 2: is this turn a brief addressed to an agent rather than typed conversation?"""
    body = turn.strip()
    if _CLAIM_OPENING_RE.match(body):
        return True
    lowered = body.lower()
    return len(body) > LANE_BRIEF_CHARS and any(mark in lowered for mark in _LANE_MARKERS)

def is_typed_text(turn: str) -> bool:
    """Core 2: "≥2 human turns of **typed text**" — one turn, judged.

    False for the two shapes the clause names, and for nothing else:

    * a **lane brief** (`looks_like_a_lane_brief`);
    * a **system-reminder-only continuation** — a turn with nothing left once the
      harness's own `<system-reminder…>` blocks are removed.

    A turn that carries reminders *and* a sentence the human typed is typed text: the
    reminders are stripped for this judgement only, never from the turn itself, so
    `verify`'s quote check still sees exactly what was recorded.
    """
    if not turn.strip():
        return False
    if _without_reminders(turn).strip() == "":
        return False
    return not looks_like_a_lane_brief(turn)

class MalformedReply(ValueError):
    """The model's reply was not `REPLY_SHAPE`. Counted, never guessed at."""

def parse_reply(reply: str) -> list[tuple[str, str]]:
    """The reply as `(text, quote)` pairs, or `MalformedReply`.

    Tolerates the one wrapper models add on their own — a ```json fence — and nothing
    else. Inventing structure out of prose is exactly the guessing Core 4 forbids.
    """
    body = reply.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[-1] if "\n" in body else ""
        body = body.rsplit("```", 1)[0].strip()
    if not body:
        raise MalformedReply(f"empty reply; expected {REPLY_SHAPE}")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise MalformedReply(f"not JSON ({exc}); expected {REPLY_SHAPE}") from exc
    if not isinstance(payload, list):
        raise MalformedReply(f"reply is {type(payload).__name__}, expected {REPLY_SHAPE}")
    out: list[tuple[str, str]] = []
    for entry in payload:
        if not isinstance(entry, dict):
            raise MalformedReply(f"list entry is {type(entry).__name__}, expected {REPLY_SHAPE}")
        text, quote = entry.get("text"), entry.get("quote")
        if not isinstance(text, str) or not isinstance(quote, str):
            raise MalformedReply(f"entry is missing text/quote; expected {REPLY_SHAPE}")
        out.append((text.strip(), quote))
    return out

def _flatten(text: str) -> str:
    return " ".join(text.split())

def verify(quote: str, human_turns: Sequence[str]) -> bool:
    """suggestions.v2 Core 4: the quote must appear verbatim in a human turn of that session.

    Whitespace-normalised on both sides, because a transcript re-wraps and a model
    re-flows; nothing else is relaxed. This is the poisoning gate: a candidate whose
    quote is absent from every human turn is rejected and counted, so a model that
    invents a preference cannot get it into the inbox, let alone into memory
    (AGENTS.md rule 7).

    `run_suggest` passes the session's **typed** turns, the same ones the judge was
    shown (Core 2): a quote lifted out of a `<system-reminder>` block or a lane brief is
    not something the human typed, so it fails here even if it is verbatim.
    """
    if not quote.strip():
        return False
    needle = _flatten(quote)
    return any(needle in _flatten(turn) for turn in human_turns)
