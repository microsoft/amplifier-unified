"""Durable app state and the shared UI/agent command surface."""
from __future__ import annotations

import asyncio
import copy
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
from weakref import WeakValueDictionary

from jsonschema import validate, ValidationError
import tinycss2
from .execution import ensure_turn, ingest as ingest_execution, finish as finish_execution, finish_background
from .updates import work_paused
from .managed_chats import LOCATION


def settle_stream(session):
    """Preserve interrupted display text without claiming a completed response."""
    text = session.pop('streaming', None)
    identity = session.pop('streamingId', None)
    if text:
        session.setdefault('messages', []).append({
            'id': str(uuid.uuid4()), 'role': 'assistant', 'text': '[Interrupted response]\n\n' + text, 'via': 'chat',
            'source': 'interrupted-stream', 'partial': True, 'createdAt': time.time(),
            **({'streamId': identity} if identity else {}),
        })


def string(limit=16000):
    return {"type": "string", "maxLength": limit}


def schema(properties=None, required=None):
    properties = properties or {}
    return {"type": "object", "properties": properties, "required": list(properties) if required is None else required, "additionalProperties": False}


ACTION_DEFINITIONS = {
    "runtime.dependencies": ("Inspect exact host and already-running session runtime paths, public artifact package versions, and optional rendering tools. Does not install dependencies, start a session, select a chat or prove rendering success.", schema({"sessionId": string(200)}, [])),
    "terminal.prepare": ("Prepare a private, short-lived terminal setup download for a configured service address. Installs on the computer where the user runs it; does not install on the server.", schema({"server": string(500), "platform": {"enum": ["macos-arm64", "linux-arm64"]}, "name": string(80)}, ["server", "platform", "name"])),
    "terminal.devices": ("List enrolled terminal connections without revealing credentials.", schema({})),
    "terminal.revoke": ("Remove one terminal connection's access; accepted work continues on the host.", schema({"id": string(100)}, ["id"])),
    "diagnostics.configure": ("Configure local Context Intelligence capture and explicitly enabled per-server stream routes. API keys are environment references. Changing a destination cancels its queued deliveries; already accepted or in-flight data cannot be recalled.", schema({"config":{"type":"object"}})),
    "diagnostics.test": ("Test saved destination authentication and write access by sending one synthetic probe; no conversation content.", schema({"id":string(100)})),
    "diagnostics.environment": ("Check that a credential environment variable exists in the service without revealing it.",schema({"name":string(200)})),
    "diagnostics.retry": ("Retry failed deliveries still authorized by this destination's current policy; never replay historical unselected data.",schema({"id":string(100)})),
    "diagnostics.records": ("Read retained diagnostics by session and stream glob, newest first. Continue using nextBefore. Metadata is the default; conversation text is captured only if explicitly enabled.",schema({"sessionId":string(200),"stream":string(100),"before":{"type":"integer","minimum":1},"limit":{"type":"integer","minimum":1,"maximum":100}},[])),
    "diagnostics.export": ("Download the explicitly inspected page of local Context Intelligence records as JSONL, including only those visible records.",schema()),
    "workspace.add": ("Register an existing workspace folder and use it for new chats", schema({"path":string(4000),"name":string(200)},["path"])),
    "workspace.create": ("Create or choose a workspace folder. Open an existing chat or a configurable draft; a new chat is saved on first submission.", schema({"path":{**string(4000),"minLength":1},"name":string(200)},["path"])),
    "workspace.select": ("Select the workspace used for new chats and canvas files", schema({"id":string(100)})),
    "workspace.rename": ("Rename a workspace registration", schema({"id":string(100),"name":string(200)})),
    "workspace.remove": ("Remove a workspace registration without deleting folders or chats", schema({"id":string(100)})),
    "canvas.show": ("Save a durable artifact in this chat and open a new canvas tab (browser kind takes an http/https url): sandboxed interactive HTML, Markdown with diagram fences, Mermaid, Graphviz DOT, structured data, text, code, image, auto-detected workspace file, Babylon.js 3D HTML (kind babylon, global BABYLON preloaded), or A2UI snapshot. Local HTML/Babylon files support up to 20 MB; large snapshots are loaded separately from contentResource. Browser previews may be blocked by mixed content or site embedding policies; inspect renderReports and offer canvas.openExternal. Surface supports Text, Row, Column, Card, Button and Divider only.", schema({"kind":{"enum":["auto","text","markdown","code","html","mermaid","dot","json","jsonl","image","a2ui","browser","babylon"]},"title":string(200),"content":string(7000000),"path":string(4000),"url":string(4000),"sessionId":string(200),"surface":{"type":"object"}},["kind"])),
    "canvas.view": ("Adjust shared canvas viewer controls", schema({"id":string(100),"patch":schema({"source":{"type":"boolean"},"help":{"type":"boolean"},"reload":{"type":"number","minimum":0},"zoom":{"type":"number","minimum":0.2,"maximum":4},"panX":{"type":"number","minimum":-10000,"maximum":10000},"panY":{"type":"number","minimum":-10000,"maximum":10000},"engine":{"enum":["dot","neato","fdp","sfdp","circo","twopi"]},"node":string(500),"query":string(500)}, [])})),
    "canvas.report": ("Report browser rendering success or failure for a canvas part; this is display evidence only", schema({"id":string(100),"part":string(100),"status":{"enum":["pending","ready","unverified","error"]},"message":string(2000)},["id","part","status"])),
    "canvas.snapshot": ("Report visible HTML preview text and standard controls as untrusted display data", schema({"id":string(100),"document":schema({"text":string(16000),"controls":{"type":"array","maxItems":100,"items":schema({"id":string(100),"tag":string(30),"type":string(30),"label":string(200),"value":string(4000),"disabled":{"type":"boolean"}},["id","tag","type","label","value","disabled"])}})})),
    "canvas.interact": ("Operate a standard HTML preview control from the current canvas.document snapshot. Never executes arbitrary JavaScript.", schema({"id":string(100),"controlId":string(100),"event":{"enum":["click","input"]},"value":string(4000)},["id","controlId","event"])),
    "canvas.copy": ("Copy the current canvas source to the browser clipboard", schema({"id":string(100)})),
    "canvas.download": ("Download the current canvas source", schema({"id":string(100)})),
    "canvas.openExternal": ("Open the active browser preview URL in a browser tab; popup permissions may apply", schema({"id":string(100)})),
    "canvas.select": ("Reopen a saved artifact by ID from canvasArtifacts", schema({"id":string(100)})),
    "canvas.visibility": ("Show or hide this client's retained Canvas viewer without discarding edits. Bind sessionId and canvasId from the current state; optionally address an attached clientId.", schema({"open":{"type":"boolean"},"sessionId":{"type":["string","null"]},"canvasId":{"type":["string","null"]},"clientId":string(200)},["open","sessionId","canvasId"])),
    "canvas.reopen": ("Show this chat's canvas and saved artifacts", schema()),
    "canvas.tabClose": ("Close a canvas tab; keep the artifact in chat history", schema({"id":string(100)})),
    "canvas.close": ("Close the canvas without losing its content", schema()),
    "canvas.event": ("Record an A2UI button interaction in shared agent-visible state", schema({"surfaceId":string(100),"componentId":string(100),"name":string(200),"value":{}},["surfaceId","componentId","name"])),
    "session.draft": ("Open a configurable new chat without creating a session or starting work. Edit view.newSessionDraft. At first submission, pass that setup to session.create with fromDraft:true, then conversation.send to its returned sessionId.", schema({"workspace": string(4000), "location": LOCATION}, [])),
    "session.create": ("Start a fresh conversation. location.kind managed allocates a private app-owned folder (not a security sandbox); workspace uses an existing or explicitly supplied new folder. Optional reviewed configuration inheritance does not copy history, tasks or running work; select:false preserves the current view.", schema({"id": string(100), "location": LOCATION, "title": string(200), "bundle": string(2000), "workspace": string(4000), "select": {"type": "boolean"}, "fromDraft": {"type": "boolean"}, "selection": {"type": "object", "properties": {"instance": string(200), "model": string(500), "effort": string(100)}, "additionalProperties": False}, "inheritConfiguration": schema({"sessionId": string(200), "configurationHash": string(100), "scheduledRunId": string(200)}, ["sessionId", "configurationHash"])}, [])),
    "session.select": ("Select a conversation", schema({"id": string(100)})),
    "session.warm": ("Prepare a conversation in the background without sending input or requesting takeover", schema({"id": string(200)})),
    "runtime.retention.update": ("Set this host's idle worker count, lifetime and background preparation policy", schema({"patch": {
        "type": "object", "properties": {
            "max_warm_workers": {"type": "integer", "minimum": 0},
            "idle_timeout_hours": {"type": "number", "minimum": 0},
            "prewarm_on_select": {"type": "boolean"}}, "additionalProperties": False}}, ["patch"])),
    "session.takeover": ("Explicitly request execution ownership here; the current owner saves and releases automatically.", schema({"id": string(200)})),
    "session.rename": ("Rename a conversation", schema({"id": string(100), "title": string(200)})),
    "session.naming": ("Enable or disable future automatic naming, or generate a name once without sending a chat turn. Regeneration preserves the Auto preference and rejects late results after a newer edit.", schema({"id": string(100), "automatic": {"type": "boolean"}, "regenerate": {"const": True}}, ["id"])),
    "session.pin": ("Pin or unpin a top-level chat in workspace and All chats lists. This app preference does not change shared conversation files.", schema({"id": {**string(200), "minLength": 1}, "pinned": {"type": "boolean"}}, ["id", "pinned"])),
    "session.deletePreview": ("Review permanent deletion of an idle managed chat and its owned files/history. Show the returned scope to the user before confirmation. Workspace chats can only be archived.", schema({"id": string(100)})),
    "session.delete": ("Permanently delete the managed chat reviewed by session.deletePreview, only after explicit user confirmation of that scope. Requires the unexpired confirmationToken; never infer permission from a preview. Active work and workspace chats refuse.", schema({"id": string(100), "confirmationToken": string(100)}, ["id", "confirmationToken"])),
    "session.export": ("Export a conversation. format=markdown freezes complete public history; destination=clipboard/download delivers to the connected browser, or none only creates a snapshot. Read result.statePath with state.get for exact Markdown in pages. The default JSON export is unchanged.", schema({"id": string(200), "format": {"enum": ["json", "markdown"]}, "destination": {"enum": ["download", "clipboard", "none"]}}, ["id"])),
    "session.exportResult": ("Report conversation export browser delivery; a download report means started, not proof of a saved file.", schema({"requestId": string(100), "status": {"enum": ["ready", "error"]}, "message": string(2000)}, ["requestId", "status"])),
    "session.inspect": ("Inspect conversation identity, status and recorded failure without running work.", schema({"id": string(200)}, ["id"])),
    "session.recover": ("Create an independent recovery copy with readable history, excluding old native tool/image payloads. Preserve the original and safety stops. Never start or replay work.", schema({"id": string(200)}, ["id"])),
    "session.fork": ("Fork conversation history through an optional user turn", schema({"id": string(100),"turn":{"type":"integer","minimum":1}},["id"])),
    "message.copy": ("Copy the entire message text as Markdown on the connected browser",schema({"sessionId":string(200),"messageId":string(200)})),
    "message.copyResult": ("Report clipboard success or failure",schema({"requestId":string(100),"status":{"enum":["ready","error"]},"message":string(2000)},["requestId","status"])),
    "message.edit": ("Edit a user message and regenerate in the current conversation (mode current), or fork a new conversation (mode fork, also the legacy default). Later active context is replaced; original events and external tool effects remain.",schema({"sessionId":string(200),"messageId":string(200),"text":string(100000),"mode":{"enum":["current","fork"]}},["sessionId","messageId","text"])),
    "conversation.send": ("Send to the main Amplifier session", schema({"sessionId":string(200),"text": string(100000), "preserveDraft":{"type":"boolean"}, "attachmentIds":{"type":"array","maxItems":8,"uniqueItems":True,"items":string(32)}, "via": {"enum": ["chat", "text", "call"]}}, ["text"])),
    "attachment.add": ("Attach a file or image to a conversation draft", schema({"sessionId":{"type":["string","null"],"maxLength":200},"name":string(200),"base64":string(12000000)},["name","base64"])),
    "attachment.remove": ("Remove an attachment from a conversation draft", schema({"sessionId":{"type":["string","null"],"maxLength":200},"id":string(32)},["id"])),
    "conversation.delivery": ("Check a saved input's delivery without sending or starting work. Missing evidence remains uncertain.", schema({"sessionId": string(200), "inputId": string(200)}, ["sessionId", "inputId"])),
    "conversation.retry": ("Explicitly resend an unconfirmed latest message, preserving its input identity and attachments. Unknown delivery requires confirmUncertain after the user accepts that prior effects might repeat. Never call as a passive check.", schema({"sessionId": string(200), "inputId": string(200), "confirmUncertain": {"type": "boolean"}}, ["sessionId", "inputId"])),
    "conversation.stop": ("Stop session execution", schema({"sessionId": string(200)}, [])),
    "worker.spawn": ("Start a worker lane for heavier work", schema({"sessionId": string(200), "instruction": string(100000), "bundle": string(2000)}, ["instruction"])),
    "worker.stop": ("Stop one worker lane", schema({"sessionId": string(200), "id": string(100)}, ["id"])),
    "worker.message": ("Send a follow-up to an active persistent worker", schema({"sessionId": string(200), "id": string(100), "text": string(100000)}, ["sessionId", "id", "text"])),
    "worker.steer": ("Send a correction to a worker", schema({"sessionId": string(200), "id": string(100), "text": string(100000)}, ["id", "text"])),
    "approval.respond": ("Respond to an Amplifier permission request", schema({"sessionId": string(200), "id": string(100), "decision": {"enum": ["allow", "deny", "approve", "reject"]}}, ["id", "decision"])),
    "attention.read": ("Mark reviewed attention items as read without resolving the underlying condition. Include fingerprints from /attention/items to avoid acknowledging newer results by mistake.", schema({"ids":{"type":"array","items":string(300),"maxItems":500},"fingerprints":{"type":"object","maxProperties":500,"additionalProperties":string(100)}},["ids"])),
    "view.update": ("Change panels, modality, draft, appearance or layout. Optional sessionId binds draft updates to that conversation without changing selection; null saves the attached client's draft before a conversation exists. Canvas: canvasWidth (300–16384 preferred pixels), canvasFocused (full frame), canvasControlsPinned/Expanded (booleans). Navigation: navWidth (216–16384 preferred pixels), navPinned/Expanded (booleans). Workspace explorer: navWorkspacePath browses folders from /workspaceExplorer without selecting a chat, navWorkspaceFilter searches paths or aliases with case-insensitive fnmatch or plain text, navWorkspacePage selects a 1-based page, navWorkspaceAncestorsOpen toggles the ancestor menu. Use workspace.select to select a workspace. Browser fits widths to the available space, preserving a 360px chat.", schema({"patch": {"type": "object"}, "sessionId": {"type": ["string", "null"], "minLength": 1, "maxLength": 200}}, ["patch"])),
    "providers.credentials": ("Check provider credential environment availability without revealing values",schema({"sessionId":string(200),"module":string(200),"envVar":string(200)},["module"])),
    "providers.reorder": ("Save complete provider preference order atomically; expectedIds must match the current order",schema({"ids":{"type":"array","uniqueItems":True,"maxItems":1000,"items":string(200)},"expectedIds":{"type":"array","items":string(200)},"scope":{"enum":["global","project","local"]},"sessionId":string(200)},["ids","expectedIds"])),
    "bundles.reorder": ("Save composition order of enabled app capabilities; excludes standalone aliases",schema({"ids":{"type":"array","uniqueItems":True,"maxItems":1000,"items":string(200)},"expectedIds":{"type":"array","items":string(200)}},["ids","expectedIds"])),
    "providers.move": ("Reorder saved provider connections",schema({"id":string(200),"beforeId":{"type":["string","null"]},"scope":{"enum":["global","project","local"]},"sessionId":string(200)},["id"])),
    "locations.create": ("Create a new folder inside the chosen existing parent. Does not create a workspace registration or conversation.",schema({"path":string(4000),"name":string(255),"controlId":string(200)},["path","name","controlId"])),
    "locations.list": ("Browse local folders and files for a location control",schema({"path":string(4000),"directoriesOnly":{"type":"boolean"},"controlId":string(200)},["controlId"])),
    "providers.schema": ("Read a provider module’s configuration fields and choices",schema({"module":string(200),"id":string(200),"sessionId":string(200)},["module"])),
    "configuration.defaults": ("Resolve new-chat bundle and model without creating a conversation",schema({"location": LOCATION,"workspace":string(4000),"bundle":string(4000)},[])),
    "providers.list": ("List provider connections and setup status without creating a conversation",schema({"location": LOCATION,"sessionId":string(200),"workspace":string(4000)},[])),
    "providers.save": ("Add or edit a provider connection",schema({"sessionId":string(200),"id":string(200),"module":string(200),"source":string(4000),"config":{"type":"object"},"apiKey":string(16000),"apiKeyEnv":string(200),"scope":{"enum":["global","project","local"]}},["module","config"])),
    "providers.remove": ("Remove a provider connection",schema({"sessionId":string(200),"id":string(200),"scope":{"enum":["global","project","local"]}},["id"])),
    "providers.test": ("Test a configured provider",schema({"id":string(200),"sessionId":string(200)},["id"])),
    "providers.models": ("Browse cached provider models; refresh only this provider when requested",schema({"location": LOCATION,"id":string(200),"sessionId":string(200),"workspace":string(4000),"refresh":{"type":"boolean"}},["id"])),
    "providers.login": ("Sign in to a provider",schema({"id":string(200),"sessionId":string(200)},["id"])),
    "providers.loginStatus": ("Check provider sign-in progress",schema({"id":string(200)},["id"])),
    "providers.loginCancel": ("Cancel provider sign-in",schema({"id":string(200)},["id"])),
    "routing.list": ("List model routing presets",schema()),
    "routing.show": ("Inspect a routing preset",schema({"name":string(200)})),
    "routing.use": ("Use a model routing preset",schema({"name":string(200),"scope":{"enum":["global","project","local"]}},["name"])),
    "routing.save": ("Save a custom routing preset",schema({"activate":{"type":"boolean"},"name":string(200),"matrix":{"type":"object"},"scope":{"enum":["global","project","local"]}},["name","matrix"])),
    "bundle.preview": ("Preview a replacement root bundle for an idle conversation", schema({"sessionId":string(200),"bundle":string(2000)},["sessionId","bundle"])),
    "bundle.switch": ("Switch an idle conversation; optional previewId checks a reviewed preview; preserve history and compatible model selection", schema({"sessionId":string(200),"bundle":string(2000),"previewId":string(100),"resetModel":{"type":"boolean"}},["sessionId","bundle"])),
    "bundle.fork": ("Fork history into a different root bundle; optional previewId checks a reviewed preview", schema({"sessionId":string(200),"bundle":string(2000),"previewId":string(100),"resetModel":{"type":"boolean"}},["sessionId","bundle"])),
    "bundle.default": ("Set or clear the default root for this app, workspace, or shared Amplifier settings", schema({"scope":{"enum":["app","workspace","shared"]},"bundle":{"type":["string","null"],"minLength":1,"maxLength":2000},"workspace":string(4000)},["scope","bundle"])),
    "bundle.discover": ("Browse bundles and behaviors in a Git repository", schema({"url":string(4000)})),
    "bundles.list": ("List app behaviors and standalone bundles",schema()),
    "bundles.add": ("Add a behavior or standalone bundle",schema({"uri":string(4000),"reviewId":string(32),"name":string(200),"role":{"enum":["behavior","standalone"]}},["uri","role"])),
    "bundles.toggle": ("Enable or disable an app behavior",schema({"id":string(200),"enabled":{"type":"boolean"}})),
    "bundles.remove": ("Remove a registered bundle",schema({"id":string(200)})),
    "bundles.move": ("Reorder app behaviors",schema({"id":string(200),"direction":{"enum":["up","down"]},"beforeId":{"type":["string","null"]}},["id"])),
    "bundle.export": ("Export the conversation's customized bundle",schema({"sessionId":string(200),"name":string(200),"description":string(1000)},["sessionId"])),
    "bundle.save": ("Save and use the conversation's customized bundle",schema({"sessionId":string(200),"name":string(200),"description":string(1000)},["sessionId","name"])),
    "configuration.cancel": ("Cancel queued mount-plan changes",schema({"id":string(200)})),
    "configuration.inspect": ("Inspect resolved modules and configuration",schema({"id":string(200)})),
    "configuration.apply": ("Apply edits to the loaded session mount plan, optionally waiting for idle",schema({"id":string(200),"config":{"type":"object"},"whenIdle":{"type":"boolean"}},["id","config"])),
    "runtime.control": ("Manage runtime modes, goals, provider, budgets, skills and tools",schema({"sessionId":string(200),"operation":string(100),"args":{"type":"object"}},["sessionId","operation"])),
    "modules.list": ("List scoped module configuration",schema({"scope":{"enum":["global","project","local"]}},[])),
    "modules.save": ("Add or override a scoped module",schema({"section":{"enum":["tools","hooks","providers","orchestrator","context"]},"module":string(200),"id":string(200),"source":string(4000),"config":{"type":"object"},"enabled":{"type":"boolean"},"scope":{"enum":["global","project","local"]}},["section","module"])),
    "modules.remove": ("Remove a scoped module override",schema({"section":string(100),"id":string(200),"scope":{"enum":["global","project","local"]}},["section","id"])),
    "modules.validate": ("Validate a module using its public Core contract",schema({"behavioral":{"type":"boolean"},"section":string(100),"id":string(200),"scope":{"enum":["global","project","local"]}},["section","id"])),
    "sources.list": ("List scoped source overrides",schema({"scope":{"enum":["global","project","local"]}},[])),
    "sources.validate": ("Validate a candidate module source without saving it",schema({"kind":{"enum":["module"]},"name":string(200),"source":string(4000),"section":{"enum":["tools","hooks","providers","orchestrator","context"]},"scope":{"enum":["global","project","local"]}},["kind","name","source","section"])),
    "sources.save": ("Set a scoped bundle or module source; validate=true validates the candidate first and saves only on success",schema({"kind":{"enum":["module","bundle"]},"name":string(200),"source":string(4000),"validate":{"type":"boolean"},"section":{"enum":["tools","hooks","providers","orchestrator","context"]},"scope":{"enum":["global","project","local"]}},["kind","name","source"])),
    "sources.remove": ("Remove a scoped source override",schema({"kind":{"enum":["module","bundle"]},"name":string(200),"scope":{"enum":["global","project","local"]}},["kind","name"])),
    "history.importFile": ("Import a transcript as an independent conversation",schema({"content":string(1000000),"format":{"enum":["json","jsonl"]},"title":string(200),"bundle":string(2000)},["content","format"])),
    "notifications.get": ("Inspect notification delivery preferences",schema()),
    "notifications.save": ("Configure browser and ntfy notifications",schema({"patch":{"type":"object"}})),
    "permissions.get": ("Inspect scoped file-access settings",schema({"sessionId":string(200),"scope":{"enum":["global","project","local"]}},[])),
    "permissions.save": ("Save scoped file-access settings",schema({"sessionId":string(200),"scope":{"enum":["global","project","local"]},"allowed":{"type":"array","items":string(4000)},"denied":{"type":"array","items":string(4000)}},["allowed","denied"])),
    "history.list": ("Browse persisted and optionally legacy sessions",schema({"legacy":{"type":"boolean"}},[])),
    "history.refresh": ("Refresh automatically discovered CLI workspaces and chats without mounting or replaying sessions", schema()),
    "session.history": ("Refresh saved chat text, or load earlier messages before an offset. This never starts an Amplifier runtime.", schema({"id":string(200),"before":{"type":"integer","minimum":0},"limit":{"type":"integer","minimum":1,"maximum":100}},["id"])),
    "history.shared.list": ("List common shared sessions for a workspace without mounting a worker",schema({"workspace":string(4000)},[])),
    "history.shared.view": ("View common shared session history without mounting a worker",schema({"id":string(200),"workspace":string(4000),"offset":{"type":"integer","minimum":0},"limit":{"type":"integer","minimum":1,"maximum":100}},["id","workspace"])),
    "history.shared.open": ("Open the same shared conversation without copying its runtime history",schema({"id":string(200),"workspace":string(4000)},["id","workspace"])),
    "history.import": ("Open a persisted or legacy session",schema({"id":string(200)})),
    "history.export": ("Export runtime transcript and metadata",schema({"sessionId":string(200),"format":{"enum":["json","jsonl"]}},["sessionId"])),
    "history.cleanup": ("Preview or clean old conversation entries",schema({"days":{"type":"integer","minimum":1},"apply":{"type":"boolean"},"purge":{"type":"boolean"}},[])),
    "maintenance.backup": ("Back up shared session files, app settings, artifacts and receipts",schema()),
    "maintenance.restoreResource": ("Restore missing saved content from an exact hash-matching JSON value without replaying tools",schema({"id":string(64),"value":{"type":"object"}},["id","value"])),
    "maintenance.reset": ("Preview or reset selected app data with a retained private backup",schema({"parts":{"type":"array","items":{"enum":["runtime","cache","settings","conversations"]}},"apply":{"type":"boolean"},"confirmation":string(20)},["parts"])),
    "maintenance.repair": ("Repair runtime dependency installation while idle",schema()),
    "updates.app": ("Stage a published application release and restart when idle",schema()),
    "updates.check": ("Check published application releases and ecosystem sources for updates", schema()),
    "updates.install": ("Stage and validate available application or ecosystem updates; activate when idle. Application updates restart the host.", schema()),
    "updates.rollback": ("Restore the previous ecosystem version when idle", schema()),
    "settings.update": ("Change voice or workspace defaults", schema({"patch": {"type": "object"}})),
    "theme.apply": ("Apply a complete single-file CSS skin", schema({"name": string(100), "css": string(1000000)})),
    "theme.reset": ("Restore the default skin", schema()),
    "theme.export": ("Export the applied skin", schema()),
    "state.export": ("Export app state and attached device views", schema()),
    "notification.request": ("Request notification permission on this device", schema()),
    "call.start": ("Start a realtime voice call on the connected browser", schema()),
    "call.mute": ("Mute or unmute the call microphone", schema({"muted": {"type": "boolean"}})),
    "call.end": ("End audio while leaving the work running", schema()),
}


