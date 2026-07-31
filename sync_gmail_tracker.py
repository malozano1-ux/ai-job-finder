#!/usr/bin/env python3
"""Sync application confirmations and hiring-stage emails into tracker tabs."""

from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from openai import OpenAI


GMAIL_SCOPE = ["https://www.googleapis.com/auth/gmail.readonly"]
SHEETS_SCOPE = ["https://www.googleapis.com/auth/spreadsheets"]
HEADERS = [
    "Company", "Job Title", "Job URL", "Date Applied", "Stage", "VC Firm",
    "VC Partner", "Recruiter Name", "Recruiter Email", "Recruiter LinkedIn",
    "Last Contact", "Next Follow-Up", "Resume Version", "Referral", "Notes",
]
STAGES = [
    "Discovered", "Applied", "Initial Interview", "Technical Assessment",
    "Final Interview", "Accepted", "Rejected", "Other",
]
STAGE_RANK = {
    "Discovered": 0, "Applied": 1, "Initial Interview": 2,
    "Technical Assessment": 3, "Final Interview": 4, "Other": 2,
    "Accepted": 5, "Rejected": 5,
}
TIMEZONE = ZoneInfo("America/Los_Angeles")


def gmail_credentials(token_file: Path) -> Credentials:
    credentials = Credentials.from_authorized_user_file(str(token_file), GMAIL_SCOPE)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    if not credentials.valid:
        raise RuntimeError(
            "The Gmail OAuth token is invalid. Run authorize_gmail.py locally again."
        )
    return credentials


def sheets_credentials(credentials_file: Path) -> service_account.Credentials:
    return service_account.Credentials.from_service_account_file(
        str(credentials_file), scopes=SHEETS_SCOPE
    )


def _decode_body(payload: dict[str, Any]) -> str:
    plain: list[str] = []
    rich: list[str] = []

    def visit(part: dict[str, Any]) -> None:
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data and mime in {"text/plain", "text/html"}:
            decoded = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
            text = decoded.decode("utf-8", errors="replace")
            if mime == "text/html":
                text = re.sub(r"<[^>]+>", " ", html.unescape(text))
                rich.append(re.sub(r"\s+", " ", text).strip())
            else:
                plain.append(re.sub(r"\s+", " ", text).strip())
        for child in part.get("parts", []):
            visit(child)

    visit(payload)
    return "\n".join(plain or rich)[:20_000]


def recent_candidate_emails(gmail: Any, days: int) -> list[dict[str, str]]:
    phrases = [
        '"thank you for applying"', '"thanks for applying"',
        '"application received"', '"application submitted"',
        '"received your application"', '"reviewing your application"',
        '"application update"', '"your application"', '"phone screen"',
        '"technical assessment"', '"coding assessment"', '"take-home"',
        '"final round"', '"final interview"', '"move forward"',
        '"not moving forward"', '"another candidate"', '"position has been filled"',
        '"presentar tu solicitud"', '"hemos recibido tu solicitud"',
    ]
    query = f"newer_than:{days}d -in:spam -in:trash (" + " OR ".join(phrases) + ")"
    rows: list[dict[str, str]] = []
    page_token: str | None = None
    while len(rows) < 200:
        response = gmail.users().messages().list(
            userId="me", q=query, maxResults=min(100, 200 - len(rows)),
            pageToken=page_token,
        ).execute()
        for item in response.get("messages", []):
            message = gmail.users().messages().get(
                userId="me", id=item["id"], format="full"
            ).execute()
            headers = {
                h["name"].lower(): h["value"]
                for h in message.get("payload", {}).get("headers", [])
            }
            timestamp = datetime.fromtimestamp(
                int(message["internalDate"]) / 1000, TIMEZONE
            )
            rows.append({
                "message_id": message["id"],
                "thread_id": message.get("threadId", ""),
                "date": timestamp.strftime("%Y-%m-%d"),
                "from": headers.get("from", ""),
                "to": headers.get("to", ""),
                "subject": headers.get("subject", ""),
                "body": _decode_body(message.get("payload", {})),
            })
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return rows


def read_tabs(
    sheets: Any, spreadsheet_id: str, tabs: list[str]
) -> dict[str, list[list[str]]]:
    result: dict[str, list[list[str]]] = {}
    for tab in tabs:
        response = sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=f"'{tab}'!A:O",
            valueRenderOption="FORMATTED_VALUE",
        ).execute()
        values = response.get("values", [])
        if values and values[0][:15] != HEADERS:
            raise RuntimeError(f"{tab} headers do not match the expected A:O schema.")
        result[tab] = values
    return result


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(text.splitlines()[1:-1]).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError("OpenAI did not return valid JSON.")
    return json.loads(text[start:end + 1])


