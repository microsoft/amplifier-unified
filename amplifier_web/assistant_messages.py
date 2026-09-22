"""Public message provenance and replay deduplication, independent of turns."""
import math


def metadata(event):
    provenance = {}
    for source, target in (("message_id", "messageId"), ("event_id", "eventId")):
        value = event.get(source)
        if isinstance(value, str) and value:
            provenance[target] = value
    # Runtime sequences restart when a worker is recreated. A generation gives
    # them a durable scope; an unscoped sequence is not a message identity.
    if event.get("generation_id") and type(event.get("sequence")) is int:
        provenance["sequence"] = event["sequence"]
    result = {"runtimeMessage": provenance} if provenance else {}
    at = event.get("time")
    if type(at) in (int, float) and math.isfinite(at) and at >= 0:
        result.update(createdAt=at, timestampKnown=True)
    return result


def duplicate(messages, payload):
    """Keep distinct blocks in one generation while retaining legacy fallback."""
    generation, input_id = payload.get("generationId"), payload.get("inputId")
    identity = payload.get("runtimeMessage") or {}
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        if generation:
            same_scope = message.get("generationId") == generation
        elif input_id:
            same_scope = not message.get("generationId") and message.get("inputId") == input_id
        else:
            same_scope = not message.get("generationId") and not message.get("inputId")
        if not same_scope:
            continue
        previous = message.get("runtimeMessage") or {}
        if any(key in previous and previous[key] == value for key, value in identity.items()):
            return True
        if identity and previous:
            continue  # Distinct observed blocks may deliberately repeat text.
        if (generation or input_id) and message.get("text") == payload.get("text", ""):
            # Older/fallback producers do not have per-message identities. Their
            # exact repeated answer is the only safe available replay match.
            return True
    return False
