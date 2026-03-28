"""
Guardrail tests for news_fetcher.py.

These tests do NOT call the real Claude API or real RSS feeds — everything is mocked.
They verify that budget constants are respected and edge cases are handled safely
across both the standard (Haiku + RSS) and premium (Sonnet + web search) tiers.

Run with:
    pip install pytest anthropic feedparser
    pytest tests/
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add lambda/ to path so we can import the module directly
sys.path.insert(0, str(Path(__file__).parent.parent / "lambda"))

import news_fetcher
from news_fetcher import (
    MAX_ARTICLES_PER_FEED,
    MAX_PROMPT_CHARS,
    MAX_SUMMARY_CHARS,
    MAX_TOTAL_ARTICLES,
    PREMIUM_MAX_TOKENS,
    PREMIUM_MAX_USES,
    fetch_articles,
    _get_stories_standard,
    _get_stories_premium,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_feed_entry(title="Headline", summary="Some summary text.", link="https://example.com/story"):
    entry = MagicMock()
    entry.title = title
    entry.summary = summary
    entry.description = summary
    entry.link = link
    entry.get = lambda key, default="": {"title": title, "link": link, "published": ""}.get(key, default)
    return entry


def make_feed(entries):
    feed = MagicMock()
    feed.entries = entries
    return feed


def make_standard_response(stories: list[dict], input_tokens=400, output_tokens=300):
    """Mock Anthropic response for standard tier (single text content block)."""
    response = MagicMock()
    response.content = [MagicMock(text=json.dumps(stories), type="text")]
    response.usage = MagicMock(input_tokens=input_tokens, output_tokens=output_tokens)
    return response


def make_premium_response(stories: list[dict], input_tokens=2000, output_tokens=600):
    """Mock Anthropic response for premium tier (mixed content blocks including text)."""
    # Simulate a response that includes a server_tool_use block followed by a text block,
    # as the web_search tool generates before Claude's final answer.
    tool_block = MagicMock()
    tool_block.type = "server_tool_use"
    # No .text attribute on tool blocks — accessing it should not be needed.
    del tool_block.text

    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = json.dumps(stories)

    response = MagicMock()
    response.content = [tool_block, text_block]
    response.usage = MagicMock(input_tokens=input_tokens, output_tokens=output_tokens)
    return response


def sample_stories(n=5):
    return [
        {"title": f"Story {i}", "summary": f"Summary {i}.", "link": f"https://ex.com/{i}"}
        for i in range(n)
    ]


def sample_articles(n=10):
    return [
        {"title": f"Article {i}", "summary": "Short summary.", "link": f"https://ex.com/{i}"}
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Canary: budget constants must not be raised above safe thresholds
# ---------------------------------------------------------------------------

class TestBudgetConstants:
    """
    These tests fail if any guardrail constant is raised beyond its safe limit.
    They are intentional tripwires — a change here must be made consciously.
    Standard tier constants only; premium tier limits are tested separately.
    """

    def test_max_articles_per_feed_within_limit(self):
        assert MAX_ARTICLES_PER_FEED <= 3, (
            f"MAX_ARTICLES_PER_FEED={MAX_ARTICLES_PER_FEED} exceeds safe limit of 3. "
            "Raising this increases token input. Update this threshold if intentional."
        )

    def test_max_total_articles_within_limit(self):
        assert MAX_TOTAL_ARTICLES <= 15, (
            f"MAX_TOTAL_ARTICLES={MAX_TOTAL_ARTICLES} exceeds safe limit of 15."
        )

    def test_max_summary_chars_within_limit(self):
        assert MAX_SUMMARY_CHARS <= 250, (
            f"MAX_SUMMARY_CHARS={MAX_SUMMARY_CHARS} exceeds safe limit of 250. "
            "Each extra 4 chars is ~1 extra token sent to Claude."
        )

    def test_max_prompt_chars_within_limit(self):
        assert MAX_PROMPT_CHARS <= 6000, (
            f"MAX_PROMPT_CHARS={MAX_PROMPT_CHARS} exceeds safe limit of 6000 chars (~1500 tokens)."
        )

    def test_premium_max_uses_within_limit(self):
        assert PREMIUM_MAX_USES <= 1, (
            f"PREMIUM_MAX_USES={PREMIUM_MAX_USES} exceeds safe limit of 1. "
            "Each web search costs $0.01. Raising this raises the monthly ceiling."
        )

    def test_premium_max_tokens_within_limit(self):
        assert PREMIUM_MAX_TOKENS <= 1200, (
            f"PREMIUM_MAX_TOKENS={PREMIUM_MAX_TOKENS} exceeds safe limit of 1200. "
            "At Sonnet output pricing ($15/M), each extra 1000 tokens adds $0.45/month."
        )

    def test_worst_case_articles_text_fits_prompt_budget(self):
        """
        Worst-case article sizes must stay under MAX_PROMPT_CHARS after the drop-loop.
        title=100 chars, summary=MAX_SUMMARY_CHARS, URL=100 chars → ~480 chars/article.
        15 such articles = 7,200 chars > 6,000, so the drop-loop must bring it down.
        """
        long_title = "A" * 100
        long_summary = "B" * MAX_SUMMARY_CHARS
        long_url = "https://example.com/" + "c" * 80
        articles = [
            {"title": long_title, "summary": long_summary, "link": long_url}
            for _ in range(MAX_TOTAL_ARTICLES)
        ]

        def build_text(arts):
            return "\n\n".join(
                f"[{i+1}] Title: {a['title']}\nSummary: {a['summary']}\nURL: {a['link']}"
                for i, a in enumerate(arts)
            )

        arts = list(articles)
        text = build_text(arts)
        while len(text) > MAX_PROMPT_CHARS and len(arts) > 1:
            arts = arts[:-1]
            text = build_text(arts)

        assert len(text) <= MAX_PROMPT_CHARS, (
            f"Even after dropping articles, prompt is {len(text)} chars > {MAX_PROMPT_CHARS}."
        )
        assert len(arts) >= 1


# ---------------------------------------------------------------------------
# fetch_articles: RSS parsing and per-feed caps (standard tier)
# ---------------------------------------------------------------------------

class TestFetchArticles:
    # feedparser is imported inside fetch_articles(), so we patch feedparser.parse directly.
    def _feed_with_n_entries(self, n):
        return make_feed([
            make_feed_entry(title=f"Story {i}", summary="x" * 300, link=f"https://ex.com/{i}")
            for i in range(n)
        ])

    @patch("feedparser.parse")
    def test_per_feed_cap_respected(self, mock_parse):
        mock_parse.return_value = self._feed_with_n_entries(20)
        with patch.object(news_fetcher, "RSS_FEEDS", ["https://feed1.example.com/"]):
            articles = fetch_articles()
        assert len(articles) == MAX_ARTICLES_PER_FEED

    @patch("feedparser.parse")
    def test_total_article_cap_respected(self, mock_parse):
        mock_parse.return_value = self._feed_with_n_entries(MAX_ARTICLES_PER_FEED)
        many_feeds = [f"https://feed{i}.example.com/" for i in range(20)]
        with patch.object(news_fetcher, "RSS_FEEDS", many_feeds):
            articles = fetch_articles()
        assert len(articles) <= MAX_TOTAL_ARTICLES

    @patch("feedparser.parse")
    def test_summary_truncated_to_max_chars(self, mock_parse):
        mock_parse.return_value = make_feed([make_feed_entry(summary="X" * 1000)])
        with patch.object(news_fetcher, "RSS_FEEDS", ["https://feed.example.com/"]):
            articles = fetch_articles()
        assert len(articles[0]["summary"]) <= MAX_SUMMARY_CHARS

    @patch("feedparser.parse")
    def test_html_stripped_from_summary(self, mock_parse):
        mock_parse.return_value = make_feed([make_feed_entry(summary="<p>Hello <b>world</b></p>")])
        with patch.object(news_fetcher, "RSS_FEEDS", ["https://feed.example.com/"]):
            articles = fetch_articles()
        assert "<" not in articles[0]["summary"]
        assert "Hello" in articles[0]["summary"]

    @patch("feedparser.parse")
    def test_failed_feed_is_skipped(self, mock_parse):
        good_feed = make_feed([make_feed_entry(title="Good story")])
        mock_parse.side_effect = [Exception("timeout"), good_feed]
        with patch.object(news_fetcher, "RSS_FEEDS", ["https://bad.example.com/", "https://good.example.com/"]):
            articles = fetch_articles()
        assert len(articles) == 1
        assert articles[0]["title"] == "Good story"


# ---------------------------------------------------------------------------
# Standard tier: API call parameters and output guardrails
# ---------------------------------------------------------------------------

class TestStandardTier:
    @patch("news_fetcher.Anthropic")
    def test_uses_haiku_model(self, mock_anthropic_cls):
        """Standard tier must always use claude-haiku — never a more expensive model."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = make_standard_response(sample_stories())

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("news_fetcher.fetch_articles", return_value=sample_articles()):
                _get_stories_standard()

        model = mock_client.messages.create.call_args.kwargs["model"]
        assert model == "claude-haiku-4-5-20251001", (
            f"Expected claude-haiku-4-5-20251001, got {model!r}. "
            "Switching to a pricier model will blow the cost estimate."
        )

    @patch("news_fetcher.Anthropic")
    def test_max_tokens_enforced(self, mock_anthropic_cls):
        """Standard tier max_tokens must be <= 800."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = make_standard_response(sample_stories())

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("news_fetcher.fetch_articles", return_value=sample_articles()):
                _get_stories_standard()

        max_tokens = mock_client.messages.create.call_args.kwargs["max_tokens"]
        assert max_tokens <= 800, f"max_tokens={max_tokens} exceeds safe limit of 800."

    @patch("news_fetcher.Anthropic")
    def test_no_web_search_tool(self, mock_anthropic_cls):
        """Standard tier must not use the web_search tool (it costs $10/1000 searches)."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = make_standard_response(sample_stories())

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("news_fetcher.fetch_articles", return_value=sample_articles()):
                _get_stories_standard()

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert "tools" not in call_kwargs, (
            "Standard tier passed tools to Claude. web_search costs $10/1000 searches."
        )

    @patch("news_fetcher.Anthropic")
    def test_prompt_respects_char_budget(self, mock_anthropic_cls):
        """Prompt articles block stays within MAX_PROMPT_CHARS even with fat articles."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = make_standard_response(sample_stories())

        fat_articles = [
            {"title": "T" * 100, "summary": "S" * MAX_SUMMARY_CHARS, "link": "https://ex.com/" + "l" * 80}
            for _ in range(MAX_TOTAL_ARTICLES)
        ]

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("news_fetcher.fetch_articles", return_value=fat_articles):
                _get_stories_standard()

        prompt_sent = mock_client.messages.create.call_args.kwargs["messages"][0]["content"]
        # 600 chars of slack for the fixed instruction text surrounding articles_text
        assert len(prompt_sent) <= MAX_PROMPT_CHARS + 600

    @patch("news_fetcher.Anthropic")
    def test_output_capped_at_5(self, mock_anthropic_cls):
        """Standard tier returns at most 5 stories even if Claude returns more."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = make_standard_response(sample_stories(n=10))

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("news_fetcher.fetch_articles", return_value=sample_articles()):
                result = _get_stories_standard()

        assert len(result) == 5

    @patch("news_fetcher.Anthropic")
    def test_token_usage_is_logged(self, mock_anthropic_cls, capsys):
        """Token usage must be printed so it appears in CloudWatch Logs."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = make_standard_response(
            sample_stories(), input_tokens=1234, output_tokens=567
        )

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("news_fetcher.fetch_articles", return_value=sample_articles()):
                _get_stories_standard()

        out = capsys.readouterr().out
        assert "1234" in out
        assert "567" in out
        assert "estimated cost" in out.lower()

    @patch("news_fetcher.Anthropic")
    def test_invalid_json_raises_value_error(self, mock_anthropic_cls):
        """Truncated or garbled JSON raises ValueError with context."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        bad = MagicMock()
        bad.content = [MagicMock(text='[{"title": "incomplete', type="text")]
        bad.usage = MagicMock(input_tokens=100, output_tokens=50)
        mock_client.messages.create.return_value = bad

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("news_fetcher.fetch_articles", return_value=sample_articles()):
                with pytest.raises(ValueError, match="invalid JSON"):
                    _get_stories_standard()

    @patch("news_fetcher.Anthropic")
    def test_markdown_fences_stripped(self, mock_anthropic_cls):
        """Handles Claude wrapping JSON in ```json ... ``` code fences."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        stories = sample_stories(n=5)
        fenced = f"```json\n{json.dumps(stories)}\n```"
        resp = MagicMock()
        resp.content = [MagicMock(text=fenced, type="text")]
        resp.usage = MagicMock(input_tokens=100, output_tokens=200)
        mock_client.messages.create.return_value = resp

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("news_fetcher.fetch_articles", return_value=sample_articles()):
                result = _get_stories_standard()

        assert len(result) == 5
        assert result[0]["title"] == "Story 0"

    @patch("news_fetcher.Anthropic")
    def test_preamble_before_fenced_json(self, mock_anthropic_cls):
        """Handles Claude adding a text preamble before the JSON code fence."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        stories = sample_stories(n=5)
        with_preamble = f"Here are the top stories:\n\n```json\n{json.dumps(stories)}\n```"
        resp = MagicMock()
        resp.content = [MagicMock(text=with_preamble, type="text")]
        resp.usage = MagicMock(input_tokens=100, output_tokens=200)
        mock_client.messages.create.return_value = resp

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("news_fetcher.fetch_articles", return_value=sample_articles()):
                result = _get_stories_standard()

        assert len(result) == 5
        assert result[0]["title"] == "Story 0"


