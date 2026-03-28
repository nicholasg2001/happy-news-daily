"""Fetch positive news articles from RSS feeds and summarize with Claude Haiku."""

import json
import os
import feedparser
from anthropic import Anthropic

# Curated list of positive/uplifting news RSS feeds.
# Add or remove feeds to customize your digest.
RSS_FEEDS = [
    # Dedicated positive news sources
    "https://www.goodnewsnetwork.org/feed/",
    "https://www.positive.news/feed/",
    "https://www.optimistdaily.com/feed/",
    # Science & environment progress
    "https://feeds.feedburner.com/upi/Science",
    "https://www.sciencedaily.com/rss/top/science.xml",
    # General world news (Claude will filter for uplifting stories)
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://apnews.com/rss",
]

# --- Token budget guardrails ---
# These constants directly control how much text is sent to Claude Haiku.
# Rough token estimate: 1 token ≈ 4 characters.
# With defaults: 15 articles × ~310 chars each ≈ 4,650 chars ≈ 1,160 tokens input.
# Plus prompt overhead (~250 tokens) + max output (800 tokens) ≈ 2,200 tokens/run.
# At 30 runs/month and Haiku pricing: ~$0.01/month. Do not raise these without re-checking.
MAX_ARTICLES_PER_FEED = 3    # per RSS feed (was 5)
MAX_TOTAL_ARTICLES = 15      # hard cap before sending to Claude (was 30)
MAX_SUMMARY_CHARS = 250      # per article summary (was 500) — ~62 tokens each
MAX_PROMPT_CHARS = 6000      # absolute ceiling on the full prompt text sent to Claude
                             # ≈ 1,500 tokens. Truncates articles_text if feeds produce more.


def fetch_articles() -> list[dict]:
    """Parse all RSS feeds and return a flat list of article dicts."""
    articles = []
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:MAX_ARTICLES_PER_FEED]:
                summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
                # Strip any HTML tags from the summary (basic approach)
                summary = _strip_html(summary)[:MAX_SUMMARY_CHARS]
                articles.append({
                    "title": entry.get("title", "").strip(),
                    "summary": summary.strip(),
                    "link": entry.get("link", ""),
                    "published": entry.get("published", ""),
                })
        except Exception:
            # Skip feeds that fail — don't let one bad feed break everything
            pass
    return articles[:MAX_TOTAL_ARTICLES]


def _strip_html(text: str) -> str:
    """Remove HTML tags from a string."""
    import re
    return re.sub(r"<[^>]+>", " ", text).strip()


def summarize_with_claude(articles: list[dict]) -> list[dict]:
    """
    Pass articles to Claude Haiku and ask it to pick the 5 most uplifting stories.
    Returns a list of dicts: [{title, summary, link}, ...]
    """
    if not articles:
        return []

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    articles_text = "\n\n".join(
        f"[{i+1}] Title: {a['title']}\n"
        f"Summary: {a['summary']}\n"
        f"URL: {a['link']}"
        for i, a in enumerate(articles)
    )

    # Hard cap: drop whole articles (not mid-truncate) until we're under the budget.
    # Truncating mid-article produces garbled input; dropping the last article is cleaner.
    while len(articles_text) > MAX_PROMPT_CHARS and len(articles) > 1:
        articles = articles[:-1]
        articles_text = "\n\n".join(
            f"[{i+1}] Title: {a['title']}\n"
            f"Summary: {a['summary']}\n"
            f"URL: {a['link']}"
            for i, a in enumerate(articles)
        )
    if len(articles_text) > MAX_PROMPT_CHARS:
        # Single article still too long — shouldn't happen with MAX_SUMMARY_CHARS in place,
        # but hard-truncate as a final fallback.
        articles_text = articles_text[:MAX_PROMPT_CHARS]
    article_count = len(articles)
    if article_count < MAX_TOTAL_ARTICLES:
        print(f"WARNING: articles dropped to {article_count} to stay within MAX_PROMPT_CHARS={MAX_PROMPT_CHARS}.")

    prompt = (
        "Below are recent news articles. Your job is to select the 5 most genuinely "
        "uplifting, positive, and hopeful stories. Prioritize stories about real progress: "
        "scientific breakthroughs, environmental wins, people helping people, humanitarian "
        "milestones, community achievements. Avoid stories that are only superficially "
        "positive or that involve bad news with a silver lining.\n\n"
        f"{articles_text}\n\n"
        "Return ONLY a JSON array with exactly 5 objects. Each object must have these keys:\n"
        '  "title": the original headline (string)\n'
        '  "summary": a 2-3 sentence uplifting summary written in a warm, engaging tone (string)\n'
        '  "link": the original URL (string)\n\n'
        "Return only the JSON array, no other text."
    )

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,  # 5 stories × ~120 tokens each + JSON overhead. Hard ceiling.
        messages=[{"role": "user", "content": prompt}],
    )

    # Log actual token usage on every run so you can track real costs in CloudWatch.
    usage = response.usage
    estimated_cost_usd = (usage.input_tokens / 1_000_000 * 0.80) + (usage.output_tokens / 1_000_000 * 4.00)
    print(
        f"Claude usage — input: {usage.input_tokens} tokens, "
        f"output: {usage.output_tokens} tokens, "
        f"estimated cost this run: ${estimated_cost_usd:.6f}"
    )

    raw = response.content[0].text.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        stories = json.loads(raw)
    except json.JSONDecodeError as e:
        # Most likely cause: response was cut off at max_tokens=800 before JSON closed.
        # Raise with context so CloudWatch shows the raw response for debugging.
        raise ValueError(
            f"Claude returned invalid JSON (possibly truncated at max_tokens). "
            f"Error: {e}. Raw response: {raw[:300]!r}"
        ) from e
    return stories[:5]


def get_top_positive_stories() -> list[dict]:
    """Full pipeline: fetch RSS → summarize with Claude → return top 5 stories."""
    articles = fetch_articles()
    if not articles:
        raise RuntimeError("No articles fetched from any RSS feed.")
    return summarize_with_claude(articles)