def plan_changes(
    emails: list[dict[str, str]], tab_rows: dict[str, list[list[str]]]
) -> dict[str, Any]:
    tracker: list[dict[str, Any]] = []
    for tab, rows in tab_rows.items():
        for index, row in enumerate(rows[1:], start=2):
            if any(row):
                tracker.append({
                    "tab": tab, "row_number": index,
                    **dict(zip(HEADERS, row + [""] * (15 - len(row)))),
                })
    prompt = f"""
Analyze the Gmail messages and propose safe changes to this job application
tracker. Return JSON only. Never infer missing facts or use outside knowledge.

TRACKER ROWS:
{json.dumps(tracker, ensure_ascii=False)}

EMAILS:
{json.dumps(emails, ensure_ascii=False)}

Tabs have these meanings:
- Job Tracker: full-time jobs.
- Internship Tracker: internships and co-ops.
- UW Part-Time: University of Washington or other student part-time roles.

For an application confirmation, first match an existing Discovered row and
update it to Applied. Match using an exact requisition ID, exact URL, or
normalized company plus job title. If an email explicitly proves an application
but no row exists, add it to the correct tab. Company alone is insufficient.
Do not create a row for a generic confirmation that does not identify a role.

Exclude job alerts, recommendations, recruiter marketing, saved-job messages,
incomplete-application reminders, and emails sent by the job-finder agent itself.
Treat each distinct position or requisition as a distinct application even when
several confirmations share one Gmail thread.

For later updates, match exactly one row. Use only these stages:
Applied, Initial Interview, Technical Assessment, Final Interview, Accepted,
Rejected, Other. An unaccepted offer is Other. Never downgrade a stage based on
an older email. A rejection may replace any nonterminal stage. When multiple
messages concern the same row, return only the latest supported stage.

Only include recruiter details explicitly stated in the sender or signature.
Never treat a no-reply, ATS, or generic recruiting address as a named recruiter.
Notes must briefly cite the email date, subject, and sender.

Return exactly:
{{
  "adds": [{{"tab": "", "company": "", "job_title": "", "job_url": "",
    "date_applied": "YYYY-MM-DD", "stage": "Applied",
    "recruiter_name": "", "recruiter_email": "",
    "recruiter_linkedin": "", "resume_version": "", "referral": "",
    "notes": "", "evidence_message_id": ""}}],
  "updates": [{{"tab": "", "row_number": 2, "stage": "",
    "last_contact": "YYYY-MM-DD", "job_url": "", "recruiter_name": "",
    "recruiter_email": "", "recruiter_linkedin": "",
    "resume_version": "", "referral": "", "note_to_append": "",
    "evidence_message_id": ""}}],
  "skipped": [{{"message_id": "", "reason": ""}}]
}}
"""
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    response = client.responses.create(
        model=os.getenv("OPENAI_MODEL", "gpt-5.4-mini"), input=prompt
    )
    return _extract_json(response.output_text)


def validate_plan(
    plan: dict[str, Any],
    emails: list[dict[str, str]],
    tab_rows: dict[str, list[list[str]]],
) -> dict[str, list[dict[str, Any]]]:
    known_ids = {email["message_id"] for email in emails}
    tabs = set(tab_rows)
    adds: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    add_keys: set[tuple[str, str, str, str]] = set()
    for row in plan.get("adds", []):
        key = (
            str(row.get("tab", "")).strip(),
            str(row.get("company", "")).strip().casefold(),
            str(row.get("job_title", "")).strip().casefold(),
            str(row.get("date_applied", "")).strip(),
        )
        if (
            key[0] in tabs and row.get("evidence_message_id") in known_ids
            and key[1] and key[2] and row.get("stage") == "Applied"
            and re.fullmatch(r"\d{4}-\d{2}-\d{2}", key[3])
            and key not in add_keys
        ):
            add_keys.add(key)
            adds.append(row)

    seen_rows: set[tuple[str, int]] = set()
    for row in plan.get("updates", []):
        tab = str(row.get("tab", "")).strip()
        try:
            number = int(row.get("row_number", 0))
        except (TypeError, ValueError):
            continue
        stage = str(row.get("stage", "")).strip()
        key = (tab, number)
        if (
            tab in tabs and 2 <= number <= len(tab_rows[tab])
            and key not in seen_rows and stage in STAGES
            and row.get("evidence_message_id") in known_ids
        ):
            current = (tab_rows[tab][number - 1] + [""] * 15)[:15]
            current_stage = current[4] or "Discovered"
            if (
                stage not in {"Accepted", "Rejected"}
                and STAGE_RANK.get(stage, 0) < STAGE_RANK.get(current_stage, 0)
            ):
                continue
            seen_rows.add(key)
            updates.append(row)
    return {"adds": adds, "updates": updates}


