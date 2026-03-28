"""Fetch and summarize positive news stories.

Two tiers, selected by the TIER environment variable:

  standard (default) — Claude Haiku + RSS feeds. ~$0.11/month.
                        No web access; stories may be 1-3 days old.

  premium            — Claude Sonnet + built-in web search. ~$0.95/month.
                        Searches the live web for today's stories; fresher
                        and more diverse, with higher-quality prose.
"""

import json
import os
from datetime import date
from anthropic import Anthropic

# ---------------------------------------------------------------------------
# Standard tier — RSS feeds + Claude Haiku
# ---------------------------------------------------------------------------

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

# --- Token budget guardrails (standard tier only) ---
# These constants directly control how much text is sent to Claude Haiku.
# Rough token estimate: 1 token ≈ 4 characters.
# With defaults: 15 articles × ~310 chars each ≈ 4,650 chars ≈ 1,160 tokens input.
# Plus prompt overhead (~250 tokens) + max output (800 tokens) ≈ 2,200 tokens/run.
# At 30 runs/month and Haiku pricing: ~$0.11/month. Do not raise these without re-checking.
MAX_ARTICLES_PER_FEED = 3    # per RSS feed
MAX_TOTAL_ARTICLES = 15      # hard cap before sending to Claude
MAX_SUMMARY_CHARS = 250      # per article summary — ~62 tokens each
MAX_PROMPT_CHARS = 6000      # absolute ceiling on the articles block sent to Claude
                             # ≈ 1,500 tokens. Drops whole articles to stay under.


def fetch_articles() -> list[dict]:
    """Parse all RSS feeds and return a flat list of article dicts."""
    import feedparser

    articles = []
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:MAX_ARTICLES_PER_FEED]:
                summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
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


def _get_stories_standard() -> list[dict]:
    """
    Standard tier: fetch RSS feeds, send to Claude Haiku for summarization.
    Returns list of dicts: [{title, summary, link}, ...]
    """
    articles = fetch_articles()
    if not articles:
        raise RuntimeError("No articles fetched from any RSS feed.")

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    articles_text = "\n\n".join(
        f"[{i+1}] Title: {a['title']}\n"
        f"Summary: {a['summary']}\n"
        f"URL: {a['link']}"
        for i, a in enumerate(articles)
    )

    # Hard cap: drop whole articles until we're under the prompt character budget.
    # Dropping is cleaner than mid-string truncation, which would garble the last entry.
    while len(articles_text) > MAX_PROMPT_CHARS and len(articles) > 1:
        articles = articles[:-1]
        articles_text = "\n\n".join(
            f"[{i+1}] Title: {a['title']}\n"
            f"Summary: {a['summary']}\n"
            f"URL: {a['link']}"
            for i, a in enumerate(articles)
        )
    if len(articles_text) > MAX_PROMPT_CHARS:
        articles_text = articles_text[:MAX_PROMPT_CHARS]
    if len(articles) < MAX_TOTAL_ARTICLES:
        print(f"WARNING: articles dropped to {len(articles)} to stay within MAX_PROMPT_CHARS={MAX_PROMPT_CHARS}.")

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

    _log_usage(response.usage, tier="standard")
    return _parse_stories(response.content[0].text)


# ---------------------------------------------------------------------------
# Premium tier — Claude Sonnet + web search
# ---------------------------------------------------------------------------

# Premium tier budget guardrails.
# web_search: $10/1000 searches. max_uses=1 → at most $0.01/run → $0.30/month.
# Sonnet input tokens with search results: ~3,000-5,000/run → ~$0.36-$0.45/month.
# Sonnet output tokens at max_tokens=1200: ~$0.54/month worst case.
# Total ceiling: ~$1.00/month. Do not raise max_uses or max_tokens without re-checking.
PREMIUM_MAX_USES = 1      # web searches per run — $0.01 each
PREMIUM_MAX_TOKENS = 1200 # output token ceiling for Sonnet


