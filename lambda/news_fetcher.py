"""Fetch and summarize positive news stories.

TIER=standard (default): Claude Haiku + RSS feeds. ~$0.11/month.
TIER=premium:            Claude Sonnet + web search. ~$1.50-1.80/month.
"""

import json
import os
from datetime import date
from anthropic import Anthropic

# Add or remove feeds to customize your digest.
RSS_FEEDS = [
    "https://www.goodnewsnetwork.org/feed/",
    "https://www.positive.news/feed/",
    "https://www.optimistdaily.com/feed/",
    "https://feeds.feedburner.com/upi/Science",
    "https://www.sciencedaily.com/rss/top/science.xml",
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://apnews.com/rss",
]

# Standard tier token budget. Canary tests enforce these ceilings — update tests if you raise them.
MAX_ARTICLES_PER_FEED = 3
MAX_TOTAL_ARTICLES = 15
MAX_SUMMARY_CHARS = 250
MAX_PROMPT_CHARS = 6000

# Premium tier budget. Each web search costs $0.01; Sonnet input runs ~10K-15K tokens/run.
PREMIUM_MAX_USES = 1
PREMIUM_MAX_TOKENS = 1200


def fetch_articles() -> list[dict]:
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
            pass
    return articles[:MAX_TOTAL_ARTICLES]


def _strip_html(text: str) -> str:
    import re
    return re.sub(r"<[^>]+>", " ", text).strip()


def _get_stories_standard() -> list[dict]:
    articles = fetch_articles()
    if not articles:
        raise RuntimeError("No articles fetched from any RSS feed.")

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    def build_text(arts):
        return "\n\n".join(
            f"[{i+1}] Title: {a['title']}\nSummary: {a['summary']}\nURL: {a['link']}"
            for i, a in enumerate(arts)
        )

    articles_text = build_text(articles)
    while len(articles_text) > MAX_PROMPT_CHARS and len(articles) > 1:
        articles = articles[:-1]
        articles_text = build_text(articles)
    if len(articles_text) > MAX_PROMPT_CHARS:
        articles_text = articles_text[:MAX_PROMPT_CHARS]
    if len(articles) < MAX_TOTAL_ARTICLES:
        print(f"WARNING: articles dropped to {len(articles)} to stay within prompt budget.")

    prompt = (
        "Below are recent news articles. Select the 5 most genuinely uplifting, positive, "
        "and hopeful stories. Prioritize real progress: scientific breakthroughs, environmental "
        "wins, people helping people, humanitarian milestones, community achievements. Avoid "
        "stories that are only superficially positive or silver linings around bad events.\n\n"
        f"{articles_text}\n\n"
        "Return ONLY a JSON array with exactly 5 objects, each with keys:\n"
        '  "title": the original headline (string)\n'
        '  "summary": a 2-3 sentence uplifting summary in a warm, engaging tone (string)\n'
        '  "link": the original URL (string)\n\n'
        "Return only the JSON array, no other text."
    )

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )

    _log_usage(response.usage, tier="standard")
    return _parse_stories(response.content[0].text)


def _get_stories_premium() -> list[dict]:
    today = date.today().strftime("%B %d, %Y")
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    prompt = (
        f"Today is {today}. Search the web for 5 genuinely positive, uplifting global "
        "news stories published today or within the last 48 hours. Cover a variety of "
        "topics: science, environment, humanitarian, community, technology, health, etc. "
        "Focus on real progress and concrete achievements, not feel-good fluff or silver "
        "linings around bad events.\n\n"
        "Return ONLY a JSON array with exactly 5 objects, each with keys:\n"
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
    text_parts = [block.text for block in response.content if block.type == "text"]
    return _parse_stories("\n".join(text_parts))


_HAIKU_INPUT_COST_PER_M = 0.80
_HAIKU_OUTPUT_COST_PER_M = 4.00
_SONNET_INPUT_COST_PER_M = 3.00
_SONNET_OUTPUT_COST_PER_M = 15.00
_WEB_SEARCH_COST_PER_SEARCH = 0.01


def _log_usage(usage, *, tier: str) -> None:
    if tier == "premium":
        token_cost = (
            (usage.input_tokens / 1_000_000 * _SONNET_INPUT_COST_PER_M)
            + (usage.output_tokens / 1_000_000 * _SONNET_OUTPUT_COST_PER_M)
        )
        search_cost = _WEB_SEARCH_COST_PER_SEARCH * PREMIUM_MAX_USES
        print(
            f"Claude usage ({tier}) — input: {usage.input_tokens} tokens, "
            f"output: {usage.output_tokens} tokens, "
            f"estimated cost this run: ${token_cost + search_cost:.6f} "
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
    """Parse a JSON array from Claude's response, handling preamble text and code fences."""
    raw = raw.strip()

    if "```" in raw:
        parts = raw.split("```")
        if len(parts) >= 3:
            fenced = parts[1]
            if fenced.startswith("json"):
                fenced = fenced[4:]
            raw = fenced.strip()

    if not raw.startswith("["):
        start = raw.find("[")
        end = raw.rfind("]")
        if start != -1 and end > start:
            raw = raw[start:end + 1]

    try:
        stories = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Claude returned invalid JSON (possibly truncated at max_tokens). "
            f"Error: {e}. Raw response: {raw[:300]!r}"
        ) from e
    return stories[:5]


def get_top_positive_stories() -> list[dict]:
    tier = os.environ.get("TIER", "standard").lower()
    if tier == "premium":
        print("Running in premium tier (Sonnet + web search).")
        return _get_stories_premium()
    print("Running in standard tier (Haiku + RSS feeds).")
    return _get_stories_standard()
