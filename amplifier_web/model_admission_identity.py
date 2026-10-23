"""Private provider-call input identity, distinct from visible turn ancestry."""


def input_id(call):
    # In-flight workers from an earlier app version supply only turnId. An
    # explicitly supplied empty/invalid identity must not borrow that fallback.
    value = call.get('admissionInputId') if 'admissionInputId' in call else call.get('turnId')
    return value if isinstance(value, str) and 0 < len(value) <= 200 else None
