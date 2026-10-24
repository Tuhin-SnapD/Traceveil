from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from . import __version__
from .engine import run_audit


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="traceveil", description="Evidence-first privacy deletion auditing")
    parser.add_argument("--version", action="version", version=f"Traceveil {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    audit = subparsers.add_parser("audit", help="audit every request in a workspace")
    audit.add_argument("--workspace", type=Path, required=True, help="directory containing the four CSV inputs and evidence files")
    audit.add_argument("--output", type=Path, help="report path (default: <workspace>/traceveil-report.json)")
    audit.add_argument("--verbose", action="store_true", help="show evidence extraction warnings")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if args.command == "audit":
        try:
            report = run_audit(args.workspace, args.output)
        except (OSError, ValueError, KeyError) as exc:
            parser.error(str(exc))
        destination = args.output or args.workspace / "traceveil-report.json"
        print(f"Audited {report['summary']['requests']} requests; report: {destination}")
        print(json.dumps(report["summary"], indent=2))
        return 0
    return 2
