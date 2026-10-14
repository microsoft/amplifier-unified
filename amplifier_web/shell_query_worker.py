"""Catalog reader entry point; shares the bounded, EOF-owned display transport."""
import argparse
from .history_projection_worker import run, MEMORY_LIMIT
from .shell_query_model import ShellQueryModel

INPUT_LIMIT = 8 * 1024 * 1024
OUTPUT_LIMIT = 4 * 1024 * 1024


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--spool', required=True)
    parser.add_argument('--cache-home', required=True)
    parser.add_argument('--memory-limit', type=int, default=MEMORY_LIMIT)
    args = parser.parse_args()
    run(args.spool, args.cache_home, model_factory=ShellQueryModel,
        memory_limit=args.memory_limit, input_limit=INPUT_LIMIT, output_limit=OUTPUT_LIMIT)
