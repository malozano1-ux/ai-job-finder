#!/usr/bin/env python3
"""CV-aware job finder with email digests and reviewed recruiter outreach."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import smtplib
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv
from openai import OpenAI, RateLimitError
from pypdf import PdfReader


HISTORY_FILE = Path("job_history.json")
OUTREACH_FILE = Path("outreach_queue.json")


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def read_cv(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"CV not found: {path}")
    if path.suffix.lower() == ".txt":
        text = path.read_text(encoding="utf-8").strip()
    else:
        text = "\n".join(
            page.extract_text() or "" for page in PdfReader(path).pages
        ).strip()
    if not text:
        raise RuntimeError("The CV contains no extractable text.")
    return text[:30_000]


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    ignored = {
        "utm_source", "utm_medium", "utm_campaign", "utm_term",
        "utm_content", "source", "ref", "referrer",
    }
    query = [(k, v) for k, v in parse_qsl(parts.query) if k.lower() not in ignored]
    host = parts.netloc.lower()
    path = parts.path.rstrip("/")
    if host.endswith("amazon.jobs"):
        pieces = path.split("/")
        if len(pieces) > 2 and pieces[1] != "en":
            pieces[1] = "en"
            path = "/".join(pieces)
    return urlunsplit((parts.scheme.lower(), host, path, urlencode(query), ""))


def extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(text.splitlines()[1:-1]).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError("The model did not return JSON.")
    return json.loads(text[start:end + 1])


def load_recent_urls() -> set[str]:
    if not HISTORY_FILE.exists():
        return set()
    try:
        rows = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    urls = set()
    for row in rows:
        try:
            if datetime.fromisoformat(row["sent_at"]) >= cutoff:
                urls.add(canonical_url(row["url"]))
        except (KeyError, TypeError, ValueError):
            continue
    return urls


def normalize_identity(value: str) -> str:
    """Normalize company/title text for conservative applied-job matching."""
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def load_applied_jobs() -> list[dict[str, str]]:
    """Read a live tracker file, with the private secret as a local fallback."""
    source = os.getenv("APPLIED_JOBS_FILE", "").strip()
    if source:
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"Applied-jobs file not found: {path}")
        raw = path.read_text(encoding="utf-8").strip()
    else:
        raw = os.getenv("APPLIED_JOBS", "").strip()
    if not raw:
        return []
    try:
        rows = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("APPLIED_JOBS is not valid JSON.") from error
    if not isinstance(rows, list):
        raise RuntimeError("APPLIED_JOBS must be a JSON list.")
    safe: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        company = str(row.get("company", "")).strip()
        title = str(row.get("title", "")).strip()
        url = str(row.get("url", "")).strip()
        if company and title:
            safe.append({"company": company, "title": title, "url": url})
    return safe


def openai_client() -> OpenAI:
    return OpenAI(api_key=require_env("OPENAI_API_KEY"))


def create_response_with_rate_limit_retry(
    client: OpenAI,
    *,
    max_attempts: int = 4,
    **kwargs: Any,
) -> Any:
    """Retry transient OpenAI rate limits without retrying forever."""
    for attempt in range(max_attempts):
        try:
            return client.responses.create(**kwargs)
        except RateLimitError as error:
            if attempt + 1 >= max_attempts:
                raise
            retry_after = 0.0
            response = getattr(error, "response", None)
            if response is not None:
                try:
                    retry_after = float(response.headers.get("retry-after", 0))
                except (TypeError, ValueError):
                    retry_after = 0.0
            delay = min(60.0, max(retry_after, 5.0 * (2 ** attempt)))
            print(
                f"OpenAI rate limit reached; retrying in {delay:g} seconds "
                f"(attempt {attempt + 2}/{max_attempts})."
            )
            time.sleep(delay)
    raise RuntimeError("OpenAI response retry loop ended unexpectedly.")


def find_jobs(
    cv_text: str,
    recent_urls: set[str],
    applied_jobs: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    applied_jobs = applied_jobs or []
    applied_urls = {
        canonical_url(row["url"])
        for row in applied_jobs
        if row.get("url", "").startswith("http")
    }
    applied_identities = {
        (normalize_identity(row["company"]), normalize_identity(row["title"]))
        for row in applied_jobs
    }
    model = os.getenv("OPENAI_MODEL") or "gpt-5.6-luna"
    location = os.getenv("JOB_LOCATION") or "United States and U.S. remote"
    titles = os.getenv(
        "JOB_TITLES",
        "Data Scientist, Data Analyst, Analytics Engineer, Machine Learning Engineer",
    ) or "Data Scientist, Data Analyst, Analytics Engineer, Machine Learning Engineer"
    prompt = f"""