def _get_stories_premium() -> list[dict]:
    """
    Premium tier: Claude Sonnet searches the live web for today's positive news.
    Returns list of dicts: [{title, summary, link}, ...]
    """
    today = date.today().strftime("%B %d, %Y")
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    prompt = (
        f"Today is {today}. Search the web for 5 genuinely positive, uplifting global "
        "news stories published today or within the last 48 hours. Cover a variety of "
        "topics: science, environment, humanitarian, community, technology, health, etc. "
        "Focus on real progress and concrete achievements — not feel-good fluff or silver "
        "linings around bad events.\n\n"
        "Return ONLY a JSON array with exactly 5 objects. Each object must have:\n"
        '  "title": the news headline (string)\n'
        '  "summary": a 2-3 sentence uplifting summary in a warm, engaging tone (string)\n'
        '  "link": the article URL (string)\n\n'
        "Return only the JSON array, no other text."
    )

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=PREMIUM_MAX_TOKENS,
        tools=[{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": PREMIUM_MAX_USES,
        }],
        messages=[{"role": "user", "content": prompt}],
    )

    _log_usage(response.usage, tier="premium")

    # Collect text blocks — the response may also contain server_tool_use and
    # web_search_tool_result blocks which we skip.
    text_parts = [block.text for block in response.content if block.type == "text"]
    raw = "\n".join(text_parts).strip()
    return _parse_stories(raw)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_HAIKU_INPUT_COST_PER_M = 0.80
_HAIKU_OUTPUT_COST_PER_M = 4.00
_SONNET_INPUT_COST_PER_M = 3.00
_SONNET_OUTPUT_COST_PER_M = 15.00
_WEB_SEARCH_COST_PER_SEARCH = 0.01  # $10/1000


def _log_usage(usage, *, tier: str) -> None:
    """Print token counts and estimated cost to stdout (visible in CloudWatch Logs)."""
    if tier == "premium":
        token_cost = (
            (usage.input_tokens / 1_000_000 * _SONNET_INPUT_COST_PER_M)
            + (usage.output_tokens / 1_000_000 * _SONNET_OUTPUT_COST_PER_M)
        )
        search_cost = _WEB_SEARCH_COST_PER_SEARCH * PREMIUM_MAX_USES
        total = token_cost + search_cost
        print(
            f"Claude usage ({tier}) — input: {usage.input_tokens} tokens, "
            f"output: {usage.output_tokens} tokens, "
            f"estimated cost this run: ${total:.6f} "
            f"(tokens ${token_cost:.6f} + web search ~${search_cost:.4f})"
        )
    else:
        token_cost = (
            (usage.input_tokens / 1_000_000 * _HAIKU_INPUT_COST_PER_M)
            + (usage.output_tokens / 1_000_000 * _HAIKU_OUTPUT_COST_PER_M)
        )
        print(
            f"Claude usage ({tier}) — input: {usage.input_tokens} tokens, "
            f"output: {usage.output_tokens} tokens, "
            f"estimated cost this run: ${token_cost:.6f}"
        )


def _parse_stories(raw: str) -> list[dict]:
    """Strip optional markdown fences and parse a JSON array of stories."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        stories = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Claude returned invalid JSON (possibly truncated at max_tokens). "
            f"Error: {e}. Raw response: {raw[:300]!r}"
        ) from e
    return stories[:5]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def get_top_positive_stories() -> list[dict]:
    """
    Return 5 positive news stories using the configured tier.

    Set TIER=standard (default) for Haiku + RSS (~$0.11/month).
    Set TIER=premium for Sonnet + web search (~$0.95/month).
    """
    tier = os.environ.get("TIER", "standard").lower()
    if tier == "premium":
        print("Running in premium tier (Sonnet + web search).")
        return _get_stories_premium()
    else:
        print("Running in standard tier (Haiku + RSS feeds).")
        return _get_stories_standard()
