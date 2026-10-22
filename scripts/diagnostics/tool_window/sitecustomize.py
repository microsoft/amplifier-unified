"""Prepared diagnostic only; enabled explicitly in an isolated synthetic run.

Retains the canonical functions, return values and exceptions. No retries or
source mutations. Logs are bounded and use fingerprints instead of path/key data.
"""
import os

if os.environ.get('UNIFIED_WINDOW_BOUNDARY_TRACE'):
    import base64
    import hashlib
    import hmac
    import json
    import time
    import traceback
    from pathlib import Path
    import amplifier_web.event_window as window

    destination = Path(os.environ['UNIFIED_WINDOW_BOUNDARY_TRACE'])
    count = 0
    original_end = window._end
    original_read = window.read_window

    def fingerprint(value):
        return hashlib.sha256(value).hexdigest()[:24]

    def record(row):
        global count
        if count >= 256:
            return
        count += 1
        try:
            destination.mkdir(parents=True, exist_ok=True)
            with (destination / ('window-' + str(os.getpid()) + '.jsonl')).open('a') as stream:
                stream.write(json.dumps({'atNs': time.time_ns(), 'pid': os.getpid(), **row}, separators=(',', ':')) + '\n')
        except OSError:
            pass

    def observed_end(before, key, owner, identity, stamp):
        row = {'phase': 'cursor-check', 'hasCursor': before is not None,
               'keyFingerprint': fingerprint(key), 'owner': owner,
               'window': identity, 'actualSourceStamp': stamp}
        if isinstance(before, str):
            row['cursorFingerprint'] = fingerprint(before.encode())
            try:
                raw = base64.b64decode(before + '=' * (-len(before) % 4), altchars=b'-_', validate=True)
                signature, body = raw[:32], raw[32:]
                claims = json.loads(body)
                row.update(signatureValid=hmac.compare_digest(signature, hmac.digest(key, body, 'sha256')),
                           claims=claims,
                           ownerMatches=isinstance(claims, dict) and claims.get('owner') == owner,
                           windowMatches=isinstance(claims, dict) and claims.get('window') == identity,
                           sourceStampMatches=isinstance(claims, dict) and claims.get('source') == stamp)
            except (ValueError, UnicodeError, TypeError) as error:
                row['diagnosticDecodeError'] = type(error).__name__
        try:
            value = original_end(before, key, owner, identity, stamp)
            row.update(status='accepted', returnedEnd=value[0], returnedAligned=value[1])
            return value
        except BaseException as error:
            row.update(status='raised', errorType=type(error).__name__, reason=str(error))
            raise
        finally:
            record(row)

    def observed_read(session, **kwargs):
        row = {'phase': 'window-read', 'sessionId': session.get('id'),
               'nativeIdentity': session.get('nativeIdentity'),
               'runtimeSessionId': session.get('runtimeSessionId'),
               'window': kwargs.get('window_key'),
               'hasCursor': kwargs.get('before') is not None}
        path = None
        try:
            path, native, owner = window._source(session)
            row.update(sourcePathFingerprint=fingerprint(os.fsencode(path)), native=native, owner=owner)
            row['pathStampBefore'] = window._stamp(path.stat())
        except (OSError, ValueError) as error:
            row['diagnosticSourceError'] = type(error).__name__
        try:
            value = original_read(session, **kwargs)
            row.update(status='complete', generation=value.get('generation'), coverage=value.get('coverage'))
            return value
        except BaseException as error:
            row.update(status='raised', errorType=type(error).__name__, reason=str(error),
                       frames=[{'file': Path(frame.filename).name, 'line': frame.lineno, 'function': frame.name}
                               for frame in traceback.extract_tb(error.__traceback__)])
            raise
        finally:
            if path is not None:
                try:
                    row['pathStampAfter'] = window._stamp(path.stat())
                except OSError as error:
                    row['diagnosticFinalStatError'] = type(error).__name__
            record(row)

    window._end = observed_end
    window.read_window = observed_read
