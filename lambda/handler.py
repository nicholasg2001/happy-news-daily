"""AWS Lambda entrypoint for the Positive News Mailer."""

import json
import traceback
from news_fetcher import get_top_positive_stories
from email_sender import send_email


def handler(event, context):
    """
    Lambda handler. Invoked daily by EventBridge Scheduler.
    Fetches positive news via RSS + Claude Haiku, then sends an email via Brevo.
    """
    try:
        print("Fetching positive news stories...")
        stories = get_top_positive_stories()
        print(f"Got {len(stories)} stories from Claude.")

        print("Sending email...")
        send_email(stories)
        print("Email sent successfully.")

        return {
            "statusCode": 200,
            "body": json.dumps({"message": "Email sent.", "stories": len(stories)}),
        }

    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
        raise