Act as a rigorous job-search agent. Search the live web and return only JSON.

CV:
{cv_text}

Settings:
- Locations: {location}
- Target roles: {titles}
- Seniority: entry-level, new graduate, associate, or about 0-3 years
- Prefer listings posted or refreshed within 14 days
- Exclude internships, unpaid work, senior/staff/lead/manager/director roles
- Exclude hard requirements above 3 years
- Prefer direct employer application pages
- Exclude these recently emailed URLs: {json.dumps(sorted(recent_urls))}
- Never recommend any job in this already-applied list, even if its listing URL
  has different tracking parameters:
{json.dumps(applied_jobs, ensure_ascii=False)}

Open each selected listing during this run. Never invent requirements, dates,
salary, company names, or URLs. Rank at most 10 distinct jobs scoring at least
70/100. Return exactly:
{{
  "summary": {{"reviewed": 0, "coverage": "one sentence"}},
    "jobs": [{{
    "title": "string", "company": "string", "location": "string",
    "work_arrangement": "remote|hybrid|onsite|not stated",
    "posted_date": "YYYY-MM-DD or not stated", "fit_score": 0,
    "deadline": "YYYY-MM-DD or not stated",
    "match_reasons": ["reason"], "gap": "largest honest gap",
    "hard_requirements": ["confirmed requirement"],
    "preferred_qualifications": ["confirmed preference"],
    "url": "direct https application URL"
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
        raise RuntimeError("Invalid jobs payload.")
    seen = set(recent_urls) | applied_urls
    selected = []
    for job in jobs:
        url = canonical_url(str(job.get("url", "")))
        score = int(job.get("fit_score", 0))
        identity = (
            normalize_identity(str(job.get("company", ""))),
            normalize_identity(str(job.get("title", ""))),
        )
        if (
            url.startswith("https://")
            and url not in seen
            and identity not in applied_identities
            and score >= 70
        ):
            job["url"] = url
            selected.append(job)
            seen.add(url)
    result["jobs"] = sorted(
        selected[:10], key=lambda row: int(row.get("fit_score", 0)), reverse=True
    )
    return result


def build_digest(result: dict[str, Any]) -> tuple[str, str, str]:
    date = datetime.now().astimezone().strftime("%Y-%m-%d %I:%M %p")
    jobs = result.get("jobs", [])
    summary = result.get("summary", {})
    subject = f"AI job matches - {date}"
    text = [
        subject, "",
        f"Reviewed: {summary.get('reviewed', 'not stated')} | Selected: {len(jobs)}",
        str(summary.get("coverage", "")), "",
    ]
    rich = [
        f"<h1>{html.escape(subject)}</h1>",
        f"<p><b>Reviewed:</b> {html.escape(str(summary.get('reviewed', 'not stated')))}"
        f" &nbsp; <b>Selected:</b> {len(jobs)}</p>",
        f"<p>{html.escape(str(summary.get('coverage', '')))}</p>",
    ]
    if not jobs:
        text.append("No strong new matches were found.")
        rich.append("<p><b>No strong new matches were found.</b></p>")
    for number, job in enumerate(jobs, 1):
        reasons = [str(x) for x in job.get("match_reasons", [])]
        title, company = str(job.get("title", "")), str(job.get("company", ""))
        text.extend([
            f"{number}. {title} - {company} [{job.get('fit_score', 0)}/100]",
            f"Location: {job.get('location', 'not stated')} "
            f"({job.get('work_arrangement', 'not stated')})",
            f"Posted: {job.get('posted_date', 'not stated')}",
            *(f"Match: {reason}" for reason in reasons),
            f"Gap: {job.get('gap', 'not stated')}",
            f"Apply: {job.get('url', '')}", "",
        ])
        rich.extend([
            f"<h2>{number}. {html.escape(title)} - {html.escape(company)} "
            f"({int(job.get('fit_score', 0))}/100)</h2>",
            "<ul>" + "".join(f"<li>{html.escape(x)}</li>" for x in reasons) + "</ul>",
            f"<p><b>Gap:</b> {html.escape(str(job.get('gap', 'not stated')))}</p>",
            f'<p><a href="{html.escape(str(job.get("url", "")), quote=True)}">'
            "Apply on the employer website</a></p>",
        ])
    return subject, "\n".join(text), "\n".join(rich)


def smtp_send(
    recipient: str,
    subject: str,
    text: str,
    rich: str | None = None,
    attachment: Path | None = None,
) -> None:
    sender = require_env("GMAIL_ADDRESS")
    password = require_env("GMAIL_APP_PASSWORD").replace(" ", "")
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = sender, recipient, subject
    message.set_content(text)
    if rich:
        message.add_alternative(rich, subtype="html")
    if attachment:
        message.add_attachment(
            attachment.read_bytes(),
            maintype="application",
            subtype="pdf",
            filename=attachment.name,
        )
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(sender, password)
        smtp.send_message(message)


def save_history(jobs: list[dict[str, Any]]) -> None:
    try:
        rows = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        rows = []
    now = datetime.now(timezone.utc)
    rows.extend({"url": job["url"], "sent_at": now.isoformat()} for job in jobs)
    HISTORY_FILE.write_text(json.dumps(rows[-500:], indent=2), encoding="utf-8")


def prepare_outreach(cv_text: str, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not jobs:
        return []
    model = os.getenv("OPENAI_MODEL") or "gpt-5.6-luna"
    name = os.getenv("CANDIDATE_NAME", "Candidate")
    linkedin = os.getenv("CANDIDATE_LINKEDIN", "")
    prompt = f"""
Search the live web for one appropriate recruiting contact per company below.
Return only JSON. Candidate: {name}. LinkedIn: {linkedin}. CV: {cv_text[:20_000]}
Jobs: {json.dumps(jobs)}

Use only professional email addresses explicitly published on a reputable public
source. Never infer an email pattern. Reject personal email providers. Require
public evidence of current company affiliation and the exact email. Prepare a
concise individual note mentioning the exact role and 1-2 supported CV strengths.
Ask whether the contact is open to connecting or sharing application guidance.
Do not exaggerate, imply a referral, or claim prior contact.

Return: {{"outreach": [{{
  "company": "string", "job_title": "string", "job_url": "https URL",
  "recruiter_name": "string", "recruiter_title": "string",
  "recruiter_email": "public company email", "evidence_url": "https URL",
  "subject": "string", "body": "plain-text email"
}}]}}
Omit contacts lacking an explicitly published professional email.
"""
    response = openai_client().responses.create(
        model=model,
        input=prompt,
        tools=[{"type": "web_search"}],
        reasoning={"effort": "medium"},
    )
    rows = extract_json(response.output_text).get("outreach", [])
    safe = []
    seen = set()
    blocked = ("@gmail.com", "@yahoo.com", "@outlook.com", "@hotmail.com")
    for row in rows if isinstance(rows, list) else []:
        email = str(row.get("recruiter_email", "")).strip().lower()
        company = str(row.get("company", "")).strip().lower()
        evidence = str(row.get("evidence_url", ""))
        if "@" in email and not email.endswith(blocked) and evidence.startswith("https://") and company not in seen:
            row.update(
                approved=False,
                status="pending_review",
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            safe.append(row)
            seen.add(company)
    return safe


def save_outreach(rows: list[dict[str, Any]]) -> None:
    OUTREACH_FILE.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def send_approved(cv_path: Path, cap: int = 3) -> int:
    rows = json.loads(OUTREACH_FILE.read_text(encoding="utf-8"))
    sent = 0
    for row in rows:
        if sent >= cap:
            break
        if row.get("approved") is True and row.get("status") == "pending_review":
            smtp_send(
                str(row["recruiter_email"]),
                str(row["subject"]),
                str(row["body"]),
                attachment=cv_path,
            )
            row["status"] = "sent"
            row["sent_at"] = datetime.now(timezone.utc).isoformat()
            sent += 1
    OUTREACH_FILE.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return sent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cv", type=Path, default=Path("cv.pdf"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--prepare-outreach", action="store_true")
    parser.add_argument("--send-approved-outreach", action="store_true")
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args()
    load_dotenv()

    if args.send_approved_outreach:
        print(f"Sent {send_approved(args.cv)} approved recruiter email(s).")
        return

    cv_text = read_cv(args.cv)
    result = find_jobs(cv_text, load_recent_urls(), load_applied_jobs())
    if args.output_json:
        args.output_json.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    subject, text, rich = build_digest(result)
    if args.dry_run:
        print(text)
    else:
        smtp_send(os.getenv("EMAIL_TO", require_env("GMAIL_ADDRESS")), subject, text, rich)
        save_history(result["jobs"])
        print(f"Sent {len(result['jobs'])} job match(es).")

    if args.prepare_outreach:
        rows = prepare_outreach(cv_text, result["jobs"])
        save_outreach(rows)
        print(
            f"Prepared {len(rows)} draft(s) in {OUTREACH_FILE}. "
            'Review evidence and set "approved": true before sending.'
        )


if __name__ == "__main__":
    main()
