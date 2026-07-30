#!/usr/bin/env python3
"""Find Summer 2027 master's internships and email a verified digest."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from job_finder_agent import (
    canonical_url,
    extract_json,
    normalize_identity,
    openai_client,
    read_cv,
    require_env,
    smtp_send,
)


def find_internships(
    cv_text: str,
    existing: list[dict[str, str]],
) -> dict[str, Any]:
    model = os.getenv("OPENAI_MODEL") or "gpt-5.6-luna"
    profile = os.getenv("INTERNSHIP_SEARCH_PROFILE", "").strip()
    locations = os.getenv(
        "INTERNSHIP_LOCATIONS",
        "United States and U.S. remote",
    ).strip()
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
Act as a rigorous internship-search agent. Search the live web and return JSON only.

CANDIDATE CV:
{cv_text}

ADDITIONAL CANDIDATE PROFILE:
{profile}

SEARCH RULES:
- Find United States Summer 2027 internships or co-op opportunities.
- Locations: {locations}
- Target Data Scientist Intern, Applied Data Scientist Intern, Product Data
  Science/Analytics Intern, Decision Scientist Intern, Data/Business Analytics
  Intern, Business Intelligence Engineer Intern, Applied Scientist Intern,
  Machine Learning Intern, and closely related quantitative roles.
- The candidate must be eligible as a master's student who returns to school
  after Summer 2027 and expects to graduate in Winter 2028.
- Prefer roles explicitly open to graduate or master's students.
- Exclude roles limited to undergraduates, requiring a PhD, or whose graduation
  window excludes Winter 2028.
- Exclude primarily software-engineering, computer-vision, robotics, embedded,
  or low-level systems roles unless the work is substantially data science,
  experimentation, NLP/LLMs, forecasting, recommender systems, or applied ML.
- Prefer direct official employer application pages. Verify each selected role
  is open during this run. Never invent eligibility, dates, requirements, or URLs.
- Do not return anything already present in this tracker snapshot:
{json.dumps(existing, ensure_ascii=False)}

Return up to 10 distinct opportunities scoring at least 70/100. Return exactly:
{{
  "summary": {{"reviewed": 0, "coverage": "one sentence"}},
  "jobs": [{{
    "title": "string",
    "company": "string",
    "location": "string",
    "work_arrangement": "remote|hybrid|onsite|not stated",
    "term": "Summer 2027|2027 co-op",
    "posted_date": "YYYY-MM-DD or not stated",
    "deadline": "YYYY-MM-DD or not stated",
    "fit_score": 0,
    "masters_eligibility": "explicit evidence or uncertainty",
    "graduation_eligibility": "explicit evidence or uncertainty",
    "work_authorization": "explicit evidence or not stated",
    "match_reasons": ["reason one", "reason two"],
    "gap": "largest honest gap",
    "url": "direct official https application URL"
  }}]
}}
"""
    response = openai_client().responses.create(
        model=model,
        input=prompt,
        tools=[{"type": "web_search"}],
        reasoning={"effort": "medium"},
    )
    result = extract_json(response.output_text)
    jobs = result.get("jobs", [])
    if not isinstance(jobs, list):
        raise RuntimeError("Invalid internship payload.")
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
            and score >= 70
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
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %I:%M %p")
    jobs = result.get("jobs", [])
    summary = result.get("summary", {})
    subject = f"Summer 2027 internship matches - {stamp}"
    lines = [
        subject,
        "",
        f"Reviewed: {summary.get('reviewed', 'not stated')} | Selected: {len(jobs)}",
        str(summary.get("coverage", "")),
        "",
    ]
    if not jobs:
        lines.append("No new strong Summer 2027 matches were found.")
    for number, job in enumerate(jobs, 1):
        lines.extend([
            f"{number}. {job.get('title', '')} - {job.get('company', '')} "
            f"[{job.get('fit_score', 0)}/100]",
            f"Location: {job.get('location', 'not stated')} "
            f"({job.get('work_arrangement', 'not stated')})",
            f"Term: {job.get('term', 'not stated')}",
            f"Posted: {job.get('posted_date', 'not stated')} | "
            f"Deadline: {job.get('deadline', 'not stated')}",
            f"Master's eligibility: {job.get('masters_eligibility', 'not stated')}",
            f"Graduation eligibility: {job.get('graduation_eligibility', 'not stated')}",
            f"Work authorization: {job.get('work_authorization', 'not stated')}",
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
    parser.add_argument("--output", type=Path, default=Path("internships.json"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--email-result", type=Path)
    args = parser.parse_args()
    load_dotenv()

    if args.email_result:
        email_result(json.loads(args.email_result.read_text(encoding="utf-8")))
        print("Sent Summer 2027 internship digest.")
        return

    existing = (
        json.loads(args.existing.read_text(encoding="utf-8"))
        if args.existing and args.existing.exists()
        else []
    )
    result = find_internships(read_cv(args.cv), existing)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if args.dry_run:
        print(f"Prepared {len(result.get('jobs', []))} internship match(es).")
    else:
        email_result(result)


if __name__ == "__main__":
    main()
