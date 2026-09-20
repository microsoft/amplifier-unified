"""Executed only in the owned Python child; newline JSON on standard streams."""

import ast
import asyncio
import contextlib
import importlib.metadata
import json
import os
import platform
import sys
import traceback
from contextvars import ContextVar

_wire = sys.stdout
_cell = ContextVar("cell", default=None)


def send(value):
    _wire.write(json.dumps(value, ensure_ascii=True) + "\n")
    _wire.flush()


class Output:
    def __init__(self, stream):
        self.stream = stream

    def write(self, text):
        text = str(text)
        for offset in range(0, len(text), 1000):
            send(
                {
                    "type": "output",
                    "cellId": _cell.get(),
                    "stream": self.stream,
                    "text": text[offset : offset + 1000],
                }
            )
        return len(text)

    def flush(self):
        pass


namespace = {"__name__": "__kernel__", "__builtins__": __builtins__}
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
packages = {}
for name in ("python-docx", "python-pptx", "openpyxl", "reportlab", "pypdf", "Pillow"):
    try:
        packages[name] = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        pass
send(
    {
        "type": "ready",
        "runtime": {
            "language": "python",
            "executable": sys.executable,
            "version": platform.python_version(),
            "prefix": sys.prefix,
            "pid": os.getpid(),
            "packages": packages,
        },
    }
)
for line in sys.stdin:
    request = json.loads(line)
    _cell.set(request["cellId"])
    try:
        tree = ast.parse(request["code"], mode="exec")
        expression = None
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            expression = ast.Expression(tree.body.pop().value)
        with (
            contextlib.redirect_stdout(Output("stdout")),
            contextlib.redirect_stderr(Output("stderr")),
        ):
            try:
                body = compile(
                    tree, "<cell>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT
                )
                pending = eval(body, namespace)
                if asyncio.iscoroutine(pending):
                    loop.run_until_complete(pending)
                result = None
                if expression:
                    result = eval(
                        compile(
                            expression,
                            "<cell>",
                            "eval",
                            flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
                        ),
                        namespace,
                    )
                    if asyncio.iscoroutine(result):
                        result = loop.run_until_complete(result)
                rendered = repr(result) if result is not None else None
            finally:
                pending_tasks = asyncio.all_tasks(loop)
                for task in pending_tasks:
                    task.cancel()
                if pending_tasks:
                    loop.run_until_complete(
                        asyncio.gather(*pending_tasks, return_exceptions=True)
                    )

        send(
            {
                "type": "done",
                "cellId": _cell.get(),
                "success": True,
                "result": rendered[:4000] if rendered else None,
                "resultTruncated": bool(rendered and len(rendered) > 4000),
            }
        )
    except BaseException:  # noqa: BLE001 - Child SystemExit/KeyboardInterrupt are cell failures.
        send(
            {
                "type": "done",
                "cellId": _cell.get(),
                "success": False,
                "error": traceback.format_exc(limit=12)[-4000:],
            }
        )
    finally:
        _cell.set(None)
