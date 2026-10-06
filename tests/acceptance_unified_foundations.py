"""UNIT4: installed, credential-free acceptance; not collected by pytest.

Root session owns execution. From outside all source checkouts, run the qualified
DTU lane venv's Python with -I and this file's absolute path, --root NEW_DIRECTORY.
--scenario defaults to full. warm-voice is not cold-resume evidence;
chatgpt-mount qualifies only a fresh synthetic-token mount without input.
No dependencies are installed here. Every required module must be non-editable
in that venv. The foreground loopback server substitutes only the HTTP endpoint,
not the provider, SDK, Core, Foundation, service, or JSON worker transport.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import signal
import socket
import sys
import sysconfig
import uuid


ANSWER = "PUBLIC-OFFLINE-ANSWER"
MODEL = "gpt-5.4"
FIRST_ID = "offline-first-input"
SECOND_ID = "offline-resumed-input"
CALL_ID = "offline_call"
DELEGATION_ID = "offline_delegation"
VOICE_ID = f"voice:{CALL_ID}:{DELEGATION_ID}"
SPEECH = "OFFLINE-RECORDED-SPEECH"
WRAPPER = (
    "This is a user message arriving through the voice interface of this same "
    "Amplifier conversation.\nHost directions: answer the spoken request.\n"
    '<voice_reference>\n[{"role":"user","text":"OFFLINE-RECORDED-SPEECH"}]\n'
    "</voice_reference>\nCurrent spoken user request:\nOFFLINE-PRIVATE-PARAPHRASE"
)
PROVIDER_KEYS = (
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AZURE_OPENAI_API_KEY",
    "AZURE_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS",
    "GROQ_API_KEY", "MISTRAL_API_KEY", "DEEPSEEK_API_KEY", "XAI_API_KEY",
    "OPENROUTER_API_KEY", "TOGETHER_API_KEY", "FIREWORKS_API_KEY",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "GITHUB_TOKEN", "GH_TOKEN", "COPILOT_API_KEY", "COPILOT_AGENT_TOKEN",
    "OPENAI_ACCESS_TOKEN", "OPENAI_REFRESH_TOKEN", "CHATGPT_ACCESS_TOKEN",
)
PACKAGES = {
    "amplifier_web": "amplifier-unified",
    "amplifier_core": "amplifier-core",
    "amplifier_foundation": "amplifier-foundation",
    "amplifier_module_loop_live": "amplifier-module-loop-live",
    "amplifier_module_loop_streaming": "amplifier-module-loop-streaming",
    "amplifier_module_context_simple": "amplifier-module-context-simple",
    "amplifier_module_provider_openai": "amplifier-module-provider-openai",
    "amplifier_module_provider_openai_chatgpt": "amplifier-module-provider-openai-chatgpt",
    "amplifier_module_hooks_session_naming": "amplifier-module-hooks-session-naming",
    "openai": "openai",
}


def private_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


class Harness:
    def __init__(self, root, scenario="full"):
        self.root = root
        self.scenario = scenario
        self.active_scope = "preflight"
        self.services = []
        self.runtimes = []
        self.processes = []
        self.runner = None
        self.sock = None
        self.requests = []
        self.result = {
            "status": "failed", "stage": "preflight", "pid": os.getpid(),
            "scenario": scenario,
            "qualification_scope": "Selected acceptance scenario only; not complete UNIT qualification.",
            "python": sys.executable, "prefix": sys.prefix, "imports": {},
            "checks": {}, "requests": self.requests, "counts": {},
            "check_scopes": {name: {"present": True, "status": "not_started"} for name in (
                "preflight", "first_input", "cold_resume", "instruction_refresh",
                "private_voice", "read_only_projections", "chatgpt_mount", "request_validation", "cleanup",
            )},
            "limitations": [
                "No real account OAuth, token refresh, or hosted model qualification.",
                "Custom installed-Python worker command; default uv launcher not qualified.",
                "Working-directory/worktree decoy qualification skipped; no worktree API used.",
                "No explicit tool-bash or context-intelligence prerequisite checks; automatically composed hooks are not excluded.",
                "Voice checks cover host-private admission only, not browser HTTP authentication or live audio.",
                "No OS egress firewall asserted here; root must retain DTU network isolation.",
            ],
            "skipped": {"account_oauth": True, "default_uv_launcher": True,
                        "working_directory": True, "unit3_tools": True},
        }
        self.result["check_scopes"]["unit3_prerequisites"] = {
            "present": False, "status": "not_in_harness",
        }
        self.scope("preflight", "in_progress")
        if scenario != "full":
            self.result["skipped"]["cold_resume"] = True
            self.scope("cold_resume", "skipped")
            self.result["limitations"].append("Cold resume skipped; this scenario is not cold-resume evidence.")
        if scenario == "warm-voice":
            self.result["skipped"]["chatgpt_mount"] = True
            self.scope("chatgpt_mount", "skipped")
            self.result["limitations"].append(
                "ChatGPT mount skipped after voice; the mounted shared source store has changed."
            )
        elif scenario == "chatgpt-mount":
            for name in ("first_input", "instruction_refresh", "private_voice", "read_only_projections"):
                self.result["skipped"][name] = True
                self.scope(name, "skipped")
            self.result["limitations"].append("Mount-only scenario: no model calls or voice/projection qualification.")

    def scope(self, name, status):
        self.result["check_scopes"][name]["status"] = status
        if status == "in_progress":
            self.active_scope = name

    def record_failure(self, exc):
        # Never stringify exceptions or dump environment/configuration values.
        if self.result["check_scopes"][self.active_scope]["status"] == "in_progress":
            self.scope(self.active_scope, "failed")
        errors, candidates, seen = [], [], set()
        current = exc
        while current is not None and id(current) not in seen and len(seen) < 8:
            seen.add(id(current))
            errors.append(type(current).__name__)
            diagnostic = getattr(current, "diagnostic_path", None)
            if isinstance(diagnostic, (str, Path)):
                candidates.append(Path(diagnostic))
            current = current.__cause__ or current.__context__
        # Dispatch can wrap the exception without its diagnostic_path. This new
        # isolated root owns all these actual receipts; never parse error strings.
        candidates.extend((self.root / "app/logs/workers").glob("startup-*.log"))
        paths = []
        for candidate in candidates:
            if candidate.is_symlink():
                continue
            path = candidate.resolve()
            if not path.is_relative_to(self.root) or not path.is_file():
                continue
            relative = str(path.relative_to(self.root))
            if relative in paths:
                continue
            paths.append(relative)
            # Original worker exception types are in the private receipt header.
            # Extract only an identifier, never message, frames, stderr or tokens.
            try:
                with path.open(encoding="utf-8") as stream:
                    header = json.loads(stream.readline(60_000))
                failure = header.get("failure")
                if isinstance(failure, str):
                    failure = json.loads(failure)
                error_type = failure.get("errorType") if isinstance(failure, dict) else None
                if isinstance(error_type, str) and len(error_type) <= 100 and error_type.isidentifier():
                    errors.append(error_type)
            except (OSError, ValueError, AttributeError):
                pass
        errors = list(dict.fromkeys(errors))
        self.result["error_type"] = type(exc).__name__
        self.result["error_types"] = errors
        self.result["diagnostic_paths"] = paths
        log = self.root / "startup-failure.json"
        private_write(log, json.dumps({
            "scenario": self.scenario, "stage": self.result["stage"],
            "error_types": errors, "diagnostic_paths": paths,
            "check_scopes": self.result["check_scopes"],
        }, indent=2, sort_keys=True) + "\n")
        self.result["private_startup_log"] = str(log.relative_to(self.root))

    def check(self, name, condition):
        self.result["checks"][name] = bool(condition)
        if not condition:
            raise AssertionError(name)

    def stage(self, name):
        self.result["stage"] = name

    def credentials_absent(self):
        # Names only, never values. Even an empty supplied key is not "absent".
        present = [name for name in PROVIDER_KEYS if name in os.environ]
        self.result["credential_environment_names_present"] = present
        self.check("known_provider_credentials_absent", not present)

    def installed_sources(self):
        self.check("running_in_venv", sys.prefix != sys.base_prefix)
        self.check("no_pythonpath", not os.environ.get("PYTHONPATH"))
        self.check("outside_source_cwd", not any(
            (parent / "amplifier_web").is_dir()
            for parent in (Path.cwd(), *Path.cwd().parents)
        ))
        roots = {Path(sysconfig.get_path(key)).resolve() for key in ("purelib", "platlib")}
        self.check("venv_site_packages", all(
            "site-packages" in root.parts and root.is_relative_to(Path(sys.prefix).absolute())
            for root in roots
        ))
        sources = {}
        for package, distribution_name in PACKAGES.items():
            self.stage("installed_import:" + package)
            module = importlib.import_module(package)
            path = Path(module.__file__).resolve()
            distribution = importlib.metadata.distribution(distribution_name)
            direct = json.loads(distribution.read_text("direct_url.json") or "{}")
            self.result["imports"][package] = {
                "__file__": str(path), "distribution": distribution_name,
                "version": distribution.version,
            }
            self.check(f"installed_{package}", any(path.is_relative_to(root) for root in roots)
                       and not direct.get("dir_info", {}).get("editable", False))
            sources[package] = str(path.parent)
        return sources

    def configure_home(self):
        native, app, workspace = (self.root / name for name in ("native", "app", "workspace"))
        for path in (native, app, workspace / ".amplifier", self.root / "home"):
            path.mkdir(parents=True, mode=0o700)
        os.environ.update({
            "HOME": str(self.root / "home"),
            "AMPLIFIER_HOME": str(native),
            "AMPLIFIER_WEB_HOME": str(app),
            "AMPLIFIER_SESSION_STATE_HOME": str(self.root / "ownership"),
            "AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH": str(self.root / "capture"),
            "AMPLIFIER_RUNTIME_IMMUTABLE": "1",
            "XDG_CONFIG_HOME": str(self.root / "xdg-config"),
            "XDG_CACHE_HOME": str(self.root / "xdg-cache"),
            "XDG_STATE_HOME": str(self.root / "xdg-state"),
            "UV_CACHE_DIR": str(self.root / "uv-cache"),
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        })
        private_write(native / "settings.yaml", "bundle:\n  app: []\n")
        self.instructions(1)
        return app, workspace

    def instructions(self, version):
        private_write(self.root / "native/AGENTS.md", f"GLOBAL-v{version}\n")
        private_write(self.root / "workspace/.amplifier/AGENTS.md",
                      f"PROJECT-v{version}\n@../AGENTS.md\n")
        private_write(self.root / "workspace/AGENTS.md", f"ROOT-v{version}\n")

    async def server(self):
        from aiohttp import web

        async def models(request):
            self.requests.append({"method": "GET", "path": request.path})
            return web.json_response({"object": "list", "data": [
                {"id": MODEL, "object": "model", "created": 0, "owned_by": "offline"}
            ]})

        async def respond(request):
            body = await request.json()
            self.requests.append({
                "method": "POST", "path": request.path, "body": body,
                "synthetic_authorization": request.headers.get("Authorization") == "Bearer offline-synthetic-key",
            })
            index = len(self.posts())
            await self.response_ready(index)
            return web.json_response({
                "id": f"resp_offline_{index}", "object": "response", "created_at": 0,
                "status": "completed", "error": None, "incomplete_details": None,
                "model": MODEL, "instructions": None, "max_output_tokens": None,
                "output": [{"id": f"msg_offline_{index}", "type": "message",
                            "role": "assistant", "status": "completed", "content": [
                                {"type": "output_text", "text": ANSWER, "annotations": []}]}],
                "parallel_tool_calls": True, "previous_response_id": None,
                "reasoning": {"effort": None, "summary": None},
                "store": False, "temperature": 1.0, "text": {"format": {"type": "text"}},
                "tool_choice": "auto", "tools": [], "top_p": 1.0,
                "truncation": "disabled", "user": None, "metadata": {},
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
                          "input_tokens_details": {"cached_tokens": 0},
                          "output_tokens_details": {"reasoning_tokens": 0}},
            })

        async def unexpected(request):
            self.requests.append({"method": request.method, "path": request.path, "unexpected": True})
            return web.json_response({"error": {"message": "Unsupported offline endpoint"}}, status=400)

        application = web.Application()
        application.router.add_get("/v1/models", models)
        application.router.add_post("/v1/responses", respond)
        application.router.add_route("*", "/{path:.*}", unexpected)
        # Reserve and retain the exact socket: no close/rebind free-port race.
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(128)
        self.sock.setblocking(False)
        port = self.sock.getsockname()[1]
        self.runner = web.AppRunner(application, access_log=None)
        await self.runner.setup()
        await web.SockSite(self.runner, self.sock).start()
        base_url = f"http://127.0.0.1:{port}/v1"
        self.result["base_url"] = base_url
        return base_url

    async def response_ready(self, index):
        """Optional fixture gate; never replaces provider/runtime execution."""

    def posts(self):
        return [row for row in self.requests
                if row["method"] == "POST" and row["path"] == "/v1/responses"]

    def bundle(self, sources, provider, config, filename):
        import yaml

        def declaration(name, package, **extra):
            return {"module": name, "source": sources[package], **extra}

        plan = {
            "bundle": {"name": filename.removesuffix(".yaml"), "version": "1.0.0"},
            "session": {
                "orchestrator": declaration("loop-live", "amplifier_module_loop_live"),
                "context": declaration("context-simple", "amplifier_module_context_simple"),
            },
            "providers": [declaration(provider, "amplifier_module_" + provider.replace("-", "_"), config=config)],
            "tools": [],
            "hooks": [],
            "instruction": "Offline acceptance. Reply with PUBLIC-OFFLINE-ANSWER. Do not use tools.",
        }
        path = self.root / filename
        private_write(path, yaml.safe_dump(plan, sort_keys=False))
        return path

    def service(self, app, workspace):
        from amplifier_web.runtime import RuntimeManager
        from amplifier_web.service import AppService

        service = AppService(app, workspace=workspace)
        self.services.append(service)
        runtime = RuntimeManager(command=[sys.executable, "-I", "-X",
                                 f"pycache_prefix={self.root / 'worker-bytecode'}",
                                 "-m", "amplifier_web.runtime_worker"],
                                 app_bridge=service.app_bridge, startup_timeout=120)
        runtime.home = app
        service.runtime = runtime
        self.runtimes.append(runtime)
        return service, runtime

    def remember_processes(self, runtime):
        for row in runtime.workers.values():
            process = row["process"]
            if process not in self.processes:
                self.processes.append(process)

    async def start(self, service, runtime, sid):
        await runtime.start(service._session(sid), service.on_runtime_event)
        self.remember_processes(runtime)
        report = runtime.workers[sid]["ready"].result()
        inspected = await runtime.control(sid, "configuration.inspect", {})
        plan = inspected["plan"]
        declarations = [*plan["session"].values(), *plan.get("providers", []),
                        *plan.get("tools", []), *plan.get("hooks", [])]
        mounted_sources = {row["module"]: row.get("source") for row in declarations
                           if isinstance(row, dict) and "module" in row}
        self.result.setdefault("mounted_sources", {})[sid] = mounted_sources
        # configuration.inspect preserves the declared loop source. The host
        # resolver owns its installed implementation independently of that text.
        from amplifier_web.host.session import installed_loop_source
        installed_loop = installed_loop_source()
        self.result["host_installed_loop_source"] = installed_loop
        self.check(f"host_installed_loop_source_{sid}",
                   installed_loop == self.sources["amplifier_module_loop_live"])
        for name, package in (("context-simple", "amplifier_module_context_simple"),):
            self.check(f"real_mount_source_{name}_{sid}", mounted_sources.get(name) == self.sources[package])
        for name in ("provider-openai", "provider-openai-chatgpt"):
            if name in mounted_sources:
                package = "amplifier_module_" + name.replace("-", "_")
                self.check(f"real_mount_source_{name}_{sid}", mounted_sources[name] == self.sources[package])
        return report

    async def send(self, service, runtime, sid, identity, text):
        receipt = await service.dispatch("conversation.send", {"sessionId": sid, "text": text},
                                         command_id=identity, include_state=False)
        # dispatch waits for the real worker acknowledgement before returning.
        self.check(f"accepted_{identity}", receipt.get("accepted") and receipt.get("delivery") == "accepted")
        response = await service.wait_for_response(sid, input_id=identity, timeout=60)
        self.remember_processes(runtime)
        self.finished(service, sid, identity, response)
        saved = json.loads(service.db.execute(
            "SELECT receipt FROM commands WHERE id=?", (identity,)).fetchone()[0])
        self.check(f"worker_admitted_{identity}", saved.get("delivery") == "accepted")
        return response

    def finished(self, service, sid, identity, response):
        events = [event for event in service._session(sid).get("generations", [])
                  if event.get("event") == "generation.finished" and identity in event.get("input_ids", [])]
        self.check(f"identified_generation_{identity}", len(events) == 1
                   and events[0]["generation_id"] == response.get("generation_id")
                   and response.get("input_ids") == [identity] and bool(response.get("generation_id")))
        self.check(f"exact_public_answer_{identity}", response.get("text") == ANSWER)
        self.result.setdefault("generations", {})[identity] = response

    def instruction_request(self, index, version, mode=None):
        body = self.posts()[index]["body"]
        instructions = body.get("instructions")
        suffix = f"_{mode}" if mode else ""
        self.check(f"instruction_string_v{version}{suffix}", isinstance(instructions, str))
        for prefix in ("GLOBAL", "PROJECT", "ROOT"):
            self.check(f"fresh_once_{prefix}_v{version}{suffix}", instructions.count(f"{prefix}-v{version}") == 1)
            self.check(f"no_stale_{prefix}_v{version}{suffix}", f"{prefix}-v{3 - version}" not in instructions)

    def transcript(self, workspace, sid):
        from amplifier_web.session_files import sessions_dir
        from amplifier_foundation.session.history import SessionHistoryStore

        path = sessions_dir(workspace) / sid / "transcript.jsonl"
        self.check("native_transcript_owned", path.resolve().is_relative_to(self.root / "native"))
        history = SessionHistoryStore(path.parent, session_id=sid).load(include_events=False)
        self.check("canonical_read_clean", not history.diagnostics)
        return path, history.messages

    def canonical_input(self, rows, identity, text):
        matches = [row for row in rows if row.get("role") == "user"
                   and (row.get("metadata") or {}).get("amplifier_input", {}).get("id") == identity]
        self.check(f"canonical_once_{identity}", len(matches) == 1)
        row = matches[0]
        marker = row["metadata"]["amplifier_input"]
        self.check(f"canonical_user_{identity}", marker.get("version") == 1
                   and marker.get("kind") == "user" and marker.get("source") == "user")
        self.check(f"canonical_text_{identity}", row.get("content") == text)
        return marker

    async def capacity(self, service, sid, label, expected):
        receipt = await service.dispatch("capacity.read", {"sessionId": sid}, include_state=False)
        usage = receipt["result"]["usage"]
        calls = usage["receipts"]
        types = Counter(row.get("lifecycle") or "unknown" for row in calls)
        self.result["counts"][label] = {"capacity_calls": usage["calls"],
                                      "call_types": dict(types), "generation_posts": len(self.posts())}
        self.check(f"admitted_turn_calls_{label}", usage["calls"] == expected
                   and types == {"turn": expected}
                   and all(row.get("admittedAt") and row.get("endedAt") for row in calls))

    async def run(self):
        self.credentials_absent()
        app, workspace = self.configure_home()
        sources = self.installed_sources()
        self.sources = sources
        self.scope("preflight", "completed")
        if self.scenario == "chatgpt-mount":
            self.scope("chatgpt_mount", "in_progress")
            service, runtime = self.service(app, workspace)
            await self.chatgpt_mount(service, runtime, workspace, expected_posts=0)
            self.scope("request_validation", "in_progress")
            self.credentials_absent()
            self.check("chatgpt_no_http_requests_or_server", not self.requests and self.runner is None)
            self.scope("request_validation", "completed")
            self.stage("verified_before_cleanup")
            return
        self.scope("first_input", "in_progress")
        self.stage("loopback_server")
        base_url = await self.server()
        bundle = self.bundle(sources, "provider-openai", {
            "api_key": "offline-synthetic-key", "base_url": base_url, "default_model": MODEL,
            "use_streaming": False, "max_retries": 0, "timeout": 10,
        }, "offline-openai.yaml")
        service, runtime = self.service(app, workspace)
        sid = str(uuid.uuid4())
        self.result["session_id"] = sid
        self.stage("first_input")
        await service.dispatch("session.create", {"id": sid, "workspace": str(workspace),
                                                 "bundle": str(bundle)}, include_state=False)
        await service.dispatch("session.naming", {"id": sid, "automatic": False}, include_state=False)
        first_text = "First offline request."
        await self.send(service, runtime, sid, FIRST_ID, first_text)
        self.check("first_one_post", len(self.posts()) == 1)
        self.instruction_request(0, 1)
        await self.capacity(service, sid, "first", 1)
        path, rows = self.transcript(workspace, sid)
        self.canonical_input(rows, FIRST_ID, first_text)
        admitted = [row for row in rows if row.get("role") == "user"
                    and (row.get("metadata") or {}).get("amplifier_input", {}).get("kind") == "user"]
        self.check("first_one_admitted_user", len(admitted) == 1)
        report = await self.start(service, runtime, sid)
        self.check("real_openai_mounted", "openai" in report["providers"])
        self.scope("first_input", "completed")

        warm_process = runtime.workers[sid]["process"] if self.scenario == "warm-voice" else None
        if self.scenario == "full":
            self.scope("cold_resume", "in_progress")
            self.stage("cold_resume_no_input")
            process = runtime.workers[sid]["process"]
            await runtime.stop(sid)
            self.check("first_worker_stopped", process.returncode is not None)
            saved_bytes = path.read_bytes()
            self.result["transcript_sha256_before_resume"] = hashlib.sha256(saved_bytes).hexdigest()
            await service.close()
            service, runtime = self.service(app, workspace)
            report = await self.start(service, runtime, sid)
            self.check("cold_resume_report", report.get("resumed") is True)
            self.check("resume_no_generation_post", len(self.posts()) == 1)
            _, rows = self.transcript(workspace, sid)
            self.canonical_input(rows, FIRST_ID, first_text)
            self.scope("cold_resume", "completed")

        mode = "warm" if self.scenario == "warm-voice" else "resumed"
        self.result["second_input"] = {"id": SECOND_ID, "mode": mode}
        self.result["check_scopes"]["instruction_refresh"]["mode"] = mode
        self.scope("instruction_refresh", "in_progress")
        self.stage(f"{mode}_fresh_instructions")
        self.instructions(2)
        second_text = f"Adjacent {mode} offline request."
        await self.send(service, runtime, sid, SECOND_ID, second_text)
        self.check(f"{mode}_one_new_post", len(self.posts()) == 2)
        self.instruction_request(1, 2, "warm" if mode == "warm" else None)
        await self.capacity(service, sid, mode, 2)
        _, rows = self.transcript(workspace, sid)
        self.canonical_input(rows, FIRST_ID, first_text)
        self.canonical_input(rows, SECOND_ID, second_text)
        self.scope("instruction_refresh", "completed")

        self.scope("private_voice", "in_progress")
        self.stage("private_voice")
        await service.record_voice_transcript("user", SPEECH, voice_id=CALL_ID,
                                              item_id="offline_spoken_item", session_id=sid)
        receipt = await service.voice_delegate(WRAPPER, VOICE_ID, sid,
                                               call_id=CALL_ID, delegation_id=DELEGATION_ID)
        self.check("private_voice_accepted", receipt.get("accepted") and receipt.get("voicePresentation"))
        response = await service.wait_for_response(sid, input_id=VOICE_ID, timeout=60)
        self.finished(service, sid, VOICE_ID, response)
        self.check("voice_one_new_post", len(self.posts()) == 3)
        duplicate = await service.voice_delegate(WRAPPER, VOICE_ID, sid,
                                                 call_id=CALL_ID, delegation_id=DELEGATION_ID)
        self.check("duplicate_voice_no_post", duplicate.get("duplicate") and len(self.posts()) == 3)
        await self.capacity(service, sid, "voice", 3)
        process = runtime.workers[sid]["process"]
        if warm_process is not None:
            self.check("warm_inputs_and_voice_same_worker", process is warm_process and process.returncode is None)
        await runtime.stop(sid)
        self.check("voice_worker_stopped", process.returncode is not None)
        _, rows = self.transcript(workspace, sid)
        marker = self.canonical_input(rows, VOICE_ID, WRAPPER)
        self.check("canonical_voice_call_id", marker.get("call_id") == "unified.voice.v1:" + VOICE_ID)
        self.check("canonical_all_inputs_once", sum(
            row.get("role") == "user" and
            (row.get("metadata") or {}).get("amplifier_input", {}).get("kind") == "user"
            for row in rows) == 3)
        self.scope("private_voice", "completed")

        self.scope("read_only_projections", "in_progress")
        self.stage("read_only_projections")
        from amplifier_web.automatic_history import read_transcript
        from amplifier_web.conversation_export import messages, snapshot
        from amplifier_web.history_query import query_history
        from amplifier_web.shared_state_probe import query
        from amplifier_web.voice_messages import is_internal_voice_input

        self.check("voice_provenance_proven", sum(is_internal_voice_input(row) for row in rows) == 1)
        before = path.read_bytes()
        self.result["transcript_sha256_before_projections"] = hashlib.sha256(before).hexdigest()
        await service.history.refresh()
        session = service._session(sid)
        views = {
            "display": read_transcript(session, limit=None)["messages"],
            "export_messages": messages(app, session),
            "history_query": (await query_history(service, {
                "action": "read", "session_id": sid, "limit": 50, "text_limit": 4000,
            }, sid))["messages"],
            "probe": query({"version": 1, "op": "view", "workspace": str(workspace),
                            "sessionId": sid, "limit": 100})["messages"],
        }
        for label, values in views.items():
            self.check(f"wrapper_hidden_{label}", all(
                WRAPPER not in row.get("text", "") and "OFFLINE-PRIVATE-PARAPHRASE" not in row.get("text", "")
                for row in values))
            self.check(f"public_answer_retained_{label}", sum(row.get("text") == ANSWER for row in values) == 3)
        exported, _ = snapshot(app, service._session(sid), [])
        self.check("wrapper_hidden_export", "OFFLINE-PRIVATE-PARAPHRASE" not in exported)
        self.check("speech_exported_once", exported.count(SPEECH) == 1)
        for label in ("export_messages", "history_query"):
            self.check(f"speech_once_{label}", sum(row.get("text") == SPEECH for row in views[label]) == 1)
        speech = [row for row in service._session(sid)["messages"]
                  if row.get("voiceId") == CALL_ID and row.get("voiceItemId") == "offline_spoken_item"]
        self.check("recorded_speech_once", len(speech) == 1 and speech[0]["text"] == SPEECH)
        self.check("projection_transcript_hash_unchanged", path.read_bytes() == before)
        self.result["transcript_sha256_after_projections"] = hashlib.sha256(path.read_bytes()).hexdigest()
        _, rows = self.transcript(workspace, sid)
        self.canonical_input(rows, VOICE_ID, WRAPPER)
        self.scope("read_only_projections", "completed")

        if self.scenario == "full":
            await self.chatgpt_mount(service, runtime, workspace, expected_posts=3)
        self.scope("request_validation", "in_progress")
        self.credentials_absent()
        self.check("only_supported_loopback_requests", not any(row.get("unexpected") for row in self.requests))
        self.check("actual_openai_nonstreaming_requests", len(self.posts()) == 3 and all(
            row["synthetic_authorization"] and row["body"].get("model") == MODEL
            and row["body"].get("stream", False) is False for row in self.posts()))
        self.scope("request_validation", "completed")
        self.stage("verified_before_cleanup")

    async def chatgpt_mount(self, service, runtime, workspace, *, expected_posts):
        self.scope("chatgpt_mount", "in_progress")
        self.stage("chatgpt_synthetic_mount_no_input")
        tokens = self.root / "synthetic-chatgpt.json"
        private_write(tokens, json.dumps({
            "auth_mode": "oauth", "access_token": "offline-fake-access-token",
            "refresh_token": "offline-fake-refresh-token", "account_id": "offline-fake-account",
            "expires_at": "2099-01-01T00:00:00+00:00",
        }))
        original_tokens = tokens.read_bytes()
        chatgpt_bundle = self.bundle(self.sources, "provider-openai-chatgpt", {
            "token_file_path": str(tokens), "login_on_mount": False, "default_model": MODEL,
        }, "offline-chatgpt.yaml")
        chatgpt_sid = str(uuid.uuid4())
        self.result["chatgpt_session_id"] = chatgpt_sid
        await service.dispatch("session.create", {"id": chatgpt_sid, "workspace": str(workspace),
                                                 "bundle": str(chatgpt_bundle), "select": False},
                               include_state=False)
        await service.dispatch("session.naming", {"id": chatgpt_sid, "automatic": False}, include_state=False)
        self.check("chatgpt_automatic_naming_disabled", service._session(chatgpt_sid).get("autoName") is False)
        report = await self.start(service, runtime, chatgpt_sid)
        controls = await runtime.control(chatgpt_sid, "configuration.providers", {})
        self.check("real_chatgpt_mounted", "openai-chatgpt" in report["providers"] and any(
            row.get("module") == "provider-openai-chatgpt" for row in controls["providers"]))
        self.check("chatgpt_no_input_or_post", not service._session(chatgpt_sid).get("generations")
                   and len(self.posts()) == expected_posts)
        self.result["counts"]["chatgpt_mount"] = {
            "generation_posts": len(self.posts()),
            "generations": len(service._session(chatgpt_sid).get("generations") or []),
        }
        self.check("synthetic_tokens_unchanged", tokens.read_bytes() == original_tokens)
        self.scope("chatgpt_mount", "completed")

    async def cleanup(self):
        self.scope("cleanup", "in_progress")
        failures = []
        # Attempt every cleanup even when one closer fails; failure is not success.
        for runtime in self.runtimes:
            self.remember_processes(runtime)
        for objects in (self.services, self.runtimes):
            for obj in reversed(objects):
                try:
                    await obj.close()
                except BaseException as exc:
                    failures.append(type(exc).__name__)
        for process in self.processes:
            if process.returncode is None:
                failures.append("WorkerSurvivedClose")
                try:
                    process.kill()
                    await process.wait()
                except ProcessLookupError:
                    await process.wait()
                except BaseException as exc:
                    failures.append(type(exc).__name__)
        if self.runner is not None:
            try:
                await self.runner.cleanup()
            except BaseException as exc:
                failures.append(type(exc).__name__)
        if self.sock is not None:
            self.sock.close()
        (self.root / "pid").unlink(missing_ok=True)
        self.result["worker_pids"] = [process.pid for process in self.processes]
        self.result["counts"]["http"] = dict(Counter(
            row["method"] + " " + row["path"] for row in self.requests))
        self.result["checks"]["no_worker_subprocess_left"] = all(
            process.returncode is not None for process in self.processes)
        self.result["checks"]["cleanup_complete"] = not failures
        self.scope("cleanup", "failed" if failures else "completed")
        if failures:
            self.result["cleanup_errors"] = failures
            self.result["status"] = "failed"


async def execute(root, scenario="full"):
    harness = Harness(root, scenario)
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, task.cancel)
    try:
        await harness.run()
        harness.result["status"] = "passed"
    except BaseException as exc:
        harness.result["error_type"] = type(exc).__name__
        harness.result["status"] = "failed"
        try:
            harness.record_failure(exc)
        except BaseException as diagnostic_exc:
            harness.result["diagnostic_capture_error_type"] = type(diagnostic_exc).__name__
    finally:
        try:
            await harness.cleanup()
        except BaseException as exc:
            harness.result["cleanup_error_type"] = type(exc).__name__
            harness.scope("cleanup", "failed")
            harness.result["status"] = "failed"
            (root / "pid").unlink(missing_ok=True)
        private_write(root / "result.json", json.dumps(harness.result, indent=2, sort_keys=True) + "\n")
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
    print(json.dumps({"status": harness.result["status"], "scenario": harness.scenario,
                      "stage": harness.result["stage"],
                      "result": str(root / "result.json")}), flush=True)
    return 0 if harness.result["status"] == "passed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path, help="New, nonexistent artifact directory")
    parser.add_argument("--scenario", choices=("full", "warm-voice", "chatgpt-mount"), default="full",
                        help="Bounded acceptance scope; warm-voice skips cold resume, chatgpt-mount sends no input")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    try:
        root.mkdir(parents=True, exist_ok=False, mode=0o700)
    except FileExistsError:
        parser.error("--root must not exist; existing artifacts are never overwritten")
    private_write(root / "pid", str(os.getpid()) + "\n")
    print(json.dumps({"pid": os.getpid(), "root": str(root)}), flush=True)
    return asyncio.run(execute(root, args.scenario))


if __name__ == "__main__":
    raise SystemExit(main())