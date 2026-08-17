"""CLI facade and stable public imports for Competitive Account Radar."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from monitor_core import *  # noqa: F401,F403
from monitor_core import (
    _ask, _comma_items, _load_observations_with_lines, _load_profile,
    _open_database, _parse_datetime, _reject_json_constant, _severity_rank,
    _timezone_is_valid, _validate_observation, _write_profile_atomically,
)
from monitor_delivery import build_digest, dispatch, publish, validate_alert_draft


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Competitive Account Radar deterministic monitor")
    subcommands = parser.add_subparsers(dest="command", required=True)
    init = subcommands.add_parser("init"); init.add_argument("--project", type=Path, required=True)
    check = subcommands.add_parser("validate"); check.add_argument("--project", type=Path, required=True)
    scan = subcommands.add_parser("evaluate"); scan.add_argument("--project", type=Path, required=True); scan.add_argument("--observations", type=Path, required=True); scan.add_argument("--trigger", default="manual"); scan.add_argument("--now")
    publication = subcommands.add_parser("publish"); publication.add_argument("--project", type=Path, required=True); publication.add_argument("--draft", type=Path, required=True); publication.add_argument("--now")
    delivery = subcommands.add_parser("dispatch"); delivery.add_argument("--project", type=Path, required=True); delivery.add_argument("--now")
    digest = subcommands.add_parser("digest"); digest.add_argument("--project", type=Path, required=True); digest.add_argument("--date", type=date.fromisoformat, required=True); digest.add_argument("--now")
    args = parser.parse_args(argv)
    if args.command == "init": return 0 if run_init_wizard(args.project) else 130
    if args.command == "validate":
        try: errors = validate_profile(_load_profile(args.project))
        except ValueError as error: print(error, file=sys.stderr); return 1
        if errors: print("\n".join(errors), file=sys.stderr); return 1
        print("monitoring.yaml is valid"); return 0
    try:
        now = _parse_datetime(args.now, "now") if getattr(args, "now", None) else datetime.now(timezone.utc)
        if args.command == "evaluate": document = evaluate(args.project, args.observations, args.trigger, now)
        elif args.command == "publish": document = publish(args.project, args.draft, now)
        elif args.command == "dispatch": document = dispatch(args.project, now)
        else: document = build_digest(args.project, args.date, now)
    except ValueError as error: print(error, file=sys.stderr); return 1
    print(json.dumps(document, sort_keys=True, ensure_ascii=False, default=str)); return 0


if __name__ == "__main__": raise SystemExit(main())