def apply_changes(
    sheets: Any,
    spreadsheet_id: str,
    tab_rows: dict[str, list[list[str]]],
    changes: dict[str, list[dict[str, Any]]],
) -> int:
    writes: list[dict[str, Any]] = []
    for change in changes["updates"]:
        tab = str(change["tab"])
        number = int(change["row_number"])
        current = (tab_rows[tab][number - 1] + [""] * 15)[:15]
        replacements = {
            2: change.get("job_url"), 4: change.get("stage"),
            7: change.get("recruiter_name"), 8: change.get("recruiter_email"),
            9: change.get("recruiter_linkedin"), 10: change.get("last_contact"),
            12: change.get("resume_version"), 13: change.get("referral"),
        }
        for index, value in replacements.items():
            if str(value or "").strip():
                current[index] = str(value).strip()
        note = str(change.get("note_to_append", "")).strip()
        if note and note not in current[14]:
            current[14] = f"{current[14]}\n{note}".strip()
        writes.append({"range": f"'{tab}'!A{number}:O{number}", "values": [current]})

    next_rows = {tab: max(2, len(rows) + 1) for tab, rows in tab_rows.items()}
    for row in changes["adds"]:
        tab = str(row["tab"])
        values = [
            row.get("company", ""), row.get("job_title", ""), row.get("job_url", ""),
            row.get("date_applied", ""), "Applied", "", "",
            row.get("recruiter_name", ""), row.get("recruiter_email", ""),
            row.get("recruiter_linkedin", ""), row.get("date_applied", ""), "",
            row.get("resume_version", ""), row.get("referral", ""), row.get("notes", ""),
        ]
        number = next_rows[tab]
        next_rows[tab] += 1
        writes.append({"range": f"'{tab}'!A{number}:O{number}", "values": [values]})

    if writes:
        sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": writes},
        ).execute()
    return len(writes)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gmail-token", type=Path, required=True)
    parser.add_argument("--sheets-credentials", type=Path, required=True)
    parser.add_argument("--spreadsheet-id", required=True)
    parser.add_argument(
        "--tabs", nargs="+",
        default=["Job Tracker", "Internship Tracker", "UW Part-Time"],
    )
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--state-file", type=Path, default=Path("gmail_sync_state.json"))
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    gmail = build(
        "gmail", "v1", credentials=gmail_credentials(args.gmail_token),
        cache_discovery=False,
    )
    sheets = build(
        "sheets", "v4",
        credentials=sheets_credentials(args.sheets_credentials),
        cache_discovery=False,
    )
    state: dict[str, Any] = {"processed_message_ids": []}
    if args.state_file.exists():
        state = json.loads(args.state_file.read_text(encoding="utf-8"))
    processed = set(state.get("processed_message_ids", []))
    all_emails = recent_candidate_emails(gmail, args.days)
    emails = [row for row in all_emails if row["message_id"] not in processed]
    rows = read_tabs(sheets, args.spreadsheet_id, args.tabs)
    proposed: dict[str, list[dict[str, Any]]] = {
        "adds": [], "updates": [], "skipped": []
    }
    for start in range(0, len(emails), 25):
        batch = plan_changes(emails[start:start + 25], rows)
        for key in proposed:
            proposed[key].extend(batch.get(key, []))
    changes = validate_plan(proposed, emails, rows)
    writes = 0 if args.preview else apply_changes(
        sheets, args.spreadsheet_id, rows, changes
    )
    result = {
        "preview": args.preview, "emails_found": len(all_emails),
        "new_emails_reviewed": len(emails),
        "rows_added": len(changes["adds"]),
        "rows_updated": len(changes["updates"]), "writes": writes,
        "changes": changes, "skipped": proposed.get("skipped", []),
    }
    rendered = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    if not args.preview:
        retained = list(dict.fromkeys(
            [row["message_id"] for row in all_emails] + list(processed)
        ))[:2000]
        args.state_file.write_text(
            json.dumps({"processed_message_ids": retained}, indent=2),
            encoding="utf-8",
        )
    print(rendered)


if __name__ == "__main__":
    main()
