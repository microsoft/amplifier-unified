"""Offline transport boundary for *installed* provider candidates.

Run this file as SetupManager's probe_command; stdin/stdout remain the real
provider_probe protocol. Native-worker fixtures may import and call install().
Nothing replaces provider construction, schemas, discovery, mapping or caches.

Required opt-in: UNIFIED_CATALOG_CANDIDATES=1. Controls (read per request):
UNIFIED_CATALOG_MODE=success|empty|http-error|malformed|timeout
UNIFIED_CATALOG_STATUS=401 (used only by http-error)
UNIFIED_CATALOG_LOG=/absolute/path/to/requests.jsonl (safe, append-only evidence)
UNIFIED_CATALOG_PROVIDER=openai (optional; limits class-evidence imports)

Copilot replaces SDK construction/RPC, not its real provider/wrapper. Its CLI,
wire decoder and service are deliberately outside this qualification. HTTP
providers use real SDK request construction/parsing and MockTransport. Network
socket attempts fail closed, including attempts outside the mocked clients.
"""
from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import importlib
import importlib.metadata
import importlib.util
import inspect
import json
import os
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

PROVIDERS = (
    "openai", "anthropic", "gemini", "chat-completions", "openai-chatgpt", "github-copilot",
)
SYNTHETIC_KEY = "catalog-fixture-not-a-live-credential"
SELECTION_ONLY = "selection-only-not-returned-by-api"
MODEL_IDS = {
    "openai": ["gpt-6", "gpt-6-2026-11-01", "gpt-6-mini", "gpt-4o", "future-unrecognized-family"],
    "anthropic": ["claude-sonnet-4-5-20250929", "account-only-experimental-model"],
    "gemini": ["gemini-future-exp", "future-unrecognized-family", "tunedModels/customer-custom"],
    "chat-completions": ["account-model-one", "future-unrecognized-family"],
    "openai-chatgpt": ["account-zeta", "literal-fast"],
    "github-copilot": ["gpt-4.1", "future-unrecognized-family"],
}
OPENAI_DEFAULT_IDS = ["gpt-6", "gpt-6-mini"]
_installed = False


