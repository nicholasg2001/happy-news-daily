# Positive News Mailer

A self-hosted AWS app that sends you a daily email digest of uplifting news from around the world, curated by Claude Haiki or Sonnet. Because who can afford to use Opus for this?

Two tiers to choose from:

| Tier | Model | News source | Cost/month |
|------|-------|-------------|------------|
| `standard` (default) | Claude Haiku | Curated RSS feeds | ~$0.11 |
| `premium` | Claude Sonnet | Live web search | ~$0.95 |

All AWS infrastructure (Lambda, EventBridge) runs on the permanent free tier. Email sending is free via Brevo (300 emails/day).

```
EventBridge Scheduler (daily, timezone-aware cron)
        |
        v
  AWS Lambda (Python)
        |
        +-- standard: fetch RSS feeds --> Claude Haiku --> summarize top 5
        |
        +-- premium:  Claude Sonnet + web search --> find today's top 5
        |
        v
  Send HTML email via Brevo SMTP
```

---

## Tiers

### Standard (~$0.11/month)

Uses a curated list of positive news RSS feeds (Good News Network, Positive News, Science Daily, etc.) as input. Claude Haiku reads the feed summaries and selects the 5 most genuinely uplifting stories.

- Stories may be 1-3 days old depending on feed update frequency
- Limited to sources in `RSS_FEEDS` in `news_fetcher.py`
- Token spend is tightly capped by guardrail constants in the same file

### Premium (~$0.95/month) (In this economy)

Claude Sonnet uses the built-in web search tool to find today's positive news from across the internet. Stories are fresher, drawn from a wider range of sources, and written with more nuanced prose.

- Searches the live web for stories published in the last 24-48 hours
- One web search per run ($0.01/search x 30 days = $0.30/month)
- Remaining cost is Sonnet token usage (~$0.65/month)

To switch tiers, set `TIER=premium` in your `.env` file or pass `Tier=premium` to `sam deploy`.