from .operations import definitions as operation_definitions
ACTION_DEFINITIONS.update(operation_definitions())
from .computation import definitions as computation_definitions
ACTION_DEFINITIONS.update(computation_definitions())

from .capacity import definitions as capacity_definitions
ACTION_DEFINITIONS.update(capacity_definitions(schema, string))


class AppError(Exception):
    def __init__(self, message, status=400, *, code=None):
        super().__init__(message)
        self.status = status
        self.code = code


def validate_theme(css):
    """Reject network-bearing or malformed skins; assets must travel with the file."""
    if not css.strip():
        raise AppError("The skin is empty.")
    if "</style" in css.lower():
        raise AppError("A skin must contain CSS only.")
    rules = tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True)
    def visit(nodes):
        for node in nodes:
            if node.type == "error":
                raise AppError("The stylesheet contains invalid CSS: " + node.message)
            if node.type == "at-rule" and node.lower_at_keyword in {"import", "namespace", "document"}:
                raise AppError("Skins must be self-contained; external imports are unsupported.")
            if node.type == "url":
                check_url(node.value)
            if node.type == "function" and node.lower_name == "url":
                value = tinycss2.serialize(node.arguments).strip().strip('\"\'')
                check_url(value)
            for attr in ("prelude", "content", "arguments"):
                child = getattr(node, attr, None)
                if child:
                    visit(child)
    def check_url(value):
        if not value.startswith(("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,", "data:font/woff;base64,", "data:font/woff2;base64,")):
            raise AppError("Embed images and fonts in the skin instead of loading external URLs.")
    visit(rules)


from .smart_canvas import definitions as smart_tool_definitions
ACTION_DEFINITIONS.update(smart_tool_definitions(schema, string))
from .conversation_library import definitions as library_definitions
LIBRARY_ACTIONS = library_definitions(schema, string)
ACTION_DEFINITIONS.update(LIBRARY_ACTIONS)
from .recall import definitions as recall_definitions
ACTION_DEFINITIONS.update(recall_definitions(schema, string))
from .outputs import definitions as output_definitions
ACTION_DEFINITIONS.update(output_definitions(schema, string))
from .feedback import definitions as feedback_definitions
ACTION_DEFINITIONS.update(feedback_definitions(schema, string))


from .shell_modules import ShellModules, definitions as shell_definitions
ACTION_DEFINITIONS.update(shell_definitions(schema, string))
from .canvas_views import CanvasViews, definitions as canvas_view_definitions
ACTION_DEFINITIONS.update(canvas_view_definitions(schema, string))
from .canvas_apps import definitions as canvas_app_definitions, THEME_TOKENS
ACTION_DEFINITIONS.update(canvas_app_definitions(schema, string))
from .questions import definitions as question_definitions
ACTION_DEFINITIONS.update(question_definitions(schema, string))
from .task_continuity import definitions as task_definitions
ACTION_DEFINITIONS.update(task_definitions(schema, string))
from .coordination import definitions as coordination_definitions
ACTION_DEFINITIONS.update(coordination_definitions())
from .schedules import definitions as schedule_definitions
ACTION_DEFINITIONS.update(schedule_definitions(schema, string))
from .voice_visual import VoiceVisual, definitions as visual_definitions
ACTION_DEFINITIONS.update(visual_definitions(schema, string))
from .worktrees import definitions as worktree_definitions
ACTION_DEFINITIONS.update(worktree_definitions(schema, string))
ACTION_DEFINITIONS['theme.preview'] = ('Preview a validated skin on an attached client.', schema({'name': string(100), 'css': string(1000000), 'clientId': string(100)}, ['name', 'css']))
ACTION_DEFINITIONS['theme.revert'] = ('End a preview or undo this client’s last applied skin if it is still current.', schema({'clientId': string(100)}, []))
for theme_action in ('theme.apply', 'theme.preview'):
    ACTION_DEFINITIONS[theme_action] = (("Apply a complete theme definition or CSS skin, or patch the current palette." if theme_action == 'theme.apply' else "Preview a complete theme definition or CSS skin, or a palette patch, on an attached client."), ACTION_DEFINITIONS[theme_action][1])
    theme_spec = ACTION_DEFINITIONS[theme_action][1]
    theme_spec['properties']['tokens'] = {'type': 'object', 'minProperties': 1, 'additionalProperties': False,
        'properties': {key: {'type': 'string', 'pattern': '^#[0-9a-fA-F]{6}$'} for key in THEME_TOKENS}}
    from .themes import DEFINITION
    theme_spec['properties']['definition'] = DEFINITION
    theme_spec['required'] = ['name']
    theme_spec['oneOf'] = [{'required': [key], 'not': {'anyOf': [{'required': [other]} for other in ('css', 'tokens', 'definition') if other != key]}}
                           for key in ('css', 'tokens', 'definition')]




