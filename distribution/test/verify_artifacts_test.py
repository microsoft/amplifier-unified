"""The two reviewed protocol data exports are the only wildcard exception."""
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('artifact_qualification', ROOT / 'scripts/verify-artifacts.py')
qualifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qualifier)


class ProtocolExports(unittest.TestCase):
    def archive(self, *, name=qualifier.PROTOCOL, exports=None, entries=None, extra=None, fields=None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / 'component.tgz'
        manifest = {'name': name, 'version': '0.1.2', 'exports': exports if exports is not None else qualifier.DATA_EXPORTS}
        manifest.update(fields or {})
        rows = [('package/package.json', json.dumps(manifest).encode())]
        rows += entries if entries is not None else [('package/schemas/test.json', b'{}'), ('package/fixtures/nested/test.json', b'{}')]
        with tarfile.open(path, 'w:gz') as archive:
            for filename, data in rows:
                member = tarfile.TarInfo(filename)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            if extra is not None:
                archive.addfile(extra)
        return path

    def verify(self, path, name=qualifier.PROTOCOL):
        qualifier.verify_archive(path, name, '0.1.2')

    def test_exact_shipped_protocol_archive(self):
        receipt = json.loads((ROOT / 'components.json').read_text())['components'][qualifier.PROTOCOL]
        qualifier.verify_archive(ROOT / receipt['artifact'], qualifier.PROTOCOL, receipt['version'])

    def test_two_exact_patterns_cover_nonempty_nested_regular_files(self):
        self.verify(self.archive())

    def test_other_packages_cannot_use_known_patterns(self):
        with self.assertRaisesRegex(ValueError, 'Wildcard'):
            self.verify(self.archive(name='different-package'), 'different-package')

    def test_other_patterns_keys_targets_and_nested_conditions_are_refused(self):
        for exports in ({'./other/*': './schemas/*'}, {'./schemas/*': './fixtures/*'},
                        {'./schemas/*': {'default': './schemas/*'}},
                        {'./schemas/*': './schemas/test.json'},
                        {'./schemas/test.json': './schemas/*'},
                        {'default': {'./unexpected/*': './schemas/test.json'}}):
            with self.subTest(exports=exports), self.assertRaisesRegex(ValueError, 'Wildcard'):
                self.verify(self.archive(exports=exports))

    def test_missing_and_empty_target_namespaces_are_refused(self):
        for entries in ([], [('package/schemas/test.json', b'{}')],
                        [('package/schemas/test.json', b''), ('package/fixtures/test.json', b'{}')]):
            with self.subTest(entries=entries), self.assertRaisesRegex(ValueError, 'missing or empty'):
                self.verify(self.archive(entries=entries))

    def test_unsafe_paths_are_not_normalized_into_matches(self):
        for filename in ('/package/fixtures/test.json', 'package/fixtures/../test.json',
                         'package//fixtures/test.json', 'package/./fixtures/test.json',
                         'package/fixtures/with\\\\separator.json', 'package/fixtures/test*.json',
                         'other/fixtures/test.json', 'package/fixtures/control\x01.json'):
            with self.subTest(filename=filename), self.assertRaisesRegex(ValueError, 'Unsafe'):
                self.verify(self.archive(entries=[(filename, b'{}')]))

    def test_links_directories_and_special_members_are_refused(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.DIRTYPE):
            member = tarfile.TarInfo('package/fixtures/link.json')
            member.type = kind
            member.linkname = 'package/schemas/test.json'
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'Unsafe'):
                self.verify(self.archive(extra=member))

    def test_duplicate_members_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            self.verify(self.archive(entries=[('package/schemas/test.json', b'{}')] * 2))

    def test_member_size_and_total_size_bounds(self):
        with self.assertRaisesRegex(ValueError, 'bounds'):
            self.verify(self.archive(entries=[('package/schemas/large.json', b'x' * (qualifier.MAX_PROTOCOL_MEMBER_BYTES + 1))]))
        entries = [('package/schemas/' + str(i) + '.json', b'x' * qualifier.MAX_PROTOCOL_MEMBER_BYTES) for i in range(8)]
        with self.assertRaisesRegex(ValueError, 'bounds'):
            self.verify(self.archive(entries=entries))

    def test_member_count_bound(self):
        entries = [('package/schemas/' + str(i) + '.json', b'{}') for i in range(qualifier.MAX_PROTOCOL_MEMBERS)]
        with self.assertRaisesRegex(ValueError, 'bounds'):
            self.verify(self.archive(entries=entries))

    def test_wildcards_in_runtime_fields_are_not_data_exports(self):
        for field in ('main', 'module', 'types', 'bin'):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'Wildcard'):
                self.verify(self.archive(fields={field: './schemas/*'}))

    def test_malformed_archive_is_refused(self):
        path = self.archive()
        path.write_bytes(b'not a tar archive')
        with self.assertRaises(tarfile.ReadError):
            self.verify(path)

    def test_exact_runtime_entrypoints_still_require_members(self):
        self.verify(self.archive(name='ordinary', exports='./schemas/test.json'), 'ordinary')
        with self.assertRaisesRegex(ValueError, 'entrypoint missing'):
            self.verify(self.archive(name='ordinary', exports='./missing.js'), 'ordinary')

    def test_manifest_identity_and_regular_manifest_remain_required(self):
        with self.assertRaisesRegex(ValueError, 'identity'):
            self.verify(self.archive(name='other'))
        member = tarfile.TarInfo('package/package.json')
        member.type = tarfile.SYMTYPE
        member.linkname = 'package/schemas/test.json'
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            self.verify(self.archive(extra=member))


if __name__ == '__main__':
    unittest.main()
