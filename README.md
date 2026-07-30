# AI Job Finder

A privacy-conscious Python agent that reads a PDF CV, searches live job listings,
ranks matches, emails a digest, and prepares recruiter outreach for human review.

## Features

- CV-aware job matching with configurable titles and locations
- Live web search through the OpenAI Responses API
- Direct employer links, fit scores, strengths, and honest gaps
- 30-day URL deduplication
- Markdown and HTML Gmail digests
- Recruiter-contact research using only publicly evidenced professional addresses
- Review-before-send outreach queue with a three-message cap

## Safety model

The agent never applies to jobs. Recruiter outreach is never sent automatically:
each draft must be reviewed and changed to `"approved": true`. Do not treat model
output as proof that a listing or contact is valid—open every application and
evidence link before acting.

No CV, credentials, application history, or outreach queue belongs in Git.

## Requirements

- Python 3.11+
- An OpenAI API key with API billing enabled
- A Gmail or Google Workspace account with 2-Step Verification and an app password
- A text-readable PDF CV

## Setup

```bash
git clone https://github.com/malozano1-ux/ai-job-finder.git
cd ai-job-finder
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`, then place your CV at `cv.pdf`.

Create an OpenAI key at <https://platform.openai.com/api-keys>. Create a Google
app password at <https://myaccount.google.com/apppasswords>. Never use your normal
Gmail password.

## Find jobs

Preview without sending:

```bash
python job_finder_agent.py --cv cv.pdf --dry-run
```

Email the digest:

```bash
python job_finder_agent.py --cv cv.pdf
```

## Prepare recruiter outreach

```bash
python job_finder_agent.py --cv cv.pdf --dry-run --prepare-outreach
```

Open `outreach_queue.json`. Verify every contact, evidence URL, claim, and message.
Set `"approved": true` only for drafts you want to send.

```bash
python job_finder_agent.py --cv cv.pdf --send-approved-outreach
```

## Schedule it with GitHub Actions

The included workflow runs at 8:00 AM, 1:00 PM, and 6:00 PM in
`America/Los_Angeles`, including daylight-saving-time changes. It can also be run
manually from the repository's **Actions** tab.

In **Settings → Secrets and variables → Actions**, create these repository secrets:

- `OPENAI_API_KEY`
- `GMAIL_ADDRESS`
- `GMAIL_APP_PASSWORD`
- `EMAIL_TO`
- `CV_TEXT` — the plain text extracted from your CV, without API keys or passwords
- `GOOGLE_SERVICE_ACCOUNT_JSON` — the complete JSON key for a Google Cloud
  service account that has Viewer access to the tracker

Optional repository variables:

- `OPENAI_MODEL`
- `JOB_LOCATION`
- `JOB_TITLES`
- `JOB_TRACKER_SPREADSHEET_ID` — required for live tracker synchronization

The workflow recreates `cv.txt` temporarily on the private GitHub runner. It does
not commit the CV to the public repository. GitHub Actions secrets are limited in
size, so plain CV text is used instead of a base64-encoded PDF.

Before every digest, the workflow reads columns A:C of `Job Tracker` directly
from Google Sheets with read-only access. The agent rejects already-applied roles
by canonical URL and normalized company/title, so tracking parameters cannot make
an old role look new. The URL history is also restored between runs using GitHub's
Actions cache to reduce repeat recommendations. Scheduled workflows run from the
default branch and may
occasionally start a few minutes late during periods of high GitHub Actions load.

## Summer 2027 master's internship agent

The separate `Summer 2027 internship digest` workflow searches for verified U.S.
Data Science, Analytics, Applied Science, and ML internships or co-ops that accept
master's students returning to school after Summer 2027. It emails a separate
digest and appends new opportunities to the `Internship Tracker` tab with Stage
`Discovered`; Date Applied remains blank until the candidate applies.

This workflow requires the Google service account to have **Editor** access to the
spreadsheet. It reads and writes only the configured spreadsheet and uses these
repository variables:

- `INTERNSHIP_SEARCH_PROFILE`
- `INTERNSHIP_LOCATIONS`

## UW part-time student-job agent

The separate `UW part-time student jobs` workflow searches official University of
Washington sources for current roles that an enrolled graduate or general student
can perform alongside a master's program. It prioritizes research, data, analytics,
computing, tutoring, library, administrative, operations, and realistic general
campus jobs, while clearly flagging work-study requirements, weekly hours, pay,
eligibility, and deadlines when stated.

New opportunities are appended to `UW Part-Time` with Stage `Discovered` and a
blank Date Applied. The workflow runs at 9:00 AM, 2:00 PM, and 7:00 PM Pacific and
uses the `UW_STUDENT_PROFILE` repository variable.

## Limitations

- Web search results can be stale, localized, or incorrect.
- Recruiter identities and addresses can change.
- Some employer sites block automated verification.
- API and web-search usage can incur costs.

Always verify job status, qualifications, recipients, and message content.

## License

MIT
