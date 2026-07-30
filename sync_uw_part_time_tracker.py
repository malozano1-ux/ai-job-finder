#!/usr/bin/env python3
"""Append newly discovered UW student jobs to the UW Part-Time tracker."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from google.oauth2 import service_account
from googleapiclient.discovery import build

from sync_applied_jobs import rows_to_jobs


SHEETS_WRITE = ["https://www.googleapis.com/auth/spreadsheets"]


def tracker_rows(result: dict) -> list[list[str]]:
    rows: list[list[str]] = []
    discovered = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%d/%m/%Y")
    for job in result.get("jobs", []):
        notes = " | ".join([
            f"Discovered {discovered}",
            f"Fit {job.get('fit_score', 0)}/100",
            f"Department: {job.get('department', 'not stated')}",
            f"Hours: {job.get('hours_per_week', 'not stated')}",
            f"Pay: {job.get('pay', 'not stated')}",
            f"Work-study: {job.get('work_study', 'not stated')}",
            f"Eligibility: {job.get('student_eligibility', 'not stated')}",
            f"Deadline: {job.get('deadline', 'not stated')}",
            f"Gap: {job.get('gap', 'not stated')}",
        ])
        rows.append([
            str(job.get("company", "")),
            str(job.get("title", "")),
            str(job.get("url", "")),
            "",
            "Discovered",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            notes,
        ])
    return rows


def append_new_rows(
    credentials_file: Path,
    spreadsheet_id: str,
    result: dict,
    tab: str = "UW Part-Time",
) -> int:
    credentials = service_account.Credentials.from_service_account_file(
        str(credentials_file),
        scopes=SHEETS_WRITE,
    )
    sheets = build("sheets", "v4", credentials=credentials, cache_discovery=False)
    current = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab}'!A2:C",
        valueRenderOption="FORMATTED_VALUE",
    ).execute()
    existing = rows_to_jobs(current.get("values", []))
    existing_urls = {row["url"] for row in existing if row["url"]}
    existing_pairs = {
        (row["company"].casefold(), row["title"].casefold())
        for row in existing
    }
    fresh = [
        row for row in tracker_rows(result)
        if row[2] not in existing_urls
        and (row[0].casefold(), row[1].casefold()) not in existing_pairs
    ]
    if fresh:
        sheets.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=f"'{tab}'!A:O",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": fresh},
        ).execute()
    return len(fresh)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--spreadsheet-id", required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--tab", default="UW Part-Time")
    args = parser.parse_args()
    result = json.loads(args.input.read_text(encoding="utf-8"))
    count = append_new_rows(
        args.credentials,
        args.spreadsheet_id,
        result,
        args.tab,
    )
    print(f"Added {count} UW part-time opportunity row(s).")


if __name__ == "__main__":
    main()
