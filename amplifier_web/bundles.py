"""App-owned bundle discovery, registrations, composition order, and export.

Discovery inspects Git blobs, never checks out or executes repository content.
Only declared bundle metadata is exposed; provider credentials stay on the host.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit
import uuid

import yaml

from .builtin_behaviors import SHELL_BEHAVIOR_URI, app_behaviors, resource_root

SNAPSHOT_VERSION = "1.0.0+amplifier-unified.snapshot.1"
MAX_DOCUMENT = 256 * 1024
MODULE_KEYS = {"session", "providers", "tools", "hooks", "agents", "context", "spawn"}
SECRET_KEYS = {"api_key", "apikey", "token", "access_token", "refresh_token", "id_token", "auth_token", "secret", "client_secret", "password", "passwd", "authorization", "cookie", "cookies", "credentials", "private_key", "bearer_token"}


def remote_source(value: str) -> tuple[str, str | None, str]:
    """Parse community git+https URIs without permitting local Git protocols."""
    if not isinstance(value, str) or len(value) > 2048 or any(ord(char) < 32 for char in value):
        raise ValueError("Enter a valid HTTPS Git repository URL.")
    raw = value.removeprefix("git+")
    parts = urlsplit(raw)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.query:
        raise ValueError("Use an HTTPS Git URL without embedded credentials or query parameters.")
    path, ref = parts.path, None
    if "@" in path:
        path, ref = path.rsplit("@", 1)
    if parts.hostname.lower() == "github.com":
        components = path.strip("/").split("/")
        if len(components) >= 4 and components[2] == "tree":
            path, ref = "/" + "/".join(components[:2]), components[3]
            tree_subdir = "/".join(components[4:])
        elif len(components) == 2:
            tree_subdir = ""
        else:
            raise ValueError("Use a GitHub repository URL or git+https URL with a ref and subdirectory.")
    else:
        tree_subdir = ""
    if ref is not None and (not re.fullmatch(r"[A-Za-z0-9_./-]{1,200}", ref) or ref.startswith(("-", ".")) or ".." in ref or ref.endswith(("/", "."))):
        raise ValueError("The Git branch, tag, or commit is invalid.")
    fragment = parse_qs(parts.fragment, keep_blank_values=True)
    if set(fragment) - {"subdirectory"} or len(fragment.get("subdirectory", [])) > 1:
        raise ValueError("Use #subdirectory=path for a bundle entry.")
    subdirectory = unquote(fragment.get("subdirectory", [tree_subdir])[0]).strip("/")
    if "\\" in subdirectory or any(part in {"..", "."} for part in PurePosixPath(subdirectory).parts):
        raise ValueError("Bundle paths cannot escape the repository.")
    if not path.strip("/"):
        raise ValueError("The Git repository path is missing.")
    return urlunsplit(("https", parts.netloc, path.rstrip("/"), "", "")), ref, subdirectory


def validate_uri(uri: str) -> str:
    if not isinstance(uri, str) or not uri.strip():
        raise ValueError("A bundle URI is required.")
    uri = uri.strip()
    if uri.startswith(("https://", "git+https://")):
        url, ref, subdirectory = remote_source(uri)
        return "git+" + url + ("@" + ref if ref else "") + ("#subdirectory=" + subdirectory if subdirectory else "")
    if re.fullmatch(r"[A-Za-z0-9_-]+(?::[A-Za-z0-9_./-]+)?", uri) and ".." not in uri:
        return uri
    raise ValueError("Use a registered bundle name or an HTTPS Git bundle URI.")


async def git(*args: str, cwd: Path | None = None, timeout: int = 60) -> bytes:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"}
    process = await asyncio.create_subprocess_exec("git", "-c", "protocol.file.allow=never", "-c", "protocol.ext.allow=never", *args,
        cwd=cwd, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout)
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    if process.returncode:
        raise ValueError("The Git repository or revision could not be read. Check access and the URL.")
    if len(output) > 8 * 1024 * 1024:
        raise ValueError("The repository index is too large to inspect safely.")
    return output


def document_metadata(raw: str, path: str) -> dict | None:
    if path.lower().endswith(".md"):
        if not raw.startswith("---"):
            return None
        blocks = raw.split("\n---", 2)
        if len(blocks) < 2:
            return None
        raw = blocks[0][3:]
    try:
        value = yaml.safe_load(raw)
    except yaml.YAMLError:
        return None
    if not isinstance(value, dict) or not ("bundle" in value or "includes" in value or MODULE_KEYS.intersection(value)):
        return None
    info = value.get("bundle") or {}
    if not isinstance(info, dict):
        return None
    name = str(info.get("name") or PurePosixPath(path).stem)[:100]
    description = str(info.get("description") or "")[:500]
    behavior = "behaviors" in PurePosixPath(path).parts or (PurePosixPath(path).name not in {"bundle.md", "bundle.yaml", "bundle.yml"} and "session" not in value)
    return {"name": name, "description": description, "kind": "behavior" if behavior else "standalone"}


def sanitize_export(value, path=(), secrets=None):
    """Keep placeholders; replace actual credentials and drop private host keys."""
    secrets = secrets if secrets is not None else []
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            key = str(key)
            if key.startswith("_") or key in {"keys", "keys_file", "keyring", "credential_store"}:
                continue
            normalized = key.lower().replace("-", "_")
            if normalized in SECRET_KEYS or normalized in {"token_file_path", "credential_file", "credentials_file"} or normalized.endswith(("_api_key", "_secret", "_password", "_token")):
                if isinstance(item, str) and re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", item):
                    result[key] = item
                    secrets.append(item[2:-1])
                else:
                    name = "AMPLIFIER_" + re.sub(r"[^A-Za-z0-9]+", "_", "_".join((*path, key))).upper()
                    result[key] = "${" + name + "}"
                    secrets.append(name)
            else:
                result[key] = sanitize_export(item, (*path, key), secrets)
        return result
    if isinstance(value, list):
        return [sanitize_export(item, (*path, str(index)), secrets) for index, item in enumerate(value)]
    if isinstance(value, str) and value.startswith(("git+https://", "https://", "http://")):
        parsed = urlsplit(value.removeprefix("git+"))
        if parsed.username or parsed.password or parsed.query:
            raise ValueError("An exported source contains credentials or private URL parameters; replace it with a portable credential-free source first.")
    return copy.deepcopy(value)


class BundleManager:
    def __init__(self, home: Path, *, store=None):
        if store is None:
            from .preferences import SettingsStore
            store = SettingsStore(home)
        self.home, self.store = Path(home), store
        self.lock = asyncio.Lock()

    @staticmethod
    def entries(settings: dict) -> list[dict]:
        # YAML's standard bundle fields decide activation and order. UI row
        # metadata can preserve labels/disabled entries but never veto CLI edits.
        metadata = settings.get("web_bundles", {}).get("entries") or []
        rows = []
        def entry(uri, role, name):
            previous = next((row for row in metadata if row.get("uri") == uri and row.get("role") == role and (role != "standalone" or row.get("name") == name)), {})
            return {**copy.deepcopy(previous), "id": previous.get("id") or uuid.uuid5(uuid.NAMESPACE_URL, role + ":" + uri + (":" + name if role == "standalone" else "")).hex,
                    "uri": uri, "name": previous.get("name", name) if role == "behavior" else name, "role": role, "enabled": True}
        for uri in app_behaviors(settings):
            if isinstance(uri, str):
                rows.append(entry(uri, "behavior", "Unified shell" if uri == SHELL_BEHAVIOR_URI else uri.split("/")[-1]))
        for name, uri in settings.get("bundle", {}).get("added", {}).items():
            if isinstance(uri, str):
                rows.append(entry(uri, "standalone", name))
        for row in metadata:
            if row.get("enabled") is False and not any(item["uri"] == row.get("uri") and item["role"] == row.get("role") and (item["role"] != "standalone" or item["name"] == row.get("name")) for item in rows):
                rows.append(copy.deepcopy(row))
        return rows

    @staticmethod
    def public_entries(settings):
        rows = BundleManager.entries(settings)
        for row in rows:
            uri = str(row.get("uri", ""))
            if uri.startswith(("https://", "git+https://")):
                parts = urlsplit(uri.removeprefix("git+"))
                if parts.username or parts.password or parts.query:
                    row["uri"] = "git+" + urlunsplit((parts.scheme, parts.hostname or "", parts.path, "", parts.fragment))
                    row["sourceNeedsCredentials"] = True
        return rows

    @staticmethod
    def save_entries(settings: dict, entries: list[dict], excluded=None):
        settings.setdefault("web_bundles", {}).update(version=1, entries=entries)
        if excluded is not None:
            settings["web_bundles"]["excluded"] = sorted(excluded)
        bundle = settings.setdefault("bundle", {})
        bundle["app"] = [row["uri"] for row in entries if row["role"] == "behavior" and row["enabled"]]
        bundle["added"] = {row["name"]: row["uri"] for row in entries if row["role"] == "standalone" and row["enabled"]}
        return settings

    async def discover(self, value: str) -> dict:
        url, ref, subdirectory = remote_source(value)
        with tempfile.TemporaryDirectory(prefix="amplifier-bundles-") as directory:
            root = Path(directory) / "repo"
            await git("clone", "--depth=1", "--filter=blob:none", "--no-checkout", "--", url, str(root), timeout=90)
            if ref:
                await git("fetch", "--depth=1", "origin", ref, cwd=root, timeout=60)
                commit = (await git("rev-parse", "FETCH_HEAD", cwd=root)).decode().strip()
            else:
                commit = (await git("rev-parse", "HEAD", cwd=root)).decode().strip()
            records = (await git("ls-tree", "-r", "-z", commit, cwd=root)).split(b"\0")
            candidates = []
            if len(records) > 25000:
                raise ValueError("The repository contains too many files for bundle discovery.")
            for record in records:
                if not record or len(candidates) >= 200:
                    continue
                info, raw_path = record.split(b"\t", 1)
                mode, kind, object_id = info.decode().split()
                path = raw_path.decode("utf-8", errors="strict")
                if mode not in {"100644", "100755"} or kind != "blob":
                    continue
                if subdirectory and path != subdirectory and not path.startswith(subdirectory.rstrip("/") + "/"):
                    continue
                if not path.lower().endswith((".yaml", ".yml", "/bundle.md")) and path.lower() != "bundle.md" and not ("behaviors" in PurePosixPath(path).parts and path.lower().endswith(".md")):
                    continue
                size = int((await git("cat-file", "-s", object_id, cwd=root)).strip())
                if size > MAX_DOCUMENT:
                    continue
                content = (await git("cat-file", "blob", object_id, cwd=root)).decode("utf-8", errors="replace")
                meta = document_metadata(content, path)
                if meta:
                    candidates.append({**meta, "path": path, "uri": "git+" + url + "@" + commit + "#subdirectory=" + path, "revision": commit})
            candidates.sort(key=lambda row: (row["path"].count("/"), row["path"]))
            return {"url": url, "revision": commit, "candidates": candidates, "classification": "advisory"}

    async def perform(self, action: str, args: dict, *, effective_config=None, root_bundle=None, provenance=None, resources=None) -> dict:
        workspace = args.get("workspace") or str(Path.cwd())
        scope = args.get("scope", "global")
        if action == "bundle.discover":
            return {"discovery": await self.discover(args["url"])}
        if action in {"bundle.export", "bundle.save"}:
            settings = self.store.read(workspace, scope)
            plan = effective_config.get("plan", effective_config) if isinstance(effective_config, dict) else None
            exported = self.export_document(settings, effective_plan=plan, root_bundle=root_bundle, name=args.get("name", "custom-amplifier"), description=args.get("description", ""), provenance=provenance, resources=resources)
            if action == "bundle.export":
                return exported
            from .host.config import write_private
            filename = exported["filename"]
            name = filename.removesuffix(".md")
            target = self.store.shared_home / "bundles" / filename
            def register(current):
                entries = self.entries(current)
                existing = next((row for row in entries if row["role"] == "standalone" and row["name"] == name), None)
                if existing and existing["uri"] != str(target):
                    raise ValueError("That bundle name is already registered to another source.")
                write_private(target, exported["content"])
                if existing:
                    existing["enabled"] = True
                else:
                    entries.append({"id": uuid.uuid4().hex, "uri": str(target), "name": name, "role": "standalone", "enabled": True})
                return self.save_entries(current, entries)
            updated = self.store.update(workspace, scope, register)
            return {"bundles": self.public_entries(updated), "saved": {"name": name, "uri": str(target), "filename": filename}, "export": exported}
        async with self.lock:
            settings = self.store.read(workspace, scope)
            if action == "bundles.list":
                from .host.config import load_config
                config = load_config(workspace, home=self.home)
                path = config.registry_home / 'registry.json'
                registry = json.loads(path.read_text()).get('bundles', {}) if path.exists() else {}
                names = {name for name,row in registry.items() if isinstance(row,dict) and row.get('is_root')
                         and row.get('uri') != resource_root().as_uri()}
                names.update(config.registrations)
                disabled = {row['name'] for row in self.entries(settings) if row.get('role')=='standalone' and row.get('enabled') is False}
                from .bundle_selection import catalog_entry
                return {"bundles": self.public_entries(settings), "registeredBundles":[catalog_entry(name) for name in sorted(names-disabled)]}
            def mutate(current):
                entries = self.entries(current)
                excluded = set(current.get("web_bundles", {}).get("excluded", []))
                if action == "bundles.add":
                    uri = validate_uri(args["uri"])
                    role = args.get("role", "behavior")
                    if role not in {"behavior", "standalone"}:
                        raise ValueError("Choose behavior or standalone.")
                    name = args.get("name") or PurePosixPath(uri.split("#subdirectory=")[-1]).stem
                    name = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-")[:100] or "bundle"
                    existing = next((row for row in entries if row["uri"] == uri and row["role"] == role), None)
                    if existing:
                        existing.update(enabled=True, name=name)
                    else:
                        if role == "standalone" and any(row["role"] == role and row["name"] == name for row in entries):
                            raise ValueError("That standalone bundle name is already registered; choose another name.")
                        entries.append({"id": uuid.uuid4().hex, "uri": uri, "name": name, "role": role, "enabled": True})
                    excluded.discard(uri)
                else:
                    index = next((index for index, row in enumerate(entries) if row["id"] == args.get("id")), None)
                    if index is None:
                        raise ValueError("The registered bundle no longer exists.")
                    if action == "bundles.remove":
                        excluded.add(entries[index]["uri"])
                        entries.pop(index)
                    elif action == "bundles.toggle":
                        entries[index]["enabled"] = bool(args["enabled"])
                    elif action == "bundles.move":
                        if 'beforeId' in args:
                            before=args['beforeId']
                            if before is not None and not any(row['id']==before for row in entries):raise ValueError('Refresh the bundle list before reordering.')
                            if before!=args['id']:
                                row=entries.pop(index)
                                target=next((i for i,item in enumerate(entries) if item['id']==before),len(entries))
                                entries.insert(target,row)
                        else:
                            direction=args.get('direction')
                            if direction not in {'up','down'}:raise ValueError('Choose up or down.')
                            target=index+(-1 if direction=='up' else 1)
                            if 0<=target<len(entries):entries[index],entries[target]=entries[target],entries[index]
                    else:
                        raise ValueError("Unknown bundle operation.")
                self.save_entries(current, entries, excluded)
                return current
            updated = self.store.update(workspace, scope, mutate)
            # Shared SettingsStore may return the committed mapping or only write it.
            return {"bundles": self.public_entries(updated if isinstance(updated, dict) else self.store.read(workspace, scope)), "scope": scope, "takesEffect": "new_sessions"}

    def export_document(self, settings: dict, *, effective_plan=None, root_bundle=None, name="custom-amplifier", description="", provenance=None, resources=None) -> dict:
        """Flatten the prepared plan and static resources into one community bundle.

        Deliberately no includes: Foundation merges module lists additively, so
        including the old root would restore modules the user disabled.
        """
        if not isinstance(effective_plan, dict) or not isinstance(resources, dict):
            raise ValueError("Prepare the session before exporting its effective bundle and resources.")
        if resources.get("errors"):
            raise ValueError("Bundle resources cannot be exported: " + "; ".join(resources["errors"]))
        warnings = list(resources.get("warnings", []))
        namespaces = {row["namespace"]: row for row in resources.get("namespaces", [])}
        contexts = {row["name"].removeprefix("@"): row for row in resources.get("context", [])}
        mention = re.compile(r"@([A-Za-z0-9_-]+):([A-Za-z0-9_./-]+)")
        used = set()

        def expand(text, stack=()):
            def replace(match):
                key = match[1] + ":" + match[2]
                row = contexts.get(key)
                if row is None:
                    raise ValueError("Missing portable bundle resource: " + key)
                if key in stack:
                    return "[Context already included: " + key + "]"
                used.add(key)
                return "\n<!-- Bundle context: " + key + " -->\n" + expand(row["text"], (*stack, key)) + "\n"
            return mention.sub(replace, text or "")

        def portable_reference(value):
            match = mention.fullmatch(value)
            if match:
                namespace = namespaces.get(match[1])
                if not namespace:
                    raise ValueError("Unknown resource namespace: " + match[1])
                uri = namespace.get("uri", "")
                base = namespace.get("subdirectory", "")
                # Namespace roots can begin below the Git repository root.
                url, ref, subdir = remote_source(uri)
                base = base or subdir
                if base.endswith((".md", ".yaml", ".yml")):
                    base = str(PurePosixPath(base).parent)
                path = str(PurePosixPath(base) / match[2])
                if ".." in PurePosixPath(path).parts:
                    raise ValueError("A resource path escapes its namespace.")
                return "git+" + url + ("@" + ref if ref else "") + "#subdirectory=" + path
            for row in namespaces.values():
                local = row.get("localRoot")
                if local and (value == local or value.startswith(local.rstrip("/") + "/")):
                    suffix = value[len(local):].lstrip("/")
                    return portable_reference("@" + row["namespace"] + ":" + suffix)
            return value

        sources = settings.get("sources", {}).get("modules", {})
        def portable(node, key=""):
            if isinstance(node, dict):
                if node.get("module") and node.get("enabled") is False:
                    return None
                if node.get("module") == "hooks-routing":
                    node = copy.deepcopy(node)
                    matrix = resources.get("routingMatrix")
                    if matrix:
                        if "baseRoles" not in matrix:
                            raise ValueError("The routing bundle does not expose the shipped matrix needed for a portable export.")
                        roles = copy.deepcopy(matrix["roles"])
                        for role in matrix["baseRoles"]:
                            roles.setdefault(role, {"description":"Unavailable in exported routing policy", "candidates":[]})
                        node.setdefault("config", {}).update(default_matrix="balanced", overrides=roles)
                        node["config"].pop("custom_routing_dirs", None)
                    elif node.get("config", {}).get("custom_routing_dirs"):
                        raise ValueError("The active routing policy cannot be flattened; inspect its resolver before exporting.")
                result = {k: portable(v, k) for k, v in node.items() if k != "enabled"}
                source = result.get("source")
                if isinstance(source, str) and (source.startswith(("/", "file:", "~")) or "runtime_deps/vendor" in source):
                    replacement = sources.get(result.get("module"))
                    if isinstance(replacement, str) and replacement.startswith("git+https://"):
                        result["source"] = replacement
                    elif result.get("module") == "loop-live":
                        result["module"] = "loop-streaming"
                        result["source"] = "git+https://github.com/microsoft/amplifier-module-loop-streaming@603aa6eefacd28367fc886d58eb89de993c58dea"
                        result.get("config", {}).pop("configured_bundle", None)
                        result.get("config", {}).pop("background_delegate", None)
                        warnings.append("Amplifier Unified reapplies its live engine when this bundle loads; other hosts use the portable streaming engine.")
                    else:
                        raise ValueError("A module has a local source without a portable Git equivalent.")
                return result
            if isinstance(node, list):
                return [value for item in node if (value := portable(item, key)) is not None]
            if isinstance(node, str):
                if key in {"instruction", "instructions", "system_prompt"}:
                    return expand(node)
                value = portable_reference(node)
                if mention.search(value):
                    raise ValueError("An unresolved namespace reference remains in " + key)
                if key in {"source", "sources", "path", "paths"} and value.startswith(("/", "~/", "file:")):
                    # Module source is handled by the parent mapping above.
                    if key != "source":
                        raise ValueError("A configured resource uses a local path with no portable source.")
                return value
            return copy.deepcopy(node)

        document = {"bundle": {"name": re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-")[:100] or "custom-amplifier", "version": SNAPSHOT_VERSION, "description": description}}
        for key, value in effective_plan.items():
            if key in MODULE_KEYS - {"context"}:
                document[key] = portable(value, key)
        # Keep literal environment references when inspection redacts them.
        def restore(current, raw):
            if not isinstance(current, dict) or not isinstance(raw, dict): return
            for key, value in current.items():
                if isinstance(value, dict): restore(value, raw.get(key))
                elif isinstance(value, str) and value in ("[REDACTED]", "<redacted>"):
                    candidate = raw.get(key)
                    if isinstance(candidate, str) and re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", candidate): current[key] = candidate
        for section in ("providers", "tools", "hooks"):
            original = settings.get("config", {}).get(section, [])
            for row in document.get(section, []):
                identity = row.get("id") or row.get("instance_id") or row.get("module")
                source = next((item for item in original if (item.get("id") or item.get("instance_id") or item.get("module")) == identity), {})
                restore(row, source)
        body = expand(resources.get("instruction", ""))
        for key, row in contexts.items():
            if key not in used:
                used.add(key)
                body += "\n\n<!-- Bundle context: " + key + " -->\n" + expand(row["text"], (key,))
        required = []
        clean = sanitize_export(document, secrets=required)
        # Provenance is declarative metadata, never executable includes.
        origins = [{"namespace": row["namespace"], "uri": row.get("uri", "")} for row in namespaces.values()]
        safe_provenance = sanitize_export({"root": root_bundle, "sources": origins})
        clean["bundle"]["description"] = description or "Customized Amplifier bundle"
        output = "---\n" + yaml.safe_dump(clean, sort_keys=False, allow_unicode=True) + "---\n\n<!-- Amplifier Unified provenance: " + json.dumps(safe_provenance, ensure_ascii=True).replace("--", "\u002d\u002d") + " -->\n\n" + body.strip() + "\n"
        return {"filename": clean["bundle"]["name"] + ".md", "content": output, "mimeType": "text/markdown", "requiredEnvironment": sorted(set(required)), "includes": [], "warnings": list(dict.fromkeys(warnings))}
