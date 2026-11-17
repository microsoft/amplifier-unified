"""Run with .venv/bin/python -I -S tests/test_windows_files.py.

Portable tests check policy/ordering, not Windows kernel behavior. Native tests
use a new directory under the current profile, never repair a runner's temp ACL.
"""
from __future__ import annotations

import ctypes as C
from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).parents[1] / "amplifier_web/windows_files.py"
spec = importlib.util.spec_from_file_location("windows_files", SOURCE)
wf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wf)


class PolicyTests(unittest.TestCase):
    def test_win64_abi(self):
        self.assertEqual(C.sizeof(wf.DWORD), 4)
        self.assertEqual(C.sizeof(wf.BOOL), 4)
        self.assertEqual(C.sizeof(wf.FILE_INFO), 52)
        if C.sizeof(C.c_void_p) == 8:
            self.assertEqual(C.sizeof(wf.SECURITY_ATTRIBUTES), 24)
        self.assertIs(wf._SIGNATURES["CreateFileW"][0], C.c_void_p)
        self.assertIs(wf._SIGNATURES["GetCurrentProcess"][0], C.c_void_p)

    def test_rename_info_win64_layout_and_binding(self):
        self.assertEqual(C.sizeof(wf.FILE_RENAME_INFO._fields_[0][1]), 4)  # Flags, not BOOLEAN
        self.assertEqual(C.sizeof(wf.FILE_RENAME_INFO._fields_[-1][1]), 2)  # WCHAR, not host wchar
        if C.sizeof(C.c_void_p) == 8:
            self.assertEqual(wf.FILE_RENAME_INFO.RootDirectory.offset, 8)
            self.assertEqual(wf.FILE_RENAME_INFO.FileNameLength.offset, 16)
            self.assertEqual(wf.FILE_RENAME_INFO.FileName.offset, 20)
            self.assertEqual(C.sizeof(wf.FILE_RENAME_INFO), 24)
        result, arguments = wf._SIGNATURES["SetFileInformationByHandle"]
        self.assertIs(result, wf.BOOL)
        self.assertEqual(arguments, [wf.HANDLE, C.c_int32, wf.PVOID, wf.DWORD])
        self.assertNotIn("MoveFileExW", wf._SIGNATURES)

    def test_components_and_local_paths(self):
        for name in ("server.yaml", "session-secret", "control-token", "配置"):
            wf._component(name)
        for name in ("", ".", "..", "a/b", "a\\b", "NUL.txt", "NUL .txt", "COM¹",
                     "LPT9", "x:stream", "a ", "a.", "x~1", "a\0", "a?"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                wf._component(name)
        for name in (r"\\server\share\x", r"\\?\C:\x", r"\\.\C:\x",
                     r"C:x", r"C:\x\..\y", r"C:\x\.\y", "C:\\" + "a" * 256):
            with self.subTest(path=name), self.assertRaises(ValueError):
                wf._local(name)
        wf._local(r"C:\Users\Example\.amplifier-unified\config")
        wf._local("C:\\")

    def test_foreign_traversal_grants(self):
        # Only directory creation is exempt; file creation and attribute writes
        # can convert a pinned ancestor to a reparse point.
        wf._check_ace(0, 0, 4, "foreign", private=False)
        for mask in (2, 0x100, 0x40, 0x10000, 0x40000, 0x80000,
                     0x40000000, 0x10000000):
            with self.subTest(mask=mask), self.assertRaises(PermissionError):
                wf._check_ace(0, 0, mask, "foreign", private=False)
        with self.assertRaises(PermissionError):
            wf._check_ace(0, 0, 1, "foreign", private=True)

    def test_acl_policy_fails_closed(self):
        for kind, mask in ((5, 1), (0, 0x2000000)):
            with self.assertRaises(PermissionError):
                wf._check_ace(kind, 0, mask, "owner", private=True)
        for sid in ("owner", *wf._OS_AUTHORITIES, "S-1-3-4"):
            wf._check_ace(0, 0, 0x1F01FF, sid, private=True)
        wf._check_ace(1, 0, 0x1F01FF, "foreign", private=True)
        wf._check_ace(0, 8, 0x1F01FF, "S-1-3-0", private=False)
        with self.assertRaises(PermissionError):
            wf._check_ace(0, 8, 0x1F01FF, "foreign", private=True)

    def test_address_does_not_resolve_symlinks(self):
        with patch.object(Path, "resolve", side_effect=AssertionError("resolve")):
            self.assertTrue(wf.address("relative").is_absolute())

    def test_security_attributes_are_supplied_at_create(self):
        api = wf._API.__new__(wf._API)
        events = []

        @contextmanager
        def security(*, directory=False):
            events.append("private-dacl")
            yield wf.SECURITY_ATTRIBUTES(C.sizeof(wf.SECURITY_ATTRIBUTES), 123, False)

        def create(path, access, sharing, attributes, disposition, flags, template):
            attributes = C.cast(attributes, C.POINTER(wf.SECURITY_ATTRIBUTES)).contents
            self.assertEqual(attributes.descriptor, 123)
            self.assertFalse(attributes.inherit)
            self.assertEqual(disposition, 1)  # CREATE_NEW
            self.assertEqual(sharing & 2, 0)  # no writer sharing
            self.assertTrue(access & 0x10000)  # DELETE for retained-handle rename/cleanup
            events.append("create")
            return 100

        api.private_security, api.CreateFileW = security, create
        api.info = lambda *args, **kwargs: None
        api.open(wf.PureWindowsPath(r"C:\private\temporary"), create=True)
        self.assertEqual(events, ["private-dacl", "create"])

    def test_reopened_rename_source_has_delete_not_write_and_preserves_sharing(self):
        api = wf._API.__new__(wf._API)
        calls = []

        @contextmanager
        def security(*, directory=False):
            yield wf.SECURITY_ATTRIBUTES()

        def open_file(path, access, sharing, attributes, disposition, flags, template):
            self.assertTrue(access & 0x10000)  # DELETE, required for rename
            self.assertTrue(access & 0x80000000)  # READ, not WRITE
            self.assertFalse(access & 0x40000000)
            self.assertEqual(sharing, 5)  # READ | DELETE, never WRITE
            self.assertEqual(disposition, 3)  # OPEN_EXISTING, never create/truncate
            self.assertEqual(flags & 0x200000, 0x200000)  # OPEN_REPARSE_POINT
            calls.append(path)
            return 101

        api.private_security, api.CreateFileW = security, open_file
        api.info = lambda *args, **kwargs: None
        self.assertEqual(api.open(wf.PureWindowsPath(r"C:\private\temporary"), cleanup=True), 101)
        self.assertEqual(calls, [r"C:\private\temporary"])

    def test_existing_inherited_acl_and_wrong_owner_null_dacl(self):
        # Real ACL policy method with injected descriptor pointers. Native
        # inherited ACL coverage below tests the kernel's actual descriptor.
        api = wf._API.__new__(wf._API)
        api.sid = "user"
        owner = ["user"]
        present = [True]

        def security(handle, kind, fields, owner_out, group, dacl_out, sacl, sd_out):
            C.cast(owner_out, C.POINTER(wf.PVOID))[0] = 1
            C.cast(dacl_out, C.POINTER(wf.PVOID))[0] = 2 if present[0] else None
            return 0

        def control(sd, output, revision):
            C.cast(output, C.POINTER(wf.WORD))[0] = 4  # present, NOT protected
            return True

        api.GetSecurityInfo, api.GetSecurityDescriptorControl = security, control
        api.sid_text = lambda pointer: owner[0]
        api.GetAclInformation = lambda *args: True  # empty DACL: grants nobody
        api.LocalFree = lambda *args: None
        api.validate_acl(1, private=True)
        for candidate in ("foreign", *sorted(wf._OS_AUTHORITIES)):
            with self.subTest(owner=candidate):
                owner[0] = candidate
                # OS authorities are trusted traversal owners, not TokenUser
                # owners of private state (including on an elevated runner).
                with self.assertRaises(PermissionError):
                    api.validate_acl(1, private=True)
                if candidate in wf._OS_AUTHORITIES:
                    api.validate_acl(1, private=False)
        owner[0], present[0] = "user", False
        with self.assertRaises(PermissionError):
            api.validate_acl(1, private=True)

    def test_inherited_fixture_oracle_uses_structure_not_sddl_aliases(self):
        from types import SimpleNamespace
        api = wf._API.__new__(wf._API)
        api.sid = "S-1-5-21-100-200-300-500"
        state = {"owner": api.sid, "control": 4, "dacl": 2, "flags": 0x10}
        ace = wf.ACE()
        rendered = "O:LAD:AI(A;ID;FA;;;LA)"
        rendered_buffer = C.create_unicode_buffer(rendered)

        def security(handle, kind, fields, owner, group, dacl, sacl, descriptor):
            C.cast(owner, C.POINTER(wf.PVOID))[0] = 1
            C.cast(dacl, C.POINTER(wf.PVOID))[0] = state["dacl"]
            C.cast(descriptor, C.POINTER(wf.PVOID))[0] = 3
            return 0

        def control(descriptor, output, revision):
            C.cast(output, C.POINTER(wf.WORD))[0] = state["control"]
            return True

        def acl_info(dacl, output, size, kind):
            C.cast(output, C.POINTER(wf.ACL_INFO)).contents.count = 1
            return True

        def get_ace(dacl, index, output):
            ace.flags = state["flags"]
            C.cast(output, C.POINTER(wf.PVOID))[0] = C.addressof(ace)
            return True

        def convert(descriptor, revision, fields, output, length):
            C.cast(output, C.POINTER(wf.PVOID))[0] = C.addressof(rendered_buffer)
            return True

        api.GetSecurityInfo, api.GetSecurityDescriptorControl = security, control
        api.GetAclInformation, api.GetAce = acl_info, get_ace
        api.sid_text = lambda owner: state["owner"]
        api.open = lambda *a, **k: 10
        api.close = api.LocalFree = lambda value: None
        dll = SimpleNamespace(ConvertSecurityDescriptorToStringSecurityDescriptorW=convert)
        with patch.object(wf, "_API", return_value=api), patch.object(C, "WinDLL", return_value=dll, create=True):
            snapshot = lambda: NativeTests.security_text(self, SOURCE, inherited=True)
            self.assertEqual(snapshot(), rendered)  # LA alias is not a different owner.
            for field, value in (("owner", "S-1-5-32-544"), ("control", 0x1004),
                                 ("control", 0), ("dacl", 0), ("flags", 0)):
                with self.subTest(field=field, value=value), patch.dict(state, {field: value}):
                    # Unchanged plausible SDDL cannot hide a structurally bad fixture.
                    with self.assertRaises(AssertionError):
                        snapshot()

    def test_new_security_descriptor_is_protected_owner_and_system(self):
        api = wf._API.__new__(wf._API)
        api.sid = "S-1-5-21-100-200-300-1001"
        descriptors = []

        def convert(sddl, revision, output, length):
            descriptors.append(sddl)
            C.cast(output, C.POINTER(wf.PVOID))[0] = 123
            return True

        api.ConvertStringSecurityDescriptorToSecurityDescriptorW = convert
        api.LocalFree = lambda value: None
        with api.private_security(directory=True) as security:
            self.assertEqual(security.descriptor, 123)
        self.assertEqual(descriptors, [
            f"O:{api.sid}D:P(A;OICI;FA;;;{api.sid})(A;OICI;FA;;;SY)"])

    def test_exact_handle_cleanup_checks_identity_before_delete(self):
        api = wf._API.__new__(wf._API)
        api.info = lambda *args, **kwargs: wf.FILE_INFO(volume=1, links=1, index_low=99)
        deleted = []
        api.SetFileInformationByHandle = lambda *args: deleted.append(args) or True
        with self.assertRaises(PermissionError):
            api.discard(1, Path("owned-temp"), (1, 0, 98))
        self.assertFalse(deleted)
        api.discard(1, Path("owned-temp"), (1, 0, 99))
        self.assertEqual(len(deleted), 1)
        self.assertEqual(deleted[0][:2], (1, 4))

    def test_broad_ancestor_is_checked_before_opening_any_child(self):
        events = []
        root = Path.cwd()
        target = root / "owned" / "child"

        class API:
            def GetDriveTypeW(self, path):
                return 3

            def open(self, path, **kwargs):
                events.append(("open", path))
                return 100

            def info(self, *args, **kwargs):
                return wf.FILE_INFO(volume=1, index_low=1)

            def GetVolumeInformationByHandleW(self, handle, a, b, c, d, e, fs, size):
                fs.value = "NTFS"
                return True

            ok = staticmethod(wf._API.ok)

            def validate_acl(self, handle, **kwargs):
                events.append(("acl", handle))
                raise PermissionError("untrusted attributes grant")

            def close(self, handle):
                events.append(("close", handle))

        with patch.object(wf, "_API", API), self.assertRaises(PermissionError):
            with wf.directory(target, owned_root=root / "owned", create=True):
                pass
        self.assertEqual([event[0] for event in events], ["open", "acl", "close"])

    @unittest.skipIf(os.name == "nt", "non-Windows refusal")
    def test_no_posix_privacy_emulation(self):
        with self.assertRaises(RuntimeError):
            with wf.directory(Path.cwd(), owned_root=Path.cwd()):
                pass


class RenameTests(unittest.TestCase):
    def test_exact_source_handle_utf16_destination_and_explicit_flags(self):
        for replace in (False, True):
            with self.subTest(replace=replace):
                api = wf._API.__new__(wf._API)
                calls = []
                source = 0x1234567887654321
                target = wf.PureWindowsPath("C:/private/config/配置-😀.yaml")
                encoded = str(target).encode("utf-16-le")

                def rename(handle, kind, buffer, size):
                    calls.append("rename")
                    self.assertEqual(handle, source)
                    self.assertEqual(kind, 22)  # FileRenameInfoEx
                    info = wf.FILE_RENAME_INFO.from_buffer(buffer)
                    self.assertEqual(info.Flags, 3 if replace else 0)
                    self.assertIsNone(info.RootDirectory)
                    self.assertEqual(info.FileNameLength, len(encoded))
                    self.assertGreaterEqual(size, C.sizeof(wf.FILE_RENAME_INFO) + len(encoded))
                    self.assertEqual(C.string_at(
                        C.addressof(buffer) + wf.FILE_RENAME_INFO.FileName.offset, len(encoded)), encoded)
                    return True

                api.SetFileInformationByHandle = rename
                api.move(source, target, replace=replace)
                self.assertEqual(calls, ["rename"])

    def test_failed_rename_is_not_retried_or_fallback_and_collision_keeps_type(self):
        for code in (5, 32, 80, 183, 1, 50, 87, 120):
            with self.subTest(code=code):
                api = wf._API.__new__(wf._API)
                calls = []
                failure = FileExistsError("winner") if code in {80, 183} else OSError("native failure")
                failure.winerror = code

                def rename(*args):
                    calls.append("rename")
                    return False

                def checked(result):
                    self.assertFalse(result)
                    raise failure

                api.SetFileInformationByHandle, api.ok = rename, checked
                unsupported = code in {1, 50, 87, 120}
                with self.assertRaises(NotImplementedError if unsupported else type(failure)) as caught:
                    api.move(123, wf.PureWindowsPath("C:/private/server.yaml"), replace=True)
                if unsupported:
                    self.assertIn("Windows 10", str(caught.exception))
                    self.assertIn("no fallback", str(caught.exception))
                    self.assertIs(caught.exception.__cause__, failure)
                else:
                    self.assertIs(caught.exception, failure)
                self.assertEqual(calls, ["rename"])


class PublicationTests(unittest.TestCase):
    """Injected handles prove control flow, not NTFS semantics."""

    class API:
        sid = "owner"

        def __init__(self):
            self.events = []
            self.present = False
            self.flush_error = False
            self.rename_error = None
            self.discard_error = None
            self.post_error = False
            self.superseded = False
            self.acl_error_after_rename = False
            self.existing_unlinked = 0
            self.renamed = False
            self.bytes = bytearray()

        def open(self, path, *, directory=False, create=False, cleanup=False, exclusive=False):
            if exclusive:
                self.events.append(("exclusive", path.name))
            self.events.append(("open", path.name, create))
            if not create and not cleanup and not self.present and not self.renamed:
                raise FileNotFoundError(path)
            # 20 is the existing file; a higher number is an object that
            # another publisher replaced after this open.
            return 10 if create or cleanup else 20 + self.existing_unlinked

        def validate_acl(self, handle, *, private):
            self.events.append(("acl", handle))
            if self.acl_error_after_rename and self.renamed:
                raise PermissionError("Windows private-file DACL grants foreign access")

        def info(self, handle, path, *, directory):
            if self.post_error and self.renamed:
                raise OSError("post-rename check")
            if self.superseded and self.renamed:
                raise wf.PrivateFileUnlinked("another publisher replaced it")
            if handle > 20:
                self.existing_unlinked -= 1
                raise wf.PrivateFileUnlinked("replaced after the open")
            return wf.FILE_INFO(volume=1, links=1, index_low=handle)

        def write(self, handle, content):
            self.events.append(("write", handle))
            self.bytes.extend(content)

        def flush(self, handle):
            self.events.append(("flush", handle))
            if self.flush_error:
                raise OSError("flush failed")

        def move(self, handle, destination, *, replace):
            if handle != 10:
                raise AssertionError("rename must use the retained DELETE source handle")
            self.assert_not_closed_at_move()
            self.events.append(("move", replace))
            if self.rename_error:
                raise self.rename_error
            self.renamed = True

        def assert_not_closed_at_move(self):
            # The write handle was closed; the read/DELETE cleanup handle was
            # reopened and is still held. The prior target reader is held too.
            if len([event for event in self.events if event[0] == "open"]) < 3:
                raise AssertionError("missing source cleanup handle")
            if ("close", 20) in self.events:
                raise AssertionError("prior reader closed to work around rename failure")

        def discard(self, handle, path, identity):
            self.events.append(("discard", path.name))
            if self.discard_error is not None:
                raise self.discard_error

        def close(self, handle):
            self.events.append(("close", handle))

    def pinned(self, api):
        pinned = wf.PinnedDirectory.__new__(wf.PinnedDirectory)
        pinned.path = Path("private")
        pinned._api = api
        pinned._pins = [(Path("private"), 1, (1, 0, 1), True)]
        pinned._check = lambda: None
        return pinned

    def test_private_removal_uses_exact_open_object_and_content(self):
        import hashlib
        api = self.API()
        api.read = lambda handle, limit: b"owned link"
        pinned = self.pinned(api)
        pinned.remove_private("Launch.lnk", hashlib.sha256(b"owned link").hexdigest())
        self.assertIn(("exclusive", "Launch.lnk"), api.events)
        self.assertEqual(api.events[-2:], [("discard", "Launch.lnk"), ("close", 10)])

    def test_private_removal_refuses_changed_content_without_discard(self):
        import hashlib
        api = self.API()
        api.read = lambda handle, limit: b"changed link"
        with self.assertRaisesRegex(ValueError, "changed"):
            self.pinned(api).remove_private("Launch.lnk", hashlib.sha256(b"old").hexdigest())
        self.assertNotIn(("discard", "Launch.lnk"), api.events)
        self.assertEqual(api.events[-1], ("close", 10))

    def test_prepared_private_removal_keeps_handle_until_called_or_abandoned(self):
        import hashlib
        api = self.API()
        api.read = lambda handle, limit: b"owned link"
        with self.pinned(api).removal_private("Launch.lnk", hashlib.sha256(b"owned link").hexdigest()):
            self.assertNotIn(("close", 10), api.events)
            self.assertNotIn(("discard", "Launch.lnk"), api.events)
        self.assertEqual(api.events[-1], ("close", 10))
        self.assertNotIn(("discard", "Launch.lnk"), api.events)

    def test_flush_failure_never_renames(self):
        api = self.API()
        api.flush_error = True
        with self.assertRaisesRegex(OSError, "flush"):
            self.pinned(api).publish_private("session-secret", b"credential")
        self.assertFalse(any(event[0] == "move" for event in api.events))
        self.assertTrue(any(event[0] == "discard" for event in api.events))

    def test_default_never_overwrites(self):
        api = self.API()
        api.present = True
        with self.assertRaises(FileExistsError):
            self.pinned(api).publish_private("control-token", b"new")
        self.assertFalse(any(event[0] == "write" for event in api.events))

    def test_acl_before_bytes_and_flush_before_move(self):
        api = self.API()
        self.pinned(api).publish_private("server.yaml", b"complete")
        self.assertEqual(api.bytes, b"complete")
        self.assertLess(api.events.index(("acl", 10)), api.events.index(("write", 10)))
        self.assertLess(api.events.index(("flush", 10)), api.events.index(("move", False)))
        self.assertFalse(any(event[0] == "discard" for event in api.events))
        # A completed publication exposes no live WRITE handle.
        close = api.events.index(("close", 10))
        self.assertLess(close, api.events.index(("move", False)))

    def test_racing_no_replace_error_is_preserved(self):
        api = self.API()
        api.rename_error = FileExistsError("winner")
        with self.assertRaises(FileExistsError):
            self.pinned(api).publish_private("control-token", b"loser")
        self.assertFalse(api.renamed)

    def test_collision_with_failed_cleanup_is_not_a_clean_race(self):
        api = self.API()
        collision = api.rename_error = FileExistsError("another creator won")
        cleanup = api.discard_error = PermissionError("exact temporary cleanup failed")
        with self.assertRaises(OSError) as caught:
            self.pinned(api).publish_private("server.yaml", b"loser")
        error = caught.exception
        self.assertNotIsInstance(error, FileExistsError)
        self.assertIn("temporary may remain", str(error))
        self.assertIsInstance(error.__cause__, ExceptionGroup)
        self.assertEqual(error.__cause__.exceptions, (collision, cleanup))
        self.assertFalse(api.renamed)
        self.assertEqual([event[0] for event in api.events].count("discard"), 1)
        self.assertEqual(api.events[-1], ("close", 10))

    def test_sharing_failure_has_no_delete_first_fallback(self):
        api = self.API()
        api.present = True
        api.rename_error = PermissionError("sharing conflict")
        with self.assertRaisesRegex(PermissionError, "sharing"):
            self.pinned(api).publish_private("server.yaml", b"new", replace=True)
        self.assertEqual([event for event in api.events if event[0] == "move"], [("move", True)])
        self.assertEqual(len([event for event in api.events if event[0] == "discard"]), 1)

    def test_unsupported_rename_cleans_temp_and_never_reports_publication(self):
        api = self.API()
        api.present = True
        api.rename_error = NotImplementedError("FileRenameInfoEx unsupported")
        with self.assertRaisesRegex(NotImplementedError, "unsupported"):
            self.pinned(api).publish_private("server.yaml", b"new", replace=True)
        self.assertFalse(api.renamed)
        self.assertEqual([event for event in api.events if event[0] == "move"], [("move", True)])
        self.assertEqual(len([event for event in api.events if event[0] == "discard"]), 1)

    def test_post_rename_failure_is_explicit_and_does_not_delete(self):
        api = self.API()
        api.post_error = True
        with self.assertRaisesRegex(OSError, "published"):
            self.pinned(api).publish_private("server.yaml", b"new", replace=True)
        self.assertFalse(any(event[0] == "discard" for event in api.events))

    def test_publication_that_another_publisher_replaced_at_once_is_not_a_failure(self):
        api = self.API()
        api.present = api.superseded = True
        self.pinned(api).publish_private("task-failure.json", b"mine", replace=True)
        # Nothing but the name check is dropped for the object without a name.
        self.assertGreater(len(api.events) - 1 - api.events[::-1].index(("acl", 10)),
                           api.events.index(("move", True)))
        self.assertFalse(any(event[0] == "discard" for event in api.events))
        self.assertIn(("close", 10), api.events)

    def test_acl_failure_of_a_publication_that_lost_its_name_is_still_reported(self):
        api = self.API()
        api.present = api.superseded = api.acl_error_after_rename = True
        with self.assertRaisesRegex(OSError, "published; post-publication verification failed") as failed:
            self.pinned(api).publish_private("task-failure.json", b"mine", replace=True)
        self.assertIn("DACL", str(failed.exception.__cause__))
        self.assertFalse(any(event[0] == "discard" for event in api.events))

    def test_existing_file_check_of_a_publication_opens_a_replaced_name_again(self):
        api = self.API()
        api.present, api.existing_unlinked = True, 2
        with patch.object(wf.time, "sleep"):
            self.pinned(api).publish_private("task-failure.json", b"mine", replace=True)
        self.assertEqual(api.events.count(("open", "task-failure.json", False)), 3)
        # Both replaced objects are closed before the name is opened again;
        # the valid one is held until the publication ends.
        closes = [event for event in api.events if event[0] == "close" and event[1] >= 20]
        self.assertEqual(closes, [("close", 22), ("close", 21), ("close", 20)])
        self.assertLess(api.events.index(("close", 21)), api.events.index(("move", True)))
        self.assertLess(api.events.index(("move", True)), api.events.index(("close", 20)))

    def test_write_loops_on_partial_writes(self):
        api = wf._API.__new__(wf._API)
        written = bytearray()

        def write(handle, buffer, length, count, overlapped):
            size = min(length, 3)
            written.extend(C.string_at(buffer, size))
            C.cast(count, C.POINTER(wf.DWORD))[0] = size
            return True

        api.WriteFile = write
        api.write(1, b"complete credential")
        self.assertEqual(written, b"complete credential")
        api.WriteFile = lambda *args: True
        with self.assertRaisesRegex(OSError, "zero"):
            api.write(1, b"x")


class ReplacedObjectTests(unittest.TestCase):
    """Decisions on injected Windows answers. The native cases show the real answers."""

    PATH = wf.PureWindowsPath(r"C:\private\task-runtime.json")
    DELETED = r"C:\$Extend\$Deleted\00230000000093B771E89098"  # what Windows names such an object

    def api(self, links, finals=(), attributes=0x20):
        """Successive link counts and final paths (None: the path query fails)."""
        api = wf._API.__new__(wf._API)
        self.links, self.finals = list(links), list(finals)

        def information(handle, reference):
            links = self.links.pop(0)
            if links is None:  # the query itself fails; Windows fills nothing in
                return 0
            reference._obj.attributes, reference._obj.links = attributes, links
            return 1

        def ok(result):
            if not result:
                raise OSError("the Windows query failed")
        api.ok = ok

        def final_path(handle, buffer, size, flags):
            value = self.finals.pop(0)
            if value is None:
                return 0
            buffer.value = "\\\\?\\" + value
            return len(buffer.value)

        api.GetFileInformationByHandle, api.GetFinalPathNameByHandleW = information, final_path
        return api

    def refused(self, api, error=PermissionError, *, directory=False, path=None):
        with self.assertRaises(error) as refused:
            api.info(7, path or self.PATH, directory=directory)
        self.assertEqual((self.links, self.finals), ([], []), "an expected Windows query did not run")
        return refused.exception

    def test_one_link_at_the_exact_path_is_accepted(self):
        info = self.api([1], [str(self.PATH)]).info(7, self.PATH, directory=False)
        self.assertEqual(info.links, 1)

    def test_two_links_stay_a_hardlink_refusal(self):
        for links in (2, 3, 0xFFFFFFFF):
            error = self.refused(self.api([links]))
            self.assertIs(type(error), PermissionError)
            self.assertIn("hardlink", str(error))

    def test_no_link_is_its_own_answer_and_not_a_hardlink(self):
        error = self.refused(self.api([0]), wf.PrivateFileUnlinked)
        self.assertNotIn("hardlink", str(error))
        # Every earlier handler of the refusal still catches it.
        self.assertIsInstance(error, PermissionError)

    def test_reparse_point_and_wrong_type_stay_refused_with_no_link(self):
        for attributes in (0x420, 0x10):
            error = self.refused(self.api([0], attributes=attributes))
            self.assertIs(type(error), PermissionError)

    def test_name_lost_between_the_two_queries_is_not_an_alias(self):
        self.refused(self.api([1, 0], [self.DELETED]), wf.PrivateFileUnlinked)
        self.refused(self.api([1, 0], [None]), wf.PrivateFileUnlinked)

    def test_failed_second_query_is_an_error_and_not_a_replaced_object(self):
        # An empty answer has 0 links. It must not be read as "replaced",
        # which would open the name again in place of a refusal.
        for final in (self.DELETED, None):
            error = self.refused(self.api([1, None], [final]), OSError)
            self.assertIs(type(error), OSError)
            self.assertIn("query failed", str(error))

    def test_other_path_of_a_linked_object_stays_refused(self):
        for final in (self.DELETED, r"C:\private\TASK-RUNTIME.JSON", r"C:\PRIVAT~1\task-runtime.json"):
            error = self.refused(self.api([1, 1], [final]))
            self.assertIs(type(error), PermissionError)
            self.assertIn("alias", str(error))
        error = self.refused(self.api([1, 1], [None]), OSError)
        self.assertIs(type(error), OSError)

    def test_directory_is_never_read_as_replaced(self):
        folder = wf.PureWindowsPath(r"C:\private")
        self.assertEqual(self.api([0], [str(folder)], 0x10).info(7, folder, directory=True).links, 0)
        error = self.refused(self.api([0, 0], [r"C:\other"], 0x10), directory=True, path=folder)
        self.assertIs(type(error), PermissionError)

    class API:
        """One answer per open of the name: replaced in open(), replaced at the
        second check, a refusal to raise, or "file" for a valid file."""

        def __init__(self, *answers):
            self.answers, self.handles, self.closed = list(answers), [], []

        def open(self, path, **options):
            self.handles.append(30 + len(self.handles))
            if self.answers[0] == "replaced in open":
                self.answers.pop(0)
                self.closed.append(self.handles[-1])  # open() closes what it refuses
                raise wf.PrivateFileUnlinked("replaced")
            if isinstance(self.answers[0], OSError) and self.answers[0].errno == 2:
                raise self.answers.pop(0)
            return self.handles[-1]

        def validate_acl(self, handle, *, private):
            pass

        def info(self, handle, path, *, directory):
            answer = self.answers.pop(0)
            if answer == "replaced at the second check":
                raise wf.PrivateFileUnlinked("replaced")
            if isinstance(answer, OSError):
                raise answer
            return wf.FILE_INFO(volume=1, links=1)

        def read(self, handle, *, limit):
            return b"content of %d" % handle

        def close(self, handle):
            self.closed.append(handle)

    def pinned(self, api):
        pinned = wf.PinnedDirectory.__new__(wf.PinnedDirectory)
        pinned.path, pinned._api = Path("private"), api
        pinned._pins = [(Path("private"), 1, (1, 0, 1), True)]
        self.checks = 0

        def check():
            self.checks += 1
        pinned._check = check
        return pinned

    def test_replaced_object_is_closed_and_the_name_is_opened_and_validated_again(self):
        api = self.API("replaced at the second check", "replaced in open", "file")
        with patch.object(wf.time, "sleep") as sleep:
            self.assertEqual(self.pinned(api).read_private("task-runtime.json", limit=64), b"content of 32")
        self.assertEqual(api.handles, [30, 31, 32])
        self.assertEqual(api.closed, [30, 31, 32])  # no replaced object stays open; the read one closes last
        self.assertEqual(self.checks, 3)  # the pins are checked before each open
        self.assertEqual(len(sleep.call_args_list), 2)

    def test_file_that_never_settles_is_a_distinct_error_after_a_bounded_number_of_opens(self):
        api = self.API(*["replaced at the second check", "replaced in open"] * wf._REPLACED_ATTEMPTS)
        # A clock that stands still: only the count limit and the wait sequence act.
        with patch.object(wf.time, "sleep") as sleep, patch.object(wf.time, "monotonic", return_value=0.0), \
                self.assertRaises(wf.PrivateFileUnsettled) as unsettled:
            self.pinned(api).read_private("task-runtime.json", limit=64)
        self.assertEqual(len(api.handles), wf._REPLACED_ATTEMPTS)
        self.assertEqual(api.closed, api.handles)
        self.assertNotIsInstance(unsettled.exception, wf.PrivateFileUnlinked)
        self.assertNotIn("hardlink", str(unsettled.exception))
        self.assertIn("did not settle", str(unsettled.exception))
        self.assertIsInstance(unsettled.exception.__cause__, wf.PrivateFileUnlinked)
        waits = [call.args[0] for call in sleep.call_args_list]
        self.assertEqual(waits, [0.002, 0.004, 0.008, 0.016, 0.032, 0.05, 0.05])
        self.assertAlmostEqual(sum(waits), 0.162)  # the number in the design record
        self.assertLess(sum(waits), wf._REPLACED_SECONDS)
        self.assertLessEqual(wf._REPLACED_ATTEMPTS, 8)
        self.assertLessEqual(wf._REPLACED_SECONDS, 2)

    def clocked(self, *, open_takes=0.0, check_takes=0.0):
        """A reader on a clock that only its own steps move: (read, opens, waits, now).

        `opens` holds the time at which each open of the name started.
        """
        api = self.API(*["replaced at the second check"] * wf._REPLACED_ATTEMPTS)
        now, opens, waits, real_open = [0.0], [], [], api.open

        def opened(path, **options):
            opens.append(now[0])
            now[0] += open_takes
            return real_open(path, **options)

        def wait(seconds):
            self.assertGreater(seconds, 0, "a wait of no time or less")
            waits.append(seconds)
            now[0] += seconds

        def check():
            now[0] += check_takes
        api.open = opened
        pinned = self.pinned(api)
        pinned._check = check
        for patcher in (patch.object(wf.time, "monotonic", lambda: now[0]),
                        patch.object(wf.time, "sleep", wait)):
            patcher.start()
            self.addCleanup(patcher.stop)

        def read():
            with self.assertRaises(wf.PrivateFileUnsettled):
                pinned.read_private("task-runtime.json", limit=64)
        return read, opens, waits, now

    def test_no_open_starts_at_or_after_the_time_limit(self):
        read, opens, waits, now = self.clocked(open_takes=0.7)
        read()
        self.assertEqual(len(opens), 3)  # not the 8 that the count alone permits
        self.assertTrue(all(start < opens[0] + wf._REPLACED_SECONDS for start in opens), opens)

    def test_pin_check_that_passes_the_time_limit_ends_the_attempts(self):
        read, opens, waits, now = self.clocked(check_takes=1.5)
        read()
        # The second pin check ends after the limit: no third open.
        self.assertEqual(len(opens), 2)
        self.assertTrue(all(start < opens[0] + wf._REPLACED_SECONDS for start in opens), opens)
        self.assertGreater(now[0], opens[0] + wf._REPLACED_SECONDS)

    def test_wait_is_never_longer_than_the_remaining_time(self):
        left = 1 / 1024  # exact in binary; shorter than the first wait of 0.002
        read, opens, waits, now = self.clocked(open_takes=wf._REPLACED_SECONDS - left)
        read()
        self.assertEqual(waits, [left])
        self.assertEqual(now[0], opens[0] + wf._REPLACED_SECONDS)
        self.assertEqual(len(opens), 1)

    def test_real_refusal_is_not_opened_again(self):
        for refusal in (PermissionError("private-file path is a reparse point, hardlink, or wrong type"),
                        PermissionError("Windows private-file path has an alternate namespace/case alias"),
                        PermissionError("Windows private-file DACL grants foreign access"),
                        OSError("cannot validate final Windows private-file path")):
            api = self.API(refusal)
            with patch.object(wf.time, "sleep") as sleep, self.assertRaises(OSError) as refused:
                self.pinned(api).read_private("task-runtime.json", limit=64)
            self.assertIs(refused.exception, refusal)
            self.assertEqual((api.handles, api.closed, sleep.call_count), ([30], [30], 0))

    def test_refusal_of_the_file_that_has_the_name_now_stands(self):
        refusal = PermissionError("private-file path is a reparse point, hardlink, or wrong type")
        api = self.API("replaced at the second check", refusal)
        with patch.object(wf.time, "sleep"), self.assertRaises(PermissionError) as refused:
            self.pinned(api).read_private("task-runtime.json", limit=64)
        self.assertIs(refused.exception, refusal)
        self.assertEqual(api.closed, [30, 31])

    def test_file_removed_after_the_open_is_absent(self):
        api = self.API("replaced at the second check", FileNotFoundError(2, "gone"))
        with patch.object(wf.time, "sleep"), self.assertRaises(FileNotFoundError):
            self.pinned(api).read_private("task-runtime.json", limit=64)
        self.assertEqual(api.closed, [30])


@unittest.skipUnless(os.name == "nt", "requires native Windows local NTFS and profile ACLs")
class NativeTests(unittest.TestCase):
    def setUp(self):
        import uuid
        self.root = wf.address(Path.home() / (".unified-file-test-" + uuid.uuid4().hex))
        self.path = self.root / "config" / "auth"
        # Fail rather than silently weakening a broad runner/profile ancestor.
        with wf.directory(self.path, owned_root=self.root, create=True):
            pass
        self.addCleanup(self.cleanup)

    def cleanup(self):
        import shutil
        shutil.rmtree(self.root)

    def test_voice_diagnostics_use_native_private_storage_and_refuse_acl_changes(self):
        import json
        import threading
        import types
        package = types.ModuleType('fixture_voice')
        package.__path__ = [str(SOURCE.parent)]
        spec = importlib.util.spec_from_file_location(
            'fixture_voice.voice_diagnostics', SOURCE.with_name('voice_diagnostics.py'))
        voice = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'fixture_voice': package, 'fixture_voice.windows_files': wf}):
            spec.loader.exec_module(voice)
            diagnostics = voice.VoiceDiagnostics(self.root)
            event = threading.Event()
            event.set()
            call = types.SimpleNamespace(manager=types.SimpleNamespace(api_key='fixture-private-key'),
                provider='live', id='fixture-call', session_id='fixture-session',
                closing=True, closed=True, final=event, protocol_trace=voice.VoiceTrace())
            with patch.object(voice, 'MAX_BYTES', 1800):
                for index in range(20):
                    diagnostics.record(call, {'type': 'local.closed', 'event_id': f'closed_{index}'})
            self.assertFalse(diagnostics.storage_error)
            with wf.directory(diagnostics.directory, owned_root=self.root) as pinned:
                for name in ('events.jsonl', 'events.previous.jsonl'):
                    data = pinned.read_private(name, limit=1800)
                    self.assertTrue(data)
                    self.assertNotIn(b'fixture-private-key', data)
                    self.assertTrue(all(json.loads(line)['closed'] for line in data.splitlines()))
            path = diagnostics.directory / 'events.jsonl'
            before = path.read_bytes()
            self.set_dacl(path, '(A;;FR;;;WD)')
            try:
                diagnostics.record(call, {'type': 'local.closed'})
                self.assertTrue(diagnostics.storage_error)
                self.assertEqual(path.read_bytes(), before)
            finally:
                self.set_dacl(path)

    def set_dacl(self, path, grant=""):
        """Fixture-only ACL changes, restricted to newly allocated test paths."""
        self.assertTrue(path.is_relative_to(self.root))
        api = wf._API()
        descriptor = wf.PVOID()
        api.ok(api.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            f"D:P(A;OICI;FA;;;{api.sid})(A;OICI;FA;;;SY){grant}",
            1, C.byref(descriptor), None))
        security = C.WinDLL("advapi32", use_last_error=True)
        function = security.SetFileSecurityW
        function.argtypes = [C.c_wchar_p, wf.DWORD, wf.PVOID]
        function.restype = wf.BOOL
        try:
            api.ok(function(str(path), 0x80000004, descriptor))
        finally:
            api.LocalFree(descriptor)

    def security_text(self, path, *, inherited=False):
        api = wf._API()
        handle = api.open(path, directory=path.is_dir())
        owner, dacl, descriptor = wf.PVOID(), wf.PVOID(), wf.PVOID()
        text = C.c_wchar_p()
        convert = C.WinDLL("advapi32", use_last_error=True).ConvertSecurityDescriptorToStringSecurityDescriptorW
        convert.argtypes = [wf.PVOID, wf.DWORD, wf.DWORD, C.POINTER(C.c_wchar_p), wf.PDWORD]
        convert.restype = wf.BOOL
        try:
            error = api.GetSecurityInfo(handle, 1, 5, C.byref(owner), None,
                                        C.byref(dacl), None, C.byref(descriptor))
            if error:
                raise C.WinError(error)
            if inherited:
                # SDDL may abbreviate an owner SID (for example LA). Check the
                # actual owner and inheritance structure, not display spelling.
                self.assertTrue(api.sid_text(owner) == api.sid, "fixture must be TokenUser-owned")
                control, revision = wf.WORD(), wf.DWORD()
                api.ok(api.GetSecurityDescriptorControl(descriptor, C.byref(control), C.byref(revision)))
                self.assertTrue(control.value & 4 and dacl, "fixture must have a non-null DACL")
                self.assertFalse(control.value & 0x1000, "fixture DACL must be unprotected")
                acl, inherited_aces = wf.ACL_INFO(), []
                api.ok(api.GetAclInformation(dacl, C.byref(acl), C.sizeof(acl), 2))
                for index in range(acl.count):
                    pointer = wf.PVOID()
                    api.ok(api.GetAce(dacl, index, C.byref(pointer)))
                    inherited_aces.append(bool(C.cast(pointer, C.POINTER(wf.ACE)).contents.flags & 0x10))
                self.assertTrue(any(inherited_aces), "fixture must have real inherited ACEs")
            api.ok(convert(descriptor, 1, 5, C.byref(text), None))
            return text.value
        finally:
            if text:
                api.LocalFree(C.cast(text, wf.PVOID))
            if descriptor:
                api.LocalFree(descriptor)
            api.close(handle)

    def junction(self, link, target):
        self.assertTrue(link.is_relative_to(self.root))
        self.assertTrue(target.is_relative_to(self.root))
        # mklink /J requires neither Developer Mode nor symlink privilege.
        # Separate arguments let list2cmdline quote spaces without backslash-
        # escaping embedded quotes that cmd.exe would treat as path characters.
        result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))

    def test_safe_inherited_existing_directory_and_file(self):
        inherited = self.root / "inherited"
        target = inherited / "session-secret"
        api = wf._API()
        descriptor = wf.PVOID()
        # An elevated token can default to Administrators ownership. Specify
        # TokenUser at creation, but leave the DACL absent so the kernel
        # inherits the private parent's ACEs. Do not protect or repair it.
        api.ok(api.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            f"O:{api.sid}", 1, C.byref(descriptor), None))
        try:
            security = wf.SECURITY_ATTRIBUTES(C.sizeof(wf.SECURITY_ATTRIBUTES), descriptor, False)
            api.ok(api.CreateDirectoryW(str(inherited), C.byref(security)))
            handle = api.CreateFileW(str(target), 0x40000000, 0, C.byref(security),
                                     1, 0x200000, None)  # CREATE_NEW | OPEN_REPARSE_POINT
            self.assertNotEqual(handle, wf._INVALID, "cannot create inherited fixture file")
            try:
                api.write(handle, b"safe-existing")
            finally:
                api.close(handle)
        finally:
            api.LocalFree(descriptor)
        before = {path: self.security_text(path, inherited=True) for path in (inherited, target)}
        with wf.directory(inherited, owned_root=self.root) as pinned:
            self.assertEqual(pinned.read_private("session-secret", limit=64), b"safe-existing")
        self.assertEqual({path: self.security_text(path, inherited=True) for path in before}, before)

    def test_foreign_read_grant_is_refused_unmodified(self):
        with wf.directory(self.path, owned_root=self.root) as pinned:
            pinned.publish_private("control-token", b"sentinel")
        target = self.path / "control-token"
        self.set_dacl(target, "(A;;FR;;;WD)")
        before = self.security_text(target)
        with wf.directory(self.path, owned_root=self.root) as pinned:
            with self.assertRaisesRegex(PermissionError, "foreign access"):
                pinned.read_private(target.name, limit=64)
            with self.assertRaisesRegex(PermissionError, "foreign access"):
                pinned.publish_private(target.name, b"bad", replace=True)
        self.assertEqual(self.security_text(target), before)
        self.assertEqual(target.read_bytes(), b"sentinel")

    def test_missing_child_below_broad_parent_creates_nothing(self):
        ancestor = self.root / "broad"
        ancestor.mkdir()
        self.set_dacl(ancestor, "(A;;0x100;;;WD)")  # FILE_WRITE_ATTRIBUTES
        before = self.security_text(ancestor)
        with self.assertRaisesRegex(PermissionError, "foreign access"):
            with wf.directory(ancestor / "app/config", owned_root=ancestor / "app", create=True):
                pass
        self.assertFalse((ancestor / "app").exists())
        self.assertEqual(self.security_text(ancestor), before)

    def test_intermediate_and_leaf_junctions_preserve_outside_sentinel(self):
        outside = self.root / "outside target"
        outside.mkdir()
        sentinel = outside / "control-token"
        sentinel.write_bytes(b"outside-sentinel")
        app = self.root / "app with spaces"
        with wf.directory(app, owned_root=app, create=True):
            pass
        intermediate = app / "config"
        self.junction(intermediate, outside)
        with self.assertRaisesRegex(PermissionError, "reparse point"):
            with wf.directory(intermediate / "auth", owned_root=app, create=True):
                pass
        self.assertFalse((outside / "auth").exists())
        intermediate.rmdir()
        leaf = self.path / "control-token"
        self.junction(leaf, outside)
        with wf.directory(self.path, owned_root=self.root) as pinned:
            with self.assertRaisesRegex(PermissionError, "reparse point"):
                pinned.read_private("control-token", limit=64)
            with self.assertRaisesRegex(PermissionError, "reparse point"):
                pinned.publish_private("control-token", b"bad", replace=True)
        self.assertEqual(sentinel.read_bytes(), b"outside-sentinel")
        leaf.rmdir()

    def test_profile_roundtrip_and_no_replace(self):
        with wf.directory(self.path, owned_root=self.root) as pinned:
            pinned.publish_private("control-token", b"first\n")
            with self.assertRaises(FileExistsError):
                pinned.publish_private("control-token", b"second\n")
            self.assertEqual(pinned.read_private("control-token", limit=64), b"first\n")
            with self.assertRaises(ValueError):
                pinned.read_private("control-token", limit=2)
            with self.assertRaises(FileNotFoundError):
                pinned.read_private("absent", limit=64)

    def test_private_acl_at_creation_before_write(self):
        api = wf._API()
        original = wf._API.write

        def checked_write(instance, handle, content):
            instance.validate_acl(handle, private=True)
            descriptor = wf.PVOID()
            control, revision = wf.WORD(), wf.DWORD()
            error = instance.GetSecurityInfo(handle, 1, 5, None, None, None, None, C.byref(descriptor))
            if error:
                raise C.WinError(error)
            try:
                instance.ok(instance.GetSecurityDescriptorControl(descriptor, C.byref(control), C.byref(revision)))
                self.assertTrue(control.value & 0x1000)  # protected BEFORE bytes
            finally:
                instance.LocalFree(descriptor)
            original(instance, handle, content)

        with patch.object(wf._API, "write", checked_write):
            with wf.directory(self.path, owned_root=self.root) as pinned:
                pinned.publish_private("session-secret", b"not-real-credentials")
        # New directories too, not merely a post-write chmod.
        for path in (self.root, self.root / "config", self.path):
            self.assertIn("D:P", self.security_text(path))
            handle = api.open(path, directory=True)
            try:
                api.validate_acl(handle, private=True)
            finally:
                api.close(handle)

    def test_case_alias_refused_without_modification(self):
        with wf.directory(self.path, owned_root=self.root) as pinned:
            pinned.publish_private("control-token", b"exact")
            with self.assertRaises((PermissionError, FileNotFoundError)):
                pinned.read_private("CONTROL-TOKEN", limit=64)
            self.assertEqual(pinned.read_private("control-token", limit=64), b"exact")

    def test_hardlink_rejected_without_modification(self):
        with wf.directory(self.path, owned_root=self.root) as pinned:
            pinned.publish_private("control-token", b"sentinel")
        alias = self.root / "alias"
        os.link(self.path / "control-token", alias)
        with wf.directory(self.path, owned_root=self.root) as pinned:
            with self.assertRaises(PermissionError):
                pinned.read_private("control-token", limit=64)
            with self.assertRaises(PermissionError):
                pinned.publish_private("control-token", b"changed", replace=True)
        self.assertEqual(alias.read_bytes(), b"sentinel")

    def test_pinned_reader_survives_replace_and_sharing_conflict_preserves_old(self):
        api = wf._API()
        target = self.path / "control-token"
        with wf.directory(self.path, owned_root=self.root) as pinned:
            pinned.publish_private(target.name, b"old")
            handle = api.open(target)
            try:
                pinned.publish_private(target.name, b"new", replace=True)
                self.assertEqual(api.read(handle, limit=64), b"old")
            finally:
                api.close(handle)
            # Explicit non-delete-sharing handle should make replacement fail.
            blocking = api.CreateFileW(str(target), 0x80000000, 1, None, 3, 0x200000, None)
            self.assertNotEqual(blocking, wf._INVALID)
            try:
                with self.assertRaises(OSError):
                    pinned.publish_private(target.name, b"bad", replace=True)
            finally:
                api.close(blocking)
            self.assertEqual(pinned.read_private(target.name, limit=64), b"new")

    def test_config_replacements_keep_each_open_private_reader_snapshot(self):
        api = wf._API()
        target = self.path / "server.yaml"
        snapshots = [b"port: 9001\n# old\n", b"port: 9002\n# middle\n", b"port: 9003\n# latest\n"]
        handles = []
        try:
            with wf.directory(self.path, owned_root=self.root) as pinned:
                pinned.publish_private(target.name, snapshots[0])
                handles.append(api.open(target))
                pinned.publish_private(target.name, snapshots[1], replace=True)
                handles.append(api.open(target))
                pinned.publish_private(target.name, snapshots[2], replace=True)
                self.assertEqual(pinned.read_private(target.name, limit=1024), snapshots[2])
                for handle, expected in zip(handles, snapshots):
                    api.validate_acl(handle, private=True)
                    self.assertEqual(api.read(handle, limit=1024), expected)
                self.assertEqual([path.name for path in self.path.iterdir()], ["server.yaml"])
        finally:
            for handle in handles:
                api.close(handle)

    def test_no_replace_kernel_collision_preserves_open_complete_credential_winner(self):
        # Force a winner between the missing-destination check and rename, not
        # merely the early FileExistsError fast path.
        original = wf._API.move
        target = self.path / "control-token"
        winner = b"complete-winner\n"
        handles = []

        def concurrent_winner(instance, handle, destination, *, replace):
            self.assertEqual(destination, target)
            self.assertFalse(replace)
            with wf.directory(self.path, owned_root=self.root) as other:
                # Avoid recursive injection for the winner's publication.
                with patch.object(wf._API, "move", original):
                    other.publish_private(target.name, winner)
            handles.append(instance.open(target))
            return original(instance, handle, destination, replace=replace)

        api = wf._API()
        try:
            with wf.directory(self.path, owned_root=self.root) as pinned:
                with patch.object(wf._API, "move", concurrent_winner):
                    with self.assertRaises(FileExistsError):
                        pinned.publish_private(target.name, b"loser\n")
                self.assertEqual(pinned.read_private(target.name, limit=1024), winner)
                self.assertEqual(api.read(handles[0], limit=1024), winner)
                self.assertEqual([path.name for path in self.path.iterdir()], ["control-token"])
        finally:
            for handle in handles:
                api.close(handle)

    def test_partial_temporary_write_failure_never_exposes_partial_destination(self):
        with wf.directory(self.path, owned_root=self.root) as pinned:
            pinned.publish_private("control-token", b"complete-old")
            original = wf._API.write

            def partial(instance, handle, content):
                original(instance, handle, content[:3])
                self.assertEqual(pinned.read_private("control-token", limit=64), b"complete-old")
                raise OSError("injected partial write failure")

            with patch.object(wf._API, "write", partial), self.assertRaises(OSError):
                pinned.publish_private("control-token", b"new-content", replace=True)
            self.assertEqual(pinned.read_private("control-token", limit=64), b"complete-old")
            self.assertEqual(sorted(path.name for path in self.path.iterdir()), ["control-token"])

    def test_subprocess_credential_race(self):
        script = """
import importlib.util, pathlib, sys, time
s = importlib.util.spec_from_file_location("wf", sys.argv[1])
w = importlib.util.module_from_spec(s); s.loader.exec_module(w)
root = pathlib.Path(sys.argv[2])
(root / ("ready-" + sys.argv[3])).write_text("ready")
deadline = time.monotonic() + 30
while not (root / "go").exists():
    if time.monotonic() > deadline: raise RuntimeError("race gate timed out")
    time.sleep(.01)
with w.directory(root / "config/auth", owned_root=root) as p:
    try: p.publish_private("control-token", sys.argv[3].encode())
    except FileExistsError: pass
    print(p.read_private("control-token", limit=100000).decode())
"""
        values = ["a" * 65537, "b" * 65537]
        # Do not put the large value on Windows' bounded command line.
        script = script.replace("sys.argv[3].encode()", "(sys.argv[3] * 65537).encode()")
        processes = [subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c", script,
                                      str(SOURCE), str(self.root), value[0]],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                     for value in values]
        def settle():
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=10)
        self.addCleanup(settle)
        import time
        deadline = time.monotonic() + 30
        while not all((self.root / ("ready-" + value[0])).exists() for value in values):
            if time.monotonic() > deadline or any(process.poll() is not None for process in processes):
                self.fail("credential contenders did not reach the race gate")
            time.sleep(.01)
        (self.root / "go").write_text("go")
        results = [process.communicate(timeout=30) for process in processes]
        for process, (_, error) in zip(processes, results):
            self.assertEqual(process.returncode, 0, error.decode())
        self.assertEqual(results[0][0], results[1][0])
        self.assertIn(results[0][0].decode().strip(), values)