---

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| AWS account | [aws.amazon.com](https://aws.amazon.com) — free tier is enough |
| AWS CLI | [Install guide](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) — run `aws configure` after |
| AWS SAM CLI | [Install guide](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html) |
| Anthropic API key | [console.anthropic.com](https://console.anthropic.com/) |
| Brevo account | [app.brevo.com](https://app.brevo.com) — free, 300 emails/day, no credit card |
| Python 3.12+ | For local testing only |

---

## Quick Start

### 1. Clone the repo

```bash
git clone https://github.com/yourusername/positive-news-mailer.git
cd positive-news-mailer
```

### 2. Set up Brevo (free email sending)

1. Create a free account at [app.brevo.com](https://app.brevo.com)
2. Verify your sender email address:
   - Go to **Settings -> Senders & IP -> Senders** -> **Add a sender**
   - Add the address you want the digest sent *from* (e.g. `noreply@yourdomain.com`)
   - Click the verification link Brevo sends to that address
3. Get your SMTP credentials:
   - Go to **Settings -> SMTP & API** -> **Generate a new SMTP key**
   - Note your **Login** (your Brevo account email) and **Password** (the generated key)

### 3. Configure credentials

```bash
cp .env.example .env
```

Edit `.env`:

```bash
ANTHROPIC_API_KEY=sk-ant-...
RECIPIENT_EMAIL=you@example.com
BREVO_SMTP_USER=login@email.com
BREVO_SMTP_KEY=xsmtp-...
SENDER_EMAIL=noreply@yourdomain.com
SENDER_NAME=Positive News Digest
TIER=standard                        # or: premium
```

### 4. Test locally

```bash
python3 -m venv venv && source venv/bin/activate
pip install anthropic feedparser
python scripts/test_local.py
```

This runs the full pipeline and sends a real email to `RECIPIENT_EMAIL`. Check your inbox — check spam on the first run. If the email arrives, you're ready to deploy.

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
| `SenderEmail` | Your verified Brevo sender address |
| `SenderName` | Display name, e.g. `Positive News Digest` |
| `BrevoSmtpUser` | Your Brevo login email |
| `BrevoSmtpKey` | Your Brevo SMTP key |
| `SendHour` | Hour to send (0-23) in your timezone |
| `SendMinute` | Minute to send (0-59), default `0` |
| `SendTimezone` | IANA timezone, e.g. `America/New_York` |
| `Tier` | `standard` or `premium` |

When SAM asks "Save arguments to configuration file?", say yes — this saves your settings to `samconfig.toml` so future deploys only need `sam deploy`.

### 6. Verify the deployment

```bash
aws lambda invoke \
  --function-name positive-news-mailer \
  --log-type Tail \
  /tmp/out.json \
  --query 'LogResult' --output text | base64 -d
```

---

## Switching Tiers

To switch from standard to premium (or back) without redeploying everything:

```bash
cd infra
sam deploy --parameter-overrides Tier=premium
```

You can also combine overrides:

```bash
sam deploy --parameter-overrides Tier=premium SendHour=7 SendTimezone=America/Los_Angeles
```

---

## Customizing RSS Feeds (Standard Tier)

The feed list is in `lambda/news_fetcher.py` under `RSS_FEEDS`. Add, remove, or reorder freely.

Good positive news sources:

| Feed | URL |
|------|-----|
| Good News Network | `https://www.goodnewsnetwork.org/feed/` |
| Positive News (UK) | `https://www.positive.news/feed/` |
| The Optimist Daily | `https://www.optimistdaily.com/feed/` |
| Science Daily | `https://www.sciencedaily.com/rss/top/science.xml` |
| BBC World | `https://feeds.bbci.co.uk/news/world/rss.xml` |
| Reuters | `https://feeds.reuters.com/reuters/topNews` |
| AP News | `https://apnews.com/rss` |

After editing, redeploy:

```bash
cd infra && sam build && sam deploy
```

---

## Changing Your Send Time

```bash
cd infra
sam deploy --parameter-overrides SendHour=7 SendTimezone=America/Los_Angeles
```

Common IANA timezones:

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

## Running the Tests

The test suite verifies all cost guardrails without making real API calls or hitting real RSS feeds.

```bash
python3 -m venv venv && source venv/bin/activate
pip install pytest anthropic feedparser
pytest tests/ -v
```

The tests are split into four groups:

**`TestBudgetConstants` — canary tests**
These fail immediately if any guardrail constant is raised above its safe threshold. Changing `MAX_ARTICLES_PER_FEED`, `MAX_TOTAL_ARTICLES`, `MAX_SUMMARY_CHARS`, `MAX_PROMPT_CHARS`, `PREMIUM_MAX_USES`, or `PREMIUM_MAX_TOKENS` without updating the corresponding threshold here will cause a test failure. That's intentional — it forces a conscious decision.

**`TestFetchArticles` — RSS parsing**
Verifies that the per-feed article cap, total article cap, summary truncation, HTML stripping, and failed-feed skipping all work correctly.

**`TestStandardTier` — Haiku + RSS path**
Verifies the model is Haiku (not a more expensive model), `max_tokens` is within budget, no web search tool is passed (which would cost $10/1000 searches), the prompt stays within `MAX_PROMPT_CHARS`, output is capped at 5 stories, and token usage is logged on every run.

**`TestPremiumTier` — Sonnet + web search path**
Verifies the model is Sonnet, `max_tokens` is within `PREMIUM_MAX_TOKENS`, the `web_search_20250305` tool is present with `max_uses <= PREMIUM_MAX_USES`, only text content blocks are parsed (skipping tool-use blocks), output is capped at 5 stories, and the log includes web search cost.

**`TestTierDispatch`**
Verifies that `TIER=standard` (and no `TIER` set) routes to the standard path, and `TIER=premium` routes to the premium path.

---

## Monitoring

View live logs:

```bash
aws logs tail /aws/lambda/positive-news-mailer --follow
```

After deployment, SAM outputs a `LogGroup` URL directly to the CloudWatch console.

Every run logs token usage and estimated cost:

```
# Standard tier
Running in standard tier (Haiku + RSS feeds).
Claude usage (standard) — input: 1423 tokens, output: 612 tokens, estimated cost this run: $0.003588

# Premium tier
Running in premium tier (Sonnet + web search).
Claude usage (premium) — input: 3821 tokens, output: 734 tokens, estimated cost this run: $0.022553 (tokens $0.012543 + web search ~$0.0100)
```

A successful run takes 5-20 seconds (standard) or 10-30 seconds (premium, due to web search latency).

---

## Cost Breakdown

### Standard tier

| Service | Monthly Usage | Free Limit | Cost |
|---------|--------------|------------|------|
| AWS Lambda | 30 invocations | 1,000,000/mo | Free |
| Lambda compute | ~0.5 GB-seconds | 400,000 GB-s/mo | Free |
| EventBridge Scheduler | 30 triggers | 14,000,000/mo | Free |
| RSS feed fetching | 30 HTTP requests | N/A | Free |
| Brevo email | 30 emails | 300/day | Free |
| Claude Haiku tokens | ~60,000 tokens/mo | N/A | ~$0.11 |

### Premium tier

| Service | Monthly Usage | Free Limit | Cost |
|---------|--------------|------------|------|
| AWS Lambda | 30 invocations | 1,000,000/mo | Free |
| Brevo email | 30 emails | 300/day | Free |
| Web search | 30 searches | N/A | ~$0.30 |
| Claude Sonnet tokens | ~135,000 tokens/mo | N/A | ~$0.65 |

---

## Troubleshooting

### Email not arriving
- Check spam — the first email from a new sender often lands there. Mark it as "not spam" once.
- Verify your sender in Brevo: Settings -> Senders. The address needs a green checkmark.
- Check Brevo delivery logs: Transactional -> Email -> Logs.

### Lambda times out
- The default timeout is 60 seconds. Standard typically takes 5-20s; premium 10-30s.
- For standard tier: if a feed is consistently slow, remove it from `RSS_FEEDS`.
- For premium tier: web search latency varies. If timeouts are frequent, increase the Lambda timeout in `template.yaml` (still free tier at 60-90s with 256MB).

### No articles fetched (standard tier)
- One or more RSS feed URLs may have changed or gone offline. Test each URL in a browser.
- The app skips failed feeds silently but needs at least one to succeed.

### Invalid JSON from Claude
- Most likely cause: the response was truncated at `max_tokens`. The error message in CloudWatch will include the raw response.
- For standard tier: this is extremely unlikely at 800 tokens for 5 short JSON objects.
- For premium tier: also unlikely at 1200 tokens, but web search results can push input tokens higher, occasionally causing Sonnet to produce a longer response.

### SAM deploy fails with "no such bucket"
- On the first deploy SAM creates an S3 bucket for build artifacts. Make sure your AWS CLI has a region set: `aws configure set region us-east-1`.

### Credentials error after redeploy
- Redeploy with the updated value: `sam deploy --parameter-overrides BrevoSmtpKey=new-key`.

---

## Architecture Notes

- No database — all configuration lives in Lambda environment variables and the EventBridge schedule.
- No web UI — configured once at deploy time via SAM parameters.
- Retries are disabled at both the Lambda layer (`MaximumRetryAttempts: 0`) and the EventBridge Scheduler layer (`RetryPolicy.MaximumRetryAttempts: 0`). A failed run is logged to CloudWatch and nothing is retried, preventing accidental double charges.
- Reserved concurrency is set to 1, blocking any parallel invocations.

---

## License

MIT — do whatever you want with it.
