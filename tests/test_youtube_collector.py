"""Unit tests for the YouTube transcript collector.

Tests VTT parsing, two-tier transcript extraction strategy,
and the 24h filter.
"""

from __future__ import annotations

from unittest.mock import patch

from app.collectors.youtube_collector import YouTubeCollector


class TestVTTParsing:
    """Tests for the VTT subtitle parser."""

    def test_basic_vtt(self) -> None:
        vtt = """WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:03.000
Hello everyone welcome to the show

00:00:03.000 --> 00:00:06.000
Today we are talking about NVDA
"""
        result = YouTubeCollector._parse_vtt(vtt)
        assert "Hello everyone welcome to the show" in result
        assert "Today we are talking about NVDA" in result

    def test_dedup_overlapping_segments(self) -> None:
        """YouTube auto-captions often repeat text in overlapping segments."""
        vtt = """WEBVTT

00:00:00.000 --> 00:00:03.000
Hello world

00:00:01.000 --> 00:00:04.000
Hello world

00:00:03.000 --> 00:00:06.000
This is a test
"""
        result = YouTubeCollector._parse_vtt(vtt)
        # "Hello world" should appear only once
        assert result.count("Hello world") == 1
        assert "This is a test" in result

    def test_strips_html_tags(self) -> None:
        vtt = """WEBVTT

00:00:00.000 --> 00:00:03.000
<c>Hello</c> <00:00:01.500>world</c>
"""
        result = YouTubeCollector._parse_vtt(vtt)
        assert "<c>" not in result
        assert "Hello" in result
        assert "world" in result

    def test_empty_vtt(self) -> None:
        result = YouTubeCollector._parse_vtt("WEBVTT\n\n")
        assert result == ""

    def test_skips_metadata_lines(self) -> None:
        vtt = """WEBVTT
Kind: captions
Language: en
NOTE This is a comment

00:00:00.000 --> 00:00:03.000
Actual content here
"""
        result = YouTubeCollector._parse_vtt(vtt)
        assert "WEBVTT" not in result
        assert "Kind:" not in result
        assert "Language:" not in result
        assert "NOTE" not in result
        assert "Actual content here" in result


class TestNoTruncation:
    """Verify transcripts are stored in full — no truncation."""

    def test_full_transcript_preserved(self) -> None:
        collector = YouTubeCollector()
        long_text = "Analysis content. " * 5000  # ~90K chars
        with patch.object(
            collector, "_get_transcript_library", return_value=long_text
        ):
            result = collector._get_transcript("test_long")
            assert result == long_text
            assert "truncated" not in result

    def test_short_transcript_not_modified(self) -> None:
        collector = YouTubeCollector()
        with patch.object(
            collector, "_get_transcript_library", return_value="Short text here for testing only"
        ):
            result = collector._get_transcript("test_short")
            assert result == "Short text here for testing only"


class TestSearchQueries:
    """Test the multi-query search strategy."""

    def test_has_multiple_queries(self) -> None:
        collector = YouTubeCollector()
        assert len(collector.SEARCH_QUERIES) >= 3

    def test_queries_use_ticker_placeholder(self) -> None:
        for q in YouTubeCollector.SEARCH_QUERIES:
            assert "{ticker}" in q
            formatted = q.format(ticker="NVDA")
            assert "NVDA" in formatted
            assert "{ticker}" not in formatted

    def test_curated_channels_list(self) -> None:
        collector = YouTubeCollector()
        assert len(collector.CURATED_CHANNELS) >= 10
        assert "CNBC" in collector.CURATED_CHANNELS


class TestGetTranscriptTiering:
    """Test the two-tier transcript strategy."""

    def test_library_first_succeeds(self) -> None:
        """If library succeeds, yt-dlp should not be called."""
        collector = YouTubeCollector()
        with (
            patch.object(
                collector, "_get_transcript_library",
                return_value="Library transcript text here that is long enough to pass",
            ) as lib_mock,
            patch.object(
                collector, "_get_transcript_ytdlp",
            ) as ytdlp_mock,
        ):
            result = collector._get_transcript("test123")
            assert result == "Library transcript text here that is long enough to pass"
            lib_mock.assert_called_once_with("test123")
            ytdlp_mock.assert_not_called()

    def test_fallback_to_ytdlp(self) -> None:
        """If library fails, yt-dlp should be tried."""
        collector = YouTubeCollector()
        with (
            patch.object(
                collector, "_get_transcript_library", return_value="",
            ) as lib_mock,
            patch.object(
                collector, "_get_transcript_ytdlp",
                return_value="yt-dlp got the transcript",
            ) as ytdlp_mock,
        ):
            result = collector._get_transcript("test456")
            assert result == "yt-dlp got the transcript"
            lib_mock.assert_called_once()
            ytdlp_mock.assert_called_once_with("test456")

    def test_both_fail_returns_empty(self) -> None:
        """If both tiers fail, return empty string."""
        collector = YouTubeCollector()
        with (
            patch.object(collector, "_get_transcript_library", return_value=""),
            patch.object(collector, "_get_transcript_ytdlp", return_value=""),
        ):
            result = collector._get_transcript("test789")
            assert result == ""
