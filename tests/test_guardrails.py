"""
Guardrail tests for news_fetcher.py.

These tests do NOT call the real Claude API or real RSS feeds — everything is mocked.
They verify that the budget constants are respected and that edge cases are handled safely.

Run with:
    pip install pytest anthropic feedparser
    pytest tests/
"""

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# Add lambda/ to path so we can import the module directly
sys.path.insert(0, str(Path(__file__).parent.parent / "lambda"))

import news_fetcher
from news_fetcher import (
    MAX_ARTICLES_PER_FEED,
    MAX_PROMPT_CHARS,
    MAX_SUMMARY_CHARS,
    MAX_TOTAL_ARTICLES,
    fetch_articles,
    summarize_with_claude,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_feed_entry(title="Headline", summary="Some summary text.", link="https://example.com/story"):
    """Return a minimal feedparser-style entry object."""
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


def make_claude_response(stories: list[dict], input_tokens=400, output_tokens=300):
    """Return a mock Anthropic API response containing the given stories as JSON."""
    response = MagicMock()
    response.content = [MagicMock(text=json.dumps(stories))]
    response.usage = MagicMock(input_tokens=input_tokens, output_tokens=output_tokens)
    return response


# ---------------------------------------------------------------------------
# Canary: budget constants must not be raised above safe thresholds
# ---------------------------------------------------------------------------

class TestBudgetConstants:
    """
    These tests will fail if someone raises the guardrail constants beyond safe limits.
    They act as a tripwire — any change that increases token spend must also update
    the thresholds here intentionally.
    """

    def test_max_articles_per_feed_within_limit(self):
        assert MAX_ARTICLES_PER_FEED <= 3, (
            f"MAX_ARTICLES_PER_FEED={MAX_ARTICLES_PER_FEED} exceeds safe limit of 3. "
            "Raising this increases token input. Update the threshold here if intentional."
        )

    def test_max_total_articles_within_limit(self):
        assert MAX_TOTAL_ARTICLES <= 15, (
            f"MAX_TOTAL_ARTICLES={MAX_TOTAL_ARTICLES} exceeds safe limit of 15. "
            "Update this test if you intentionally want to raise it."
        )

    def test_max_summary_chars_within_limit(self):
        assert MAX_SUMMARY_CHARS <= 250, (
            f"MAX_SUMMARY_CHARS={MAX_SUMMARY_CHARS} exceeds safe limit of 250. "
            "Each extra 4 chars ≈ 1 extra token sent to Claude."
        )

    def test_max_prompt_chars_within_limit(self):
        assert MAX_PROMPT_CHARS <= 6000, (
            f"MAX_PROMPT_CHARS={MAX_PROMPT_CHARS} exceeds safe limit of 6000 chars (~1500 tokens)."
        )

    def test_worst_case_articles_text_fits_prompt_budget(self):
        """
        Simulate worst-case article sizes and confirm the drop-loop keeps us under budget.
        A title of 100 chars + summary of 250 chars + URL of 100 chars + formatting = ~480 chars.
        15 such articles = 7,200 chars — over budget. The drop-loop must bring it down.
        """
        long_title = "A" * 100
        long_summary = "B" * MAX_SUMMARY_CHARS
        long_url = "https://example.com/" + "c" * 80
        articles = [
            {"title": long_title, "summary": long_summary, "link": long_url}
            for _ in range(MAX_TOTAL_ARTICLES)
        ]

        # Simulate the drop-loop logic from summarize_with_claude
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
            f"Even after dropping articles, prompt is {len(text)} chars > {MAX_PROMPT_CHARS}. "
            "Reduce MAX_SUMMARY_CHARS or MAX_PROMPT_CHARS."
        )
        assert len(arts) >= 1, "All articles were dropped — something is very wrong."


# ---------------------------------------------------------------------------
# fetch_articles: RSS parsing and per-feed caps
# ---------------------------------------------------------------------------

