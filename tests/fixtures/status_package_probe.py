"""Installed wheel ownership proof; run only in a disposable owned DTU env."""
import importlib.metadata as metadata
import json
from pathlib import Path
import subprocess
import sys


def main():
    unified = metadata.distribution('amplifier-unified')
    standalone = metadata.distribution('amplifier-status-emitter')
    files = lambda dist: {str(Path(dist.locate_file(row)).resolve()) for row in dist.files}
    overlap = files(unified) & files(standalone)
    assert not overlap, sorted(overlap)
    import amplifier_status
    import amplifier_web.status_protocol as private
    assert amplifier_status.__file__ != private.__file__
    assert not (standalone.requires or []), standalone.requires
    root = Path(sys.executable).parent
    for name in ('amplifier-status', 'amplifier-unified-status'):
        subprocess.run([str(root/name), '--help'], check=True, capture_output=True)
    # Uninstalling the standalone distribution must not remove Unified's copy or script.
    subprocess.run(['uv', 'pip', 'uninstall', '--python', sys.executable, 'amplifier-status-emitter'], check=True)
    subprocess.run([sys.executable, '-I', '-c', 'import amplifier_web.status_protocol'], check=True)
    subprocess.run([str(root/'amplifier-unified-status'), '--help'], check=True, capture_output=True)
    assert not (root/'amplifier-status').exists()
    print(json.dumps({'passed': True, 'overlappingRecordPaths': 0,
                      'standaloneDependencies': [], 'uninstallPreservedUnifiedImportAndScript': True}))


if __name__ == '__main__':
    main()
