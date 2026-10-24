#!/usr/bin/env python3
"""Build a verified local Git artifact from a completed, sealed mission."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.delivery import LocalDelivery
from app.sandbox import sandbox
from app.store import store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mission", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(LocalDelivery(store, sandbox).deliver(args.mission), indent=2, ensure_ascii=False))
    except (ValueError, RuntimeError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
