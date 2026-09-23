"""Fixed helper for explicit setup preflight or call-scoped voice observation.

No target arguments, shell, tool execution, permission prompt or persistent
process. The optional computer-use library owns OS observation.
"""
import json
import sys


def main():
    operation = sys.argv[1] if len(sys.argv) == 2 else None
    if operation not in {"status", "capture"}:
        raise SystemExit(2)
    try:
        from amplifier_module_tool_computer_use.foreground import local_observer, ForegroundUnavailable
    except ImportError:
        result = {"available": False, "status": "unavailable", "code": "backend_not_installed"}
    else:
        try:
            observer = local_observer()
            result = observer.status() if operation == "status" else observer.capture()
        except ForegroundUnavailable as exc:
            result = {"available": False, "status": "unavailable", "code": exc.code}
        except Exception:
            result = {"available": False, "status": "error", "code": "native_observation_failed"}
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
