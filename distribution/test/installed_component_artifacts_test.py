import importlib.util
import io
import json
import hashlib
from pathlib import Path
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('verify_installed', Path(__file__).resolve().parents[1] / 'scripts/verify-installed-components.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class InstalledArtifacts(unittest.TestCase):
    def fixture(self, root):
        name = '@amplifier/unified-client-web'
        package = {'name': name, 'version': '0.1.0', 'exports': './index.js'}
        data = {'package.json': json.dumps(package).encode(), 'index.js': b'export const webDirectory="dist";', 'dist/index.html': b'new-client'}
        archive = root / 'client.tgz'
        with tarfile.open(archive, 'w:gz') as stream:
            for path, body in data.items():
                member = tarfile.TarInfo('package/' + path); member.size = len(body)
                stream.addfile(member, io.BytesIO(body))
                target = root / 'node_modules' / name / path
                target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(body)
        (root / 'package.json').write_text(json.dumps({'dependencies': {name: 'file:client.tgz'}}))
        (root / 'components.json').write_text(json.dumps({'components': {name: {'version': '0.1.0', 'revision': 'a'*40, 'artifact': 'client.tgz', 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}}}))
        return root / 'node_modules' / name

    def test_current_archives_build_one_identical_served_client(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.fixture(root)
            self.assertEqual(gate.verify(root, stage_web=True), 1)
            self.assertEqual(gate.verify(root), 1)

    def test_latest_served_web_does_not_mask_a_stale_bundled_dependency(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); installed = self.fixture(root)
            gate.verify(root, stage_web=True)
            (installed / 'dist/index.html').write_bytes(b'old-client')
            with self.assertRaisesRegex(ValueError, 'Installed component differs'):
                gate.verify(root, stage_web=True)
            self.assertEqual((root / 'web/index.html').read_bytes(), b'new-client')

    def test_stale_served_assets_are_refused_without_overwriting_them(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.fixture(root)
            gate.verify(root, stage_web=True)
            (root / 'web/old.js').write_bytes(b'old')
            with self.assertRaisesRegex(ValueError, 'Served web differs'):
                gate.verify(root, stage_web=True)
            self.assertTrue((root / 'web/old.js').exists())


if __name__ == '__main__': unittest.main()
