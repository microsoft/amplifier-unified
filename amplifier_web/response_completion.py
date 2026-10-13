"""Optional display provenance; never a second transcript or completion heuristic."""

MAX_BLOCKS = 128


class ResponseBlocks:
    """Correlate loop-streaming's indexed block group with live message receipts.

    A group is qualified only when its exact public blocks equal the persisted
    terminal row. Missing hooks, synthetic answers, reordered or partial groups
    leave messages unfolded. No suffix/text search chooses a message identity.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.generation = None
        self.total = 0
        self.blocks = {}
        self.current = None
        self.valid = False

    def block(self, event, data, generation):
        index, total = data.get("block_index"), data.get("total_blocks")
        if (type(index) is not int or type(total) is not int
                or not 0 <= index < total <= MAX_BLOCKS or not generation):
            self.valid = False
            return
        if event == "content_block:start":
            if index == 0:
                self.reset()
                self.generation, self.total, self.valid = generation, total, True
            if (not self.valid or generation != self.generation
                    or total != self.total or index != len(self.blocks)):
                self.valid = False
                return
            self.current = None
        else:
            block = data.get("block")
            if (not self.valid or generation != self.generation or total != self.total
                    or index != len(self.blocks) or not isinstance(block, dict)):
                self.valid = False
                return
            text = block.get("text") if block.get("type") == "text" else None
            self.blocks[index] = {"text": text, "sequence": None}
            self.current = index if isinstance(text, str) and text else None

    def message(self, event):
        if not self.valid or self.current is None:
            return
        block = self.blocks[self.current]
        if (event.get("generation_id") != self.generation or block["sequence"] is not None
                or event.get("text") != block["text"] or type(event.get("sequence")) is not int):
            self.valid = False
            return
        block["sequence"] = event["sequence"]
        self.current = None

    def terminal(self, row, generation):
        if (not self.valid or self.generation != generation or len(self.blocks) != self.total
                or row.get("role") != "assistant" or row.get("tool_calls")):
            return None
        content = row.get("content")
        texts = ([content] if isinstance(content, str) else
                 [block.get("text") for block in content if isinstance(block, dict)
                  and block.get("type") in {"text", "output_text"}]
                 if isinstance(content, list) else [])
        texts = [text for text in texts if isinstance(text, str) and text]
        public = [block for block in self.blocks.values() if block["text"]]
        if not texts or texts != [block["text"] for block in public]:
            return None
        if any(type(block["sequence"]) is not int for block in public):
            return None
        return {"version": 1, "generationId": generation,
                "sequences": [block["sequence"] for block in public]}


def apply(session, event):
    """Annotate existing display messages only after qualified root completion."""
    generation = event.get("generation_id")
    if (not generation or event.get("sessionId") != session.get("id")
            or event.get("rootSessionId") != session.get("id")):
        return False
    messages = [row for row in session.get("messages", [])
                if row.get("role") == "assistant" and row.get("generationId") == generation]
    if event.get("event") in {"generation.failed", "generation.detached"}:
        for row in messages:
            row.pop("response", None)
        return bool(messages)
    if event.get("event") != "generation.finished":
        return False
    receipt = event.get("finalMessageRefs") or {}
    sequences = receipt.get("sequences")
    if (receipt.get("version") != 1 or receipt.get("generationId") != generation
            or not isinstance(sequences, list) or not 0 < len(sequences) <= MAX_BLOCKS
            or any(type(value) is not int for value in sequences)
            or len(set(sequences)) != len(sequences) or sequences != sorted(sequences)):
        return False
    final = []
    for sequence in sequences:
        matches = [row for row in messages if (row.get("runtimeMessage") or {}).get("sequence") == sequence]
        if len(matches) != 1 or matches[0].get("partial") or not matches[0].get("text", "").strip():
            return False
        final.append(matches[0])
    ids = [row["id"] for row in final]
    inputs = [value for value in event.get("input_ids", []) if isinstance(value, str)]
    anchor = next((row["id"] for row in session.get("messages", [])
                   if row.get("role") == "user" and row.get("inputId") in inputs), None)
    for row in messages:
        sequence = (row.get("runtimeMessage") or {}).get("sequence")
        # Late/unknown/partial rows never acquire an invented place in the turn.
        if type(sequence) is not int or sequence > sequences[-1] or row.get("partial"):
            continue
        row["response"] = {"version": 1, "generationId": generation, "status": "completed",
                           "phase": "final" if row["id"] in ids else "interim",
                           "finalMessageIds": ids, "inputIds": inputs, "anchorMessageId": anchor}
    return True


def invalidate_removed(session, removed):
    """Editing away a final response also removes its remaining collapse facts."""
    for row in session.get("messages", []):
        if removed.intersection((row.get("response") or {}).get("finalMessageIds", [])):
            row.pop("response", None)