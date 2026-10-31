"""Separate complete invocation identity from bounded Work content pages."""
import json


def input_identity_bytes(header):
    """Size beyond empty identity arrays in a canonical or validated header.

    Only call after producer construction or strict DTO validation. Per-input
    diagnostics are validated scalars, not source bodies. Their number follows
    native input identity, not the content-page budget. Actual retained read
    bytes still count in full against the delivery cache's memory budget.
    """
    coverage = header.get('coverage', {})
    arrays = [header[key] for key in ('inputIds', 'associationRequestIds') if key in header]
    arrays.extend(coverage[key] for key in ('supportedInputIds', 'missingInputIds', 'unsupportedInputs')
                  if key in coverage)
    return sum(len(json.dumps(value, ensure_ascii=False).encode()) - 2 for value in arrays)