def _record(event, **values):
    """Never record headers, credentials, request bodies or raw exception text."""
    path = os.environ.get("UNIFIED_CATALOG_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(json.dumps({"event": event, "pid": os.getpid(), **values}) + "\n")


def _mode():
    mode = os.environ.get("UNIFIED_CATALOG_MODE", "success")
    if mode not in {"success", "empty", "http-error", "malformed", "timeout"}:
        raise ValueError("Unknown offline catalog transport mode")
    return mode


def _payload(provider, empty=False):
    ids = [] if empty else MODEL_IDS[provider]
    if provider in {"openai", "chat-completions"}:
        return {"object": "list", "data": [
            {"id": mid, "object": "model", "created": 1, "owned_by": "fixture"}
            for mid in ids
        ]}
    if provider == "anthropic":
        return {
            "data": [{"id": mid, "display_name": mid, "type": "model",
                      "created_at": "2026-01-01T00:00:00Z"} for mid in ids],
            "has_more": False, "first_id": ids[0] if ids else None,
            "last_id": ids[-1] if ids else None,
        }
    if provider == "gemini":
        return {"models": [{"name": mid if mid.startswith("tunedModels/") else "models/" + mid}
                           for mid in ids]}
    if provider == "openai-chatgpt":
        return {"models": [{"slug": mid, "display_name": mid, "visibility": "list",
                            "supported_in_api": True, "context_window": 64000,
                            "max_output_tokens": 8000} for mid in ids]}
    raise ValueError("Provider has no HTTP fixture")


def _http_provider(request):
    host, path = request.url.host, request.url.path
    if request.method != "GET" or request.content or not path.endswith("/models"):
        _record("forbidden", boundary="http-method-or-path")
        raise AssertionError("Only model-list GET requests are allowed")
    if host == "catalog.invalid":
        for provider in ("openai", "anthropic", "chat-completions"):
            if path == f"/{provider}/v1/models":
                return provider
    routes = {
        ("api.openai.com", "/v1/models"): "openai",
        ("api.anthropic.com", "/v1/models"): "anthropic",
        ("generativelanguage.googleapis.com", "/v1beta/models"): "gemini",
        ("chatgpt.com", "/backend-api/codex/models"): "openai-chatgpt",
    }
    if (host, path) not in routes:
        _record("forbidden", boundary="http-endpoint")
        raise AssertionError("Unexpected model-list endpoint")
    return routes[host, path]


def _response(http, request):
    provider = _http_provider(request)
    mode = _mode()
    status = int(os.environ.get("UNIFIED_CATALOG_STATUS", "401")) if mode == "http-error" else 200
    _record("request", provider=provider, boundary="http", method=request.method,
            path=request.url.path, mode=mode, status=status)
    header = "x-goog-api-key" if provider == "gemini" else "x-api-key" if provider == "anthropic" else "authorization"
    expected = SYNTHETIC_KEY if header != "authorization" else "Bearer " + SYNTHETIC_KEY
    if request.headers.get(header) != expected:
        _record("forbidden", boundary="nonfixture-credential", provider=provider)
        raise AssertionError("Only the explicit synthetic credential is accepted")
    if mode == "timeout":
        raise http.ReadTimeout("Synthetic catalog timeout", request=request)
    if mode == "http-error":
        return http.Response(status, json={"error": {"message": "Synthetic catalog failure",
                                                     "type": "api_error", "code": status}},
                             headers={"retry-after": "0"})
    if mode == "malformed":
        # Valid JSON with an invalid collection tests strict parsing, not HTTP.
        collection = "models" if provider in {"gemini", "openai-chatgpt"} else "data"
        return http.Response(200, json={collection: "not-a-list", "object": "list", "has_more": False})
    return http.Response(200, json=_payload(provider, empty=mode == "empty"))


def _patch_http(http):
    # Patch initialization, not the class: SDK isinstance checks and subclasses
    # still see the real HTTP client. SDK copies retain these same transports.
    for cls in (http.Client, http.AsyncClient):
        original = cls.__init__

        def initialize(self, *args, _original=original, **kwargs):
            kwargs.update(transport=http.MockTransport(lambda request: _response(http, request)),
                          trust_env=False)
            _original(self, *args, **kwargs)

        cls.__init__ = initialize


def _patch_gemini():
    import httpx
    from google import genai

    original = genai.Client.__init__

    def initialize(self, *args, **kwargs):
        options = kwargs.get("http_options") or genai.types.HttpOptions()
        if isinstance(options, dict):
            options = genai.types.HttpOptions(**options)
        # Explicit HTTP clients avoid the optional aiohttp SDK route. Preserve
        # the actual SDK endpoint/auth configuration supplied by the provider.
        kwargs["http_options"] = options.model_copy(update={
            "httpx_client": httpx.Client(),
            "httpx_async_client": httpx.AsyncClient(),
            "retry_options": genai.types.HttpRetryOptions(attempts=1),
        })
        original(self, *args, **kwargs)

    genai.Client.__init__ = initialize


def _patch_copilot():
    from amplifier_module_provider_github_copilot.sdk_adapter import client

    class OfflineCopilotSDK:
        """Structural typed-RPC response; the real wrapper owns lifecycle/mapping."""

        def __init__(self, **kwargs):
            if kwargs.get("github_token") != SYNTHETIC_KEY:
                _record("forbidden", boundary="copilot-credential")
                raise AssertionError("Copilot must receive the synthetic explicit token")

        async def start(self):
            self.rpc = SimpleNamespace(models=SimpleNamespace(list=self.models_list))
            _record("sdk-start", provider="github-copilot")

        async def stop(self):
            _record("sdk-stop", provider="github-copilot")

        async def models_list(self, request):
            mode = _mode()
            status = int(os.environ.get("UNIFIED_CATALOG_STATUS", "401"))
            _record("request", provider="github-copilot", boundary="typed-sdk-rpc",
                    method="models.list", mode=mode,
                    status=status if mode == "http-error" else 200)
            if mode == "http-error":
                raise RuntimeError(f"{status} synthetic catalog service error")
            if mode == "timeout":
                raise TimeoutError("Synthetic SDK catalog timeout")
            if mode == "malformed":
                return SimpleNamespace(models="not-a-list")
            rows = []
            for mid in ([] if mode == "empty" else MODEL_IDS["github-copilot"]):
                rows.append(SimpleNamespace(
                    id=mid, name=mid,
                    capabilities=SimpleNamespace(
                        supports=SimpleNamespace(vision=False, reasoning_effort=False),
                        limits=SimpleNamespace(max_prompt_tokens=64000,
                                               max_context_window_tokens=72000,
                                               max_output_tokens=8000, vision=None)),
                    policy=None, billing=None, supported_reasoning_efforts=None,
                    default_reasoning_effort=None,
                ))
            return SimpleNamespace(models=rows)

        async def list_models(self):
            _record("forbidden", boundary="copilot-legacy-convenience-cache")
            raise AssertionError("Strict discovery must use typed RPC")

    client.CopilotClient = OfflineCopilotSDK
    # No real binary is launched, inspected for repair or chmod'ed by this SDK.
    client.locate_cli_binary = lambda: None


def _forbid_network(event, args):
    if event == "socket.getaddrinfo" or (
        event in {"socket.connect", "socket.sendto"}
        and args[0].family in {socket.AF_INET, socket.AF_INET6}
    ):
        _record("forbidden", boundary="network-socket")
        raise RuntimeError("Real network is forbidden in candidate qualification")


def class_evidence(provider):
    """Verify installed Python sources against the parent's reviewed manifest."""
    from amplifier_web.provider_environment import provider_class

    cls = provider_class("provider-" + provider)
    path = Path(inspect.getfile(cls)).resolve()
    package = "amplifier_module_provider_" + provider.replace("-", "_")
    package_root = Path(importlib.import_module(package).__file__).resolve().parent
    manifest = "canonical_model_catalog_candidate.json" if provider == "openai" else "model_catalog_candidates.json"
    candidate = json.loads(Path(__file__).with_name(manifest).read_text())["providers"][provider]
    if candidate["module"] != package or not path.is_relative_to(package_root):
        raise AssertionError("Candidate class is not defined in its installed package")
    for relative, expected in candidate["files"].items():
        actual = hashlib.sha256((package_root / relative).read_bytes()).hexdigest()
        if actual != expected:
            raise AssertionError(f"Installed candidate source mismatch: {provider}/{relative}")
    distribution = "amplifier-module-provider-" + provider
    try:
        version = importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        version = None
    return {"provider": provider, "class": cls.__qualname__, "module": cls.__module__,
            "package": package, "file": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "candidateCommit": candidate["commit"], "verifiedSourceFiles": len(candidate["files"]),
            "distributionVersion": version,
            "listingDefinedBy": cls.list_models.__module__}


def install():
    """Install once in a dedicated probe/worker process; return class evidence.

    This is deliberately process-scoped (audit hooks cannot be uninstalled).
    Do not call it in the pytest host or a live user's worker.
    """
    global _installed
    if os.environ.get("UNIFIED_CATALOG_CANDIDATES") != "1":
        raise RuntimeError("Candidate transport requires explicit test opt-in")
    selected = os.environ.get("UNIFIED_CATALOG_PROVIDER")
    providers = (selected,) if selected else PROVIDERS
    if any(provider not in PROVIDERS for provider in providers):
        raise ValueError("Unknown candidate provider")
    if not _installed:
        sys.addaudithook(_forbid_network)
        import httpx
        _patch_http(httpx)
        # Reviewed Anthropic 1.x uses httpx2; older SDKs use httpx. Never replace
        # SDK modules or version requirements to make an import succeed.
        if importlib.util.find_spec("httpx2") is not None:
            httpx2 = importlib.import_module("httpx2")
            if httpx2.AsyncClient is not httpx.AsyncClient:
                _patch_http(httpx2)
        if "gemini" in providers:
            _patch_gemini()
        if "github-copilot" in providers:
            _patch_copilot()
        _installed = True
    evidence = [class_evidence(provider) for provider in providers]
    for row in evidence:
        _record("provider-class", **row)
    return evidence


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    # Protect the protocol during adapter imports too, before main redirects
    # provider stdout at the file-descriptor boundary.
    with redirect_stdout(sys.stderr):
        install()
    from amplifier_web.provider_probe import main
    main()
