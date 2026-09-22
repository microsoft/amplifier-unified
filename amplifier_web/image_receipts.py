"""Validate producer-reported image receipts against exact local artifact bytes."""
import hashlib
import json

from jsonschema import Draft202012Validator


def read_image_receipt(workspace, path):
    from .outputs import file_bytes
    from .voice_visual_image import validate_png
    data, _, _ = file_bytes(workspace, path)
    if len(data) > 64000:
        raise ValueError('Image receipt exceeds 64 KB.')
    receipt = json.loads(data)
    text = {'type': 'string', 'minLength': 1, 'maxLength': 500}
    sha = {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}
    validator = Draft202012Validator({'type': 'object', 'properties': {
        'schema': {'const': 'amplifier.image.receipt.v1'}, 'status': {'const': 'completed'},
        'requestId': text, 'requestHash': sha, 'backend': text, 'model': text,
        'operation': {'enum': ['generate', 'edit']},
        'inputs': {'type': 'array', 'maxItems': 4, 'items': {'type': 'object',
            'properties': {'path': text, 'sha256': sha, 'role': {'enum': ['target', 'reference']}},
            'required': ['path', 'sha256', 'role'], 'additionalProperties': False}},
        'artifact': {'type': 'object', 'properties': {'path': text, 'sha256': sha,
            'bytes': {'type': 'integer', 'minimum': 1}, 'mimeType': {'const': 'image/png'},
            'width': {'type': 'integer', 'minimum': 1, 'maximum': 4096},
            'height': {'type': 'integer', 'minimum': 1, 'maximum': 4096},
            'mode': {'enum': ['RGB', 'RGBA']}},
            'required': ['path', 'sha256', 'bytes', 'mimeType', 'width', 'height', 'mode'],
            'additionalProperties': False},
        'options': {'type': 'object', 'maxProperties': 10},
        'receiptPath': text, 'providerRequestId': {'type': ['string', 'null'], 'maxLength': 500},
        'usage': {'type': ['object', 'null'], 'maxProperties': 20}},
        'required': ['schema', 'status', 'requestId', 'requestHash', 'operation', 'backend', 'model', 'inputs', 'artifact'],
        'additionalProperties': False})
    if not validator.is_valid(receipt):
        raise ValueError('Choose a valid completed image receipt with exact artifact metadata.')
    inputs = receipt['inputs']
    if (receipt['operation'] == 'edit') != bool(inputs):
        raise ValueError('Image edit receipt requires its original input hash.')
    if inputs and (inputs[0]['role'] != 'target' or any(row['role'] != 'reference' for row in inputs[1:])):
        raise ValueError('The first edit input must be the target; subsequent inputs are references.')
    image, source, name = file_bytes(workspace, receipt['artifact']['path'])
    width, height = validate_png(image, max_bytes=8 * 1024 * 1024, max_dimension=4096)
    artifact = receipt['artifact']
    if (hashlib.sha256(image).hexdigest(), len(image), width, height) != (artifact['sha256'], artifact['bytes'], artifact['width'], artifact['height']):
        raise ValueError('Image bytes no longer match the completed receipt.')
    metadata = {key: receipt[key] for key in ('requestId', 'requestHash', 'backend', 'model', 'operation', 'inputs')}
    metadata.update(receiptSha256=hashlib.sha256(data).hexdigest(),
                    provenance='producer-reported; local artifact bytes verified')
    for key in ('options', 'providerRequestId', 'usage'):
        if key in receipt:
            metadata[key] = receipt[key]
    return image, source, name, metadata
