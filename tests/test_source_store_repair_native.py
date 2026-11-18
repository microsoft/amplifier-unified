"""The native steps of ``windows_removal`` that the source store repair uses, pinned on real NTFS.

``amplifier_web.source_store_repair`` imports the class ``_Native`` of
``windows_removal`` and does not copy its steps. It deletes files, so it
rests on what each step does: a name comes from the listing of an open
handle; an entry is opened relative to its parent's handle, as itself; a link
of any kind (a junction, a symbolic link to a folder or to a file) is seen as a
link on its handle and never entered; an open folder cannot be renamed or
replaced; a file that another program has open refuses; a deletion
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
    api = repair._steps()
    store, outside = installation.store, installation.outside
    (store / 'plain' / 'inner').mkdir(parents=True)
    (store / 'plain' / 'file.pyc').write_bytes(b'x')
    (store / 'plain' / 'readonly.pyc').write_bytes(b'x')
    os.chmod(store / 'plain' / 'readonly.pyc', stat.S_IREAD)
    (store / 'plain' / 'busy.pyc').write_bytes(b'x')
    (store / 'plain' / 'inner' / 'deep.txt').write_text('deep', encoding='utf-8')
    (store / 'plain' / 'empty-kept').mkdir()
    (store / 'plain' / 'empty-gone').mkdir()
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
            # 6b. An empty folder, opened as the repair opens a cache folder (to list it, with delete access).
            # It stays when its handle closes with no deletion. A deletion through the handle takes it.
            for name in ('empty-kept', 'empty-gone'):
                folder = api.open(plain, name, enter=True, delete=True)
                try:
                    facts[name] = (api.kind(folder), api.children(folder))
                    if name == 'empty-gone':
                        api.delete(folder)
                finally:
                    api.close(folder)
            facts['after the folder deletion the folder lists'] = sorted(dict(api.children(plain)))
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
        'the folder lists': ['busy.pyc', 'empty-gone', 'empty-kept', 'file.pyc', 'inner', 'junction',
                             'readonly.pyc'],
        'empty-kept': ('folder', []),
        'empty-gone': ('folder', []),
        'after the folder deletion the folder lists': ['busy.pyc', 'empty-kept', 'file.pyc', 'inner', 'junction',
                                                       'readonly.pyc'],
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
        'after the two deletions the folder lists': ['busy.pyc', 'empty-kept', 'inner', 'junction'],
    }
    assert snapshot(outside) == before and (store / 'plain' / 'inner' / 'deep.txt').read_text() == 'deep'


def test_a_link_of_each_kind_is_a_link_on_its_handle_in_each_way_that_the_repair_opens_an_entry(
        installation):  # noqa: F811
    """The repair opens a folder with ``enter=True`` (with and without delete) and a file with ``enter=False``.

    Then it reads the kind on the handle and goes on only for "folder" or "file". A junction, a symbolic
    link to a folder and a symbolic link to a file must each be "link" there. A second name of a file
    elsewhere (a hard link) is a plain file: the deletion takes this name only.
    """
    api = repair._steps()
    store, outside = installation.store, installation.outside
    (store / 'plain').mkdir()
    installation.link(outside, store / 'plain' / 'junction')
    os.symlink(outside, store / 'plain' / 'folder-link', target_is_directory=True)
    os.symlink(outside / 'precious.txt', store / 'plain' / 'file-link.pyc')
    os.link(outside / 'precious.txt', store / 'plain' / 'second-name.pyc')
    before, facts = snapshot(outside), {}
    try:
        with repair._held(api, str(store)) as top:
            plain = api.open(top, 'plain', enter=True, delete=False)
            try:
                listed = dict(api.children(plain))
                for name in sorted(listed):  # (the link attribute, the folder attribute)
                    facts[name] = (bool(listed[name] & 0x400), bool(listed[name] & 0x10))
                for name, enter, delete in (
                        ('junction', True, False), ('junction', True, True),
                        ('folder-link', True, False), ('folder-link', True, True),
                        ('file-link.pyc', False, True), ('second-name.pyc', False, True)):
                    handle = api.open(plain, name, enter=enter, delete=delete)
                    try:
                        facts[name, enter, delete] = api.kind(handle)
                        if name == 'second-name.pyc':
                            api.delete(handle)
                    finally:
                        api.close(handle)
                facts['after the deletion the folder lists'] = sorted(dict(api.children(plain)))
            finally:
                api.close(plain)
    finally:
        for remove, name in ((os.unlink, 'file-link.pyc'), (os.rmdir, 'folder-link')):
            try:
                remove(store / 'plain' / name)
            except OSError:
                pass
    assert facts == {
        'junction': (True, True), 'folder-link': (True, True),
        'file-link.pyc': (True, False), 'second-name.pyc': (False, False),
        ('junction', True, False): 'link', ('junction', True, True): 'link',
        ('folder-link', True, False): 'link', ('folder-link', True, True): 'link',
        ('file-link.pyc', False, True): 'link', ('second-name.pyc', False, True): 'file',
        'after the deletion the folder lists': ['file-link.pyc', 'folder-link', 'junction'],
    }
    # Nothing behind a link changed, and the file elsewhere keeps its content under its own name.
    assert snapshot(outside) == before and (outside / 'precious.txt').read_text(encoding='utf-8') == 'keep me'


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
