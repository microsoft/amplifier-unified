"""Bind a completed portable note to its creating connection and actual target reply."""
import logging
import hashlib
import hmac

from .provider_catalog import require_mounted_binding
from .surface_delivery import SurfaceProvider


# Context's versioned checkpoint already signs the entire record, including optional host fields.
_ORIGIN = "unifiedPortableOrigin"


def _unit(record):
    """Evidence references may advance with the suffix while this exact note stays intact."""
    if (not isinstance(record, dict) or not isinstance(record.get("identity"), dict)
            or not isinstance(record.get("configuration"), str) or not isinstance(record.get("sourceRevision"), dict)
            or record.get("progress") or record.get("recovery")
            or not isinstance(record.get("summary"), dict)
            or not isinstance(record["summary"].get("text"), str)):
        return None
    return (record["identity"], record["configuration"], record["sourceRevision"], record["summary"])


def _fingerprint(session_id, unit):
    from .context_continuity import digest
    return digest([session_id, *unit])


def _proof(key, fingerprint):
    return hmac.new(key.encode(), fingerprint.encode(), hashlib.sha256).hexdigest()


def _stamp(record, proof):
    from .context_continuity import digest
    record[_ORIGIN] = proof
    record["sha256"] = digest({k: v for k, v in record.items() if k != "sha256"})


