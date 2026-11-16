"""Reference consumer. JSON only, read-only, bounded cadence; Ctrl-C stops watch."""
import argparse
import json
import time
from .reader import discover


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--limit", type=int, default=512)
    parser.add_argument("--instance", help="Exact producer instance ID; bypass accumulated older instances")
    args = parser.parse_args()
    try:
        while True:
            print(json.dumps(discover(args.directory, limit=args.limit, instance=args.instance), sort_keys=True), flush=True)
            if not args.watch:
                return
            time.sleep(max(.5, min(60, args.interval)))
    except (KeyboardInterrupt, BrokenPipeError):
        pass


if __name__ == "__main__":
    main()
