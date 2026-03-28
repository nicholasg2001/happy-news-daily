#!/usr/bin/env python3
"""
Run the full positive-news-mailer pipeline locally.

Usage:
    cp .env.example .env          # fill in your credentials
    pip install anthropic feedparser
    python scripts/test_local.py

This sends a real email to RECIPIENT_EMAIL — use it to verify everything
works before deploying to AWS.
"""

import os
import sys
from pathlib import Path

# Load .env from the repo root (one level up from scripts/)
env_file = Path(__file__).parent.parent / ".env"
if env_file.exists():
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())
else:
    print("WARNING: .env file not found. Make sure env vars are set manually.")

# Add lambda/ to sys.path so imports work the same as in Lambda
lambda_dir = Path(__file__).parent.parent / "lambda"
sys.path.insert(0, str(lambda_dir))

from news_fetcher import fetch_articles, _get_stories_standard, _get_stories_premium, get_top_positive_stories
from email_sender import send_email


def check_env():
    required = [
        "ANTHROPIC_API_KEY",
        "RECIPIENT_EMAIL",
        "SENDER_EMAIL",
        "BREVO_SMTP_USER",
        "BREVO_SMTP_KEY",
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"ERROR: Missing required environment variables: {', '.join(missing)}")
        print(f"Copy .env.example to .env and fill in your credentials.")
        sys.exit(1)


def main():
    check_env()

    print("=" * 60)
    print("Positive News Mailer — Local Test Run")
    print("=" * 60)

    tier = os.environ.get("TIER", "standard").lower()

    if tier == "premium":
        # Premium: Claude Sonnet handles fetch + summarize in one step
        print("\n[1/2] Searching the web with Claude Sonnet (premium tier)...")
        stories = _get_stories_premium()
        print(f"      Got {len(stories)} top stories back from Claude.\n")
        for i, s in enumerate(stories, 1):
            print(f"  {i}. {s.get('title', '(no title)')}")
            print(f"     {s.get('link', '')}")
            print()
        print(f"[2/2] Sending email to {os.environ['RECIPIENT_EMAIL']}...")
        send_email(stories)
        print("      Done! Check your inbox (and spam folder on first run).")
        print()
        print("=" * 60)
        print("All steps completed successfully. Ready to deploy to AWS.")
        print("=" * 60)
        return

    # Standard: fetch RSS then summarize
    print("\n[1/3] Fetching articles from RSS feeds...")
    articles = fetch_articles()
    print(f"      Fetched {len(articles)} articles.")
    if not articles:
        print("ERROR: No articles fetched. Check your internet connection.")
        sys.exit(1)

    # Step 2: Summarize with Claude
    print("\n[2/3] Sending to Claude Haiku for summarization...")
    stories = _get_stories_standard()
    print(f"      Got {len(stories)} top stories back from Claude.\n")
    for i, s in enumerate(stories, 1):
        print(f"  {i}. {s.get('title', '(no title)')}")
        print(f"     {s.get('link', '')}")
        print()

    print(f"[3/3] Sending email to {os.environ['RECIPIENT_EMAIL']}...")
    send_email(stories)
    print("      Done! Check your inbox (and spam folder on first run).")
    print()
    print("=" * 60)
    print("All steps completed successfully. Ready to deploy to AWS.")
    print("=" * 60)


if __name__ == "__main__":
    main()
