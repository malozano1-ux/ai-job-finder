#!/usr/bin/env python3
"""Create or update application-deadline events in Google Calendar."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build

from job_finder_agent import canonical_url


CALENDAR_SCOPE = ["https://www.googleapis.com/auth/calendar.events"]


def resolve_deadline(value: str, today: date | None = None) -> tuple[date, str]:
    """Use a published deadline or a three-day personal deadline."""
    today = today or date.today()
    try:
        parsed = datetime.strptime(value.strip(), "%Y-%m-%d").date()
        if parsed >= today:
            return parsed, "Published application deadline"
    except (AttributeError, ValueError):
        pass
    return today + timedelta(days=3), "Personal apply-by deadline (no published deadline stated)"


def opportunity_key(job: dict[str, Any], source: str) -> str:
    url = canonical_url(str(job.get("url", "")))
    identity = f"{source}|{job.get('company', '')}|{job.get('title', '')}|{url}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]


def event_body(
    job: dict[str, Any],
    source: str,
    deadline: date,
    deadline_kind: str,
    key: str,
) -> dict[str, Any]:
    company = str(job.get("company", "")).strip()
    title = str(job.get("title", "")).strip()
    url = str(job.get("url", "")).strip()
    description = "\n".join([
        deadline_kind,
        f"Opportunity type: {source}",
        f"Company/department: {company}",
        f"Role: {title}",
        f"Apply: {url}",
        f"Fit score: {job.get('fit_score', 'not stated')}",
        f"Notes: {job.get('gap', 'not stated')}",
    ])
    return {
        "summary": f"Apply: {company} — {title}",
        "description": description,
        "start": {"date": deadline.isoformat()},
        "end": {"date": (deadline + timedelta(days=1)).isoformat()},
        "transparency": "transparent",
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": 1440},
                {"method": "popup", "minutes": 120},
            ],
        },
        "extendedProperties": {
            "private": {
                "opportunityKey": key,
                "opportunitySource": source,
            },
        },
    }


def sync_deadlines(
    credentials_file: Path,
    calendar_id: str,
    result: dict[str, Any],
    source: str,
) -> tuple[int, int]:
    credentials = service_account.Credentials.from_service_account_file(
        str(credentials_file),
        scopes=CALENDAR_SCOPE,
    )
    calendar = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    created = 0
    updated = 0
    for job in result.get("jobs", []):
        if not job.get("company") or not job.get("title") or not job.get("url"):
            continue
        deadline, kind = resolve_deadline(str(job.get("deadline", "")))
        key = opportunity_key(job, source)
        body = event_body(job, source, deadline, kind, key)
        matches = calendar.events().list(
            calendarId=calendar_id,
            privateExtendedProperty=f"opportunityKey={key}",
            maxResults=1,
            singleEvents=True,
        ).execute().get("items", [])
        if matches:
            event = matches[0]
            if (
                event.get("start", {}).get("date") != deadline.isoformat()
                or event.get("summary") != body["summary"]
                or event.get("description") != body["description"]
            ):
                calendar.events().update(
                    calendarId=calendar_id,
                    eventId=event["id"],
                    body=body,
                    sendUpdates="none",
                ).execute()
                updated += 1
        else:
            calendar.events().insert(
                calendarId=calendar_id,
                body=body,
                sendUpdates="none",
            ).execute()
            created += 1
    return created, updated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--calendar-id", required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument(
        "--source",
        choices=["Full-time job", "Summer 2027 internship", "UW part-time job"],
        required=True,
    )
    args = parser.parse_args()
    result = json.loads(args.input.read_text(encoding="utf-8"))
    created, updated = sync_deadlines(
        args.credentials,
        args.calendar_id,
        result,
        args.source,
    )
    print(f"Calendar deadlines created: {created}; updated: {updated}.")


if __name__ == "__main__":
    main()
