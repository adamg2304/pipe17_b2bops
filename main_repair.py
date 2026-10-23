"""CLI for the repair engine — a one-off manual heal without the webhook. Handy for a
run-job trigger or a local check.

  python main_repair.py --id '#BE123'
  python main_repair.py --id '#BE123.2' --type sr --force
  python main_repair.py --id '#BE123' --dry-run
"""
import json
import logging
import sys

import repair

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _arg(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def main():
    pid = _arg("--id")
    if not pid:
        print("usage: python main_repair.py --id '#BE123' [--type order|sr|auto] [--force] [--dry-run]")
        sys.exit(2)
    res = repair.diagnose_and_heal(
        pid, kind=_arg("--type", "auto"),
        force="--force" in sys.argv, dry_run="--dry-run" in sys.argv)
    print(json.dumps(res, indent=2))
    sys.exit(0 if res.get("ok") else 1)


if __name__ == "__main__":
    main()
