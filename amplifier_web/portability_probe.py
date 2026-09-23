"""Isolated destination execution check; never load or continue a saved task.

``accountVerified`` means the configured provider accepted one bounded request
for the selected model using destination-owned configuration. It does not name
or attest a human, organization, subscription, billing account, or remote host.
Provider discovery alone is never sufficient. Run this module in the destination
runtime subprocess so credentials stay in its memory and provider diagnostics
cannot enter the JSON protocol.
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
import inspect
import json
import os
from pathlib import Path
import re
import sys
import tempfile

if not __package__:
    from runtime_bootstrap import bootstrap_app_package
    bootstrap_app_package()

from amplifier_web.provider_environment import (
    config_schema,
    construct_provider,
    materialize_provider_config,
    provider_class,
)
from amplifier_web.portability_policy import PROMPT, MAX_OUTPUT_TOKENS, completion_receipt, policy_digest, validate_policy


MAX_REQUEST_BYTES = 65536


class ProbeFailure(ValueError):
    """Only fixed, application-owned codes may enter the result protocol."""

    def __init__(self, code):
        self.code = code


def _runtime_transfer_fence():
    """Exercise the installed capability on disposable native data."""
    try:
        from amplifier_foundation.session import SharedSessionStore, SessionTransferFencedError
        from amplifier_foundation.session.shared_state import SharedStateError
        if any(not callable(getattr(SharedSessionStore, name, None))
               for name in ("acquire_transfer", "confirm_transfer_commit")):
            raise ProbeFailure("runtime_fence_unavailable")
        previous_home = os.environ.get("AMPLIFIER_HOME")
        try:
            with tempfile.TemporaryDirectory(prefix="amplifier-transfer-probe-") as temporary, ExitStack() as handles:
                root = Path(temporary)
                workspace = root / "workspace"
                workspace.mkdir()
                os.environ["AMPLIFIER_HOME"] = str(root / "native")
                store = SharedSessionStore(workspace, "probe-session", root=root / "coordination")
                held = store.acquire(app="destination-transfer-probe")
                handles.callback(held.release)
                held.fence_transfer("probe-transfer", "destination", role="destination")
                held.release()
                try:
                    unexpected = store.acquire(app="ordinary-probe")
                except SessionTransferFencedError:
                    pass
                else:
                    handles.callback(unexpected.release)
                    raise ProbeFailure("runtime_fence_unavailable")
                recovery = store.acquire_transfer("probe-transfer", app="destination-transfer-probe")
                handles.callback(recovery.release)
                try:
                    recovery.check()
                except SharedStateError:
                    pass
                else:
                    raise ProbeFailure("runtime_fence_unavailable")
                recovery.clear_transfer()
                recovery.release()
                ordinary = store.acquire(app="destination-transfer-probe")
                handles.callback(ordinary.release)
                ordinary.check()
                ordinary.fence_transfer("probe-source-transfer", "destination", role="source")
                ordinary.release()
                source = store.acquire_transfer("probe-source-transfer", app="destination-transfer-probe")
                handles.callback(source.release)
                source.commit_transfer()
                source.release()
                confirmed = store.confirm_transfer_commit("probe-source-transfer", app="destination-transfer-probe")
                if confirmed.get('phase') != 'committed' or confirmed.get('role') != 'source':
                    raise ProbeFailure("runtime_fence_unavailable")
                try:
                    unexpected = store.acquire(app="ordinary-committed-probe")
                except SessionTransferFencedError:
                    pass
                else:
                    handles.callback(unexpected.release)
                    raise ProbeFailure("runtime_fence_unavailable")
        finally:
            if previous_home is None:
                os.environ.pop("AMPLIFIER_HOME", None)
            else:
                os.environ["AMPLIFIER_HOME"] = previous_home
    except Exception:
        raise ProbeFailure("runtime_fence_unavailable") from None


def _validate_request(request):
    if (not isinstance(request, dict)
            or set(request) - {"module", "config", "source", "registryHome", "model", "readinessPolicy"}
            or not isinstance(request.get("module"), str)
            or not re.fullmatch(r"provider-[A-Za-z0-9_-]{1,120}", request["module"])
            or not isinstance(request.get("config"), dict)
            or not isinstance(request.get("model"), str)
            or not 1 <= len(request["model"]) <= 200
            or any(ord(char) < 33 for char in request["model"])
            or (request.get("source") is not None and (
                not isinstance(request["source"], str) or not 1 <= len(request["source"]) <= 4000
                or not isinstance(request.get("registryHome"), str) or not request["registryHome"]))):
        raise ProbeFailure("invalid_request")
    try:
        policy = validate_policy(request.get('readinessPolicy'))
    except ValueError:
        raise ProbeFailure('invalid_readiness_policy') from None
    if policy['model'] != request['model'] or policy['providerModule'] != request['module']:
        raise ProbeFailure('invalid_readiness_policy')
    return policy


async def _strict_close(provider):
    close = getattr(provider, 'close', None) or getattr(provider, 'aclose', None)
    if not callable(close):
        raise ProbeFailure('provider_close_unsupported')
    try:
        result = close()
        if inspect.isawaitable(result):
            await asyncio.wait_for(result, 3)
    except Exception:
        raise ProbeFailure('provider_close_failed') from None


async def _bounded_info(provider, capability):
    info = provider.get_info()
    if inspect.isawaitable(info):
        info = await info
    value = info.model_dump(mode='json') if hasattr(info, 'model_dump') else info
    capabilities = value.get('capabilities', []) if isinstance(value, dict) else []
    if not isinstance(capabilities, (list, tuple, set)) or capability not in capabilities:
        raise ProbeFailure('single_attempt_unsupported')
    return info


async def _probe(request):
    policy = _validate_request(request)
    _runtime_transfer_fence()
    if request.get("source"):
        from amplifier_foundation import Bundle
        previous_home = os.environ.get("AMPLIFIER_HOME")
        try:
            os.environ["AMPLIFIER_HOME"] = request["registryHome"]
            bundle = Bundle.from_dict({"bundle": {"name": "destination-execution-probe"},
                "providers": [{"module": request["module"], "source": request["source"]}]})
            await bundle.prepare(strict=True)
        finally:
            if previous_home is None:
                os.environ.pop("AMPLIFIER_HOME", None)
            else:
                os.environ["AMPLIFIER_HOME"] = previous_home
    cls = provider_class(request["module"])
    schema_provider = construct_provider(cls, {})
    try:
        # Endpoint-dependent capability belongs to the materialized provider,
        # not this empty-config instance used only to discover its schema.
        schema = await config_schema(schema_provider)
    finally:
        await _strict_close(schema_provider)
    config = materialize_provider_config(request["config"], schema)
    previous_copilot = os.environ.get("COPILOT_AGENT_TOKEN")
    copilot = request["module"] == "provider-github-copilot" and config.get("github_token")
    if copilot:
        os.environ["COPILOT_AGENT_TOKEN"] = config["github_token"]
    provider = None
    try:
        provider = construct_provider(cls, config)
        await _bounded_info(provider, policy['capability'])
        complete = getattr(provider, "complete", None)
        if not callable(complete):
            raise ProbeFailure("inference_unsupported")
        from amplifier_core.message_models import ChatRequest, ChatResponse, Message
        prompt = ChatRequest(messages=[Message(role="user", content=PROMPT)], model=request["model"],
            reasoning_effort=policy['reasoningEffort'], max_output_tokens=MAX_OUTPUT_TOKENS,
            tools=None, stream=False, timeout=policy['timeoutSeconds'],
            metadata={"purpose": "destination-execution-probe"})
        options = {'single_attempt': True}
        if policy['version'] == 2:
            options['single_attempt_version'] = 2
        if not inspect.iscoroutinefunction(complete):
            raise ProbeFailure("inference_unsupported")
        try:
            inspect.signature(complete).bind(prompt, request_options=options)
        except TypeError:
            raise ProbeFailure('single_attempt_unsupported') from None
        # None leaves healthy calls running; only an explicitly admitted finite
        # experiment or historical policy imposes an elapsed deadline.
        async with asyncio.timeout(policy['timeoutSeconds']):
            response = await complete(prompt, request_options=options)
        try:
            response = ChatResponse.model_validate(response)
        except Exception:
            raise ProbeFailure("invalid_response") from None
        metadata = response.metadata or {}
        if (metadata.get('openai:status') != 'completed'
                or any(metadata.get(key) for key in ('openai:refusal', 'refusal', 'openai:error', 'openai:incomplete_reason'))
                or response.tool_calls or any(block.type not in {'text', 'thinking', 'reasoning'} for block in response.content)
                or response.finish_reason not in {None, "stop", "end_turn", "stop_sequence", "completed"}
                or not any(block.type == "text" and block.text.strip() for block in response.content)):
            raise ProbeFailure("invalid_response")
        try:
            receipt = completion_receipt(metadata.get('openai:single_attempt'), policy)
        except ValueError:
            raise ProbeFailure('invalid_completion_receipt') from None
        return {"runtimeTransferFence": True, "accountVerified": True, "method": "provider.complete",
                "model": request["model"], "providerModule": request["module"],
                "reasoningEffort": policy['reasoningEffort'], 'readinessPolicyHash': policy_digest(policy),
                'completionReceipt': receipt,
                "accountVerificationScope": "selected-model-request-accepted"}
    finally:
        try:
            if provider is not None:
                await _strict_close(provider)
        finally:
            if copilot:
                if previous_copilot is None:
                    os.environ.pop("COPILOT_AGENT_TOKEN", None)
                else:
                    os.environ["COPILOT_AGENT_TOKEN"] = previous_copilot


async def probe(request):
    """Return only bounded success evidence or sanitized failure, never secrets."""
    try:
        return await _probe(request)
    except ProbeFailure as exc:
        return {"error": "Destination execution probe did not verify readiness.", "code": exc.code}
    except TimeoutError:
        return {"error": "Destination execution probe timed out.", "code": "probe_timeout"}
    except Exception:
        return {"error": "Destination execution probe failed. Check destination credentials, model and runtime.",
                "code": "probe_failed"}


def main():
    # Provider code and SDKs may print secrets to either stream, including with
    # native writes. Keep only a private duplicate for the safe result protocol.
    output = os.fdopen(os.dup(sys.stdout.fileno()), "w")
    with open(os.devnull, "w") as quiet:
        os.dup2(quiet.fileno(), sys.stdout.fileno())
        os.dup2(quiet.fileno(), sys.stderr.fileno())
        try:
            raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
            if len(raw) > MAX_REQUEST_BYTES:
                raise ValueError("oversized request")
            request = json.loads(raw)
        except Exception:
            result = {"error": "Invalid destination probe request.", "code": "invalid_request"}
        else:
            result = asyncio.run(probe(request))
        output.write(json.dumps(result) + "\n")
        output.flush()


if __name__ == "__main__":
    main()