class PortableHandoff:
    def __init__(self, continuity):
        self.host = continuity
        self.created = None

    def _connection(self, provider, identity, *, generating=False):
        from .host.session import SelectedProvider
        coordinator = self.host.coordinator
        if provider is not self.host._provider() or identity != self.host.current_identity():
            return None
        mounted = (coordinator.get("providers") or {}).get(identity["provider"])
        if isinstance(provider, SelectedProvider):
            if provider.original is not mounted:
                return None
        elif not generating or provider is not mounted:
            return None
        catalog = coordinator.get_capability("web.provider_catalog") or {}
        key = (catalog.get("connectionKeys") or {}).get(identity["provider"])
        if not isinstance(key, str) or not key:
            return None
        require_mounted_binding(coordinator, identity["provider"], key)
        return key

    def generated(self, provider, record):
        """Only the context owner's successful new generation can establish an origin."""
        unit = _unit(record)
        if unit is None:
            return
        try:
            key = self._connection(provider, record["identity"], generating=True)
        except ValueError:
            return  # An unavailable/rotated connection cannot attest to this note.
        if key:
            fingerprint = _fingerprint(self.host.session_id, unit)
            self.created = (fingerprint, _proof(key, fingerprint))

    def stamp(self, record):
        unit = _unit(record)
        if unit is None:
            return
        prior = self.host._accepted_record or {}
        if self.created and self.created[0] == _fingerprint(self.host.session_id, unit):
            _stamp(record, self.created[1])
        elif prior.get(_ORIGIN) and _unit(prior) == unit:
            _stamp(record, prior[_ORIGIN])

    async def offer(self, messages, target):
        """A source classified during preserve may be carried by this particular fresh Send."""
        from .context_continuity import ContextRecoveryError
        host = self.host
        source = host._accepted_record
        if (not isinstance(target, dict) or host._transition is None or host._foreign_rejection or host._recovery_busy
                or not source
                or (source.get("identity") or {}).get("provider") != target.get("provider")
                or source["identity"] == target or host._transition.get("sha256") != source.get("sha256")):
            return None
        unit = _unit(source)
        if unit is None:
            return None
        legacy = _ORIGIN not in source
        if not legacy and (not isinstance(source[_ORIGIN], str)
                           or len(source[_ORIGIN]) != 64 or not source[_ORIGIN].isascii()):
            return None
        provider = host._provider()
        coordinator = host.coordinator
        try:
            key = self._connection(provider, target)
            if (not key or (not legacy and not hmac.compare_digest(
                    source[_ORIGIN], _proof(key, _fingerprint(host.session_id, unit))))
                    or callable(getattr(provider, "request_budget", None)) or callable(getattr(provider, "stream", None))):
                return None
            execution = provider._execution_provider()
            if execution is not provider.original and not isinstance(execution, SurfaceProvider):
                return None
            foreground = host._foreground()
            if foreground.get("connection") != key:
                return None
            existing = host._recovery_record()
            if existing and existing.get("state") != "committed":
                return None
            authorized, _ = host._fresh_guard(transition=True)
            authorized()
        except (ContextRecoveryError, ValueError):
            return None

        from .context_continuity import digest
        source_digest = digest(source)
        writer = host.writer_guard

        def check():
            authorized()
            if (host._provider() is not provider or host._accepted_record is not source
                    or host._transition is None or host._foreground() != foreground
                    or self._connection(provider, target) != key
                    or host._recovery_record() is not existing):
                raise ContextRecoveryError("transition_contract_changed")
            host._check_writer()
            host._check_transition_file({"sourceCheckpointDigest": source_digest})

        def commit(candidate, *, restore=False):
            _stamp(candidate, _proof(key, _fingerprint(host.session_id, _unit(candidate))))
            classify = coordinator.get_capability("context.checkpoint.transition")
            if classify(candidate, target).get("status") != "compatible":
                raise ContextRecoveryError("recovery_candidate_invalid")
            host._write_record(candidate, writer)
            if restore:
                restored = coordinator.get_capability("context.checkpoint.restore")
                host.status.update(restored(candidate, target))
            host._transition = None

        if legacy:
            # This old record may fence replacement. It never supplies note bytes.
            empty = coordinator.get_capability("context.checkpoint.prepared_originals")
            if callable(empty) and empty(target) is True:
                self._observe_rebuild(provider, target, key, source, check, commit)
            return None

        def stage(candidate, note, context_guard):
            check()
            context_guard()
            if (not isinstance(note, str) or _unit(candidate) is None or candidate["identity"] != target
                    or candidate["sourceRevision"] != source["sourceRevision"]
                    or candidate["summary"] != source["summary"]):
                raise ContextRecoveryError("recovery_candidate_invalid")

            def admit(_selected, request):
                if ((getattr(request, "metadata", None) or {}).get("purpose")
                        or request.model != target["model"]
                        or sum(row.role == "user" and row.content == note for row in request.messages) != 1):
                    return None
                check()
                context_guard()

                def accepted():
                    check()
                    context_guard()
                    commit(candidate, restore=True)
                return accepted

            provider.portable_handoff = admit

        return source, stage

    def _observe_rebuild(self, provider, target, key, source, check, commit):
        """An empty target context may publish its own completed work after its reply."""
        coordinator = self.host.coordinator
        initial = self.created
        export = coordinator.get_capability("context.checkpoint.export")
        writer = coordinator.get_capability("context.persist_checkpoint")
        if not callable(export) or writer != self.host.save:
            return

        def current_owner():
            return (coordinator.get_capability("context.checkpoint.export") is export
                    and coordinator.get_capability("context.persist_checkpoint") is writer)

        def admit(selected, request):
            if (self.created is initial or not self.created or selected.model != target["model"]
                    or request.model != target["model"] or (request.metadata or {}).get("purpose")
                    or not current_owner()):
                return None
            try:
                check()
                candidate = export(target)
            except (OSError, RuntimeError, ValueError):
                return None  # Optional publication must not obstruct the ordinary reply.
            unit = _unit(candidate)
            if (unit is None or unit[0] != target
                    or unit[2]["messages"] < source["sourceRevision"]["messages"]):
                return None
            fingerprint = _fingerprint(self.host.session_id, unit)
            if self.created != (fingerprint, _proof(key, fingerprint)):
                return None
            # Identify the Context-rendered row on the ordinary request, then
            # require that entire carrier to survive the host's final projection.
            text = candidate["summary"]["text"]
            carriers = [row for row in selected.messages if row.role == "user"
                        and (row.metadata or {}).get("source") == "context-managed"
                        and isinstance(row.content, str) and row.content.partition("\n")[2] == text]
            if len(carriers) != 1 or sum(row == carriers[0] for row in request.messages) != 1:
                return None

            def accepted():
                check()
                if current_owner() and _unit(export(target)) == unit:
                    commit(candidate)
            return accepted

        provider.portable_handoff = admit


async def complete(provider, execution, selected, options, admit):
    """Bind acceptance to the actual dispatched projection and its own terminal response."""
    accepted = None

    def observe(outgoing):
        nonlocal accepted
        accepted = admit(selected, outgoing)

    if isinstance(execution, SurfaceProvider):
        response = await execution._complete(selected, options, observe)
    else:
        if execution is provider.original:
            observe(selected)
        response = await execution.complete(selected, **options)

    metadata = getattr(response, "metadata", None)
    if (accepted and isinstance(metadata, dict) and metadata.get("provider_response_completed") is True
            and getattr(response, "finish_reason", None) in {"stop", "end_turn", "tool_calls"}):
        try:
            accepted()
        except Exception as exc:
            # A successful user response is never replayed because a checkpoint could not move.
            logging.getLogger(__name__).warning("Portable history retained its prior checkpoint (%s)", type(exc).__name__)
    return response
