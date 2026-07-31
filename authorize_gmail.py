#!/usr/bin/env python3
"""One-time local authorization for unattended read-only Gmail access."""

from __future__ import annotations

import argparse
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow


SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", type=Path, default=Path("gmail_client.json"))
    parser.add_argument("--output", type=Path, default=Path("gmail_token.json"))
    args = parser.parse_args()
    flow = InstalledAppFlow.from_client_secrets_file(str(args.client), SCOPES)
    credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    args.output.write_text(credentials.to_json(), encoding="utf-8")
    print(f"Saved Gmail token to {args.output}. Keep this file private.")


if __name__ == "__main__":
    main()
