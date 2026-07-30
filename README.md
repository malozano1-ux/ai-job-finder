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
- `APPLIED_JOBS` — a private JSON snapshot of company, title, and URL from your
  application tracker

Optional repository variables:

- `OPENAI_MODEL`
- `JOB_LOCATION`
- `JOB_TITLES`

The workflow recreates `cv.txt` temporarily on the private GitHub runner. It does
not commit the CV to the public repository. GitHub Actions secrets are limited in
size, so plain CV text is used instead of a base64-encoded PDF.

The URL history is restored between runs using GitHub's Actions cache to reduce
repeat recommendations. The agent also rejects already-applied roles by canonical
URL and normalized company/title, so tracking parameters cannot make an old role
look new. Scheduled workflows run from the default branch and may
occasionally start a few minutes late during periods of high GitHub Actions load.

## Limitations

- Web search results can be stale, localized, or incorrect.
- Recruiter identities and addresses can change.
- Some employer sites block automated verification.
- API and web-search usage can incur costs.

Always verify job status, qualifications, recipients, and message content.

## License

MIT
