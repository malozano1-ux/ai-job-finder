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

## Schedule it

On macOS or Linux, use `cron`, `launchd`, or another trusted scheduler. Keep `.env`
and `cv.pdf` on the machine running the agent. Avoid placing personal data in
GitHub Actions; public-repository workflows are not an appropriate place for a
private CV.

## Limitations

- Web search results can be stale, localized, or incorrect.
- Recruiter identities and addresses can change.
- Some employer sites block automated verification.
- API and web-search usage can incur costs.

Always verify job status, qualifications, recipients, and message content.

## License

MIT
