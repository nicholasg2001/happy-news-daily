# Positive News Mailer

A self-hosted AWS app that sends you a daily email digest of uplifting news from around the world — curated by Claude AI.

**Cost: ~$0.004/month** (just Claude Haiku token usage). All AWS infrastructure runs on the permanent free tier. Email sending is free via Brevo (300 emails/day).

```
┌─────────────────────────────────────────────────────┐
│  EventBridge Scheduler (daily, timezone-aware cron) │
└──────────────────────────┬──────────────────────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │   AWS Lambda (Python)  │
              │                        │
              │  1. Fetch RSS feeds    │  ← Free
              │  2. Claude Haiku       │  ← ~$0.004/mo
              │     summarizes top 5   │
              │  3. Send HTML email    │  ← Free (Brevo)
              └────────────────────────┘
```

---

## Sample Email

Each morning you receive a clean HTML digest like this:

> **Your Daily Positive News — Tuesday, March 28, 2026**
>
> 🌱 **Scientists discover breakthrough in coral reef restoration** — Researchers at the Australian Institute of Marine Science have developed...
>
> 🤝 **Community solar project brings clean energy to 500 rural homes** — A nonprofit in rural Tennessee...

---

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| **AWS account** | [aws.amazon.com](https://aws.amazon.com) — free tier is enough |
| **AWS CLI** | [Install guide](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) — run `aws configure` after |
| **AWS SAM CLI** | [Install guide](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html) |
| **Anthropic API key** | [console.anthropic.com](https://console.anthropic.com/) — uses Claude Haiku (~$0.004/mo) |
| **Brevo account** | [app.brevo.com](https://app.brevo.com) — free, 300 emails/day, no credit card |
| **Python 3.12+** | For local testing only |

---

## Quick Start

### 1. Clone the repo

```bash
git clone https://github.com/yourusername/positive-news-mailer.git
cd positive-news-mailer
```

### 2. Set up Brevo (free email sending)

1. Create a free account at [app.brevo.com](https://app.brevo.com)
2. Verify your sender email address or domain:
   - Go to **Settings → Senders & IP → Senders** → click **Add a sender**
   - Add the email address you want the digest to be sent *from* (e.g. `noreply@yourdomain.com` or even a Gmail address)
   - Click the verification link sent to that address
3. Get your SMTP credentials:
   - Go to **Settings → SMTP & API** → click **Generate a new SMTP key**
   - Note your **Login** (your Brevo account email) and **Password** (the generated SMTP key)

### 3. Set up your credentials

```bash
cp .env.example .env
```

Edit `.env` and fill in every value:

```bash
ANTHROPIC_API_KEY=sk-ant-...       # from console.anthropic.com
RECIPIENT_EMAIL=you@example.com    # where to send the digest
BREVO_SMTP_USER=login@email.com    # your Brevo account email
BREVO_SMTP_KEY=xsmtp-...           # your Brevo SMTP key
SENDER_EMAIL=noreply@yourdomain.com  # verified sender in Brevo
SENDER_NAME=Positive News Digest
```

### 4. Test locally (send a real email now)

```bash
pip install anthropic feedparser
python scripts/test_local.py
```

This runs the full pipeline end-to-end and sends a real email to `RECIPIENT_EMAIL`. Check your inbox — check spam on the first run.

If the email arrives, you're ready to deploy.

### 5. Deploy to AWS

```bash
cd infra
sam build
sam deploy --guided
```

SAM will prompt you for each parameter:

| Parameter | What to enter |
|-----------|---------------|
| `AnthropicApiKey` | Your Anthropic API key |
| `RecipientEmail` | Your inbox email |
| `SenderEmail` | Your verified Brevo sender |
| `SenderName` | e.g. `Positive News Digest` |
| `BrevoSmtpUser` | Your Brevo login email |
| `BrevoSmtpKey` | Your Brevo SMTP key |
| `SendHour` | Hour to send (0–23) in your timezone |
| `SendMinute` | Minute to send (0–59), default `0` |
| `SendTimezone` | IANA timezone, e.g. `America/New_York` |

When SAM asks *"Save arguments to configuration file?"*, say **yes** — this saves your settings to `samconfig.toml` so future deploys just need `sam deploy`.

### 6. Test the deployed Lambda

```bash
aws lambda invoke \
  --function-name positive-news-mailer \
  --log-type Tail \
  /tmp/out.json \
  --query 'LogResult' --output text | base64 -d
```

Check `/tmp/out.json` for the response and the decoded log output for any errors.

---

## Customizing RSS Feeds

The list of RSS feeds is in `lambda/news_fetcher.py` under `RSS_FEEDS`. You can add, remove, or reorder any feeds.

**Some good positive news sources:**

| Feed | URL |
|------|-----|
| Good News Network | `https://www.goodnewsnetwork.org/feed/` |
| Positive News (UK) | `https://www.positive.news/feed/` |
| The Optimist Daily | `https://www.optimistdaily.com/feed/` |
| Science Daily | `https://www.sciencedaily.com/rss/top/science.xml` |
| BBC World | `https://feeds.bbci.co.uk/news/world/rss.xml` |
| Reuters | `https://feeds.reuters.com/reuters/topNews` |
| AP News | `https://apnews.com/rss` |

After editing feeds, redeploy with:

```bash
cd infra && sam build && sam deploy
```

---

## Changing Your Send Time

Edit the schedule by redeploying with updated parameters:

```bash
cd infra
sam deploy --parameter-overrides SendHour=7 SendTimezone=America/Los_Angeles
```

Or run `sam deploy --guided` again to walk through all parameters interactively.

**Common IANA timezones:**

| Location | Timezone |
|----------|----------|
| US Eastern | `America/New_York` |
| US Central | `America/Chicago` |
| US Mountain | `America/Denver` |
| US Pacific | `America/Los_Angeles` |
| UK | `Europe/London` |
| Central Europe | `Europe/Berlin` |
| India | `Asia/Kolkata` |
| Japan | `Asia/Tokyo` |
| Australia (East) | `Australia/Sydney` |

Full list: [en.wikipedia.org/wiki/List_of_tz_database_time_zones](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones)

---

## Monitoring

**View live logs:**
```bash
aws logs tail /aws/lambda/positive-news-mailer --follow
```

**View recent executions in AWS Console:**

After deployment, SAM will output a `LogGroup` URL. Open it to see all Lambda invocations and their output.

**What to expect in the logs:**
```
Fetching positive news stories...
Fetched 28 articles from 28 entries.
Sending to Claude Haiku for summarization...
Got 5 top stories back from Claude.
Sending email to you@example.com...
Email sent successfully.
```

A successful run typically takes 5–15 seconds (RSS fetch + Claude API call).

---

## Cost Breakdown

| Service | Monthly Usage | Free Limit | Cost |
|---------|--------------|------------|------|
| AWS Lambda | 30 invocations | 1,000,000/mo | **Free** |
| Lambda compute | ~0.5 GB-seconds | 400,000 GB-s/mo | **Free** |
| EventBridge Scheduler | 1 rule, 30 triggers | 14,000,000/mo | **Free** |
| RSS feed fetching | 30 HTTP requests | N/A | **Free** |
| Brevo email sending | 30 emails | 300/day (9,000/mo) | **Free** |
| **Claude Haiku tokens** | ~30,000 tokens/mo | N/A | **~$0.004** |

Total: roughly **half a cent per month**.

---

## Troubleshooting

### Email not arriving
- **Check spam** — the first email from a new sender often lands in spam. Mark it as "not spam" once.
- **Verify your sender** — in Brevo, go to Settings → Senders. The sender email must have a green checkmark.
- **Check Brevo logs** — go to Transactional → Email → Logs to see if Brevo received and delivered the email.

### Lambda times out
- The default timeout is 60 seconds. RSS + Claude usually takes 5–15s.
- If a feed is very slow, try removing it from `RSS_FEEDS` in `news_fetcher.py`.

### `No articles fetched` error
- One or more RSS feed URLs may have changed. Check each URL in a browser.
- The app skips failed feeds silently but needs at least one to succeed.

### `JSONDecodeError` from Claude response
- Rare — happens if Claude's response isn't valid JSON. The `summarize_with_claude` function strips markdown fences, but edge cases can occur.
- Add more context to the prompt in `news_fetcher.py` if this happens repeatedly.

### SAM deploy fails with "no such bucket"
- On the very first deploy SAM needs to create an S3 bucket. Make sure your AWS CLI is configured with a region: `aws configure set region us-east-1`.

### Credentials error after redeploy
- If you change your Brevo SMTP key, redeploy with the new key: `sam deploy --parameter-overrides BrevoSmtpKey=new-key`.

---

## Architecture Notes

- **No database** — configuration lives entirely in Lambda environment variables and the EventBridge schedule. Nothing to maintain.
- **No web UI** — configured once at deploy time via SAM parameters.
- **Idempotent** — running the Lambda multiple times on the same day just sends multiple emails. No deduplication logic needed for a once-daily schedule.
- **Error visibility** — if anything fails, the Lambda throws an exception which is logged to CloudWatch and marked as a failed invocation in the AWS Console.

---

## License

MIT — do whatever you want with it.