_REPLACING_WRITER = r"""
import importlib.util, pathlib, sys, time
s = importlib.util.spec_from_file_location("wf", sys.argv[1])
w = importlib.util.module_from_spec(s); s.loader.exec_module(w)
root, name, mode = pathlib.Path(sys.argv[2]), sys.argv[3], sys.argv[4]
with w.directory(root / "config/auth", owned_root=root) as p:
    print("ready", flush=True)
    if mode == "step":
        for line in sys.stdin:
            p.publish_private(name, line.strip().encode(), replace=True)
            print("done", flush=True)
    else:
        values, count, deadline = [b"a" * 70000, b"b" * 300], 0, time.monotonic() + 60
        while not (root / "stop").exists() and time.monotonic() < deadline:
            p.publish_private(name, values[count % 2], replace=True)
            count += 1
        print(count, flush=True)
"""


class _Child:
    """Another process whose output lines are read with a time limit.

    A wait for a line that never comes ends at the limit. Errors of the child
    arrive on the same stream, so a failure message can show them.
    """

    def __init__(self, arguments):
        import queue
        import threading
        self.process = subprocess.Popen(arguments, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True)
        self.lines = queue.Queue()

        def pump():
            for line in self.process.stdout:
                self.lines.put(line.rstrip("\n"))
            self.lines.put(None)
        threading.Thread(target=pump, daemon=True).start()

    def line(self, seconds=30):
        """The next line; None at the end of the output or at the limit."""
        import queue
        try:
            line = self.lines.get(timeout=seconds)
        except queue.Empty:
            return None
        if line is None:
            self.lines.put(None)  # the end stays the end
        return line

    def stop(self):
        """End the child. Return the lines that nobody read (10 seconds in total)."""
        import time
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=10)
        unread, limit = [], time.monotonic() + 10
        while (left := limit - time.monotonic()) > 0 and (line := self.line(left)) is not None:
            unread.append(line)
        for stream in (self.process.stdin, self.process.stdout):
            try:
                stream.close()
            except OSError:
                pass
        return "\n".join(unread)


