"""The retained controller manifest owns executable source, not adjacent caches."""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import py_compile
import subprocess
import sys

import pytest

from amplifier_web.registered_test_runners import VERIFY


@pytest.mark.parametrize('loading', ['namespace', 'file-location'])
@pytest.mark.parametrize('reexec', [False, True])
@pytest.mark.parametrize('cache_kind', ['cached', 'sourceless', 'source-drift'])
def test_controller_runs_only_exact_manifest_sources(tmp_path, reexec, cache_kind, loading):
    folder = tmp_path / 'amplifier_web'
    folder.mkdir()
    (folder / '__init__.py').write_text('')
    helper = folder / 'helper.py'
    helper.write_text("VALUE = 'verified'\n")
    controller = folder / 'test_runner_controller.py'
    controller.write_text(
        "import os, sys\n"
        + ("from amplifier_web import helper\n" if loading == 'namespace' else
           "import importlib.util\n"
           "from pathlib import Path\n"
           "spec = importlib.util.spec_from_file_location('_direct_helper', Path(__file__).with_name('helper.py'))\n"
           "helper = importlib.util.module_from_spec(spec)\n"
           "spec.loader.exec_module(helper)\n")
        + ("if '--qualified-runtime' not in sys.argv:\n"
           "    os.execv(sys.executable, [sys.executable, '-I', str(__file__), '--qualified-runtime'])\n" if reexec else '')
        + "print(helper.VALUE)\n")
    names = ['__init__.py', controller.name, helper.name]
    manifest = {'amplifier_web/' + name: base64.b64encode((folder / name).read_bytes()).decode() for name in names}
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    attack = tmp_path / 'attack.py'
    attack.write_text("VALUE = 'unverified'\n")
    if cache_kind == 'cached':
        # This cache is accepted by ordinary Python without checking source.
        py_compile.compile(str(attack), cfile=importlib.util.cache_from_source(str(helper)),
                           doraise=True, invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
    elif cache_kind == 'sourceless':
        # Unlisted sourceless imports must not expand the verified program.
        py_compile.compile(str(attack), cfile=str(folder / 'unlisted.pyc'), doraise=True)
        helper.write_text('from amplifier_web import unlisted\nVALUE = unlisted.VALUE\n' if loading == 'namespace' else
            "import importlib.util\nfrom pathlib import Path\n"
            "spec=importlib.util.spec_from_file_location('_unlisted',Path(__file__).with_name('unlisted.pyc'))\n"
            "unlisted=importlib.util.module_from_spec(spec)\nspec.loader.exec_module(unlisted)\nVALUE=unlisted.VALUE\n")
        manifest['amplifier_web/helper.py'] = base64.b64encode(helper.read_bytes()).decode()
        digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    else:
        helper.write_text("VALUE = 'source changed'\n")
    result = subprocess.run([sys.executable, '-I', '-B', '-c', VERIFY,
                             str(controller), digest, json.dumps(names)],
                            capture_output=True, text=True, timeout=15)
    if cache_kind == 'cached':
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == 'verified'
    else:
        assert result.returncode != 0
        assert 'unverified' not in result.stdout
        assert ('manifest' if cache_kind == 'sourceless' else 'Controller bytes changed') in result.stderr


@pytest.mark.parametrize('reexec', [False, True])
@pytest.mark.parametrize('redirect', ['helper', 'directory', 'during-load'])
def test_controller_rejects_redirected_manifest_inputs(tmp_path, reexec, redirect):
    real = tmp_path / 'real-controller'
    real.mkdir()
    folder = tmp_path / 'amplifier_web'
    if redirect == 'directory':
        folder.symlink_to(real, target_is_directory=True)
    else:
        folder.mkdir()
    (folder / '__init__.py').write_text('')
    helper = folder / 'helper.py'
    outside = tmp_path / 'outside.py'
    outside.write_text("VALUE = 'verified'\n")
    if redirect == 'helper':
        helper.symlink_to(outside)
    else:
        helper.write_text(outside.read_text())
    controller = folder / 'test_runner_controller.py'
    controller.write_text(
        "import os, sys, importlib.util\nfrom pathlib import Path\n"
        + ("if '--qualified-runtime' not in sys.argv:\n"
           "    os.execv(sys.executable, [sys.executable, '-I', str(__file__), '--qualified-runtime'])\n" if reexec else '')
        + ("Path(__file__).with_name('helper.py').unlink()\n"
           + "Path(__file__).with_name('helper.py').symlink_to(" + repr(str(outside)) + ")\n" if redirect == 'during-load' else '')
        + "spec=importlib.util.spec_from_file_location('_helper',Path(__file__).with_name('helper.py'))\n"
        "helper=importlib.util.module_from_spec(spec)\nspec.loader.exec_module(helper)\nprint(helper.VALUE)\n")
    names = ['__init__.py', controller.name, helper.name]
    manifest = {'amplifier_web/' + name: base64.b64encode((folder / name).read_bytes()).decode() for name in names}
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    attack = tmp_path / 'attack.py'
    attack.write_text("VALUE = 'unverified'\n")
    py_compile.compile(str(attack), cfile=importlib.util.cache_from_source(str(helper)),
                       doraise=True, invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
    result = subprocess.run([sys.executable, '-I', '-B', '-c', VERIFY,
                             str(controller), digest, json.dumps(names)],
                            capture_output=True, text=True, timeout=15)
    if redirect == 'during-load':
        # Even a post-capture redirect cannot escape lexical manifest ownership.
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == 'verified'
    else:
        assert result.returncode != 0, result.stdout
        assert 'unverified' not in result.stdout
        assert 'manifest' in result.stderr
