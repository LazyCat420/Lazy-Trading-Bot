"""Tests for the research tools module.

Tests each tool function with mocked DuckDB queries.
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock, patch

# All research tools are standalone async functions
from app.services.research_tools import (
  RESEARCH_TOOL_DESCRIPTIONS,
  RESEARCH_TOOL_NAMES,
  SEARCH_TOOL_DESCRIPTION,
  TOOL_CATEGORIES,
  TOOL_DETAIL_DESCRIPTIONS,
  TOOL_REGISTRY,
  check_insider_activity,
  compare_financials,
  fetch_sec_filings,
  get_earnings_calendar,
  get_price_history,
  get_technicals_detail,
  save_finding,
  search_news,
  search_reddit_sentiment,
  search_tools,
)


# ------------------------------------------------------------------
# Registry integrity
# ------------------------------------------------------------------

def test_tool_registry_contains_all_tools():
  """All 11 tools (8 research + 3 meta) are registered."""
  expected = {
    "fetch_sec_filings",
    "search_news",
    "get_technicals_detail",
    "check_insider_activity",
    "compare_financials",
    "get_price_history",
    "search_reddit_sentiment",
    "get_earnings_calendar",
    # Meta-tools
    "search_tools",
    "save_finding",
    "recall_findings",
  }
  assert set(TOOL_REGISTRY.keys()) == expected


def test_tool_names_matches_registry():
  """RESEARCH_TOOL_NAMES list matches registry keys."""
  assert set(RESEARCH_TOOL_NAMES) == set(TOOL_REGISTRY.keys())


def test_tool_descriptions_mentions_all_tools():
  """Every tool in the registry is mentioned in the LLM descriptions."""
  for tool_name in RESEARCH_TOOL_NAMES:
    assert tool_name in RESEARCH_TOOL_DESCRIPTIONS, (
      f"Tool {tool_name} not found in RESEARCH_TOOL_DESCRIPTIONS"
    )


def test_all_tools_are_async():
  """Every tool function is a coroutine function."""
  import asyncio
  for name, func in TOOL_REGISTRY.items():
    assert asyncio.iscoroutinefunction(func), (
      f"Tool {name} is not async"
    )


# ------------------------------------------------------------------
# Individual tool tests (with mocked DB)
# ------------------------------------------------------------------

@pytest.fixture
def mock_db():
  """Return a mock DuckDB connection."""
  db = MagicMock()
  db.execute.return_value.fetchall.return_value = []
  db.execute.return_value.fetchone.return_value = None
  with patch("app.services.research_tools.get_db", return_value=db):
    yield db


@pytest.mark.asyncio
async def test_fetch_sec_filings_missing_ticker():
  """Returns error when ticker is missing."""
  result = await fetch_sec_filings({})
  assert "error" in result


@pytest.mark.asyncio
async def test_fetch_sec_filings_no_data(mock_db):
  """Returns empty holdings when no 13F data exists."""
  result = await fetch_sec_filings({"ticker": "ZZZZ"})
  assert result["ticker"] == "ZZZZ"
  assert result["holdings"] == []


@pytest.mark.asyncio
async def test_search_news_missing_params():
  """Returns error when neither ticker nor query is provided."""
  result = await search_news({})
  assert "error" in result


@pytest.mark.asyncio
async def test_search_news_no_results(mock_db):
  """Returns empty articles when nothing matches."""
  result = await search_news({"ticker": "ZZZZ"})
  assert result["articles_count"] == 0


@pytest.mark.asyncio
async def test_get_technicals_detail_missing_ticker():
  """Returns error when ticker is missing."""
  result = await get_technicals_detail({})
  assert "error" in result


@pytest.mark.asyncio
async def test_get_technicals_detail_no_data(mock_db):
  """Returns error when no technical data exists."""
  result = await get_technicals_detail({"ticker": "ZZZZ"})
  assert "error" in result


@pytest.mark.asyncio
async def test_check_insider_activity_missing_ticker():
  """Returns error when ticker is missing."""
  result = await check_insider_activity({})
  assert "error" in result


@pytest.mark.asyncio
async def test_check_insider_activity_no_data(mock_db):
  """Returns empty insider data when nothing exists."""
  result = await check_insider_activity({"ticker": "ZZZZ"})
  assert result["ticker"] == "ZZZZ"
  assert result["congress_count"] == 0


@pytest.mark.asyncio
async def test_compare_financials_too_few_tickers():
  """Returns error when fewer than 2 tickers provided."""
  result = await compare_financials({"tickers": ["AAPL"]})
  assert "error" in result


@pytest.mark.asyncio
async def test_compare_financials_no_data(mock_db):
  """Returns error entries when no fundamental data exists."""
  result = await compare_financials({"tickers": ["AAPL", "GOOGL"]})
  assert result["comparison_count"] == 2
  for t in result["tickers"]:
    assert "error" in t


@pytest.mark.asyncio
async def test_get_price_history_missing_ticker():
  """Returns error when ticker is missing."""
  result = await get_price_history({})
  assert "error" in result


@pytest.mark.asyncio
async def test_get_price_history_no_data(mock_db):
  """Returns error when no price data exists."""
  result = await get_price_history({"ticker": "ZZZZ"})
  assert "error" in result


@pytest.mark.asyncio
async def test_search_reddit_sentiment_missing_ticker():
  """Returns error when ticker is missing."""
  result = await search_reddit_sentiment({})
  assert "error" in result


@pytest.mark.asyncio
async def test_search_reddit_sentiment_no_data(mock_db):
  """Returns empty mentions when nothing found."""
  result = await search_reddit_sentiment({"ticker": "ZZZZ"})
  assert result["ticker"] == "ZZZZ"
  assert result["reddit_mention_count"] == 0


@pytest.mark.asyncio
async def test_get_earnings_calendar_missing_ticker():
  """Returns error when ticker is missing."""
  result = await get_earnings_calendar({})
  assert "error" in result


@pytest.mark.asyncio
async def test_get_earnings_calendar_no_data(mock_db):
  """Returns note about missing data when no calendar exists."""
  result = await get_earnings_calendar({"ticker": "ZZZZ"})
  assert result["ticker"] == "ZZZZ"
  assert "note" in result.get("earnings", {}) or "error" not in result.get("earnings", {})


# ------------------------------------------------------------------
# TradingAgent integration check
# ------------------------------------------------------------------

def test_trading_agent_imports_research_tools():
  """TradingAgent uses research tools from the registry."""
  from app.services.trading_agent import RESEARCH_TOOL_NAMES as agent_names
  assert set(agent_names) == set(RESEARCH_TOOL_NAMES)


# ------------------------------------------------------------------
# PortfolioStrategist integration check
# ------------------------------------------------------------------

def test_strategist_schema_includes_research_tools():
  """PortfolioStrategist ACTION_SCHEMA includes research tool names."""
  from app.services.portfolio_strategist import ACTION_SCHEMA
  enum_values = ACTION_SCHEMA["properties"]["action"]["enum"]
  for tool_name in RESEARCH_TOOL_NAMES:
    assert tool_name in enum_values, (
      f"Research tool {tool_name} not in ACTION_SCHEMA enum"
    )


def test_strategist_descriptions_include_research_tools():
  """PortfolioStrategist TOOL_DESCRIPTIONS mentions research tools."""
  from app.services.portfolio_strategist import TOOL_DESCRIPTIONS
  for tool_name in RESEARCH_TOOL_NAMES:
    assert tool_name in TOOL_DESCRIPTIONS, (
      f"Research tool {tool_name} not in strategist TOOL_DESCRIPTIONS"
    )


# ------------------------------------------------------------------
# search_tools meta-tool tests
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_tools_missing_query():
  """Returns error when query is missing."""
  result = await search_tools({})
  assert "error" in result
  assert "available_categories" in result


@pytest.mark.asyncio
async def test_search_tools_category_match():
  """Finds tools by exact category name."""
  result = await search_tools({"query": "technicals"})
  assert result["matched_count"] >= 2
  tool_names = [t["tool_name"] for t in result["tools"]]
  assert "get_technicals_detail" in tool_names
  assert "get_price_history" in tool_names


@pytest.mark.asyncio
async def test_search_tools_fuzzy_match():
  """Finds tools by partial keyword in description."""
  result = await search_tools({"query": "insider"})
  tool_names = [t["tool_name"] for t in result["tools"]]
  assert "check_insider_activity" in tool_names


@pytest.mark.asyncio
async def test_search_tools_no_match():
  """Returns empty list for unknown query."""
  result = await search_tools({"query": "xyznonexistent"})
  assert result["matched_tools"] == []


# ------------------------------------------------------------------
# Memory tool tests
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_save_finding_missing_note():
  """Returns error when note is missing."""
  result = await save_finding({})
  assert "error" in result


@pytest.mark.asyncio
async def test_save_finding_success():
  """Saves a finding successfully."""
  result = await save_finding({"note": "RSI=28, oversold"})
  assert result["status"] == "saved"
  assert result["note"] == "RSI=28, oversold"


def test_search_tool_description_is_compact():
  """Compact description is much shorter than full descriptions."""
  assert len(SEARCH_TOOL_DESCRIPTION) < len(RESEARCH_TOOL_DESCRIPTIONS)
  assert len(SEARCH_TOOL_DESCRIPTION) < len(RESEARCH_TOOL_DESCRIPTIONS) / 2


def test_tool_categories_index_is_valid():
  """All tool names in categories exist in TOOL_DETAIL_DESCRIPTIONS."""
  for cat, tools in TOOL_CATEGORIES.items():
    for tool in tools:
      assert tool in TOOL_DETAIL_DESCRIPTIONS, (
        f"Tool {tool} in category {cat} not in TOOL_DETAIL_DESCRIPTIONS"
      )
