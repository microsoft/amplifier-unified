"""Bounded JSON-lines transport, shared by both ends of the worker pipe."""
import json

MAX_MESSAGE_BYTES = 32_000_000


def encode_message(data):
    encoded = (json.dumps(data, ensure_ascii=False, default=str) + '\n').encode('utf-8')
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise ValueError('Runtime message exceeds the 32 MB transport limit. Request a smaller state page.')
    return encoded
