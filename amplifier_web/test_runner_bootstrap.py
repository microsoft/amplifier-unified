"""Fixed remote entry point: execute only the verified controller sources.

Sent as the SSH Python -c program, including for retained older controllers.
Cached or sourceless bytecode beside a controller is never source authority.
"""
import base64
import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import json
import os
import stat
from pathlib import Path
import sys


def main():
    controller = Path(os.path.abspath(sys.argv[1]))
    directory = controller.parent
    if any(path.is_symlink() for path in (directory, *directory.parents)):
        raise ValueError('The controller manifest directory was redirected.')
    expected = sys.argv[2]
    names = json.loads(sys.argv[3])
    sources = {}
    for name in names:
        if not isinstance(name, str) or Path(name).name != name or not name.endswith('.py'):
            raise ValueError('Invalid controller manifest member.')
        path = directory / name
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError('The controller manifest source is not a regular file.')
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('The controller manifest source is not a regular file.')
            sources[name] = stream.read()
    actual = {'amplifier_web/' + name: base64.b64encode(content).decode()
              for name, content in sources.items()}
    if hashlib.sha256(json.dumps(actual, sort_keys=True).encode()).hexdigest() != expected:
        raise ValueError('Controller bytes changed')

    class SourceOnly(importlib.abc.MetaPathFinder, importlib.abc.Loader):
        def find_spec(self, fullname, path=None, target=None):
            if fullname != 'amplifier_web' and not fullname.startswith('amplifier_web.'):
                return None
            name = '__init__.py' if fullname == 'amplifier_web' else fullname.removeprefix('amplifier_web.') + '.py'
            if name not in sources:
                raise ImportError('The controller module is outside its verified manifest.')
            spec = importlib.util.spec_from_loader(fullname, self, is_package=fullname == 'amplifier_web')
            spec.origin = str(controller.parent / name)
            spec.has_location = True
            return spec

        def create_module(self, spec):
            return None

        def exec_module(self, module):
            name = Path(module.__spec__.origin).name
            exec(compile(sources[name], module.__spec__.origin, 'exec', dont_inherit=True), module.__dict__)

    sys.meta_path.insert(0, SourceOnly())
    # Some fixed helpers deliberately use spec_from_file_location instead of
    # package imports. Keep those SourceFileLoader calls on the same verified
    # bytes; they must not reintroduce adjacent cache authority.
    source_get_code = importlib.machinery.SourceFileLoader.get_code
    bytecode_get_code = importlib.machinery.SourcelessFileLoader.get_code
    def source_code(loader, fullname):
        path = Path(os.path.abspath(loader.path))
        if path.is_relative_to(directory) or path.resolve().is_relative_to(directory):
            if path.parent != directory or path.name not in sources:
                raise ImportError('The controller module is outside its verified manifest.')
            return compile(sources[path.name], str(path), 'exec', dont_inherit=True)
        return source_get_code(loader, fullname)
    def bytecode_code(loader, fullname):
        path = Path(os.path.abspath(loader.path))
        if path.is_relative_to(directory) or path.resolve().is_relative_to(directory):
            raise ImportError('Controller bytecode is outside its verified source manifest.')
        return bytecode_get_code(loader, fullname)
    importlib.machinery.SourceFileLoader.get_code = source_code
    importlib.machinery.SourcelessFileLoader.get_code = bytecode_code
    # Older retained controllers bootstrap from system Python into their exact
    # qualified interpreter. Keep that transition on this same verified source
    # path; executing the old script directly would restore normal pyc loading.
    program = sys.orig_argv[sys.orig_argv.index('-c') + 1]
    original_exec = os.execv
    def qualified_exec(executable, arguments):
        if (str(controller) in arguments and '--qualified-runtime' in arguments):
            return original_exec(executable, [executable, '-I', '-B', '-c', program,
                str(controller), expected, json.dumps(names), '--qualified-runtime'])
        return original_exec(executable, arguments)
    os.execv = qualified_exec
    content = sources.get(controller.name)
    if content is None:
        raise ValueError('The controller entry point is outside its manifest.')
    namespace = {'__name__': '__main__', '__file__': str(controller), '__package__': None}
    exec(compile(content, str(controller), 'exec', dont_inherit=True), namespace)


if __name__ == '__main__':
    main()

