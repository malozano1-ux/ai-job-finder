#!/usr/bin/env python3
"""Download applied-job identities from a private Google Sheets tracker."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build


SHEETS_READONLY = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def rows_to_jobs(values: list[list[Any]]) -> list[dict[str, str]]:
    """Convert Company, Job Title, Job URL rows into a compact private snapshot."""
    jobs: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in values:
        padded = list(row) + ["", "", ""]
        company = str(padded[0] or "").strip()
        title = str(padded[1] or "").strip()
        url = str(padded[2] or "").strip()
        if not company or not title:
            continue
        key = (company.casefold(), title.casefold(), url)
        if key in seen:
            continue
        seen.add(key)
        jobs.append({"company": company, "title": title, "url": url})
    return jobs


def download_jobs(
    credentials_file: Path,
    spreadsheet_id: str,
    tab: str = "Job Tracker",
) -> list[dict[str, str]]:
    credentials = service_account.Credentials.from_service_account_file(
        str(credentials_file),
        scopes=SHEETS_READONLY,
    )
    sheets = build("sheets", "v4", credentials=credentials, cache_discovery=False)
    response = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab}'!A2:C",
        valueRenderOption="FORMATTED_VALUE",
    ).execute()
    return rows_to_jobs(response.get("values", []))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--spreadsheet-id", required=True)
    parser.add_argument("--tab", default="Job Tracker")
    parser.add_argument("--output", type=Path, default=Path("applied_jobs.json"))
    args = parser.parse_args()

    jobs = download_jobs(args.credentials, args.spreadsheet_id, args.tab)
    args.output.write_text(
        json.dumps(jobs, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Loaded {len(jobs)} applied job(s) from Google Sheets.")


if __name__ == "__main__":
    main()
