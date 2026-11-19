"""Start a macOS updater uv process with room to resolve concurrent Git sources."""

import os
import sys


def main(argv):
    import resource

    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    target = 4096 if hard == resource.RLIM_INFINITY else min(4096, hard)
    if soft != resource.RLIM_INFINITY and soft < target:
        # A system policy can impose a lower ceiling than RLIMIT_NOFILE. Keep
        # the original allowance if the OS rejects the increase.
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
        except (ValueError, OSError):
            pass
    os.execvp(argv[0], argv)


if __name__ == '__main__':
    main(sys.argv[1:])
