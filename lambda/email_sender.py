"""Build and send the HTML digest email via Brevo SMTP."""

import os
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

BREVO_SMTP_HOST = "smtp-relay.brevo.com"
BREVO_SMTP_PORT = 587


def build_html(stories: list[dict], date_str: str) -> str:
    """Render the stories into a clean HTML email body."""
    story_blocks = ""
    for story in stories:
        title = story.get("title", "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        summary = story.get("summary", "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        link = story.get("link", "#")
        story_blocks += f"""
        <div style="margin-bottom:28px; padding-bottom:24px; border-bottom:1px solid #e8f4ea;">
          <h2 style="margin:0 0 8px; font-size:18px; color:#1a4a2e; line-height:1.4;">
            <a href="{link}" style="color:#1a4a2e; text-decoration:none;">{title}</a>
          </h2>
          <p style="margin:0 0 10px; font-size:15px; color:#444; line-height:1.6;">{summary}</p>
          <a href="{link}"
             style="display:inline-block; font-size:13px; color:#2d7a4f; font-weight:600;
                    text-decoration:none; border:1px solid #2d7a4f; padding:4px 12px;
                    border-radius:4px;">
            Read more &rarr;
          </a>
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0; padding:0; background:#f5faf6; font-family: Georgia, 'Times New Roman', serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f5faf6; padding:32px 0;">
    <tr>
      <td align="center">
        <table width="600" cellpadding="0" cellspacing="0"
               style="background:#ffffff; border-radius:8px; overflow:hidden;
                      box-shadow:0 2px 8px rgba(0,0,0,0.07); max-width:600px; width:100%;">
          <!-- Header -->
          <tr>
            <td style="background:#2d7a4f; padding:32px 40px;">
              <p style="margin:0; font-size:13px; color:#a8d5b5; letter-spacing:2px;
                        text-transform:uppercase; font-family:Arial,sans-serif;">
                {date_str}
              </p>
              <h1 style="margin:8px 0 0; font-size:28px; color:#ffffff; line-height:1.2;">
                Your Daily Positive News
              </h1>
              <p style="margin:10px 0 0; font-size:15px; color:#c5e8d0;">
                Five uplifting stories from around the world &#127758;
              </p>
            </td>
          </tr>
          <!-- Stories -->
          <tr>
            <td style="padding:36px 40px 8px;">
              {story_blocks}
            </td>
          </tr>
          <!-- Footer -->
          <tr>
            <td style="padding:20px 40px 36px; border-top:1px solid #e8f4ea;">
              <p style="margin:0; font-size:12px; color:#999; font-family:Arial,sans-serif;
                        line-height:1.6;">
                Curated daily by Claude AI &bull;
                Powered by
                <a href="https://github.com/nicholasgasior/positive-news-mailer"
                   style="color:#2d7a4f;">positive-news-mailer</a>
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def send_email(stories: list[dict]) -> None:
    """Send the digest email via Brevo SMTP."""
    recipient = os.environ["RECIPIENT_EMAIL"]
    sender_email = os.environ["SENDER_EMAIL"]
    sender_name = os.environ.get("SENDER_NAME", "Positive News Digest")
    smtp_user = os.environ["BREVO_SMTP_USER"]
    smtp_key = os.environ["BREVO_SMTP_KEY"]

    today = datetime.now(timezone.utc)
    date_str = today.strftime("%A, %B %-d, %Y")
    subject = f"Your Positive News Digest — {today.strftime('%b %-d')}"

    html_body = build_html(stories, date_str)
    plain_body = "\n\n".join(
        f"{s.get('title', '')}\n{s.get('summary', '')}\n{s.get('link', '')}"
        for s in stories
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{sender_name} <{sender_email}>"
    msg["To"] = recipient
    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(BREVO_SMTP_HOST, BREVO_SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(smtp_user, smtp_key)
        server.sendmail(sender_email, recipient, msg.as_string())
