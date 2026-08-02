#!/usr/bin/env python3
"""Find UW part-time student jobs and email a verified digest."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from job_finder_agent import (
    canonical_url,
    create_response_with_rate_limit_retry,
    extract_json,
    normalize_identity,
    openai_client,
    read_cv,
    require_env,
    smtp_send,
)


def find_uw_jobs(
    cv_text: str,
    existing: list[dict[str, str]],
) -> dict[str, Any]:
    model = os.getenv("OPENAI_MODEL") or "gpt-5.6-luna"
    profile = os.getenv("UW_STUDENT_PROFILE", "").strip()
    existing_urls = {
        canonical_url(row.get("url", ""))
        for row in existing
        if row.get("url", "").startswith("http")
    }
    existing_identities = {
        (
            normalize_identity(row.get("company", "")),
            normalize_identity(row.get("title", "")),
        )
        for row in existing
    }
    prompt = f"""
Act as a rigorous University of Washington student-employment search agent.
Search the live web and return JSON only.

CANDIDATE CV:
{cv_text}

UW STUDENT PROFILE:
{profile}

SEARCH RULES:
- Find currently open, part-time jobs at or directly affiliated with the
  University of Washington that a UW graduate student or general enrolled UW
  student can hold while completing a master's degree.
- Prioritize UW Seattle and remote/hybrid UW roles.
- Search official UW employment pages, UW departments, research centers,
  libraries, Housing & Food Services, recreation, student life, tutoring,
  administrative units, and official UW Handshake listings when verifiable.
- Prioritize research assistant, graduate assistant, data/analytics, statistics,
  computing, program assistant, student assistant, library, tutoring, operations,
  and other roles compatible with the candidate's quantitative background.
- Also include realistic general student jobs even when not data-related.
- Prefer 20 hours per week or fewer during the academic term.
- Clearly identify work-study-only positions; do not assume work-study eligibility.
- Exclude full-time jobs, unpaid positions, volunteer roles, expired listings,
  non-UW employers, and roles whose eligibility excludes this candidate.
- Open and verify each selected official listing during this run. Never invent
  pay, hours, eligibility, deadline, department, or URLs.
- Exclude everything already in this tracker snapshot:
{json.dumps(existing, ensure_ascii=False)}

Return up to 10 distinct roles scoring at least 65/100. Return exactly:
{{
  "summary": {{"reviewed": 0, "coverage": "one sentence"}},
  "jobs": [{{
    "title": "string",
    "company": "UW department or affiliated unit",
    "department": "string or not stated",
    "location": "string",
    "work_arrangement": "remote|hybrid|onsite|not stated",
    "hours_per_week": "explicit value or not stated",
    "pay": "explicit value or not stated",
    "work_study": "required|preferred|not required|not stated",
    "student_eligibility": "explicit evidence or uncertainty",
    "posted_date": "YYYY-MM-DD or not stated",
    "deadline": "YYYY-MM-DD or not stated",
    "fit_score": 0,
    "match_reasons": ["reason one", "reason two"],
    "gap": "largest honest gap",
    "url": "direct official https application URL"
  }}]
}}
"""
    response = create_response_with_rate_limit_retry(
        openai_client(),
        model=model,
        input=prompt,
        tools=[{"type": "web_search"}],
        reasoning={"effort": "medium"},
        max_output_tokens=12_000,
    )
    result = extract_json(response.output_text)
    jobs = result.get("jobs", [])
    if not isinstance(jobs, list):
        raise RuntimeError("Invalid UW part-time payload.")
    selected: list[dict[str, Any]] = []
    seen_urls = set(existing_urls)
    seen_identities = set(existing_identities)
    for job in jobs:
        url = canonical_url(str(job.get("url", "")))
        identity = (
            normalize_identity(str(job.get("company", ""))),
            normalize_identity(str(job.get("title", ""))),
        )
        score = int(job.get("fit_score", 0))
        if (
            url.startswith("https://")
            and url not in seen_urls
            and identity not in seen_identities
            and score >= 65
        ):
            job["url"] = url
            selected.append(job)
            seen_urls.add(url)
            seen_identities.add(identity)
    result["jobs"] = sorted(
        selected[:10],
        key=lambda row: int(row.get("fit_score", 0)),
        reverse=True,
    )
    return result


def build_digest(result: dict[str, Any]) -> tuple[str, str]:
    stamp = datetime.now(ZoneInfo("America/Los_Angeles")).strftime(
        "%Y-%m-%d %I:%M %p"
    )
    jobs = result.get("jobs", [])
    summary = result.get("summary", {})
    subject = f"UW part-time student job matches - {stamp}"
    lines = [
        subject,
        "",
        f"Reviewed: {summary.get('reviewed', 'not stated')} | Selected: {len(jobs)}",
        str(summary.get("coverage", "")),
        "",
    ]
    if not jobs:
        lines.append("No new strong UW student-job matches were found.")
    for number, job in enumerate(jobs, 1):
        lines.extend([
            f"{number}. {job.get('title', '')} - {job.get('company', '')} "
            f"[{job.get('fit_score', 0)}/100]",
            f"Department: {job.get('department', 'not stated')}",
            f"Location: {job.get('location', 'not stated')} "
            f"({job.get('work_arrangement', 'not stated')})",
            f"Hours: {job.get('hours_per_week', 'not stated')} | "
            f"Pay: {job.get('pay', 'not stated')}",
            f"Work-study: {job.get('work_study', 'not stated')}",
            f"Eligibility: {job.get('student_eligibility', 'not stated')}",
            f"Posted: {job.get('posted_date', 'not stated')} | "
            f"Deadline: {job.get('deadline', 'not stated')}",
            *(f"Match: {reason}" for reason in job.get("match_reasons", [])),
            f"Gap: {job.get('gap', 'not stated')}",
            f"Apply: {job.get('url', '')}",
            "",
        ])
    return subject, "\n".join(lines)


def email_result(result: dict[str, Any]) -> None:
    subject, body = build_digest(result)
    smtp_send(
        os.getenv("EMAIL_TO", require_env("GMAIL_ADDRESS")),
        subject,
        body,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cv", type=Path, default=Path("cv.pdf"))
    parser.add_argument("--existing", type=Path)
    parser.add_argument("--output", type=Path, default=Path("uw_part_time_jobs.json"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--email-result", type=Path)
    args = parser.parse_args()
    load_dotenv()

    if args.email_result:
        email_result(json.loads(args.email_result.read_text(encoding="utf-8")))
        print("Sent UW part-time student job digest.")
        return

    existing = (
        json.loads(args.existing.read_text(encoding="utf-8"))
        if args.existing and args.existing.exists()
        else []
    )
    result = find_uw_jobs(read_cv(args.cv), existing)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if args.dry_run:
        print(f"Prepared {len(result.get('jobs', []))} UW job match(es).")
    else:
        email_result(result)


if __name__ == "__main__":
    main()
