"""The native steps of ``windows_removal`` that the source store repair uses, pinned on real NTFS.

``amplifier_web.source_store_repair`` imports the class ``_Native`` of
``windows_removal`` and does not copy its steps. It deletes files, so it
rests on what each step does: a name comes from the listing of an open
handle; an entry is opened relative to its parent's handle, as itself; a link
of any kind is seen as a link and never entered; an open folder cannot be
renamed or replaced; a file that another program has open refuses; a deletion
goes through the handle. A change of one of these in ``windows_removal`` must
fail here.

Windows only. ``tests/test_source_store_repair.py`` runs the repair itself
with these steps on Windows. The lane "Source store bytecode" fails on a skip.
"""
from __future__ import annotations

import os
import stat

import pytest

from amplifier_web import source_store_repair as repair
from test_source_store_repair import installation, snapshot  # noqa: F401  (the fixture)

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='The native steps exist on Windows only')


def test_each_native_step_that_the_repair_uses(installation):  # noqa: F811
    import _winapi
    api = repair._steps()
    store, outside = installation.store, installation.outside
    (store / 'plain' / 'inner').mkdir(parents=True)
    (store / 'plain' / 'file.pyc').write_bytes(b'x')
    (store / 'plain' / 'readonly.pyc').write_bytes(b'x')
    os.chmod(store / 'plain' / 'readonly.pyc', stat.S_IREAD)
    (store / 'plain' / 'busy.pyc').write_bytes(b'x')
    (store / 'plain' / 'inner' / 'deep.txt').write_text('deep', encoding='utf-8')
    installation.link(outside, store / 'plain' / 'junction')
    before = snapshot(outside)
    facts = {}

    with repair._held(api, str(store)) as top:
        # 1. The store folder is held open: nothing can rename or replace it meanwhile.
        try:
            os.rename(store, str(store) + '-moved')
            facts['the held store folder can be renamed'] = True
        except OSError:
            facts['the held store folder can be renamed'] = False
        # 2. A listing gives each name with its attributes; a junction has the folder and the link attribute.
        rows = dict(api.children(top))
        facts['the store lists'] = sorted(rows)
        plain = api.open(top, 'plain', enter=True, delete=False)
        try:
            listed = dict(api.children(plain))
            facts['the folder lists'] = sorted(listed)
            facts['attributes of the junction'] = (bool(listed['junction'] & 0x400), bool(listed['junction'] & 0x10))
            facts['attributes of a file and a folder'] = (
                bool(listed['file.pyc'] & 0x410), bool(listed['inner'] & 0x10), bool(listed['inner'] & 0x400))
            # 3. A junction is opened as itself and is a link. Its target is not listed through it.
            junction = api.open(plain, 'junction', enter=False, delete=False)
            try:
                facts['kind of the junction'] = api.kind(junction)
            finally:
                api.close(junction)
            # 4. Only a single entry name is opened.
            refused = []
            for name in ('..', '.', '', 'inner\\deep.txt', 'inner/deep.txt', 'C:', 'absent.pyc'):
                try:
                    api.close(api.open(plain, name, enter=False, delete=False))
                except FileNotFoundError:
                    refused.append((name, 'absent'))
                except OSError:
                    refused.append((name, 'refused'))
            facts['names that are not opened'] = refused
            # 5. A kind is read on the handle. 6. A folder that holds something is not deleted.
            inner = api.open(plain, 'inner', enter=True)
            try:
                facts['kind of a folder'] = api.kind(inner)
                try:
                    api.delete(inner)
                    facts['a folder that holds a file is deleted'] = True
                except OSError:
                    facts['a folder that holds a file is deleted'] = False
            finally:
                api.close(inner)
            # 7. A file that another program has open refuses the open for deletion, and stays.
            with open(store / 'plain' / 'busy.pyc', 'rb'):
                try:
                    api.close(api.open(plain, 'busy.pyc', enter=False))
                    facts['a file in use is opened for deletion'] = True
                except OSError:
                    facts['a file in use is opened for deletion'] = False
            # 8. A file, also a read-only one, goes through its handle when the handle closes.
            for name in ('file.pyc', 'readonly.pyc'):
                file = api.open(plain, name, enter=False)
                try:
                    facts['kind of ' + name] = api.kind(file)
                    api.delete(file)
                finally:
                    api.close(file)
            facts['after the two deletions the folder lists'] = sorted(dict(api.children(plain)))
        finally:
            api.close(plain)

    assert facts == {
        'the held store folder can be renamed': False,
        'the store lists': ['plain'],
        'the folder lists': ['busy.pyc', 'file.pyc', 'inner', 'junction', 'readonly.pyc'],
        'attributes of the junction': (True, True),
        'attributes of a file and a folder': (False, True, False),
        'kind of the junction': 'link',
        'names that are not opened': [('..', 'refused'), ('.', 'refused'), ('', 'refused'),
                                      ('inner\\deep.txt', 'refused'), ('inner/deep.txt', 'refused'),
                                      ('C:', 'refused'), ('absent.pyc', 'absent')],
        'kind of a folder': 'folder',
        'a folder that holds a file is deleted': False,
        'a file in use is opened for deletion': False,
        'kind of file.pyc': 'file',
        'kind of readonly.pyc': 'file',
        'after the two deletions the folder lists': ['busy.pyc', 'inner', 'junction'],
    }
    assert snapshot(outside) == before and (store / 'plain' / 'inner' / 'deep.txt').read_text() == 'deep'


def test_the_store_folder_is_refused_when_it_is_a_link_or_absent(installation):  # noqa: F811
    api = repair._steps()
    alias = installation.root / 'alias'
    installation.link(installation.store, alias)
    refused = {}
    for name, path in (('a junction to the store', alias), ('a folder that is absent', installation.root / 'no')):
        try:
            with repair._held(api, str(path)):
                refused[name] = False
        except (OSError, ValueError):
            refused[name] = True
    with repair._held(api, str(installation.store)):
        refused['the store itself'] = False
    assert refused == {'a junction to the store': True, 'a folder that is absent': True, 'the store itself': False}
