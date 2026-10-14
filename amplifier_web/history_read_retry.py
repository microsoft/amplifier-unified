"""Bounded reacquisition for read-only projections of changing JSONL history."""
from functools import wraps


def retry_indexed_read(read):
    @wraps(read)
    def attempt(*args, **kwargs):
        for number in range(2):
            try:
                return read(*args, **kwargs)
            except ValueError as exc:
                # Retry the whole projection with a fresh index. Never retry
                # identity/anchor validation, mutations, or model execution.
                if type(exc) is not ValueError or exc.args != ('Session history changed during indexed read',):
                    raise
                if number:
                    raise ValueError('This chat is being saved. Please try opening it again.') from exc
    return attempt