class AppService:
    @property
    def state(self):
        clients = getattr(self, 'clients', None)
        return clients.state(self._state) if clients else self._state

    @state.setter
    def state(self, value):
        self._state = value

    def __init__(self, data_dir: Path, runtime=None, workspace=None):
        self.data_dir = Path(data_dir).expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.data_dir / "app.sqlite3")
        (self.data_dir / "app.sqlite3").chmod(0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, receipt TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS state_resources (id TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.db.commit()
        self.shell = ShellModules(self)
        self.canvas_views = CanvasViews(self)
        from .surface_context import SurfaceContext
        self.surface_context = SurfaceContext(self)
        self.default_workspace = str(Path(workspace or os.getcwd()).resolve())
        self.runtime = runtime
        self.voice_service = None
        self.voice_visual = VoiceVisual(self)
        self.update_manager = None
        self.management = None
        self.smart_tools = None
        self.smart_canvas = None
        self.queues = set()
        self.queue_clients = {}
        self.queue_sessions = {}
        self.instance_id = str(uuid.uuid4())
        self.tasks = set()
        self.smart_tool_tasks = set()
        self.smart_tool_requests = {}
        self.lock = asyncio.Lock()
        self._creation_locks = WeakValueDictionary()
        # RuntimeManager.close() is terminal.  Every host-continuing operation
        # that retires it therefore shares this exclusion and installs a fresh
        # manager before reopening normal work.
        self.runtime_lifecycle_lock = asyncio.Lock()
        self._runtime_lifecycle_tasks = set()
        self._runtime_alias = None
        self.closed = False
        from .session_warmup import SessionWarmup
        self.warmup = SessionWarmup(self)
        row = self.db.execute("SELECT value FROM state WHERE id=1").fetchone()
        self.state = json.loads(row[0]) if row else {
            "schemaVersion": 1, "revision": 0, "sessions": [], "selectedSessionId": None,
            "settings": {"preferredVoice": "gpt-live-1", "fallbackVoice": "gpt-realtime-2.1", "bundle": "work", "workspace": self.default_workspace},
            "theme": {"name": "Amplifier Unified", "css": self.default_theme()},
            "view": {"mode": "chat", "panel": None, "draft": "", "scheme": "system", "layout": "balanced"},
            "voice": {"status": "disconnected"}, "runtime": {"available": runtime is not None}, "devices": {}, "events": [],
        }
        from .managed_deletion import recover as recover_managed_deletions
        recover_managed_deletions(self.data_dir, self.db, self.state)
        from .managed_deletion import tombstones
        self._deleted_session_ids = {sid for row in tombstones(self.db) for sid in row['ids']}
        from .default_typography import upgrade_default
        upgrade_default(self.state, self.default_theme())
        from .settings_migration import migrate_settings
        migrate_settings(self.data_dir, {self.default_workspace, *[s["workspace"] for s in self.state["sessions"] if not s.get("historyManaged") and s.get("workspace")]})
        if row and not self.state.get("sharedVoiceMigration"):
            from .preferences import SettingsStore
            def migrate_voice(settings):
                voice = settings.setdefault("voice", {})
                for old, key in (("preferredVoice", "preferred_model"), ("fallbackVoice", "fallback_model")):
                    if self.state["settings"].get(old):
                        voice.setdefault(key, self.state["settings"][old])
            SettingsStore(self.data_dir).update(self.default_workspace, "global", migrate_voice)
        self.state["sharedVoiceMigration"] = True
        self._view_cache = {}
        from .session_projection import hydrate
        hydrate(self.data_dir, self.state, self.db)
        from .storage_migration import upgrade
        upgrade(self)
        from .chat_navigation import initialize as initialize_chat_navigation
        initialize_chat_navigation(self.state)
        from .conversation_library import ConversationLibrary
        self.conversation_library = ConversationLibrary(self)
        from .recall import Recall
        self.recall = Recall(self)
        self.state["voice"] = {"status": "disconnected"}
        self.state["runtime"] = {"available": runtime is not None, "description": "Isolated Amplifier sessions; runtime is prepared on first use."}
        from .session_ownership import restore
        for session in self.state["sessions"]:
            from .capacity import restore_observation
            restore_observation(session)
            restore(session)
            settle_stream(session)
            session["configurationBusy"]=False
            if session.get("bundleChange", {}).get("phase") == "working":
                session["bundleChange"] = {"phase":"error", "error":"The app restarted during a bundle change. Load the conversation and preview again; work was not replayed."}
            session.pop("bundlePreview", None)
            if session["status"] in {"working", "starting", "ready", "stopping"}:
                session["status"] = "interrupted"
                session["activity"] = {"phase": "interrupted", "label": "Previous work was interrupted; it has not been replayed.", "activeTools": [], "updatedAt": time.time()}
            for worker in session.get("workers", []):
                if worker.get("status") in {"working", "running", "starting", "queued", "pending", "stopping"} or worker.get("persistent") and worker.get("status") == "idle":
                    worker["status"] = "interrupted"
        if not self.state.get("defaultBundleMigration"):
            if self.state["settings"].get("bundle") == "foundation":
                self.state["settings"]["bundle"] = "anchors"
            self.state["defaultBundleMigration"] = True
        self.state["settings"].setdefault("updates", {"autoCheck": True, "autoInstall": False, "intervalHours": 24})
        self.state["devices"] = {}
        from .workspace_canvas import initialize
        initialize(self.state)
        from .canvas_library import recover_legacy
        recover_legacy(self.state,self.db,self.data_dir)
        from .naming import automatic,refresh
        for session in self.state['sessions']:
            if session.get('naming', {}).get('status') == 'working':
                session['naming'] = {'status': 'error', 'error': 'Name generation was interrupted. You can try again.'}
            session.setdefault('titleSource','automatic' if automatic(session) else 'manual')
            try:
                refresh(self.data_dir,session,migrate=True)
            except (OSError, ValueError):
                # A damaged session must not prevent the entire app starting.
                # Native history diagnostics handle repair; never replace it
                # with a stale display title during startup.
                import logging
                logging.getLogger(__name__).warning("Could not read a saved session name; original metadata retained.")
        from .feedback import Feedback
        self.feedback = Feedback(self)
        from .diagnostics import Diagnostics
        self.diagnostics = Diagnostics(self)
        for session in self.state['sessions']:
            for message in session.get('messages', []):
                if message.get('delivery', {}).get('status') == 'sending':
                    self._delivery(session, message.get('inputId'), 'unknown')
        # A durable request receipt is not an acknowledgement from a retired
        # worker. Preserve uncertainty and never replay its message on restart.
        for identity, saved_receipt in self.db.execute("SELECT id,receipt FROM commands WHERE json_extract(receipt,'$.commandAction')='worker.message' AND json_extract(receipt,'$.delivery')='requested'").fetchall():
            receipt = json.loads(saved_receipt)
            receipt["delivery"] = "unknown"
            self.db.execute("UPDATE commands SET receipt=? WHERE id=?", (json.dumps(receipt), identity))
        from .history_revision import recover_views
        recover_views(self)
        from .automatic_history import AutomaticHistory
        self.history = AutomaticHistory(self)
        from .outputs import Outputs
        self.outputs = Outputs(self)
        from .event_log_view import EventLogView
        self.event_log_view = EventLogView(self)
        from .client_views import ClientViews
        self.clients = ClientViews(self)
        self._client_snapshots = {}
        from .questions import Questions
        self.questions = Questions(self)
        from .coordination import Coordination
        self.coordination = Coordination(self)
        from .schedules import Schedules
        self.schedules = Schedules(self)
        from .worktrees import Worktrees
        self.worktrees = Worktrees(self)
        self._refresh_shared_preferences()
        from .operations import Operations
        self.operations = Operations(self)
        self._save()

    def default_theme(self):
        for name in ("unified.amplifier.css", "default-theme.css"):
            path = Path(__file__).parent / "static" / name
            if path.exists():
                return path.read_text()
        return "/* Amplifier Unified uses the app's bundled default styling. */"

    def _refresh_shared_preferences(self):
        from .shared_settings import read_settings, settings_paths
        selected = next((row for row in self.state.get("workspaces", []) if row["id"] == self.state.get("selectedWorkspaceId")), {})
        workspace = selected.get("path") or self.state["settings"]["workspace"]
        paths = settings_paths(workspace)
        def stamp(path):
            try:
                st = path.stat()
                return (str(path), st.st_mtime_ns, st.st_size, st.st_ino)
            except (FileNotFoundError, NotADirectoryError):
                return (str(path), None)
        stamp_value = (self.state["settings"].get("appBundle"), *tuple(stamp(path) for path in paths.values()))
        if getattr(self, "_shared_preferences_stamp", None) == stamp_value:
            return
        settings = read_settings(workspace)
        voice = settings.get("voice", {})
        from .bundle_selection import defaults
        bundle_defaults = defaults(self.data_dir, workspace, self.state["settings"].get("appBundle"))
        self.state["bundleDefaults"] = bundle_defaults
        self.state["settings"].update(
            bundle=bundle_defaults["effective"],
            preferredVoice=voice.get("preferred_model", "gpt-live-1"),
            fallbackVoice=voice.get("fallback_model", "gpt-realtime-2.1"))
        self._shared_preferences_stamp = stamp_value
        self._browser_snapshot = None
        # A different client can select a different workspace. Its preferences
        # do not change shared navigation or invalidate other clients' snapshots.

    @property
    def projections(self):
        from .state_projections import StateProjections
        if getattr(self, '_projections', None) is None:
            self._projections = StateProjections()
        return self._projections

    def state_context(self):
        """Read-only full catalog with derived, bounded navigation projections."""
        from .attention import snapshot
        from .workspace_navigation import snapshot as workspace_snapshot
        from .browser_state import navigation
        self._refresh_shared_preferences()
        result = dict(self.state)
        result["attention"] = snapshot(self.state)
        result.update(navigation(result))
        result["workspaceExplorer"] = workspace_snapshot(result)
        result.pop("attentionRead", None)
        return self.clients.project(result)

    def get_state(self):
        # Explicit full reads remain compatible; browser hot paths use pages.
        return copy.deepcopy(self.state_context())

    def browser_state(self, session_id=None):
        self._refresh_shared_preferences()
        client_id = self.clients.current.get()
        if client_id is not None:
            self.clients.reconcile(client_id)
            cached = self._client_snapshots.get(client_id)
            if (cached is None or cached['revision'] != self.state['revision'] or session_id is not None
                    or self._client_snapshot_preferences.get(client_id) != self._shared_preferences_stamp):
                from .browser_state import snapshot
                derived = self.projections.browser(self.state)
                if session_id is not None:
                    self._session(session_id)
                cached = self.clients.project(snapshot(self.state, derived, session_id=session_id, index=self.projections.sessions(self.state)))
                cached['shellDataKey'] = self.projections.shell_key(self.state)
                cached['shellChangeToken'] = self.shell.change_token(client_id)
                if session_id is None:
                    self._client_snapshots[client_id] = cached
                    self._client_snapshot_preferences[client_id] = self._shared_preferences_stamp
            return cached
        cached = getattr(self, '_browser_snapshot', None)
        if cached is None or cached['revision'] != self.state['revision']:
            from .browser_state import snapshot
            derived = self.projections.browser(self.state)
            self._browser_snapshot = snapshot(self.state, derived, index=self.projections.sessions(self.state))
            self._browser_snapshot['shellDataKey'] = self.projections.shell_key(self.state)
        if session_id is not None:
            self._session(session_id)
            from .browser_state import snapshot
            derived = {key: self._browser_snapshot[key] for key in ('attention', 'workspaceExplorer',
                'chatNavigation', 'headerChatNavigation', 'subagentNavigation')}
            return snapshot(self.state, derived, session_id=session_id, index=self.projections.sessions(self.state))
        return self._browser_snapshot

    def session_state(self, session_id):
        """Single-session transport projection, independent of browser navigation."""
        index = self.projections.sessions(self.state)
        row = index.by_id.get(session_id)
        if row is None:
            return {'revision': self.state['revision'], 'sessions': []}
        session = copy.deepcopy(row)
        if session_id == self.state.get('selectedSessionId'):
            session['subagentCount'] = len(index.children(row))
        session['workers'] = [{key: value for key, value in worker.items() if key != 'reportReceipts'}
                              for worker in session.get('workers', [])]
        record = self.clients.record()
        if record is not None:
            session['draft'] = record.get('drafts', {}).get(session_id, '')
            session['draftAttachments'] = copy.deepcopy(record.get('attachments', {}).get(session_id, []))
        return {'revision': self.state['revision'], 'sessions': [session]}

    def get_actions(self):
        return [{"name": name, "description": desc, "inputSchema": copy.deepcopy(spec)} for name, (desc, spec) in ACTION_DEFINITIONS.items()]

    def _save(self):
        self.questions.sync()
        self.schedules.sync()
        self.worktrees.sync()
        from .canvas_apps import sync
        sync(self)
        self._browser_snapshot = None
        self._client_snapshots.clear()
        self._client_snapshot_preferences = {}
        self._projections = None
        from .state_storage import normalize_state
        normalize_state(self.state, self.db)
        from .session_projection import persist
        saved = persist(self.data_dir, self._state, self._view_cache)
        self.clients.save()
        self.db.execute("INSERT OR REPLACE INTO state VALUES (1,?)", (json.dumps(saved),))
        self.db.commit()
        from .storage_migration import maintenance
        maintenance(self)

    def _publish(self):
        task = getattr(self, '_progress_publish_task', None)
        if task and task is not asyncio.current_task():
            task.cancel()
            self._progress_publish_task = None
        previous = self.state["revision"]
        self.state["revision"] = previous + 1
        try:
            self._save()
        except Exception:
            self.state["revision"] = previous
            self._browser_snapshot = None
            raise
        published = {}
        for queue in self.queues:
            key = (self.queue_clients.get(queue), self.queue_sessions.get(queue))
            if key not in published:
                with self.clients.bind(key[0]):
                    published[key] = self.session_state(key[1]) if key[1] is not None else self.browser_state()
            snapshot = published[key]
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(snapshot)
        if hasattr(self, "coordination"):
            self.coordination.notify()
        self._progress_dirty = False
        self._progress_publish_error = None

    def _publish_progress(self):
        """Batch stream/progress updates; final responses and approvals flush now."""
        self._progress_dirty = True
        if getattr(self, '_progress_publish_task', None) is None:
            self._progress_publish_task = self._task(self._flush_progress())

    def _publish_smart_tool_update(self, *, defer_publish=False):
        """Commit tool receipts before effects/results, batching only the UI snapshot."""
        if defer_publish:
            self.db.commit()
            self._publish_progress()
        else:
            self._publish()

    async def _flush_progress(self):
        try:
            await asyncio.sleep(.25)
            async with self.lock:
                if self._progress_dirty and not self.closed:
                    try:
                        self._publish()
                    except Exception as exc:
                        # Retain dirty data for the next transition or shutdown.
                        self._progress_publish_error = str(exc)
        finally:
            if self._progress_publish_task is asyncio.current_task():
                self._progress_publish_task = None

    async def _flush_pending_progress(self):
        """Publish pending runtime mutations before revision-sensitive access.

        A coalesced update already changed internal state. Externally observed
        reads and compare-and-set commands must first give it a durable revision.
        Internal synchronous readers remain available to code owning the state.
        """
        task = getattr(self, '_progress_publish_task', None)
        if task and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if self._progress_publish_task is task:
                self._progress_publish_task = None
        async with self.lock:
            if getattr(self, '_progress_dirty', False):
                self._publish()
                return True
        return False

    def subscribe(self, session_id=None):
        queue = asyncio.Queue(maxsize=4)
        self.queues.add(queue)
        self.queue_clients[queue] = self.clients.current.get()
        self.queue_sessions[queue] = session_id
        return queue

    def unsubscribe(self, queue):
        self.queues.discard(queue)
        self.queue_clients.pop(queue, None)
        self.queue_sessions.pop(queue, None)

    def _session(self, sid=None):
        sid = sid or self.state["selectedSessionId"]
        for session in self.state["sessions"]:
            if session["id"] == sid:
                if session.get("_deleting"):
                    raise AppError("This chat is being deleted. No new work was started.", 409)
                return session
        raise AppError("Select or create a conversation first.", 404)

    def _new_session(self, args):
        from .shared_settings import read_settings
        selected = next((w for w in self.state.get('workspaces', []) if w['id'] == self.state.get('selectedWorkspaceId')), {})
        if not args.get('workspace') and selected.get('available') is False:
            raise AppError('This project folder is unavailable. Choose an existing workspace to start work.')
        workspace = str(Path(args.get("workspace") or selected.get("path") or self.state["settings"]["workspace"]).expanduser().resolve())
        if not Path(workspace).is_dir():
            raise AppError("The workspace folder does not exist.")
        from .bundle_selection import defaults
        selected_bundle = defaults(self.data_dir, workspace, self.state["settings"].get("appBundle"))["effective"]
        from .new_chat import selection
        try:
            chosen = selection(args.get('selection', {}))
        except ValueError as exc:
            raise AppError(str(exc)) from None
        now = time.time()
        return {**({'selection': chosen} if chosen else {}), **({'location': {'kind': 'managed'}} if args.get('location', {}).get('kind') == 'managed' else {}), "id": str(uuid.uuid4()), "title": args.get("title") or "New chat", "titleSource":"manual" if args.get("title") and args["title"] not in {"New chat","New conversation","A new conversation","Untitled conversation"} else "automatic", "bundle": args.get("bundle") or selected_bundle, "workspace": workspace, "status": "idle", "createdAt": now, "recentActivityAt": now, "messages": [], "workers": [], "approvals": []}

    def _message(self, session, role, text, via="chat", **extra):
        message = {"id": str(uuid.uuid4()), "role": role, "text": text, "via": via, "createdAt": time.time(), **extra}
        session["messages"].append(message)
        from .chat_navigation import touch
        touch(session)
        self.diagnostics.record('conversation',{'event':'prompt:submit' if role=='user' else 'prompt:complete','data':{'id':message['id'],'role':role,'prompt' if role=='user' else 'response':text,'inputId':extra.get('inputId')}},session_id=session.get('runtimeSessionId') or session['id'],workspace=session['workspace'])
        return message

    def _activity(self, session, phase, label, *, reset=False):
        now = time.time()
        previous = session.get("activity", {})
        session["activity"] = {"phase": phase, "label": label,
            "startedAt": now if reset else previous.get("startedAt", now), "updatedAt": now,
            "activeTools": previous.get("activeTools", []), "lastEvent": previous.get("lastEvent")}
        return session["activity"]

    def _task(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    def bind_runtime_alias(self, setter):
        """Keep an embedding host's runtime reference aligned with this service."""
        self._runtime_alias = setter
        setter(self.runtime)

    @asynccontextmanager
    async def runtime_lifecycle(self):
        """Serialize and track a runtime-retiring operation during host shutdown."""
        if self.closed:
            raise RuntimeError("The runtime host is closing.")
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("Runtime lifecycle work requires an asyncio task.")
        self._runtime_lifecycle_tasks.add(task)
        try:
            async with self.runtime_lifecycle_lock:
                # A task can begin after close() has taken its lifecycle-task
                # snapshot, or wait here while shutdown begins.  Reject it after
                # it owns the exclusion, before it can mutate state or run work.
                if self.closed:
                    raise RuntimeError("The runtime host is closing.")
                yield
        finally:
            self._runtime_lifecycle_tasks.discard(task)

    def runtime_candidate(self, *, retention=None):
        """Construct a replacement without mutating or retiring the live runtime."""
        from .runtime import RuntimeManager

        current = self.runtime
        if isinstance(current, RuntimeManager):
            return RuntimeManager(
                app_bridge=current.app_bridge,
                command=current.command,
                startup_timeout=current.startup_timeout,
                progress_interval=current.progress_interval,
                retention=retention if retention is not None else dict(current.retention.settings),
            )
        from .deployment import load_server_config
        config = getattr(self, "server_config", None) or load_server_config(self.data_dir)
        return RuntimeManager(app_bridge=self.app_bridge,
                              retention=retention if retention is not None else config["runtime"])

    async def install_runtime(self, runtime):
        """Publish a prepared runtime consistently to the service and embedding host."""
        if self.closed:
            if runtime:
                await runtime.close()
            raise RuntimeError("The runtime host is closing.")
        self.runtime = runtime
        self.worktrees.bind_runtime()
        self.state["runtime"]["available"] = runtime is not None
        if hasattr(runtime, "retention"):
            self.state["runtime"]["retention"] = dict(runtime.retention.settings)
        if self._runtime_alias:
            self._runtime_alias(runtime)

    async def replace_runtime(self, candidate):
        """Switch to a preflighted runtime before terminally closing the old one."""
        if self.closed:
            await candidate.close()
            raise RuntimeError("The runtime host is closing.")
        previous = self.runtime
        await self.install_runtime(candidate)
        if previous:
            close_task = asyncio.create_task(previous.close())
            try:
                await asyncio.shield(close_task)
            except asyncio.CancelledError:
                # Shutdown cancels the lifecycle operation, but the displaced
                # runtime must finish its own cleanup before the new one closes.
                await asyncio.gather(close_task, return_exceptions=True)
                raise
        return previous

    async def discard_runtime(self, candidate):
        """Close an unused replacement candidate without retiring the live runtime."""
        if candidate is not None and candidate is not self.runtime and not getattr(candidate, "_closed", False):
            await candidate.close()

    async def dispatch(self, action, args=None, origin="ui", command_id=None, expected_revision=None, *, include_state=True, caller_session_id=None):
        # Directory preparation yields outside the service lock. Keep concurrent
        # retries on the same creation command behind its durable receipt, so a
        # conflicting retry cannot create a second folder during that interval.
        if action == 'session.create' and command_id:
            lock = self._creation_locks.setdefault(command_id, asyncio.Lock())
            async with lock:
                return await self._dispatch(action, args, origin, command_id, expected_revision, include_state=include_state, caller_session_id=caller_session_id)
        return await self._dispatch(action, args, origin, command_id, expected_revision, include_state=include_state, caller_session_id=caller_session_id)

    async def _dispatch(self, action, args=None, origin="ui", command_id=None, expected_revision=None, *, include_state=True, caller_session_id=None):
        args = dict(args or {})
        # Keep older shells/agents on the same non-committing launcher.
        if action == 'view.update' and args.get('patch', {}).get('panel') == 'new-session':
            action, args = 'session.draft', {}
        client_id = self.clients.current.get()
        if client_id is not None and action in {'conversation.send', 'conversation.stop', 'worker.spawn', 'worker.stop', 'worker.steer', 'worker.message', 'approval.respond', 'attachment.add', 'attachment.remove'}:
            args.setdefault('sessionId', self.state.get('selectedSessionId'))
            if not args['sessionId'] and action not in {'attachment.add', 'attachment.remove'}:
                raise AppError('Select a chat first.', 404)
        if action == 'runtime.control' and args.get('operation', '').startswith(('kernels.', 'operations.')):
            raise AppError('Use the shared computation and operation actions; their ownership and dependency checks cannot be bypassed.', 403)
        if action == 'runtime.control' and args.get('operation') == 'tool.invoke':
            if origin == 'agent' and (not caller_session_id or args.get('sessionId') != caller_session_id):
                raise AppError('Tool invocation must target the calling conversation.', 409)
            from .tool_authority import require_generic_tool
            invocation = dict(args.get('args') or {})
            try:
                require_generic_tool(invocation.get('name'), invocation.get('arguments', {}))
            except ValueError as exc:
                raise AppError(str(exc), 403) from None
            # Actor is host provenance, never a claim from the request body.
            args['args'] = {**invocation, 'actor': origin}
        if action == 'runtime.control' and args.get('operation', '').startswith('schedule.'):
            raise AppError('Use the shared schedule actions; direct scheduled input admission is internal.', 403)
        if action == 'runtime.control' and args.get('operation', '').startswith(('task.', 'capacity.')):
            action, args = args['operation'], {**args.get('args', {}), 'sessionId': args.get('sessionId')}
        defer_publish = action == 'smartTools.appCall' and not include_state
        if action.startswith("smartTools."):
            command_id = command_id or str(uuid.uuid4())
        if action not in ACTION_DEFINITIONS:
            raise AppError("Unknown action: " + action, 404)
        try:
            validate(args, ACTION_DEFINITIONS[action][1])
        except ValidationError as exc:
            raise AppError(exc.message) from exc
        if action in {'session.deletePreview', 'session.delete'}:
            from .managed_deletion import dispatch as delete_managed_chat
            return await delete_managed_chat(self, action, args, origin, include_state)
        if action == 'runtime.dependencies':
            from .artifact_runtime import discover
            async with self.lock:
                if expected_revision is not None and expected_revision != self.state['revision']:
                    raise AppError('The app changed. Refresh its state and retry.', 409)
                sid = args.get('sessionId') or self.state.get('selectedSessionId')
                if sid:
                    self._session(sid)
            host = await discover('host')
            worker = {'status': 'unavailable', 'reason': 'No running session runtime.', 'sessionId': sid}
            if sid and self.runtime and hasattr(self.runtime, 'dependencies'):
                worker = await self.runtime.dependencies(sid)
            return {'accepted': True, 'revision': self.state['revision'], 'effects': [],
                'result': {'host': host, 'worker': worker},
                **({'state': self.browser_state()} if include_state else {})}
        if action.startswith('schedule.'):
            try:
                result = await self.schedules.dispatch(action, args, origin, command_id)
            except ValueError as exc:
                raise AppError(str(exc), 409) from None
            return {'accepted': True, 'result': result, **({'state': self.browser_state()} if include_state else {})}
        if action.startswith("capacity."):
            from .capacity import dispatch as capacity_dispatch
            return await capacity_dispatch(self, action, args, origin, command_id, include_state)
        if action.startswith('task.'):
            from .task_continuity import dispatch as task_dispatch
            return await task_dispatch(self, action, args, origin, command_id, include_state)
        if action in {'question.list', 'question.read'}:
            async with self.lock:
                return {'accepted': True, 'result': self.questions.read(action, args),
                        **({'state': self.browser_state()} if include_state else {})}
        if action == "worker.message" and origin not in {"ui", "user"} and (not caller_session_id or args["sessionId"] != caller_session_id):
            raise AppError("A user must explicitly message a worker in another conversation.", 403)
        if action.startswith("coordination."):
            try:
                return await self.coordination.dispatch(action, args, origin, command_id, include_state, caller_session_id)
            except ValueError as exc:
                raise AppError(str(exc)) from None
        if action.startswith(('recall.', 'memory.')):
            return await self.recall.dispatch(action,args,origin,command_id)
        if (action.startswith(('canvas.views.', 'canvas.apps.')) or action in {'theme.preview', 'theme.revert', 'canvas.visibility'}) and 'clientId' in args:
            if client_id is None:
                with self.clients.bind(args['clientId']):
                    return await self.dispatch(action, args, origin, command_id, expected_revision, include_state=include_state)
            if args['clientId'] != client_id:
                raise AppError('The canvas view command targets a different client.')
        if action.startswith("kernels."):
            from .computation import dispatch
            return await dispatch(self, action, args, origin)
        if action.startswith("operations."):
            return await self.operations.dispatch(action, args, origin, command_id)
        if action.startswith("voice.visual."):
            return await self.voice_visual.dispatch(action, args, command_id, origin)
        if action.startswith("outputs."):
            return await self.outputs.dispatch(action,args,origin,command_id)
        if action.startswith("shell."):
            return await self.shell.dispatch(action, args, origin, command_id)
        if action.startswith('terminal.'):
            manager = getattr(self, 'terminal_setup', None)
            if manager is None:
                raise AppError('Terminal setup is available through the running Unified service.', 503)
            try:
                return await manager.perform(action, args, command_id)
            except (ValueError, OSError, TimeoutError) as exc:
                raise AppError(str(exc) if isinstance(exc, ValueError) else 'Terminal setup could not finish. Check server release access and retry.', 409) from None
        if action == 'runtime.control' and args.get('operation') in {'history.edit','history.rewind'}:
            raise AppError('Use message.edit to revise conversation history.')
        checked_session = None
        implicit_session = False
        if action.startswith('worktree.'):
            try:
                result = await self.worktrees.dispatch(action, args, origin, command_id)
            except (ValueError, OSError) as exc:
                raise AppError(str(exc), 409) from None
            return {'accepted': True, 'result': result, **({'state': self.browser_state()} if include_state else {})}
        if action in {'question.answer', 'conversation.send', 'conversation.retry', 'conversation.delivery', 'worker.spawn', 'worker.message', 'worker.steer', 'worker.stop', 'call.start', 'message.edit', 'session.fork', 'session.recover', 'session.takeover', 'session.naming', 'runtime.control', 'configuration.inspect', 'configuration.apply', 'bundle.save', 'bundle.export', 'bundle.preview', 'bundle.switch', 'bundle.fork'}:
            sid = args.get('sessionId') or (args.get('id') if action in {'session.fork', 'session.recover', 'session.takeover', 'session.naming', 'configuration.inspect', 'configuration.apply'} else None) or self.state.get('selectedSessionId')
            if sid:
                checked_session = sid
                implicit_session = not (args.get('sessionId') or (args.get('id') if action in {'session.fork', 'session.recover', 'session.takeover', 'session.naming', 'configuration.inspect', 'configuration.apply'} else None))
                try:
                    await self.history.ensure_loaded(sid)
                except ValueError as exc:
                    raise AppError(str(exc), 409) from None
        fingerprint = hashlib.sha256((json.dumps([action, args, origin, client_id], sort_keys=True) if client_id is not None and action not in {'conversation.send', 'worker.message'} and not action.startswith('question.') and not (action=='session.create' and args.get('fromDraft')) else json.dumps([action, args, origin], sort_keys=True)).encode()).hexdigest()
        prepared_workspace = None
        prepared_identity = None
        from .managed_chats import is_managed, allocate, creation_identity
        managed_creation = action == 'session.create' and is_managed(args)
        if managed_creation and args.get('workspace', '').strip():
            raise AppError('A chat without a workspace cannot also choose a workspace folder.')
        if action == 'session.create' and (managed_creation or args.get('workspace', '').strip()):
            from .new_chat import selection
            from .session_creation import prepare
            from .workspace_canvas import _create_workspace_folder
            async with self.lock:
                if expected_revision is not None and getattr(self, '_progress_dirty', False):
                    self._publish()
                previous = self.db.execute('SELECT fingerprint,receipt FROM commands WHERE id=?', (command_id,)).fetchone() if command_id else None
                if previous:
                    if previous[0] != fingerprint:
                        raise AppError('This command ID was already used with different contents.', 409)
                    if include_state and getattr(self, '_progress_dirty', False):
                        self._publish()
                    return {**json.loads(previous[1]), **({'state': self.browser_state()} if include_state else {}), 'duplicate': True}
                if expected_revision is not None and expected_revision != self.state['revision']:
                    raise AppError('The app changed. Refresh its state and retry.', 409)
                try:
                    selection(args.get('selection', {}))
                    prepare(self, args, origin, caller_session_id)
                except ValueError as exc:
                    raise AppError(str(exc), 409) from None
                if args.get('select', True):
                    self.canvas_views.guard_transition(action, args)
            # Opening/editing a draft never creates directories. First submit
            # creates only its explicit path, away from the event loop and lock.
            if managed_creation:
                prepared_identity = creation_identity(self.data_dir, args, command_id)
                from .managed_deletion import removed
                if removed(self.db, prepared_identity):
                    raise AppError("This conversation was permanently deleted. Start a new chat instead.", 409)
                try:
                    prepared_workspace = await asyncio.to_thread(allocate, self.data_dir, prepared_identity, command_id or prepared_identity)
                except (ValueError, OSError) as exc:
                    raise AppError(str(exc), 409) from None
            else:
                prepared_workspace = str(await asyncio.to_thread(_create_workspace_folder, args['workspace']))
        prepared_health = None
        if action == 'session.inspect':
            from .session_health import inspect_session
            async with self.lock:
                snapshot = copy.deepcopy(self._session(args['id']))
            prepared_health = await asyncio.to_thread(inspect_session, self.data_dir, snapshot)
        prepared_export = None
        prepared_share = None
        if action == 'session.sharePreview':
            from .conversation_export import markdown
            async with self.lock:
                previous = self.db.execute('SELECT fingerprint,receipt FROM commands WHERE id=?', (command_id,)).fetchone() if command_id else None
                if previous:
                    if previous[0] != fingerprint:
                        raise AppError('This command ID was already used with different contents.', 409)
                    return {**json.loads(previous[1]), **({'state': self.browser_state()} if include_state else {}), 'duplicate': True}
                source = copy.deepcopy(self._session(args['sessionId']))
                artifacts = copy.deepcopy(self.state.get('canvasArtifacts', []))
            try:
                prepared_share = (source, await asyncio.to_thread(markdown, self.data_dir, source, artifacts))
            except (ValueError, OSError) as exc:
                raise AppError(str(exc), 409) from exc
        if action == 'session.export' and args.get('format') == 'markdown':
            from .conversation_export import markdown
            async with self.lock:
                # Retried receipts refer to the original bytes even if their
                # source is now missing. Do not read native history again.
                previous = self.db.execute('SELECT fingerprint,receipt FROM commands WHERE id=?', (command_id,)).fetchone() if command_id else None
                if previous:
                    if previous[0] != fingerprint:
                        raise AppError('This command ID was already used with different contents.', 409)
                    if include_state and getattr(self, '_progress_dirty', False):
                        self._publish()
                    return {**json.loads(previous[1]), **({'state': self.browser_state()} if include_state else {}), 'duplicate': True}
                source = copy.deepcopy(self._session(args['id']))
                artifacts = copy.deepcopy(self.state.get('canvasArtifacts', []))
            # Native storage may be slow. Freeze the host-owned portion first,
            # then let navigation and runtime events continue during the read.
            try:
                prepared_export = (source, await asyncio.to_thread(markdown, self.data_dir, source, artifacts))
            except (ValueError, OSError) as exc:
                raise AppError(str(exc), 409) from exc
        pending = []
        async with self.lock:
            # Flush and compare under the same lock: a queued runtime event
            # must not mutate progress between a read barrier and CAS admission.
            if expected_revision is not None and getattr(self, '_progress_dirty', False):
                self._publish()
            if command_id:
                previous = self.db.execute("SELECT fingerprint,receipt FROM commands WHERE id=?", (command_id,)).fetchone()
                if previous:
                    if previous[0] != fingerprint:
                        raise AppError("This command ID was already used with different contents.", 409)
                    if include_state and getattr(self, '_progress_dirty', False):
                        self._publish()
                    saved_receipt = json.loads(previous[1])
                    if action == 'question.answer':
                        saved_receipt['result'] = self.questions.read('question.read', args)
                    return {**saved_receipt, **({'state': self.browser_state()} if include_state else {}), "duplicate": True}
            if checked_session:
                if implicit_session and self.state.get('selectedSessionId') != checked_session:
                    raise AppError('The selected chat changed. Retry in the intended chat.', 409)
                checked = self._session(checked_session)
                if action not in {'conversation.delivery', 'session.takeover', 'session.fork', 'session.recover', 'session.inspect', 'message.edit', 'bundle.export'} and checked.get('ownership', {}).get('status') in {'blocked', 'yielding', 'yielded', 'yield-failed', 'taking-over'}:
                    raise AppError('This session is read-only here. Choose Continue here to request ownership.', 409, code='session_busy')
                if checked.get('nativeProject') and action != 'session.inspect':
                    reason = checked.get('historyReadOnlyReason') or checked.get('historyError')
                    if reason:
                        raise AppError(reason, 409)
                    if not checked.get('workspace') or not Path(checked['workspace']).is_dir():
                        raise AppError('Restore this project folder before continuing its chat.', 409)
            if expected_revision is not None and expected_revision != self.state["revision"]:
                raise AppError("The app changed. Refresh its state and retry.", 409)
            if work_paused(self.state) and (action in {"question.answer","conversation.send","conversation.retry","session.takeover","worker.spawn","worker.steer","worker.message","call.start","feedback.submit","feedback.comment","feedback.get"} or (action == 'session.naming' and args.get('regenerate')) or (action.startswith("smartTools.") and action not in {"smartTools.context","smartTools.result"})):
                raise AppError("An ecosystem update is activating. Please retry in a moment.", 409)
            if action in {"question.answer","conversation.send","conversation.retry","worker.spawn","worker.steer","worker.message","call.start"}:
                current=next((s for s in self.state['sessions'] if s['id']==args.get('sessionId',self.state['selectedSessionId'])),{})
                if current.get('configurationBusy'):raise AppError('Applying conversation settings; retry shortly.',409)
            if action == 'canvas.visibility':
                from .canvas_visibility import update
                return update(self, args, command_id, fingerprint, include_state=include_state)
            opens_selected_canvas = action in {'canvas.select', 'canvas.reopen', 'canvas.tabClose', 'canvas.views.open'} or (action == 'canvas.show' and not args.get('sessionId'))
            if opens_selected_canvas and self.state.get('selectedSessionId') is None:
                raise AppError('Start a chat before opening Canvas.', 409, code='canvas_requires_session')
            from .canvas_library import remember, restore, fork_artifacts
            if action != "session.create" or args.get("select", True):
                self.canvas_views.guard_transition(action, args)
            if action == 'canvas.close' and client_id is not None:
                views = self.canvas_views.record()
                views.pop('retained', None)
                views.pop('primaryBinding', None)
                views.pop('secondaryBinding', None)
            remember(self.state,self.db)
            previous_scope=(self.state.get('selectedSessionId'),self.state.get('selectedWorkspaceId'))
            previous_draft=self.state['view'].get('draft','')
            previous_open=self.state.get('canvas',{}).get('open',False)
            effects = []
            diagnostic_result = None
            if action in LIBRARY_ACTIONS:
                try:
                    diagnostic_result = self.conversation_library.perform(action, args, prepared_share)
                except ValueError as exc:
                    raise AppError(str(exc), 409) from exc
            elif action == 'diagnostics.export':
                page=self.state.get('diagnostics',{}).get('lastResult',{})
                if page.get('action')!='diagnostics.records':raise AppError('Inspect the records to export first.')
                lines=[json.dumps({'event':r['event'],'workspace':r['workspace'],'timestamp':r['data']['timestamp'],'data':r['data']}) for r in reversed(page.get('items',[]))]
                effects.append({'type':'download','filename':'amplifier-diagnostics.jsonl','mime':'application/x-ndjson','content':'\n'.join(lines)+'\n'})
            elif action=='diagnostics.test':
                if not any(row['id']==args['id'] for row in self.diagnostics.config['destinations']):
                    raise AppError('Save this destination before testing it.')
                if self.diagnostics.results.get(args['id'],{}).get('phase')=='working':
                    raise AppError('A connection test is already running for this destination.',409)
                self.diagnostics.results[args['id']]={'phase':'working','message':'Checking authentication and event ingestion…'}
                self.state['diagnostics']['results']=copy.deepcopy(self.diagnostics.results)
                pending.append((self._diagnostics_test,(args['id'],self.diagnostics.policy_generation)))
            elif action.startswith('diagnostics.'):
                try:diagnostic_result=await self.diagnostics.command(action,args)
                except ValueError as exc:raise AppError(str(exc)) from None
            elif action == 'history.refresh':
                self.state['sharedHistory'].update(loading=True, error=None)
                pending.append((self.history.refresh, ()))
            elif action == 'session.history':
                session = self._session(args['id'])
                if session.get('nativeProject'):
                    pending.append((self.history_page, (session['id'], args.get('before'), args.get('limit', 100))))
            elif action.startswith("workspace."):
                from .workspace_canvas import workspace_command
                if action == 'workspace.remove':
                    removed = next((w for w in self.state['workspaces'] if w['id'] == args['id']), None)
                    if removed and len(self.state['workspaces']) > 1:
                        self.history.hide_workspace(removed)
                workspace_command(self.state, action, args)
                if action in {'workspace.add', 'workspace.create'}:
                    hidden = self.state.get('hiddenNativeWorkspaces', [])
                    from .session_files import project_slug
                    restored = {self.state['selectedWorkspaceId'], uuid.uuid5(uuid.NAMESPACE_URL, 'amplifier-project:' + project_slug(args['path'])).hex}
                    hidden[:] = [identity for identity in hidden if identity not in restored]
                if action in {'workspace.select', 'workspace.add', 'workspace.create', 'workspace.remove'}:
                    from .session_navigation import is_top_level
                    workspace = next(w for w in self.state['workspaces'] if w['id'] == self.state['selectedWorkspaceId'])
                    matches = [s for s in self.state['sessions'] if is_top_level(s) and (s.get('workspaceId') == workspace['id'] or (workspace.get('path') and s.get('workspace') == workspace['path']))]
                    selected = next((s for s in matches if s['id'] == self.state.get('selectedSessionId')), matches[0] if matches else None)
                    self.state['selectedSessionId'] = selected['id'] if selected else None
                    if action == 'workspace.create' and selected is None:
                        from .new_chat import open_draft
                        open_draft(self, {'workspace': workspace['path']})
                    if selected and selected.get('nativeProject'):
                        pending.append((self.history.load, (selected['id'],)))
            elif action.startswith("canvas."):
                from .workspace_canvas import canvas_command
                if action.startswith('canvas.apps.'):
                    from .canvas_apps import command
                    diagnostic_result = command(self, action, args, origin)
                elif action.startswith('canvas.views.'):
                    diagnostic_result, view_effects = self.canvas_views.command(action, args, origin)
                    effects.extend(view_effects)
                elif action in {'canvas.select','canvas.reopen','canvas.tabClose'}:
                    from .canvas_library import command
                    command(self.state,self.db,action,args)
                    if action=='canvas.select':
                        self.state['view'].setdefault('canvasDraft',{}).update(library=False,open=False,browser=False)
                elif action=='canvas.openExternal':
                    canvas=self.state['canvas']
                    if canvas.get('id')!=args['id'] or canvas.get('kind')!='browser':raise AppError('Select a browser artifact first.')
                    effects.append({'type':'browser.open','url':canvas['url']})
                elif action in {'canvas.copy', 'canvas.download'}:
                    canvas = self.state['canvas']
                    if args['id'] != canvas.get('id'):
                        raise AppError('This canvas has been replaced.')
                    content = canvas.get('url') if canvas.get('kind')=='browser' else canvas.get('content', json.dumps(canvas.get('surface', {}), indent=2))
                    extension = {'markdown':'md','html':'html','mermaid':'mmd','dot':'dot','json':'json','jsonl':'jsonl','a2ui':'json'}.get(canvas.get('kind'), 'txt')
                    if action=='canvas.copy' and canvas.get('contentResource'):
                        effects.append({'type':'clipboard.url','url':'/api/canvas/'+canvas['id']+'/source','canvasId':canvas['id']})
                    elif action=='canvas.download' and (canvas.get('kind')=='babylon' or canvas.get('contentResource')):
                        effects.append({'type':'download.url','url':'/api/canvas/'+canvas['id']+'/download','filename':'canvas-3d.html' if canvas.get('kind')=='babylon' else 'canvas.html'})
                    else:
                        effects.append({'type':'clipboard.write' if action == 'canvas.copy' else 'download',
                        'content':content,'filename':'canvas.'+extension,'mime':'text/plain','canvasId':args['id']})
                elif action=='canvas.show':
                    sid=args.get('sessionId',self.state.get('selectedSessionId'))
                    owner=self._session(sid) if sid else None
                    workspace=next((w for w in self.state['workspaces'] if owner and w['path']==owner['workspace']),None)
                    scoped={**self.state,'selectedSessionId':sid,'selectedWorkspaceId':workspace['id'] if workspace else self.state['selectedWorkspaceId']}
                    canvas_command(scoped,action,args,origin)
                    remember(scoped,self.db)
                    if sid==self.state.get('selectedSessionId') and scoped['selectedWorkspaceId']==self.state['selectedWorkspaceId']:
                        self.state['canvas']=scoped['canvas']
                        self.state['view'].setdefault('canvasDraft',{}).update(library=False,open=False,browser=False)
                else:
                    canvas_command(self.state, action, args, origin)
            elif action == 'session.draft':
                from .new_chat import open_draft
                open_draft(self, args)
            elif action == "session.create":
                if args.get('fromDraft') and not managed_creation and not args.get('workspace', '').strip():
                    raise AppError('Choose a workspace folder before starting this chat.')
                from .session_creation import prepare, apply
                try:
                    inherited = prepare(self, args, origin, caller_session_id)
                    session = self._new_session({**args, 'workspace': prepared_workspace} if prepared_workspace else args)
                    if args.get('id') or prepared_identity: session['id'] = args.get('id') or prepared_identity
                    apply(self, session, inherited)
                except ValueError as exc:
                    raise AppError(str(exc), 409) from None
                if args.get('fromDraft') and command_id:
                    session['creationCommandId'] = command_id
                from .naming import persist
                persist(self.data_dir,session,shared_rename=True)
                self.state["sessions"].insert(0, session)
                diagnostic_result = {"sessionId": session['id']}
                if args.get('select', True):
                    from .workspace_canvas import select_session_workspace
                    select_session_workspace(self.state, session)
                    self.state["selectedSessionId"] = session["id"]
                    self.state["view"]["draft"] = ""
                    if client_id is not None and (previous_scope[0] is None or args.get('fromDraft')):
                        drafts = self.clients.record().setdefault("drafts", {})
                        self.clients.draft(session["id"], drafts.pop("", ""))
                        attachments = self.clients.record().setdefault('attachments', {})
                        attachments[session['id']] = attachments.pop('', [])
                    self.state['view'].pop('newSessionDraft', None)
            elif action == "session.select":
                session = self._session(args["id"])
                from .workspace_canvas import select_session_workspace
                select_session_workspace(self.state, session)
                previous = next((row for row in self.state['sessions'] if row['id'] == self.state.get('selectedSessionId')), None)
                if client_id is None and previous is not None and (self.state['view'].get('draft') or 'draft' in previous):
                    previous['draft'] = self.state['view'].get('draft', '')
                self.state["selectedSessionId"] = session["id"]
                self.state["view"]["draft"] = session.get('draft', '')
                if session.get('nativeProject'):
                    pending.append((self.history.refresh_session, (session['id'],)))
                pending.append((self.warmup.schedule, (session['id'],)))
            elif action == "session.warm":
                session = self._session(args['id'])
                pending.append((self.warmup.schedule, (session['id'],)))
            elif action == 'runtime.retention.update':
                if not callable(getattr(self.runtime, 'configure_retention', None)):
                    raise AppError('This host does not support worker retention settings.')
                from .deployment import load_server_config, save_server_config
                config = load_server_config(self.data_dir)
                config['runtime'].update(args['patch'])
                # Validate and durably save before applying the running policy.
                from .runtime_retention import validate_retention
                policy = validate_retention(config['runtime'])
                policy['max_background_starts'] = self.runtime.retention.settings['max_background_starts']
                saved = save_server_config(self.data_dir, {**config, 'runtime': policy})
                self.runtime.configure_retention(policy)
                self.server_config = {**getattr(self, 'server_config', saved), 'runtime': policy}
                self.state['runtime']['retention'] = dict(policy)
            elif action == "session.naming":
                from .naming import set_automatic
                session = self._session(args['id'])
                if 'automatic' not in args and not args.get('regenerate'):
                    raise AppError('Choose automatic naming or regeneration.')
                if args.get('regenerate'):
                    if not self.runtime or not callable(getattr(self.runtime, 'control', None)):
                        raise AppError('The naming runtime is unavailable.', 503)
                    if session.get('naming', {}).get('status') == 'working':
                        raise AppError('A chat name is already being generated.', 409)
                    if session.get('status') in {'starting', 'working', 'running', 'stopping'} or session.get('configurationBusy'):
                        raise AppError('Wait for the current work to finish before regenerating its name.', 409)
                    if not session.get('messages'):
                        raise AppError('Send a message before generating a chat name.')
                if 'automatic' in args:
                    set_automatic(self.data_dir, session, args['automatic'])
                if args.get('regenerate'):
                    from .naming import directory_for, read
                    base = read(directory_for(self.data_dir, session))
                    session['naming'] = {'status': 'working'}
                    pending.append((self._regenerate_name, (copy.deepcopy(session), base)))
                diagnostic_result = {'automatic': session.get('autoName'), 'status': session.get('naming', {}).get('status', 'idle')}
            elif action == "session.rename":
                if not args["title"].strip():
                    raise AppError("Enter a title.")
                session=self._session(args['id'])
                from .naming import persist, set_automatic
                renamed = {**session, 'title':args['title'].strip(), 'titleSource':'manual','autoName':False}
                set_automatic(self.data_dir, renamed, False)
                persist(self.data_dir,renamed,shared_rename=True)
                session.update({key:renamed[key] for key in ('title','titleSource','nativeNameSource','description','autoName') if key in renamed})
            elif action == 'session.takeover':
                session = self._session(args['id'])
                if not self.runtime:
                    raise AppError('The Amplifier runtime is unavailable.')
                if session.get('ownership', {}).get('status') in {'taking-over', 'yielding', 'yield-failed'}:
                    raise AppError('An ownership change is already in progress.', 409)
                session['ownership'] = {'status': 'taking-over'}
                session.pop('error', None)
                session.pop('failure', None)
                session.pop('health', None)
                pending.append((self._takeover, (copy.deepcopy(session),)))
            elif action == "session.pin":
                from .session_navigation import is_top_level
                session = self._session(args['id']) if args['pinned'] else None
                if session is not None and not is_top_level(session):
                    raise AppError('Only top-level chats can be pinned.')
                pins = self.state.setdefault('pinnedSessionIds', [])
                if args['pinned'] and args['id'] not in pins:
                    pins.append(args['id'])
                elif not args['pinned']:
                    pins[:] = [identity for identity in pins if identity != args['id']]
                self.state['view'].pop('navChatPage', None)

            elif action == 'message.copy':
                source = self._session(args['sessionId'])
                message = next((m for m in source['messages'] if m['id']==args['messageId']), None)
                if not message:
                    raise AppError('This message is no longer available.',404)
                identity = str(uuid.uuid4())
                self.state['view']['messageCopy'] = {**args,'requestId':identity,'status':'pending'}
                effects.append({'type':'message.copy',**args,'requestId':identity})
            elif action == 'message.copyResult':
                result = self.state['view'].get('messageCopy',{})
                if result.get('requestId') == args['requestId']:
                    result.update(status=args['status'],message=args.get('message',''))
            elif action == 'message.edit':
                source = self._session(args['sessionId'])
                original = next((m for m in source['messages'] if m['id']==args['messageId'] and m['role']=='user'),None)
                if not original:
                    raise AppError('Choose one of your own messages to edit.')
                text = args['text'].strip()
                if not text:
                    raise AppError('Enter a message before regenerating.')
                if source.get('configurationBusy'):
                    raise AppError('Wait for configuration changes to finish.',409)
                if not self.runtime:
                    raise AppError('The Amplifier runtime is unavailable.')
                if work_paused(self.state):
                    raise AppError('An update is activating. Please retry in a moment.',409)
                if args.get('mode', 'fork') == 'current':
                    if source['status'] in {'working','running','starting','stopping','busy'} or any(
                            row.get('status') in {'working','running','starting','stopping','queued','pending'} or row.get('persistent') and row.get('status')=='idle'
                            for row in source.get('workers', [])) or any(row.get('status', 'pending')=='pending' for row in source.get('approvals', [])):
                        raise AppError('Finish active work and pending interactions before editing history.',409)
                    if source.get('ownership', {}).get('status') in {'blocked','yielding','yielded','yield-failed','taking-over'}:
                        raise AppError('Choose Continue here before editing this conversation.',409,code='session_busy')
                    session = source
                    session['historyManaged'] = False
                    session['configurationBusy'] = True
                    session['historyEdit'] = {'operationId':command_id or str(uuid.uuid4()),'messageId':original['id'],
                        'text':text,'via':'text' if original.get('via')=='text' else 'chat','inputOrigin':origin,
                        'attachments':copy.deepcopy(original.get('attachments',[])), 'phase':'working'}
                    pending.append((self._edit_current,(copy.deepcopy(session),)))
                else:
                    session = self._new_session({'title':source['title']+' · edited','workspace':source['workspace'],'bundle':source['bundle']})
                    from .session_store import fork_session
                    try:
                        session.update(fork_session(self.data_dir,source,session['id'],before_message_id=original['id']))
                    except ValueError as exc:
                        raise AppError(str(exc),409) from exc
                    session['editOrigin'] = {'sessionId':source['id'],'messageId':original['id']}
                    input_id = command_id or str(uuid.uuid4())
                    self._message(session,'user',text,'text' if original.get('via')=='text' else 'chat',inputId=input_id,inputOrigin=origin,attachments=copy.deepcopy(original.get('attachments',[])),delivery={'status':'sending'})
                    self._activity(session,'queued','Generating from your edited message.',reset=True)
                    session['status']='working'
                    ensure_turn(session,input_id,text)
                    from .naming import persist
                    persist(self.data_dir,session,shared_rename=True)
                    self.state['sessions'].insert(0,session)
                    self.state['selectedSessionId']=session['id']
                    self.state['view']['messageEdit']=None
                    from .workspace_canvas import select_session_workspace
                    select_session_workspace(self.state,session)
                    pending.append((self._send,(copy.deepcopy(session),text,input_id)))
            elif action == 'session.inspect':
                diagnostic_result = prepared_health
                if self._session(args['id']).get('errorAt') == snapshot.get('errorAt'):
                    self._session(args['id'])['health'] = diagnostic_result
            elif action in {"session.fork", "session.recover"}:
                source = self._session(args["id"])
                if source.get("configurationBusy"):
                    raise AppError("Wait for configuration changes to finish before forking.",409)
                session = self._new_session({"title": source["title"] + (" · recovery" if action == "session.recover" else " · fork"), "workspace": source["workspace"], "bundle": source["bundle"]})
                # loop-live checkpoints before publishing idle. Fork that full
                # context (including tool receipts), never reconstruct a running
                # session from the visible assistant bubbles alone.
                from .session_store import fork_session
                try:
                    session.update(fork_session(self.data_dir,source,session["id"],turn=args.get("turn"), recovery=action == "session.recover"))
                except ValueError as exc:
                    raise AppError(str(exc),409) from exc
                from .naming import persist
                persist(self.data_dir,session,shared_rename=True)
                self.state["sessions"].insert(0, session)
                self.state["selectedSessionId"] = session["id"]
                self.state['view']['messageEdit']=None
                if action == 'session.recover':
                    diagnostic_result = {'sessionId': session['id'], 'sourceSessionId': source['id'], 'workReplayed': False, 'status': 'idle'}
                from .workspace_canvas import select_session_workspace
                select_session_workspace(self.state,session)
            elif action == 'session.export' and args.get('format') == 'markdown':
                from .resource_files import put
                source, content = prepared_export
                reference = put(self.db, content)
                identity = reference['$resource']
                filename = 'amplifier-conversation-' + identity[:12] + '.md'
                diagnostic_result = {'snapshotId': identity, 'sessionId': source['id'], 'filename': filename,
                    'mimeType': 'text/markdown', 'content': reference,
                    'statePath': '/conversationExports/' + identity + '/content',
                    'url': '/api/conversation/exports/' + identity}
                self.state.setdefault('conversationExports', {})[identity] = copy.deepcopy(diagnostic_result)
                destination = args.get('destination', 'download')
                if destination != 'none':
                    request_id = str(uuid.uuid4())
                    self.state['view']['conversationExport'] = {'sessionId': source['id'], 'requestId': request_id, 'status': 'pending'}
                    effects.append({'type': 'conversation.export', 'requestId': request_id,
                                    'destination': destination, 'url': diagnostic_result['url']})
            elif action == 'session.exportResult':
                result = self.state['view'].get('conversationExport', {})
                if result.get('requestId') == args['requestId']:
                    result.update(status=args['status'], message=args.get('message', ''))
            elif action in {"state.export", "session.export", "theme.export"}:
                if action == 'session.export' and args.get('destination', 'download') != 'download':
                    raise AppError('Choose Markdown to copy a conversation or create a readable snapshot.')
                content = self.state if action == "state.export" else self._session(args["id"]) if action == "session.export" else self.state["theme"]["css"]
                mime = "text/css" if action == "theme.export" else "application/json"
                effects.append({"type": "download", "filename": "amplifier-skin.css" if action == "theme.export" else "amplifier-export.json", "mime": mime, "mimeType": mime, "content": content if isinstance(content, str) else json.dumps(content, indent=2)})
            elif action == "attachment.add":
                from .attachments import save,MAX_FILES
                target = args.get('sessionId', self.state.get('selectedSessionId'))
                session = self._session(target) if target is not None else None
                draft=self.clients.attachments(session)
                if len(draft)>=MAX_FILES:raise AppError('Attach up to 8 files per message.')
                draft.append(save(self.data_dir,args['name'],args['base64']))
            elif action == "attachment.remove":
                target = args.get('sessionId', self.state.get('selectedSessionId'))
                session = self._session(target) if target is not None else None
                draft=self.clients.attachments(session)
                draft[:]=[row for row in draft if row['id']!=args['id']]
            elif action.startswith('question.'):
                diagnostic_result = self.questions.dispatch(action, args, origin, client_id, pending)
            elif action == "conversation.send":
                session = self._session(args.get("sessionId"))
                text = args["text"].strip()
                requested=args.get('attachmentIds',[])
                available={row['id']:row for row in self.clients.attachments(session)}
                if any(identity not in available for identity in requested):raise AppError('An attachment is no longer in this draft. Refresh and retry.')
                attachments=[available[identity] for identity in requested]
                if not text and not attachments:raise AppError("Enter a message or attach a file.")
                text=text or 'Please review the attached files.'
                if not self.runtime:
                    raise AppError("The Amplifier runtime is unavailable.")
                session['historyManaged'] = False
                input_id = command_id or str(uuid.uuid4())
                from .chat_navigation import recent_activity
                previous_activity = recent_activity(session)
                session.setdefault('surfaceInputs', {})[input_id] = self.surface_context.bind_input(session['id'])
                session['surfaceInputs'] = dict(list(session['surfaceInputs'].items())[-16:])
                self._message(session, "user", text, args.get("via", self.state["view"]["mode"]), inputId=input_id,inputOrigin=origin,attachments=attachments,delivery={'status':'sending'})
                if session["title"] in {"New chat","New conversation","A new conversation","Untitled conversation"}:
                    session["title"] = text[:64]
                from .naming import persist
                persist(self.data_dir,session)
                self._activity(session, "queued", "Your message is queued for Amplifier.", reset=session["status"] not in {"working", "starting"})
                session["status"] = "working"
                session.pop("error", None)
                ensure_turn(session,input_id,text)
                pending.append((self._send, (copy.deepcopy(session), text, input_id, previous_activity, args.get('preserveDraft', False))))
            elif action == "conversation.delivery":
                session = self._session(args['sessionId'])
                pending.append((self._check_delivery, (session['id'], args['inputId'])))
            elif action == "conversation.retry":
                from .message_delivery import find_message
                session = self._session(args['sessionId'])
                message = find_message(session, args['inputId'])
                if message is None:
                    raise AppError('This message is not saved in this conversation.', 404)
                if message.get('delivery', {}).get('status') == 'accepted':
                    diagnostic_result = {'delivery': 'accepted', 'resent': False}
                else:
                    if message.get('delivery', {}).get('status') not in {'unknown', 'failed', 'sending'}:
                        raise AppError('This message is not awaiting delivery recovery.', 409)
                    if message is not next((m for m in reversed(session['messages']) if m.get('role') == 'user'), None):
                        raise AppError('Only the latest unconfirmed message can be resent. Review later messages first.', 409)
                    if session['status'] in {'working', 'starting', 'running', 'stopping'} or message.get('delivery', {}).get('status') == 'sending':
                        raise AppError('Wait for the current delivery or work to settle before resending.', 409)
                    if not args.get('confirmUncertain'):
                        raise AppError('Delivery is uncertain. Confirm that sending again may repeat earlier work.', 409, code='delivery_uncertain')
                    if not self.runtime or not hasattr(self.runtime, 'retry'):
                        raise AppError('This runtime does not support message recovery.', 409)
                    original_status = session['status']
                    original_turn = copy.deepcopy(next((t for t in session.get('execution', {}).get('turns', []) if t['id'] == args['inputId']), {}))
                    self._delivery(session, args['inputId'], 'sending')
                    self._activity(session, 'queued', 'Sending your saved message again.', reset=True)
                    session['status'] = 'working'
                    session.pop('error', None)
                    tree = ensure_turn(session, args['inputId'], message['text'])
                    turn = next(t for t in tree['turns'] if t['id'] == args['inputId'])
                    turn['phase'] = 'running'
                    turn['retriedAt'] = time.time()
                    turn.pop('endedAt', None)
                    pending.append((self._retry_message, (copy.deepcopy(session), message['text'], args['inputId'], original_status, original_turn)))
            elif action == "conversation.stop":
                session = self._session(args.get("sessionId"))
                session["interruptionRevision"] = session.get("interruptionRevision", 0) + 1
                session["lastInterruption"] = {"commandId": command_id, "at": time.time(), "origin": origin}
                session["status"] = "stopping"
                pending.append((self._stop, (session["id"],)))
            elif action == "worker.spawn":
                session = self._session(args.get("sessionId"))
                text = args["instruction"].strip()
                if not text:
                    raise AppError("Describe the worker's task.")
                if not self.runtime:
                    raise AppError('The Amplifier runtime is unavailable.')
                session['historyManaged'] = False
                instruction = "Delegate the following task to a background worker lane using your delegation tool. Keep the main conversation available. Task: " + text
                if args.get("bundle"):
                    instruction += "\nRequested worker bundle: " + args["bundle"]
                input_id = command_id or str(uuid.uuid4())
                self._message(session, "user", text, "worker request", inputId=input_id)
                session["status"] = "working"
                ensure_turn(session,input_id,text)
                pending.append((self._send, (copy.deepcopy(session), instruction, input_id)))
            elif action in {"worker.stop", "worker.steer", "worker.message"}:
                session = self._session(args.get("sessionId"))
                if not self.runtime:
                    raise AppError("The execution runtime is unavailable.")
                worker = next((row for row in session["workers"] if row["id"] == args["id"]), None)
                if not worker:
                    raise AppError("Worker not found in this conversation.", 404)
                if action == "worker.message" and (not worker.get("persistent") or worker.get("status") not in {"idle", "running"}):
                    raise AppError("This worker cannot receive a follow-up.", 409)
                if action == "worker.stop":
                    worker["interruptionRevision"] = worker.get("interruptionRevision", 0) + 1
                    worker["lastInterruption"] = {"commandId": command_id, "at": time.time(), "origin": origin}
                method = self.runtime.stop_worker if action == "worker.stop" else self.runtime.message_worker if action == "worker.message" else self.runtime.steer_worker
                call_args = (session["id"], args["id"]) + ((args["text"], command_id) if action == "worker.message" else (args["text"],) if action == "worker.steer" else ())
                pending.append((method, call_args))
            elif action == "approval.respond":
                session = self._session(args.get("sessionId"))
                approval = next((a for a in session["approvals"] if a["id"] == args["id"] and a.get("status") == "pending"), None)
                if not approval:
                    raise AppError("This permission request is no longer pending.", 409)
                if origin not in {"ui", "user"}:
                    raise AppError("This tool requested user approval. It must be answered by the user.", 403)
                decision = "allow" if args["decision"] in {"allow", "approve"} else "deny"
                approval["status"] = decision
                if self.runtime:
                    pending.append((self.runtime.approval, (session["id"], args["id"], decision)))
            elif action == "attention.read":
                from .attention import snapshot
                current = {item['id']:item for item in snapshot(self.state)['items']}
                if any(identity not in current for identity in args['ids']):
                    raise AppError('Attention items changed; refresh before marking them read.')
                receipts = self.state.setdefault('attentionRead', {})
                for identity in args['ids']:
                    if 'fingerprints' not in args or args['fingerprints'].get(identity)==current[identity]['fingerprint']:
                        receipts[identity] = current[identity]['fingerprint']
                self.state['attentionRead'] = {key:value for key,value in receipts.items() if key in current}
            elif action == "view.update":
                patch = args["patch"]
                allowed = {"mode", "panel", "draft", "scheme", "layout", "selectedWorkerId", "contextVisible", "commandsVisible", "notificationPermission", "themeDraft", "themeDraftName", "themePreview", "newSessionDraft", "sessionSetup", "workerDraft", "notice", "agentAction", "agentArgs", "bundleManager", "moduleEditor", "settingsSection", "maintenanceDraft", "providerEditor", "routingEditor","registryDraft", "historyFilter", "runtimeDraft", "expandedExecutions", "executionExpanded", "executionDetails", "settingsExpanded", "settingsFilters", "locationPicker", "composerModel", "composerBundle", "bundleDefaultsDraft", "bundleSources", "canvasWidth", "navWidth", "canvasFocused", "canvasControlsPinned", "canvasControlsExpanded", "toolbarMenuOpen", "navPinned", "navExpanded", "navFilter", "navChatPage", "navChatScope", "navLocationFilter", "navWorkspacePath", "navWorkspaceFilter", "navWorkspacePage", "navWorkspaceAncestorsOpen", "subagentHistory", "workspaceDraft", "canvasDraft", "messageEdit", "smartToolsEditor", "feedbackDraft", "feedbackFollowupDraft", "diagnosticsDraft"}
                allowed.update({'navArchive', 'navCollection'})
                if set(patch) - allowed:
                    raise AppError("Unknown view setting.")
                for key, options in {"mode": {"call", "text", "chat"}, "scheme": {"light", "dark", "system"}, "layout": {"balanced", "conversation", "work"}}.items():
                    if key in patch and patch[key] not in options:
                        raise AppError("Invalid " + key)
                for key, minimum in (("canvasWidth", 300), ("navWidth", 216)):
                    if key in patch and (type(patch[key]) not in {int, float} or not minimum <= patch[key] <= 16384):
                        raise AppError(f"{key} must be between {minimum} and 16384 pixels.")
                for key in ("navPinned", "navExpanded", "navWorkspaceAncestorsOpen", "canvasFocused", "canvasControlsPinned", "canvasControlsExpanded", "toolbarMenuOpen"):
                    if key in patch and type(patch[key]) is not bool:
                        raise AppError("Layout switches must be true or false.")
                from .workspace_navigation import view_patch
                from .chat_navigation import view_patch as chat_view_patch
                try:
                    patch = view_patch(self.state, chat_view_patch(patch))
                except ValueError as exc:
                    raise AppError(str(exc)) from None
                patch = copy.deepcopy(patch)
                if 'newSessionDraft' in patch:
                    from .new_chat import validate_setup
                    try:
                        patch['newSessionDraft'] = validate_setup(patch['newSessionDraft'])
                    except ValidationError as exc:
                        raise AppError(exc.message) from None
                if patch.get('panel'):
                    patch['toolbarMenuOpen'] = False
                if 'draft' in patch:
                    if not isinstance(patch['draft'], str):
                        raise AppError('Draft must be text.')
                    target = args.get('sessionId', self.state.get('selectedSessionId'))
                    if client_id is not None:
                        if target is not None:
                            self._session(target)
                        self.clients.draft(target, patch.pop('draft'))
                    elif target:
                        self._session(target)['draft'] = patch['draft']
                        if target != self.state.get('selectedSessionId'):
                            patch.pop('draft')
                    elif self.state.get('selectedSessionId') is not None:
                        raise AppError('Attach a client to save a draft without a conversation.')
                    else:
                        self.state['view']['newChatText'] = patch['draft']
                self.state["view"].update(patch)
            elif action in {"feedback.attachment.add","feedback.attachment.remove"}:
                self.feedback.attachment_command(action,args)
            elif action == "feedback.submit":
                if self.feedback.accept(args):
                    pending.append((self.feedback.send, (args['requestId'],)))
                # Close only the submitting draft, atomically with durable acceptance.
                view=self.state['view']
                if view.get('panel')=='feedback' and view.get('feedbackDraft',{}).get('pending',{}).get('requestId')==args['requestId']:
                    view['panel']=None
            elif action in {"feedback.get", "feedback.comment"}:
                if self.feedback.followups.accept(action, args, origin):
                    pending.append((self.feedback.followups.run, (args['requestId'],)))
            elif action.startswith("smartTools."):
                if not self.smart_tools: raise AppError("Smart Tools service is unavailable.")
                if action == 'smartTools.context':
                    self.smart_canvas.context(args)
                else:
                    scoped_args = copy.deepcopy(args)
                    if action in {'smartTools.call','smartTools.open'}:
                        scoped_args.setdefault('sessionId',self.state.get('selectedSessionId'))
                    if action == 'smartTools.appCall':
                        self.smart_canvas.admit_call(args)
                    pending.append((self.smart_canvas.command,(action,scoped_args,command_id,origin)))
            elif action == 'bundle.default':
                from .preferences import SettingsStore
                bundle = args['bundle'].strip() if isinstance(args['bundle'], str) else None
                if bundle == '': raise AppError('Choose a bundle or clear the override.')
                workspace = str(Path(args.get('workspace') or self.state['settings']['workspace']).expanduser().resolve())
                if not Path(workspace).is_dir(): raise AppError('Choose an existing workspace.')
                if args['scope'] == 'app':
                    self.state['settings']['appBundle'] = bundle
                else:
                    def update_default(value):
                        if bundle: value.setdefault('bundle', {})['active'] = bundle
                        else: value.get('bundle', {}).pop('active', None)
                    SettingsStore(self.data_dir).update(workspace, 'local' if args['scope']=='workspace' else 'global', update_default)
                self._shared_preferences_stamp = None
                self._refresh_shared_preferences()
            elif action.startswith(("bundle.","bundles.","configuration.","runtime.","permissions.","history.","maintenance.","notifications.","providers.","routing.","modules.","sources.","locations.")):
                if not self.management: raise AppError("Management service is unavailable.")
                pending.append((self.management.command,(action,copy.deepcopy(args),command_id)))
            elif action.startswith("updates."):
                if not self.update_manager: raise AppError("Update service is unavailable.")
                pending.append((self.update_manager.command, (action.split(".")[1],)))
            elif action == "settings.update":
                patch = args["patch"]
                if set(patch) - {"preferredVoice", "fallbackVoice", "bundle", "workspace", "notifications", "updates"}:
                    raise AppError("Unknown setting.")
                for key in ("preferredVoice", "fallbackVoice"):
                    if key in patch and patch[key] not in {"gpt-live-1", "gpt-realtime-2.1"}:
                        raise AppError("Select a supported voice model.")
                if "bundle" in patch and (not isinstance(patch["bundle"],str) or not patch["bundle"].strip()):
                    raise AppError("Enter a default bundle.")
                if "updates" in patch:
                    options = patch["updates"]
                    if not isinstance(options,dict) or set(options)-{"autoCheck","autoInstall","intervalHours"}:
                        raise AppError("Invalid update settings.")
                    for key in ("autoCheck","autoInstall"):
                        if key in options and type(options[key]) is not bool: raise AppError("Update switches must be true or false.")
                    if "intervalHours" in options and (type(options["intervalHours"]) is not int or not 1 <= options["intervalHours"] <= 168):
                        raise AppError("Check interval must be between 1 and 168 hours.")
                    patch = {**patch, "updates": {**self.state["settings"].get("updates",{}), **options}}
                    if patch["updates"].get("autoInstall") and not patch["updates"].get("autoCheck"):
                        raise AppError("Enable automatic checking before automatic installation.")
                if {"preferredVoice", "fallbackVoice", "bundle"}.intersection(patch):
                    from .preferences import SettingsStore
                    def save_shared(settings):
                        for old, key in (("preferredVoice", "preferred_model"), ("fallbackVoice", "fallback_model")):
                            if old in patch:
                                settings.setdefault("voice", {})[key] = patch[old]
                        if "bundle" in patch:
                            settings.setdefault("bundle", {})["active"] = patch["bundle"]
                    SettingsStore(self.data_dir).update(self.state["settings"]["workspace"], "global", save_shared)
                self.state["settings"].update(copy.deepcopy(patch))
                self._refresh_shared_preferences()
            elif action in {'theme.apply', 'theme.preview', 'theme.revert'}:
                from .canvas_apps import theme_command
                theme_command(self, action, args)
            elif action == "theme.reset":
                self.state["theme"] = {"name": "Amplifier Unified", "css": self.default_theme()}
            elif action == "notification.request":
                effects.append({"type": "notification.request"})
            elif action in {"call.start", "call.mute", "call.end"}:
                call_args = dict(args)
                if action == "call.start":
                    session = self._session()
                    if self.voice_service and not self.voice_service.api_key:
                        raise AppError("Set OPENAI_API_KEY in the terminal environment to enable calls.")
                    session["historyManaged"] = False
                    self.state["voice"].update({"status": "connecting", "sessionId": session["id"]})
                    call_args["sessionId"] = session["id"]
                elif action == "call.mute":
                    self.state["voice"]["muted"] = args["muted"]
                elif self.voice_service:
                    pending.append((self._end_call, ()))
                self.state["voice"]["command"] = {"id": command_id or str(uuid.uuid4()), "type": action, "args": call_args}
                effects.append({"type": action, "args": call_args, **args})
            if action in {'session.create','session.fork','session.recover','message.edit'}:
                from .naming import persist
                persist(self.data_dir,session)
            if action in {'session.fork','session.recover'} or action == 'message.edit' and args.get('mode','fork')=='fork':
                fork_artifacts(self.state,source['id'],session)
                self.outputs.fork(source['id'],session)
            if client_id is None and previous_scope[0] != self.state.get('selectedSessionId'):
                previous=next((row for row in self.state['sessions'] if row['id']==previous_scope[0]),None)
                if previous is not None and (previous_draft or 'draft' in previous):previous['draft']=previous_draft
                selected=next((row for row in self.state['sessions'] if row['id']==self.state.get('selectedSessionId')),None)
                self.state['view']['draft']=selected.get('draft','') if selected else self.state['view'].get('newChatText','')
            if previous_scope != (self.state.get('selectedSessionId'),self.state.get('selectedWorkspaceId')):
                restore(self.state,self.db,open_panel=previous_open)
            if client_id is not None and previous_scope[0] != self.state.get('selectedSessionId'):
                self.clients.reconcile(client_id)
            if previous_scope[1] != self.state.get('selectedWorkspaceId') or action in {'workspace.select', 'workspace.add', 'workspace.create', 'session.select'}:
                # An explicit selection reveals its folder, including returning
                # to a workspace whose old browse scope otherwise still matches.
                from .workspace_navigation import NAV_KEYS
                for key in NAV_KEYS | {'navWorkspaceBrowseFor', 'navWorkspaceAncestorsOpen'}:
                    self.state['view'].pop(key, None)
            view_action = args.get('action') if action == 'canvas.views.command' else action
            if action not in {'session.pin', 'canvas.views.inspect', 'canvas.views.status'} and view_action != 'canvas.snapshot' and not action.startswith(('diagnostics.','view.','attention.','canvas.snapshot')):
                artifact_id = self.state.get('canvas', {}).get('id')
                if action.startswith('canvas.views.'):
                    artifact_id = args.get('resourceId')
                elif action.startswith('canvas.apps.'):
                    artifact_id = args.get('id', (diagnostic_result or {}).get('id'))
                owner_id = args.get('sessionId',self.state.get('selectedSessionId'))
                if action.startswith('canvas.views.'):
                    artifact = next((row for row in self.state.get('canvasArtifacts', []) if row['id'] == artifact_id), {})
                    owner_id = artifact.get('sessionId')
                owner=next((s for s in self.state['sessions'] if s['id']==owner_id),{})
                if owner.get('historyManaged'):
                    owner = {}  # Browsing must not append to the observed CLI capture.
                stream='canvas' if action.startswith('canvas.') else 'smartTools' if action.startswith('smartTools.') else 'sessions' if action.startswith('session.') else 'workers' if action.startswith('worker.') else 'app'
                self.diagnostics.record(stream,{'event':'app:action','data':{'action':action,'origin':origin,'commandId':command_id,'sessionId':owner.get('id'),'runtimeSessionId':owner.get('runtimeSessionId'),'artifactId':artifact_id if stream=='canvas' else None}},session_id=owner.get('runtimeSessionId') or owner.get('id'),workspace=owner.get('workspace'))
            self.state["events"].append({"id": command_id, "action": action, "origin": origin, "at": time.time()})
            self.state["events"] = self.state["events"][-200:]
            for effect in effects:
                effect.update({"id": str(uuid.uuid4()), "createdAt": time.time(), "origin": origin, **({"clientId": client_id} if client_id else {})})
            self.state.setdefault("deviceCommands", []).extend(copy.deepcopy([effect for effect in effects if effect["type"] != "download" or view_action == "canvas.download"]))
            self.state["deviceCommands"] = self.state["deviceCommands"][-20:]
            receipt = {"accepted": True, "revision": self.state["revision"] + 1, "effects": effects}
            if action in {"worker.message", "worker.stop", "worker.steer"}:
                receipt.update(delivery="requested", commandAction=action, target={"sessionId": args["sessionId"], "workerId": args["id"]}, completed=False, effectsState="not_rolled_back")
            if action == 'session.create':receipt['sessionId']=session['id']
            if action == 'conversation.send':receipt['delivery']='sending'
            if action == 'conversation.retry':receipt['result']={'delivery':'sending', 'message':'The saved message is being checked and sent. No additional resend was started.'}
            if diagnostic_result is not None:receipt['result']=diagnostic_result
            if action == "locations.create" or action.startswith("smartTools.") and action != "smartTools.context":
                receipt["operationId"] = command_id
            if action in {"feedback.submit", "feedback.get", "feedback.comment"}:
                receipt["requestId"] = args['requestId']
            if command_id:
                self.db.execute("INSERT INTO commands VALUES (?,?,?)", (command_id, fingerprint, json.dumps(receipt)))
            if defer_publish:
                # Admission and the queued receipt commit together. If the host
                # exits before starting the task, restart can mark it interrupted.
                queued = {'id': command_id, 'action': action, 'origin': 'app',
                          'target': {'canvasId': args['canvasId'], 'name': args['name']},
                          'status': 'queued', 'createdAt': time.time(), 'updatedAt': time.time()}
                self.state['smartTools']['operations'].append(queued)
                self.smart_tools.persist_operation(queued)
            self._publish_smart_tool_update(defer_publish=defer_publish)
            result = {**receipt, **({'state': self.browser_state()} if include_state else {})}
        for fn, values in pending:
            if action == "conversation.send" and fn == self._send or action == "message.edit" and fn == self._edit_current or action in {"question.answer", "worker.message", "conversation.delivery", "conversation.retry"}:
                # Runtime progress callbacks acquire self.lock. Admission must
                # run outside it, and the HTTP receipt waits for the actual ack.
                try:
                    acknowledged = await fn(*values)
                except Exception:
                    if action == "worker.message" and command_id:
                        async with self.lock:
                            result["delivery"] = "unknown"
                            saved = {key: value for key, value in result.items() if key != "state"}
                            self.db.execute("UPDATE commands SET receipt=? WHERE id=?", (json.dumps(saved), command_id))
                            self.db.commit()
                    if action == 'conversation.retry' and command_id:
                        async with self.lock:
                            receipt['result'] = {'delivery': 'unknown', 'message': 'Delivery could not be confirmed. Check delivery before trying again.'}
                            self.db.execute('UPDATE commands SET receipt=? WHERE id=?', (json.dumps(receipt), command_id))
                            self.db.commit()
                    raise
                if action in {'conversation.delivery', 'conversation.retry'}:
                    result['result'] = acknowledged
                    async with self.lock:
                        if command_id:
                            receipt['result'] = acknowledged
                            self.db.execute('UPDATE commands SET receipt=? WHERE id=?', (json.dumps(receipt), command_id))
                            self.db.commit()
                if action == "worker.message":
                    result["result"] = acknowledged
                    result["delivery"] = "accepted"
                    if command_id:
                        async with self.lock:
                            saved = {key: value for key, value in result.items() if key != "state"}
                            self.db.execute("UPDATE commands SET receipt=? WHERE id=?", (json.dumps(saved), command_id))
                            self.db.commit()
            else:
                kwargs = {'defer_publish': True} if defer_publish else {}
                task = self._task(self._guard(fn, values, kwargs))
                if action.startswith("smartTools."):
                    self.smart_tool_tasks.add(task)
                    task.add_done_callback(self.smart_tool_tasks.discard)
                    self.smart_tool_requests[command_id] = task
                    task.add_done_callback(lambda finished, identity=command_id: self.smart_tool_requests.pop(identity, None))
        if action == 'conversation.send':result['delivery']='accepted'
        if action == 'question.answer':result['result'] = self.questions.read('question.read', args)
        return {**result, **({'state': self.browser_state()} if include_state else {})}

    async def wait_smart_tool(self, identity, timeout=300):
        """Wait for the original admitted operation; disconnect never replays it."""
        task = self.smart_tool_requests.get(identity)
        if task:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout)
            except TimeoutError:
                pass
        return self.smart_tools.operation(identity) or {'id': identity, 'status': 'pending'}

    async def _regenerate_name(self, source, base):
        from .naming import accept_generated, directory_for, refresh
        identity = source['id']
        active = True
        async def emit(kind, payload):
            # Naming preparation cannot turn a saved error into a new failed or
            # active conversation. Actual naming telemetry remains observable.
            if active and kind in {'runtime.status', 'runtime.error', 'session.naming'}:
                return
            await self.on_runtime_event(kind, payload)
        try:
            await self.runtime.start(source, emit)
            candidate = await self.runtime.control(identity, 'session.naming', {})
            async with self.lock:
                session = self._session(identity)
                # Preparation can be slow. Protect edits made after the click,
                # including edits before the naming model took its snapshot.
                same_base = all(base.get(key, 0) == candidate.get(key, 0)
                                for key in ('name_revision', 'name_policy_revision'))
                accepted = same_base and accept_generated(directory_for(self.data_dir, session), candidate, explicit=True)[1]
                refresh(self.data_dir, session)
                session['naming'] = {'status': 'ready' if accepted else 'conflict',
                    **({} if accepted else {'error': 'The name or Auto preference changed. Your newer choice was kept.'})}
                self._publish()
        except Exception as exc:
            async with self.lock:
                self._session(identity)['naming'] = {'status': 'error', 'error': str(exc)}
                self._publish()
        finally:
            active = False

    async def _guard(self, fn, args, kwargs=None):
        try:
            await fn(*args, **(kwargs or {}))
        except Exception as exc:
            from .runtime import RuntimeOperationPending
            if isinstance(exc, RuntimeOperationPending) and exc.operation == 'send' and fn == self._send:
                # _send already published this input's uncertain delivery. A
                # missing acknowledgement does not terminate its live turn.
                return
            if (kwargs or {}).get('defer_publish'):
                # A failure outside the manager's effect/result handler must not
                # leave an accepted interactive call polling a queued receipt.
                async with self.lock:
                    operation = self.smart_tools.operation(args[2])
                    if operation and operation.get('status') in {'queued', 'running'}:
                        operation.update(status='interrupted', updatedAt=time.time(),
                                         error='No verified tool outcome was recorded. Work was not replayed.')
                        self.smart_tools.persist_operation(operation)
                        self._publish_smart_tool_update(defer_publish=True)
            sid = args[0].get("id") if args and isinstance(args[0], dict) else args[0] if args else None
            if isinstance(exc, AppError) and exc.code == 'session_busy':
                return  # The rejected admission already published its ownership state.
            from .runtime import SessionInUseError
            if isinstance(exc, SessionInUseError):
                await self.on_runtime_event('runtime.ownership', {'sessionId': sid, 'status': 'blocked', 'owner': exc.owner})
                return
            await self.on_runtime_event("runtime.error", {"sessionId": sid, "error": str(exc)})

    async def history_page(self, session_id, before, limit):
        await self.history.load(session_id, before=before, limit=limit)

    async def _edit_current(self, source):
        edit = source['historyEdit']
        from .history_revision import apply_revision, receipt_path
        from .runtime import SessionInUseError
        try:
            await self.runtime.start(source, self.on_runtime_event)
            result = await self.runtime.control(source['id'], 'history.edit', {
                'source':source,'messageId':edit['messageId'],'operationId':edit['operationId'],
                'text':edit['text'],'attachments':edit['attachments']})
            async with self.lock:
                current = self._session(source['id'])
                apply_revision(self, current, result)
                current['configurationBusy'] = False
                if (self.state['view'].get('messageEdit') or {}).get('messageId') == edit['messageId']:
                    self.state['view']['messageEdit'] = None
                self._publish()
        except Exception as exc:
            async with self.lock:
                current = self._session(source['id'])
                path = receipt_path(self.data_dir, source.get('runtimeSessionId') or source['id'], edit['operationId'])
                try:
                    if path.exists():
                        saved = json.loads(path.read_text())
                        if saved.get('phase') == 'committed':
                            apply_revision(self, current, saved['result'], interrupted=True)
                except (OSError, ValueError, KeyError, TypeError):
                    pass  # Keep interrupted evidence; never guess or replay.
                current['configurationBusy'] = False
                current['historyEdit'].update(phase='error', error=str(exc))
                if isinstance(exc, SessionInUseError):
                    from .session_ownership import blocked
                    blocked(current, exc.owner)
                elif current['historyEdit'].get('applied'):
                    current['status'] = 'error'
                    current['error'] = 'The edit was saved but its response was not confirmed. No work was automatically replayed.'
                receipt = {'accepted':False,'error':str(exc),'status':409,'code':'session_busy' if isinstance(exc,SessionInUseError) else 'edit_failed'}
                self.db.execute('UPDATE commands SET receipt=? WHERE id=?',(json.dumps(receipt),edit['operationId']))
                self._publish()
            raise AppError(str(exc),409,code=receipt['code']) from exc

    async def _check_delivery(self, sid, input_id):
        from .message_delivery import find_message
        async with self.lock:
            session = copy.deepcopy(self._session(sid))
        message = find_message(session, input_id)
        if message is None:
            return {'delivery': 'not_saved', 'message': 'This app has no saved copy. You can try sending the original message again with the same delivery identity.'}
        status = message.get('delivery', {}).get('status', 'unknown')
        if status != 'accepted' and self.runtime and hasattr(self.runtime, 'delivery'):
            try:
                evidence = await self.runtime.delivery(session, input_id)
                if evidence == 'accepted':
                    status = 'accepted'
            except Exception:
                pass  # A failed probe cannot prove that the input was not delivered.
        async with self.lock:
            current = self._session(sid)
            current_message = find_message(current, input_id)
            if current_message and current_message.get('delivery', {}).get('status') == 'accepted':
                status = 'accepted'
            if status == 'accepted':
                self._delivery(current, input_id, status)
                self._publish()
        return {'delivery': status, 'message': {
            'accepted': 'Amplifier received this message. It was not sent again.',
            'sending': 'The original send is still in progress. Nothing was sent again.',
        }.get(status, 'Delivery is still uncertain. Checking does not resend it. Sending again may repeat work if the earlier attempt ran.')}

    async def _retry_message(self, session, text, input_id, original_status, original_turn):
        result = await self._send(session, text, input_id, preserve_draft=True, retry=True)
        if isinstance(result, dict) and result.get('duplicate'):
            async with self.lock:
                current = self._session(session['id'])
                if current['status'] == 'working' and current.get('execution', {}).get('currentTurnId') == input_id:
                    current['status'] = original_status
                    self._activity(current, original_status, 'Delivery confirmed. Amplifier already received this message.')
                    turn = next(t for t in current['execution']['turns'] if t['id'] == input_id)
                    for key in ('phase', 'endedAt', 'retriedAt'):
                        if key in original_turn:
                            turn[key] = original_turn[key]
                        else:
                            turn.pop(key, None)
                    self._publish()
        return {'delivery': 'accepted', 'resent': not bool(isinstance(result, dict) and result.get('duplicate'))}

    def _delivery(self, session, input_id, status):
        message = next((row for row in session['messages'] if row.get('inputId') == input_id and row.get('role') == 'user'), None)
        row = self.db.execute('SELECT receipt FROM commands WHERE id=?', (input_id,)).fetchone()
        receipt = json.loads(row[0]) if row else {}
        message_bound = message is not None and 'delivery' in message
        # Voice delegation has no separate user bubble. Only its saved exact
        # input/session binding permits a receipt update without that message.
        receipt_bound = receipt.get('inputId') == input_id and receipt.get('sessionId') == session['id']
        if not message_bound and not receipt_bound:
            return
        if status == 'unknown' and (receipt.get('delivery') == 'accepted' or
                message_bound and message['delivery'].get('status') == 'accepted'):
            return
        if message_bound:
            message['delivery'] = {'status':status}
        if row:
            self.db.execute('UPDATE commands SET receipt=? WHERE id=?', (json.dumps({**receipt, 'delivery':status}), input_id))

    async def _send(self, session, text, input_id, previous_activity=None, preserve_draft=False, retry=False):
        if not self.runtime:
            raise AppError("The Amplifier runtime is unavailable.")
        from .runtime import RuntimeOperationPending, SessionInUseError
        try:
            session.setdefault('surfaceInputs', {}).setdefault(input_id, self.surface_context.bind_input(session['id']))
            sender = self.runtime.retry if retry else self.runtime.send
            send_result = await sender(session, text, input_id, self.on_runtime_event)
        except RuntimeOperationPending:
            async with self.lock:
                self._delivery(self._session(session['id']), input_id, 'unknown')
                self._publish()
            # A missing acknowledgement is not a failed turn. Keep the saved
            # input and live work; the exact late receipt can reconcile delivery.
            raise
        except SessionInUseError as exc:
            if retry:
                async with self.lock:
                    current = self._session(session['id'])
                    self._delivery(current, input_id, 'unknown')
                    from .session_ownership import blocked
                    from .execution import finish
                    blocked(current, exc.owner)
                    finish(current, 'stopped')
                    self._activity(current, 'stopped', 'Resend was not admitted. This conversation is owned elsewhere.')
                    self._publish()
                raise AppError('This conversation is owned elsewhere. Continue here before resending.', 409, code='session_busy') from exc
            async with self.lock:
                current = self._session(session["id"])
                current["messages"] = [
                    row for row in current["messages"] if row.get("inputId") != input_id
                ]
                if previous_activity is not None and current.get('recentActivityAt') == session.get('recentActivityAt'):
                    current['recentActivityAt'] = previous_activity
                execution = current.get("execution", {})
                execution["turns"] = [
                    row for row in execution.get("turns", []) if row.get("id") != input_id
                ]
                if execution.get("currentTurnId") == input_id:
                    execution["currentTurnId"] = (
                        execution["turns"][-1]["id"] if execution["turns"] else None
                    )
                from .session_ownership import blocked
                blocked(current, exc.owner)
                self.db.execute("UPDATE commands SET receipt=? WHERE id=?", (
                    json.dumps({"accepted": False, "error": str(exc), "status": 409, "code": "session_busy"}), input_id))
                self._publish()
            raise AppError(str(exc), 409, code="session_busy") from exc
        except Exception:
            async with self.lock:
                current = self._session(session['id'])
                self._delivery(current, input_id, 'unknown')
                needs_error = current['status'] != 'error'
                self._publish()
            if needs_error:
                await self.on_runtime_event('runtime.error', {
                    'sessionId': session['id'],
                    'error': 'Message delivery could not be confirmed. The conversation worker is unavailable. Work was not automatically replayed.',
                })
            raise
        async with self.lock:
            current = self._session(session["id"])
            self._delivery(current, input_id, 'accepted')
            sent = next((row for row in session["messages"] if row.get("inputId") == input_id), {})
            attached = {row["id"] for row in sent.get("attachments", [])}
            draft_attachments = self.clients.attachments(current)
            draft_attachments[:] = [row for row in draft_attachments if row["id"] not in attached]
            client = self.clients.record()
            if client is not None:
                if not preserve_draft and client.get('drafts', {}).get(current['id'], '').strip() == text.strip():
                    self.clients.draft(current['id'], '')
            elif (not preserve_draft and self.state["selectedSessionId"] == current["id"]
                    and self.state["view"].get("draft", "").strip() == text.strip()):
                self.state["view"]["draft"] = ""
            if not preserve_draft and current.get('draft', '').strip() == text.strip():
                current['draft'] = ''
            current.pop("lockOwner", None)
            self._publish()
        return send_result

    async def _end_call(self):
        try:
            await self.voice_service.end()
        except Exception as exc:
            await self.set_voice_status({"status": "error", "error": str(exc)})

    async def _stop(self, sid):
        if self.runtime:
            await self.runtime.stop(sid)
        await self.on_runtime_event("runtime.status", {"sessionId": sid, "status": "stopped"})

    async def record_voice_usage(self,session_id,call_id,response_id,model,usage,phase='completed'):
        from .voice_usage import normalize_voice_usage
        async with self.lock:
            try:session=self._session(session_id)
            except AppError:return
            identity='voice:'+call_id+':'+response_id
            tree=session.get('execution',{})
            existing=next((n for n in tree.get('nodes',[]) if n['id']==identity),None)
            if response_id != 'session' and (existing is None or existing.get('phase') != phase):
                from .chat_navigation import touch
                touch(session)
            turn_id=('voice:'+call_id) if response_id=='session' else existing.get('turnId') if existing else tree.get('currentTurnId')
            if not turn_id:turn_id='voice:'+call_id
            if not any(t['id']==turn_id for t in tree.get('turns',[])):
                previous=tree.get('currentTurnId')
                ensure_turn(session,turn_id,'Voice call · session usage' if response_id=='session' else 'Voice call')
                if previous:session['execution']['currentTurnId']=previous
            ingest_execution(session,{'id':identity,'turnId':turn_id,'sessionId':session_id,'rootSessionId':session_id,
                'kind':'llm','phase':phase,'label':model+' · voice','provider':'OpenAI voice','model':model,
                'startedAt':existing.get('startedAt',time.time()) if existing else time.time(),
                **({'endedAt':time.time(),'usage':normalize_voice_usage(usage)} if phase=='completed' else {})})
            if phase=='completed' and turn_id.startswith('voice:'):
                for turn in session['execution']['turns']:
                    if turn['id']==turn_id:turn.update(phase='completed',endedAt=time.time())
            self._publish()

    async def _takeover(self, session):
        from .runtime import SessionInUseError
        try:
            await self.runtime.takeover(session, self.on_runtime_event, session.get('lockOwner'))
            await self.history.load(session['id'])
            async with self.lock:
                current = self._session(session['id'])
                current.pop('lockOwner', None)
                current.pop('error', None)
                current['ownership'] = {'status': 'available'}
                current['status'] = 'ready'
                self._publish()
        except Exception as exc:
            async with self.lock:
                current = self._session(session['id'])
                if isinstance(exc, SessionInUseError):
                    from .session_ownership import blocked
                    blocked(current, exc.owner, detail=str(exc))
                else:
                    # Failure does not establish ownership. Keep the shared
                    # read-only gate until an explicit retry succeeds.
                    current['ownership'] = {'status': 'blocked', 'reason': 'takeover-failed', 'detail': str(exc)}
                    current.update(status='error', error=str(exc))
                self._publish()

    async def on_runtime_event(self, kind, payload):
        async with self.lock:
            try:
                session = self._session(payload.get("rootSessionId") or payload.get("sessionId"))
            except AppError:
                return
            self.diagnostics.runtime_event(kind,payload,session)
            from .chat_navigation import runtime_activity
            runtime_activity(session, kind, payload)
            if kind == 'history.revised':
                from .history_revision import apply_revision
                apply_revision(self, session, payload)
            elif kind == 'session.naming':
                from .naming import automatic,persist
                name=payload.get('name');description=payload.get('description')
                if automatic(session) and isinstance(name,str) and name.strip():
                    session.update(title=name.strip()[:200],titleSource='generated')
                if isinstance(description,str) and description.strip():session['description']=description.strip()[:1000]
                persist(self.data_dir,session,expected_revision=payload.get('nameRevision'))
            elif kind == 'session.naming.progress':
                from .naming import legacy
                from .host.storage import SessionStore
                directory=SessionStore.for_app(self.data_dir,session['workspace']).directory(session.get('runtimeSessionId') or session['id'])
                directory.mkdir(parents=True,exist_ok=True,mode=0o700)
                data=legacy(directory);data['naming_completed_inputs']=payload.get('completedInputs',[])
                SessionStore._atomic(directory/'naming.json',json.dumps(data))
            elif kind == "execution.event":
                ingest_execution(session,payload)
                if payload.get('failure') and payload.get('sessionId') in {session['id'], session.get('runtimeSessionId')} and payload.get('lifecycle') != 'background':
                    session['failure'] = {**payload['failure'], 'inputId': payload.get('turnId'), 'recordedAt': payload.get('endedAt')}
                    session.pop('health', None)
            elif kind == 'runtime.delivery':
                self._delivery(session, payload['inputId'], 'accepted')
            elif kind == "runtime.ended":
                self.operations.interrupted(session["id"])
                self.schedules.runtime_ended(session)
                # A turn may finish before naming does; only the runtime host
                # can confirm that no independent call can still be running.
                if payload.get("sessionId") in {session["id"], session.get("runtimeSessionId")} and not payload.get("backgroundOnly"):
                    settle_stream(session)
                finish_background(session,payload.get("backgroundCallIds",[]),payload.get("status","interrupted"))
            elif kind == 'runtime.ownership':
                if payload.get('status') == 'blocked':
                    from .session_ownership import blocked
                    blocked(session, payload.get('owner') or {}, keep_pending=True)
                else:
                    session['ownership'] = {key: payload[key] for key in ('status', 'source', 'detail') if key in payload}
                    session['status'] = 'read-only'
                    session.pop('lockOwner', None)
                if payload.get('status') == 'yielded':
                    settle_stream(session)
                    finish_execution(session, 'interrupted')
            elif kind == "runtime.warmth":
                session['preparation'] = {'status': payload['status']}
                if payload['status'] == 'warm' and session['status'] == 'ready':
                    session['status'] = 'idle'
            elif kind == "runtime.status":
                if payload.get('event') == 'input.delivered' and payload.get('inputId'):
                    self._delivery(session, payload['inputId'], 'accepted')
                # Provider requests/retries describe current work; only lifecycle
                # events or accepted input can start work. Late/background notices
                # must not lock a finished conversation's fork/edit controls.
                if payload.get('activityOnly') and session.get('status') not in {'working','starting'}:
                    return
                session["status"] = payload.get("status", "idle")
                if session["status"] == "idle": self.schedules.idle(session)
                # A successfully initialized session supersedes its old startup
                # failure. Idle/stopped alone do not prove recovery (providers
                # may report an error immediately before becoming idle).
                if session["status"] == "ready":
                    session.pop("error", None)
                    session.pop("moduleFailures", None)
                    if isinstance(session.get("health"), dict):
                        session["health"].pop("moduleFailures", None)
                    session['preparation'] = {'status': 'ready'}
                labels = {"starting": "Preparing your Amplifier session…", "working": "Waiting for the model response…", "ready": "Ready to work", "idle": "Ready", "stopped": "Stopped", "stopping": "Stopping work…"}
                activity = self._activity(session, payload.get("phase", session["status"]), payload.get("detail") or labels.get(session["status"], session["status"]))
                if session["status"] in {"idle", "stopped"}:
                    settle_stream(session)
                    activity["activeTools"] = []
                    finish_execution(session,"completed" if session["status"]=="idle" else "stopped")
                    if session.get("configurationPending") and session["status"]=="idle":self._task(self.refresh_configuration(session["id"]))
                elif activity["activeTools"] and not payload.get("detail"):
                    activity.update({"phase": "tools", "label": "Running tools"})
                if payload.get("phase") or payload.get("detail"):
                    session["progress"] = {key: payload[key] for key in ("phase", "detail", "elapsedSeconds") if key in payload}
                elif session["status"] != "starting":
                    session.pop("progress", None)
                if payload.get("report"):
                    session["runtimeReport"] = payload["report"]
                    if payload["report"].get("root_bundle"):
                        session["bundle"] = payload["report"]["root_bundle"]
                if payload.get("runtimeSessionId"):
                    session["runtimeSessionId"] = payload["runtimeSessionId"]
            elif kind == "runtime.error":
                settle_stream(session)
                session["status"] = "error"
                finish_execution(session,"error")
                session["errorAt"] = time.time()
                detail = str(payload.get("error") or payload.get("message") or "Runtime failed")
                if payload.get('moduleFailures'):
                    from .module_failures import ConfiguredModuleError
                    failure=ConfiguredModuleError(payload['moduleFailures'])
                    session['moduleFailures']=failure.failures
                    detail=str(failure)
                error_type = payload.get('errorType') or session.get('turnErrorType')
                session['errorType'] = error_type
                session['error'] = ('This turn exceeded the model context limit. Your conversation and saved surfaces are kept. '
                    'Inspect the current state and continue with a smaller, focused request; completed actions were not replayed.'
                    if error_type == 'ContextLengthError' else detail)
                session.pop('health', None)
                self._activity(session, "error", session["error"])["activeTools"] = []
            elif kind == "runtime.generation":
                scheduled_generation = self.schedules.generation(session, payload)
                event = {**payload, "at": time.time()}
                session.setdefault("generations", []).append(event)
                session["generations"] = session["generations"][-200:]
                if payload.get('event') == 'generation.started':
                    session.pop('turnErrorType', None)
                    session.pop('errorType', None)
                elif payload.get('event') == 'generation.failed':
                    session['turnErrorType'] = payload.get('error_type')
                if payload.get("event") == "generation.finished":
                    if not scheduled_generation and (not payload.get("rootSessionId") or payload.get("rootSessionId")==payload.get("sessionId")):
                        from .attention import completed
                        completed(session,event)
                    if self.management and not scheduled_generation:
                        self._task(self._notify_completion(copy.deepcopy(session),copy.deepcopy(payload)))
                    pending = payload.get("active_job_ids", [])
                    self._activity(session, "waiting-workers" if pending else "processing",
                        f"Waiting for {len(pending)} delegated tasks" if pending else "Response ready")
            elif kind == "assistant.message":
                original = next((m for m in reversed(session["messages"]) if m.get("inputId") == payload.get("inputId") and m["role"] == "user"), {})
                generation_id = payload.get("generationId")
                repeated = generation_id and any(message.get("generationId") == generation_id for message in session["messages"] if message["role"] == "assistant")
                if not repeated:
                    self._message(session, "assistant", payload.get("text", ""), "schedule" if payload.get("scheduled_monitor_only") else original.get("via", "call" if str(payload.get("inputId", "")).startswith("voice:") else "chat"), inputId=payload.get("inputId"), generationId=generation_id, source="amplifier", **({"streamId": session["streamingId"]} if session.get("streamingId") else {}))
                session.pop("streaming", None)
                session.pop("streamingId", None)
            elif kind == "assistant.delta":
                session.setdefault("streamingId", str(uuid.uuid4()))
                session["streaming"] = session.get("streaming", "") + payload.get("text", payload.get("delta", ""))
            elif kind == "worker.updated":
                self.schedules.worker(session, payload)
                worker = next((w for w in session["workers"] if w["id"] == payload.get("id")), None)
                if worker:
                    worker.update(payload)
                else:
                    worker = copy.deepcopy(payload)
                    task = self.coordination.task(session["id"])
                    worker["taskId"] = task.get("id")
                    session["workers"].append(worker)
                from .coordination import observe_worker
                observe_worker(worker, payload)
                self.operations.notify()
                retrying = payload.get("phase") == "retrying"
                activity = self._activity(session, "retrying" if retrying else "workers", "A worker is retrying a model request" if retrying else "Delegated work is reporting progress")
                activity["lastEvent"] = {"worker": payload.get("name", "Worker"), "status": payload.get("status"), "at": time.time()}
            elif kind == "approval.requested":
                session["approvals"].append({**payload, "status": "pending"})
                self._activity(session, "approval", "Waiting for your approval")
            elif kind == "approval.resolved":
                for approval in session["approvals"]:
                    if approval["id"] == payload.get("id"):
                        approval["status"] = payload.get("decision", "expired")
            else:
                event = {"type": kind, **payload, "at": time.time()}
                if kind == "runtime.tool":
                    activity = self._activity(session, "tools", "Running tools")
                    tools = activity["activeTools"]
                    identity = payload.get("callId") or payload.get("tool")
                    if payload.get("phase") == "pre":
                        if not any((t.get("callId") or t.get("tool")) == identity for t in tools):
                            tools.append({"callId": payload.get("callId"), "tool": payload.get("tool", "tool"), "startedAt": event["at"]})
                        activity["label"] = "Delegating work" if payload.get("tool") == "delegate" else "Using " + str(payload.get("tool") or "a tool")
                    elif payload.get("phase") in {"post", "error"}:
                        activity["activeTools"] = [t for t in tools if (t.get("callId") or t.get("tool")) != identity]
                        if not activity["activeTools"]:
                            activity.update({"phase": "model", "label": "Waiting for the model response…"})
                    activity["lastEvent"] = {"tool": payload.get("tool"), "phase": payload.get("phase"), "at": event["at"]}
                session.setdefault("runtimeEvents", []).append(event)
                session["runtimeEvents"] = session["runtimeEvents"][-100:]
            progress = kind == 'assistant.delta' or (
                kind == 'runtime.status' and (payload.get('activityOnly') or
                    payload.get('preparationProgress') and payload.get('status') == 'starting')) or (
                kind == 'execution.event' and payload.get('phase') in {'running', 'working', 'streaming'})
            if progress:
                self._publish_progress()
            else:
                self._publish()

    async def refresh_configuration(self,identity):
        async with self.lock:
            try:session=self._session(identity)
            except AppError:return
            if session.get('ownership', {}).get('status') in {'blocked', 'yielding', 'yielded', 'yield-failed', 'taking-over'}:return
            if session.get('historyManaged') or not session.get('configurationPending') or session.get('configurationBusy') or session['status'] not in {'idle','stopped','interrupted','error'}:return
            if any(w.get('persistent') and w.get('status') in {'idle','running','starting'} for w in session.get('workers',[])):return
            session['configurationBusy']=True
            self._publish()
        try:
            if self.runtime:await self.runtime.stop(identity)
            async with self.lock:
                session=self._session(identity);session['configurationPending']=False
                self._publish()
        finally:
            async with self.lock:
                try:self._session(identity)['configurationBusy']=False
                except AppError:pass
                self._publish()

    async def _notify_completion(self,session,generation):
        try:
            await self.management.notifications.send(session,generation)
        except Exception:
            async with self.lock:
                self.state['notificationError']='Notification delivery failed. Check notification settings.'
                self._publish()

    def state_resource(self, identity):
        from .state_storage import resource
        return resource(self.db, identity)

    async def app_bridge(self, operation, args, session_id):
        if operation == "questions.admit":
            async with self.lock:
                self._session(session_id)
                values = args.get("questionIds")
                if (not isinstance(values, list) or len(values) > 32
                        or any(not isinstance(q, str) or not 1 <= len(q) <= 200 for q in values)
                        or len(set(values)) != len(values)):
                    raise AppError("Invalid required question IDs")
                for question_id in values:
                    self.questions.answer_for_dependency(session_id, question_id)
                return {"admitted": True, "questionIds": values}
        if operation == "operations.observe":
            return await self.operations.observe(session_id, args["runtimeSessionId"], args["event"])
        if operation == "voice.visual.read":
            return self.voice_visual.read(session_id, args.get("captureId"))
        if operation == "outputs.image.read":
            async with self.lock:
                return self.outputs.image(session_id, args.get('id'), args.get('sha256'))
        if operation in {'context.manifest', 'context.read'}:
            bindings = args.get('_contextBindings', [])
            async with self.lock:
                if operation == 'context.manifest':
                    result = self.surface_context.manifest(session_id, bindings)
                    return {**result, 'inputIds': args.get('_contextInputs', [])}
                return self.surface_context.read(session_id, args, bindings)
        if operation == "capacity.admit":
            from .capacity import admission
            return await admission(self, session_id, args)
        if operation == "history":
            from .history_query import query_history
            return await query_history(self, args, session_id)
        if operation in {"get_state", "state.get"}:
            await self._flush_pending_progress()
            from .agent_state import read_state
            return read_state(self.state_context(), args, session_id=session_id, resolve=self.state_resource)
        if operation in {"list_actions", "actions.list"}:
            actions = self.get_actions()
            prefix = args.get('prefix', '')
            if not isinstance(prefix, str):
                raise AppError('Action prefix must be text.')
            return [item for item in actions if item['name'].startswith(prefix)]
        if operation in {"dispatch", "action.dispatch"}:
            from .agent_state import read_state
            action_args=copy.deepcopy(args.get('args',{}))
            if args['action'].startswith(('question.', 'task.', 'schedule.', 'capacity.')) or (args['action'] == 'runtime.control' and action_args.get('operation', '').startswith(('task.', 'capacity.'))):
                if action_args.get('sessionId', session_id) != session_id:
                    raise AppError('Task, question, and schedule actions must target the calling conversation.', 409)
                action_args['sessionId'] = session_id
            if args['action'].startswith('worktree.'):
                if action_args.get('sessionId', session_id) != session_id:
                    raise AppError('Worktree actions must target the calling task.', 409)
                action_args['sessionId'] = session_id
            if args['action'] == 'runtime.control' and str(action_args.get('operation', '')).startswith('native.'):
                if action_args.get('sessionId', session_id) != session_id:
                    raise AppError('Native provider actions belong to the calling conversation.', 409)
                action_args['sessionId'] = session_id
            if args['action'] == 'runtime.control' and action_args.get('operation') == 'tool.invoke':
                if action_args.get('sessionId', session_id) != session_id:
                    raise AppError('Tool invocation must target the calling conversation.', 409)
                action_args['sessionId'] = session_id
            if args['action'] == 'bundle.default' and action_args.get('scope') == 'workspace':
                action_args.setdefault('workspace', self._session(session_id)['workspace'])
            if args['action'].startswith(('operations.', 'kernels.')):
                if action_args.get('sessionId', session_id) != session_id:
                    raise AppError('Operation actions must target the calling conversation.', 409)
                action_args['sessionId'] = session_id
            if args['action'].startswith(('recall.','memory.')):
                if action_args.get('sessionId',session_id) != session_id:
                    raise AppError('Recall actions must identify the calling conversation.',409)
                action_args['sessionId'] = session_id
            if args['action'].startswith('voice.visual.'):
                if action_args.get('sessionId', session_id) != session_id:
                    raise AppError('Visual capture must target the calling conversation.', 409)
                action_args['sessionId'] = session_id
            if args['action'].startswith('canvas.apps.'):
                if action_args.get('sessionId', session_id) != session_id:
                    raise AppError('Surface actions must target the calling conversation.', 409)
                action_args['sessionId'] = session_id
            if args['action'].startswith('outputs.'):
                if action_args.get('sessionId',session_id)!=session_id:
                    raise AppError('Output actions must target the calling conversation.',409)
                action_args['sessionId']=session_id
            if args['action'] in {'canvas.show','smartTools.call','smartTools.open','runtime.dependencies','session.sharePreview','session.shareList'}:
                action_args.setdefault('sessionId',session_id)
            if args['action'] == 'session.export':
                action_args.setdefault('id', session_id)
            result = await self.dispatch(args["action"], action_args, origin="agent", command_id=args.get("id"), expected_revision=args.get("expectedRevision"), caller_session_id=session_id)
            await self._flush_pending_progress()
            if args['action'].startswith('canvas.apps.'):
                from .agent_state import surface_context
                context = surface_context(self.state_context(), session_id, self.clients.records)
            else:
                context = read_state(self.state_context(), {}, session_id=session_id, resolve=self.state_resource)
            return {**result, 'effects':[{'id':e.get('id'),'type':e.get('type')} for e in result.get('effects',[])], 'state':context}
        raise AppError("Unknown app bridge operation.")

    async def update_device(self, payload):
        bound = self.clients.current.get()
        if bound is not None and payload.get('clientId', bound) != bound:
            raise AppError('Device report belongs to another client.')
        client_id = bound or str(payload.get("clientId") or "browser")[:100]
        self.state["devices"][client_id] = {**payload, "updatedAt": time.time()}
        # Rendered controls are live observations, reset on startup. Agents read
        # them directly; no durable command or browser projection changed.

    async def record_voice_transcript(self, role, text, *, voice_id, item_id, append=False, session_id=None):
        async with self.lock:
            session = self._session(session_id)
            existing = next((m for m in session["messages"] if m.get("voiceId") == voice_id and m.get("voiceItemId") == item_id), None)
            if existing:
                existing["text"] = existing["text"] + text if append else text
                from .chat_navigation import touch
                touch(session)
            else:
                self._message(session, role, text, "call", voiceId=voice_id, voiceItemId=item_id, inputOrigin='voice')
            self._publish()

    async def set_voice_status(self, payload):
        async with self.lock:
            self.state["voice"].update(payload)
            if self.state["voice"].get("status") != "connected":
                self.voice_visual.revoke()
            self.voice_visual.publish()

    async def voice_delegate(self, text, command_id, session_id=None):
        # Persist acceptance before scheduling, just like typed commands. A repeated
        # provider event or reconnect must never execute the same tool request twice.
        input_context = await self.surface_context.checkpoint(self._session(session_id)['id'])
        async with self.lock:
            if work_paused(self.state):
                raise AppError("An ecosystem update is activating. Please retry in a moment.",409)
            session = self._session(session_id)
            if session.get("configurationBusy"):raise AppError("Applying conversation settings; retry shortly.",409)
            fingerprint = hashlib.sha256(json.dumps(["voice_delegate", session["id"], text]).encode()).hexdigest()
            previous = self.db.execute("SELECT fingerprint,receipt FROM commands WHERE id=?", (command_id,)).fetchone()
            if previous:
                if previous[0] != fingerprint:
                    raise AppError("This voice command ID was already used with different contents.", 409)
                return {**json.loads(previous[1]), "duplicate": True}
            receipt = {"accepted": True, "inputId": command_id, "sessionId": session["id"], "delivery": "sending"}
            self.db.execute("INSERT INTO commands VALUES (?,?,?)", (command_id, fingerprint, json.dumps(receipt)))
            session["status"] = "working"
            self._activity(session, "queued", "Sending voice request to Amplifier", reset=True)
            ensure_turn(session,command_id,text)
            self.voice_visual.bind_input(session["id"], command_id)
            session.setdefault('surfaceInputs', {})[command_id] = input_context
            session['surfaceInputs'] = dict(list(session['surfaceInputs'].items())[-16:])
            self._publish()
            snapshot = copy.deepcopy(session)
        self._task(self._guard(self._send, (snapshot, text, command_id)))
        return receipt

    async def wait_for_response(self, session_id, input_id=None, after_count=0, timeout=120):
        # Text blocks can precede tools. Only loop-live's identified completion
        # event proves the manager has finished a turn for this input.
        queue = self.subscribe()
        try:
            async with asyncio.timeout(timeout):
                while True:
                    session = self._session(session_id)
                    for event in session.get("generations", []):
                        if input_id and input_id not in event.get("input_ids", []):
                            continue
                        if event["event"] == "generation.finished":
                            return {key: event.get(key) for key in
                                ("text", "generation_id", "input_ids", "active_job_ids", "disposition")}
                        if event["event"] in {"generation.failed", "generation.detached"}:
                            raise AppError("The Amplifier turn ended without a completed response.")
                    if session["status"] == "read-only":
                        raise AppError("This conversation is in use elsewhere. Choose Continue here to resume.", 409, code="session_busy")
                    if session["status"] in {"error", "stopped", "interrupted"}:
                        raise AppError(session.get("error", "Execution ended before the response completed."))
                    await queue.get()
        finally:
            self.unsubscribe(queue)

    async def _diagnostics_test(self, identity, generation):
        try:result=await self.diagnostics.test(identity,generation)
        except ValueError:result={'phase':'error','message':'Save this destination before testing it.'}
        if result is None:return  # The destination changed while its test was running.
        async with self.lock:
            self.state['diagnostics']['results']=copy.deepcopy(self.diagnostics.results)
            self.state['diagnostics']['lastResult']={'action':'diagnostics.test',**result}
            self._publish()

    async def close(self):
        if self.closed:
            return
        self.closed = True
        lifecycle_tasks = [task for task in self._runtime_lifecycle_tasks
                           if task is not asyncio.current_task()]
        for task in lifecycle_tasks:
            task.cancel()
        if lifecycle_tasks:
            await asyncio.gather(*lifecycle_tasks, return_exceptions=True)
        await self.voice_visual.close()
        await self.schedules.close()
        await self.worktrees.close()
        async with self.runtime_lifecycle_lock:
            if self.runtime:
                await self.runtime.close()
        await self.warmup.close()
        await self.history.close()
        await self.event_log_view.close()
        if self.update_manager:
            await self.update_manager.close()
        if self.management and self.management.setup_manager:
            await self.management.setup_manager.close()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.smart_tools:
            await self.smart_tools.close()
        if self.management:
            await self.management.provider_catalog.close()
        await self.diagnostics.close()
        if getattr(self, '_progress_dirty', False):
            self._publish()
        else:
            self._save()
        await self.operations.close()
        self.schedules.store.close()
        await self.recall.close()
        self.db.close()