class TestFetchArticles:
    def _make_feed_with_n_entries(self, n, title_prefix="Story"):
        return make_feed([
            make_feed_entry(title=f"{title_prefix} {i}", summary="x" * 300, link=f"https://ex.com/{i}")
            for i in range(n)
        ])

    @patch("news_fetcher.feedparser")
    def test_per_feed_cap_respected(self, mock_feedparser):
        """Never takes more than MAX_ARTICLES_PER_FEED entries from a single feed."""
        big_feed = self._make_feed_with_n_entries(20)
        mock_feedparser.parse.return_value = big_feed

        with patch.object(news_fetcher, "RSS_FEEDS", ["https://feed1.example.com/"]):
            articles = fetch_articles()

        assert len(articles) == MAX_ARTICLES_PER_FEED

    @patch("news_fetcher.feedparser")
    def test_total_article_cap_respected(self, mock_feedparser):
        """Total articles never exceeds MAX_TOTAL_ARTICLES even with many feeds."""
        big_feed = self._make_feed_with_n_entries(MAX_ARTICLES_PER_FEED)
        mock_feedparser.parse.return_value = big_feed

        # Use enough fake feeds to exceed MAX_TOTAL_ARTICLES
        many_feeds = [f"https://feed{i}.example.com/" for i in range(20)]
        with patch.object(news_fetcher, "RSS_FEEDS", many_feeds):
            articles = fetch_articles()

        assert len(articles) <= MAX_TOTAL_ARTICLES

    @patch("news_fetcher.feedparser")
    def test_summary_truncated_to_max_chars(self, mock_feedparser):
        """Summaries are truncated to MAX_SUMMARY_CHARS before being stored."""
        long_summary = "X" * 1000
        feed = make_feed([make_feed_entry(summary=long_summary)])
        mock_feedparser.parse.return_value = feed

        with patch.object(news_fetcher, "RSS_FEEDS", ["https://feed.example.com/"]):
            articles = fetch_articles()

        assert len(articles) == 1
        assert len(articles[0]["summary"]) <= MAX_SUMMARY_CHARS

    @patch("news_fetcher.feedparser")
    def test_html_stripped_from_summary(self, mock_feedparser):
        """HTML tags are removed from summaries."""
        feed = make_feed([make_feed_entry(summary="<p>Hello <b>world</b></p>")])
        mock_feedparser.parse.return_value = feed

        with patch.object(news_fetcher, "RSS_FEEDS", ["https://feed.example.com/"]):
            articles = fetch_articles()

        assert "<" not in articles[0]["summary"]
        assert "Hello" in articles[0]["summary"]
        assert "world" in articles[0]["summary"]

    @patch("news_fetcher.feedparser")
    def test_failed_feed_is_skipped(self, mock_feedparser):
        """A feed that raises an exception is silently skipped; others still work."""
        good_feed = make_feed([make_feed_entry(title="Good story")])
        mock_feedparser.parse.side_effect = [Exception("timeout"), good_feed]

        with patch.object(news_fetcher, "RSS_FEEDS", ["https://bad.example.com/", "https://good.example.com/"]):
            articles = fetch_articles()

        assert len(articles) == 1
        assert articles[0]["title"] == "Good story"


# ---------------------------------------------------------------------------
# summarize_with_claude: API call parameters and output guardrails
# ---------------------------------------------------------------------------