@unittest.skipUnless(os.name == "nt", "requires native Windows local NTFS and profile ACLs")
class ReplacedWhileReadNativeTests(unittest.TestCase):
    """The real reader against another process that replaces the same file.

    This is the stop command reading task-runtime.json while the host writes
    its next phase. The forced cases place one replacement at an exact point of
    the reader; the unforced case lets the two processes run freely.
    """

    NAME = "task-runtime.json"
    setUp, cleanup = NativeTests.setUp, NativeTests.cleanup

    def writer(self, mode):
        child = _Child([sys.executable, "-I", "-S", "-B", "-c", _REPLACING_WRITER,
                        str(SOURCE), str(self.root), self.NAME, mode])
        self.addCleanup(child.stop)
        self.expect(child, "ready")
        return child

    def expect(self, child, text):
        line = child.line()
        if line != text:
            self.fail(f"replacing writer: expected {text!r} within 30 seconds, got {line!r}. " + child.stop())

    def replace(self, child, content):
        """Another process renames one complete file over the name, now."""
        child.process.stdin.write(content + "\n")
        child.process.stdin.flush()
        self.expect(child, "done")

    def forced(self, pinned, point, replacements):
        """Run one replacement each time the reader reaches `point` with the file open.

        Returns the list of the reader's opens of the name.
        """
        api, target, opened, opens = pinned._api, str(self.path / self.NAME), set(), []
        create, acl, final = api.CreateFileW, api.validate_acl, api.GetFinalPathNameByHandleW

        def fire():
            if replacements:
                replacements.pop(0)()

        def create_file(path, *rest):
            handle = create(path, *rest)
            if path.lower() == target.lower():
                opens.append(path)
            if path == target and handle != wf._INVALID:
                opened.add(handle)
                if point == "after-open":
                    fire()
            return handle

        def validate_acl(handle, *, private):
            if point == "between-checks" and handle in opened:
                fire()
            return acl(handle, private=private)

        def final_path(handle, *rest):
            if point == "before-final-path" and handle in opened:
                fire()
            return final(handle, *rest)

        api.CreateFileW, api.validate_acl, api.GetFinalPathNameByHandleW = create_file, validate_acl, final_path
        return opens

    def test_facts_of_an_object_that_was_replaced_while_open(self):
        api = wf._API()
        target = self.path / self.NAME
        with wf.directory(self.path, owned_root=self.root) as pinned:
            pinned.publish_private(self.NAME, b"old")
            handle = api.open(target)
            try:
                pinned.publish_private(self.NAME, b"new", replace=True)
                info = wf.FILE_INFO()
                api.ok(api.GetFileInformationByHandle(handle, C.byref(info)))
                buffer = C.create_unicode_buffer(32768)
                length = api.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
                error = C.get_last_error()
                print(f"FACT replaced-open-object links={info.links} attributes={info.attributes:#x} "
                      f"final_path_length={length} final_path_error={error} "
                      f"final_path_tail={buffer.value[-60:]!r}")
                # Windows reports no link, and a path that is not the name.
                # Neither is a hardlink or an alias of the named file.
                self.assertEqual(info.links, 0)
                self.assertNotEqual(buffer.value.removeprefix("\\\\?\\"), str(target))
                with self.assertRaises(wf.PrivateFileUnlinked):
                    api.info(handle, target, directory=False)
                # The object keeps its private ACL and its complete content,
                # so a reader that validated it before the replacement reads
                # the complete earlier file.
                api.validate_acl(handle, private=True)
                self.assertEqual(api.read(handle, limit=64), b"old")
            finally:
                api.close(handle)
            self.assertEqual(pinned.read_private(self.NAME, limit=64), b"new")

    def test_replacement_between_the_two_checks_reads_the_new_file(self):
        # The exact place of the clean-room record: _existing's second info().
        writer = self.writer("step")
        with wf.directory(self.path, owned_root=self.root) as pinned:
            pinned.publish_private(self.NAME, b"old")
            opens = self.forced(pinned, "between-checks", [lambda: self.replace(writer, "new")])
            self.assertEqual(pinned.read_private(self.NAME, limit=64), b"new")
            self.assertEqual(len(opens), 2)

    def test_replacement_right_after_the_open_reads_the_new_file(self):
        writer = self.writer("step")
        with wf.directory(self.path, owned_root=self.root) as pinned:
            pinned.publish_private(self.NAME, b"old")
            opens = self.forced(pinned, "after-open", [lambda: self.replace(writer, "new")])
            self.assertEqual(pinned.read_private(self.NAME, limit=64), b"new")
            self.assertEqual(len(opens), 2)

    def test_replacement_before_the_final_path_query_reads_the_new_file(self):
        # Windows gives the replaced object a path below $Extend\$Deleted. That
        # is not an alternate path of the named file.
        writer = self.writer("step")
        with wf.directory(self.path, owned_root=self.root) as pinned:
            pinned.publish_private(self.NAME, b"old")
            opens = self.forced(pinned, "before-final-path", [lambda: self.replace(writer, "new")])
            self.assertEqual(pinned.read_private(self.NAME, limit=64), b"new")
            self.assertEqual(len(opens), 2)

    def test_file_replaced_at_every_attempt_is_unsettled_after_a_bounded_number_of_opens(self):
        writer = self.writer("step")
        with wf.directory(self.path, owned_root=self.root) as pinned:
            pinned.publish_private(self.NAME, b"old")
            replacements = [lambda: self.replace(writer, "new")] * (wf._REPLACED_ATTEMPTS + 3)
            opens = self.forced(pinned, "between-checks", replacements)
            # Only the count limit acts here: a slow runner must not end the
            # attempts earlier through the time limit.
            with patch.object(wf, "_REPLACED_SECONDS", 600.0), \
                    self.assertRaises(wf.PrivateFileUnsettled) as unsettled:
                pinned.read_private(self.NAME, limit=64)
            self.assertNotIn("hardlink", str(unsettled.exception))
            self.assertEqual(len(opens), wf._REPLACED_ATTEMPTS)
            self.assertEqual(len(replacements), 3)
            del replacements[:]
            self.assertEqual(pinned.read_private(self.NAME, limit=64), b"new")

    def test_no_new_open_after_the_time_limit(self):
        writer = self.writer("step")
        with wf.directory(self.path, owned_root=self.root) as pinned:
            pinned.publish_private(self.NAME, b"old")
            opens = self.forced(pinned, "between-checks", [lambda: self.replace(writer, "new")] * 3)
            with patch.object(wf, "_REPLACED_SECONDS", 0.0), self.assertRaises(wf.PrivateFileUnsettled):
                pinned.read_private(self.NAME, limit=64)
            self.assertEqual(len(opens), 1)

    def test_file_removed_while_a_third_handle_keeps_it(self):
        # Not a replacement, and not changed by the reopening: this case
        # records what the reader gets while another handle keeps a removed
        # file. Windows either has no such name, or refuses the open until
        # that handle closes ("Access is denied", error 5).
        import hashlib
        api = wf._API()
        with wf.directory(self.path, owned_root=self.root) as pinned, \
                wf.directory(self.path, owned_root=self.root) as remover:
            pinned.publish_private(self.NAME, b"old")
            digest = hashlib.sha256(b"old").hexdigest()
            third = api.open(self.path / self.NAME)
            try:
                opens = self.forced(pinned, "between-checks",
                                    [lambda: remover.remove_private(self.NAME, digest)])
                with patch.object(wf.time, "sleep") as sleep, self.assertRaises(OSError) as outcome:
                    pinned.read_private(self.NAME, limit=64)
                error = outcome.exception
                print(f"FACT removed-file-kept-by-a-third-handle reader={type(error).__name__} "
                      f"winerror={getattr(error, 'winerror', None)} opens={len(opens)}")
                self.assertNotIsInstance(error, (wf.PrivateFileUnlinked, wf.PrivateFileUnsettled))
                self.assertNotIn("hardlink", str(error))
                self.assertTrue(isinstance(error, FileNotFoundError) or error.winerror == 5, repr(error))
                # One open found the removed object, one more open met Windows' answer. No loop.
                self.assertEqual((len(opens), sleep.call_count), (2, 1))
            finally:
                api.close(third)
            with self.assertRaises(FileNotFoundError):
                pinned.read_private(self.NAME, limit=64)
            self.assertEqual(list(self.path.iterdir()), [])

    def test_refused_files_are_refused_at_the_first_open(self):
        with wf.directory(self.path, owned_root=self.root) as pinned:
            pinned.publish_private(self.NAME, b"sentinel")
            opens = self.forced(pinned, "none", [])
            with patch.object(wf.time, "sleep") as sleep:
                with self.assertRaises((PermissionError, FileNotFoundError)) as alias:
                    pinned.read_private(self.NAME.upper(), limit=64)
                self.assertNotIsInstance(alias.exception, (wf.PrivateFileUnlinked, wf.PrivateFileUnsettled))
                self.assertEqual(len(opens), 1)
                os.link(self.path / self.NAME, self.root / "alias")
                del opens[:]
                with self.assertRaises(PermissionError) as hardlink:
                    pinned.read_private(self.NAME, limit=64)
                self.assertIs(type(hardlink.exception), PermissionError)
                self.assertIn("hardlink", str(hardlink.exception))
                self.assertEqual(len(opens), 1)
                self.assertEqual(sleep.call_count, 0)
        self.assertEqual((self.root / "alias").read_bytes(), b"sentinel")

    def test_file_removed_while_the_reader_has_it_open_is_absent(self):
        import hashlib
        with wf.directory(self.path, owned_root=self.root) as pinned, \
                wf.directory(self.path, owned_root=self.root) as remover:
            pinned.publish_private(self.NAME, b"old")
            digest = hashlib.sha256(b"old").hexdigest()
            opens = self.forced(pinned, "between-checks", [lambda: remover.remove_private(self.NAME, digest)])
            # A removal is not a hardlink either. The reader finds no file.
            with self.assertRaises(FileNotFoundError):
                pinned.read_private(self.NAME, limit=64)
            self.assertEqual(len(opens), 2)
            self.assertEqual(list(self.path.iterdir()), [])

    def test_publication_whose_existing_file_was_replaced_still_publishes(self):
        writer = self.writer("step")
        with wf.directory(self.path, owned_root=self.root) as pinned:
            pinned.publish_private(self.NAME, b"old")
            self.forced(pinned, "between-checks", [lambda: self.replace(writer, "theirs")])
            pinned.publish_private(self.NAME, b"mine", replace=True)
            self.assertEqual(pinned.read_private(self.NAME, limit=64), b"mine")
            self.assertEqual([path.name for path in self.path.iterdir()], [self.NAME])

    def test_publication_that_another_process_replaces_at_once_is_not_a_failure(self):
        writer = self.writer("step")
        with wf.directory(self.path, owned_root=self.root) as pinned:
            pinned.publish_private(self.NAME, b"old")
            move = pinned._api.move

            def moved_then_replaced(handle, destination, *, replace):
                move(handle, destination, replace=replace)
                self.replace(writer, "theirs")
            pinned._api.move = moved_then_replaced
            pinned.publish_private(self.NAME, b"mine", replace=True)
            self.assertEqual(pinned.read_private(self.NAME, limit=64), b"theirs")
            self.assertEqual([path.name for path in self.path.iterdir()], [self.NAME])

    def test_free_running_reads_against_a_replacing_process_never_fail(self):
        import time
        values = {b"a" * 70000, b"b" * 300}
        with wf.directory(self.path, owned_root=self.root) as pinned:
            pinned.publish_private(self.NAME, b"b" * 300)
            opens, real = [0], pinned._api.open

            def counted(path, **options):
                opens[0] += path.name == self.NAME
                return real(path, **options)
            pinned._api.open = counted
            writer = self.writer("spin")
            reads, failures, deadline = 0, [], time.monotonic() + 6
            try:
                while reads < 3000 and time.monotonic() < deadline:
                    try:
                        content = pinned.read_private(self.NAME, limit=100000)
                    except OSError as failed:
                        failures.append(f"{type(failed).__name__}: {failed}")
                    else:
                        self.assertIn(content, values, "a read returned a mix or a partial file")
                    reads += 1
            finally:
                (self.root / "stop").write_text("stop")
            replacements = writer.line()
            if replacements is None or not replacements.isdigit():
                self.fail(f"replacing writer did not finish: {replacements!r}. " + writer.stop())
            from collections import Counter
            print(f"FACT free-running reads={reads} opens={opens[0]} (opens above reads: the name was "
                  f"opened again) replacements={replacements} failures={len(failures)} "
                  f"kinds={dict(Counter(failures))}")
            self.assertEqual(writer.process.wait(timeout=30), 0, writer.stop())
            self.assertEqual(failures[:3], [])


if __name__ == "__main__":
    unittest.main()