# ---------------------------------------------------------------------------
# Premium tier: API call parameters and output guardrails
# ---------------------------------------------------------------------------

class TestPremiumTier:
    @patch("news_fetcher.Anthropic")
    def test_uses_sonnet_model(self, mock_anthropic_cls):
        """Premium tier must use claude-sonnet-4-6."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = make_premium_response(sample_stories())

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            _get_stories_premium()

        model = mock_client.messages.create.call_args.kwargs["model"]
        assert model == "claude-sonnet-4-6", (
            f"Expected claude-sonnet-4-6, got {model!r}."
        )

    @patch("news_fetcher.Anthropic")
    def test_max_tokens_enforced(self, mock_anthropic_cls):
        """Premium tier max_tokens must be <= PREMIUM_MAX_TOKENS (1200)."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = make_premium_response(sample_stories())

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            _get_stories_premium()

        max_tokens = mock_client.messages.create.call_args.kwargs["max_tokens"]
        assert max_tokens <= PREMIUM_MAX_TOKENS, (
            f"max_tokens={max_tokens} exceeds PREMIUM_MAX_TOKENS={PREMIUM_MAX_TOKENS}."
        )

    @patch("news_fetcher.Anthropic")
    def test_web_search_tool_present(self, mock_anthropic_cls):
        """Premium tier must pass the web_search tool."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = make_premium_response(sample_stories())

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            _get_stories_premium()

        tools = mock_client.messages.create.call_args.kwargs.get("tools", [])
        tool_types = [t.get("type") for t in tools]
        assert "web_search_20250305" in tool_types, (
            "Premium tier did not pass web_search_20250305 tool."
        )

    @patch("news_fetcher.Anthropic")
    def test_web_search_max_uses_enforced(self, mock_anthropic_cls):
        """Premium tier max_uses must be <= PREMIUM_MAX_USES (1) to cap search costs."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = make_premium_response(sample_stories())

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            _get_stories_premium()

        tools = mock_client.messages.create.call_args.kwargs.get("tools", [])
        web_search_tool = next(t for t in tools if t.get("type") == "web_search_20250305")
        assert web_search_tool.get("max_uses", 1) <= PREMIUM_MAX_USES, (
            f"web_search max_uses exceeds PREMIUM_MAX_USES={PREMIUM_MAX_USES}. "
            "Each search costs $0.01; this caps the monthly search spend."
        )

    @patch("news_fetcher.Anthropic")
    def test_extracts_text_blocks_only(self, mock_anthropic_cls):
        """Premium tier skips server_tool_use blocks and only reads text blocks."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = make_premium_response(sample_stories(n=5))

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            result = _get_stories_premium()

        assert len(result) == 5
        assert result[0]["title"] == "Story 0"

    @patch("news_fetcher.Anthropic")
    def test_output_capped_at_5(self, mock_anthropic_cls):
        """Premium tier returns at most 5 stories even if Claude returns more."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = make_premium_response(sample_stories(n=10))

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            result = _get_stories_premium()

        assert len(result) == 5

    @patch("news_fetcher.Anthropic")
    def test_token_usage_logged_with_search_cost(self, mock_anthropic_cls, capsys):
        """Premium tier logs token usage and mentions web search cost."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = make_premium_response(
            sample_stories(), input_tokens=3000, output_tokens=700
        )

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            _get_stories_premium()

        out = capsys.readouterr().out
        assert "3000" in out
        assert "700" in out
        assert "web search" in out.lower()


# ---------------------------------------------------------------------------
# Tier dispatch via TIER env var
# ---------------------------------------------------------------------------

class TestTierDispatch:
    @patch("news_fetcher._get_stories_standard")
    def test_default_tier_is_standard(self, mock_standard):
        mock_standard.return_value = sample_stories()
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}, clear=False):
            os_env = {k: v for k, v in __import__("os").environ.items() if k != "TIER"}
            with patch.dict("os.environ", os_env, clear=True):
                news_fetcher.get_top_positive_stories()
        mock_standard.assert_called_once()

    @patch("news_fetcher._get_stories_standard")
    def test_tier_standard_explicit(self, mock_standard):
        mock_standard.return_value = sample_stories()
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key", "TIER": "standard"}):
            news_fetcher.get_top_positive_stories()
        mock_standard.assert_called_once()

    @patch("news_fetcher._get_stories_premium")
    def test_tier_premium(self, mock_premium):
        mock_premium.return_value = sample_stories()
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key", "TIER": "premium"}):
            news_fetcher.get_top_positive_stories()
        mock_premium.assert_called_once()