class TestSummarizeWithClaude:
    def _sample_stories(self, n=5):
        return [
            {"title": f"Story {i}", "summary": f"Summary {i}.", "link": f"https://ex.com/{i}"}
            for i in range(n)
        ]

    def _sample_articles(self, n=10):
        return [
            {"title": f"Article {i}", "summary": "Short summary.", "link": f"https://ex.com/{i}"}
            for i in range(n)
        ]

    @patch("news_fetcher.Anthropic")
    def test_model_is_haiku(self, mock_anthropic_cls):
        """Always uses claude-haiku, never a more expensive model."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = make_claude_response(self._sample_stories())

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            summarize_with_claude(self._sample_articles())

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["model"] == "claude-haiku-4-5-20251001", (
            f"Expected claude-haiku-4-5-20251001, got {call_kwargs['model']}. "
            "Using a more expensive model would blow the cost estimate."
        )

    @patch("news_fetcher.Anthropic")
    def test_max_tokens_enforced(self, mock_anthropic_cls):
        """max_tokens must be 800 or less — never raised without updating this test."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = make_claude_response(self._sample_stories())

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            summarize_with_claude(self._sample_articles())

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["max_tokens"] <= 800, (
            f"max_tokens={call_kwargs['max_tokens']} exceeds the safe limit of 800. "
            "Each extra token costs money and can exceed what 5 JSON story objects need."
        )

    @patch("news_fetcher.Anthropic")
    def test_prompt_respects_char_budget(self, mock_anthropic_cls):
        """The prompt sent to Claude never exceeds MAX_PROMPT_CHARS in its article section."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = make_claude_response(self._sample_stories())

        # Generate articles that would exceed budget if not dropped
        fat_articles = [
            {"title": "T" * 100, "summary": "S" * MAX_SUMMARY_CHARS, "link": "https://ex.com/" + "l" * 80}
            for _ in range(MAX_TOTAL_ARTICLES)
        ]

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            summarize_with_claude(fat_articles)

        prompt_sent = mock_client.messages.create.call_args.kwargs["messages"][0]["content"]
        # The article section is everything between the instruction prefix and suffix
        assert len(prompt_sent) <= MAX_PROMPT_CHARS + 600, (
            # 600 chars of slack for the fixed instruction text surrounding articles_text
            f"Full prompt is {len(prompt_sent)} chars, which is unexpectedly large."
        )

    @patch("news_fetcher.Anthropic")
    def test_output_capped_at_5_stories(self, mock_anthropic_cls):
        """Even if Claude returns more than 5 stories, we only use 5."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = make_claude_response(self._sample_stories(n=10))

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            result = summarize_with_claude(self._sample_articles())

        assert len(result) == 5

    @patch("news_fetcher.Anthropic")
    def test_empty_articles_returns_empty(self, mock_anthropic_cls):
        """If no articles are passed in, Claude is never called and we return []."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            result = summarize_with_claude([])

        mock_client.messages.create.assert_not_called()
        assert result == []

    @patch("news_fetcher.Anthropic")
    def test_token_usage_is_logged(self, mock_anthropic_cls, capsys):
        """Token usage is printed to stdout so it appears in CloudWatch Logs."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = make_claude_response(
            self._sample_stories(), input_tokens=1234, output_tokens=567
        )

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            summarize_with_claude(self._sample_articles())

        output = capsys.readouterr().out
        assert "1234" in output, "input_tokens not logged"
        assert "567" in output, "output_tokens not logged"
        assert "estimated cost" in output.lower()

    @patch("news_fetcher.Anthropic")
    def test_invalid_json_raises_value_error(self, mock_anthropic_cls):
        """Truncated or garbled JSON from Claude raises ValueError with context."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        bad_response = MagicMock()
        bad_response.content = [MagicMock(text='[{"title": "incomplete')]
        bad_response.usage = MagicMock(input_tokens=100, output_tokens=50)
        mock_client.messages.create.return_value = bad_response

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with pytest.raises(ValueError, match="invalid JSON"):
                summarize_with_claude(self._sample_articles())

    @patch("news_fetcher.Anthropic")
    def test_markdown_code_fences_stripped(self, mock_anthropic_cls):
        """Handles Claude wrapping JSON in ```json ... ``` code fences."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        stories = self._sample_stories(n=5)
        fenced = f"```json\n{json.dumps(stories)}\n```"
        response = MagicMock()
        response.content = [MagicMock(text=fenced)]
        response.usage = MagicMock(input_tokens=100, output_tokens=200)
        mock_client.messages.create.return_value = response

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            result = summarize_with_claude(self._sample_articles())

        assert len(result) == 5
        assert result[0]["title"] == "Story 0"
