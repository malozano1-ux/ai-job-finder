#!/usr/bin/env python3
"""Choose the latest due local-time schedule slot and avoid duplicate runs."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


TIMEZONE = ZoneInfo("America/Los_Angeles")


def latest_due_slot(now: datetime, hours: list[int]) -> str:
    local = now.astimezone(TIMEZONE)
    candidates = [hour for hour in sorted(hours) if hour <= local.hour]
    if candidates:
        date = local.date()
        hour = candidates[-1]
    else:
        date = (local - timedelta(days=1)).date()
        hour = max(hours)
    return f"{date.isoformat()}-{hour:02d}"


def previous_slot(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("slot", ""))
    except (json.JSONDecodeError, OSError):
        return ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--hours", type=int, nargs="+", required=True)
    args = parser.parse_args()
    if args.event == "workflow_dispatch":
        print("run=true")
        print(f"slot=manual-{os.getenv('GITHUB_RUN_ID', 'local')}")
        return
    slot = latest_due_slot(datetime.now(TIMEZONE), args.hours)
    print(f"run={'false' if previous_slot(args.state) == slot else 'true'}")
    print(f"slot={slot}")


if __name__ == "__main__":
    main()
