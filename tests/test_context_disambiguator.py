"""Tests for ContextDisambiguator — LLM-based ambiguous ticker validation."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ContextDisambiguator import (
  AMBIGUOUS_TICKERS,
  ContextDisambiguator,
)


# ── Fixtures ─────────────────────────────────────────────────────

@pytest.fixture
def disambiguator():
  return ContextDisambiguator()


# ── AMBIGUOUS_TICKERS set tests ──────────────────────────────────

class TestAmbiguousTickers:
  """Tests for the AMBIGUOUS_TICKERS constant."""

  def test_ai_is_ambiguous(self):
    assert "AI" in AMBIGUOUS_TICKERS

  def test_it_is_ambiguous(self):
    assert "IT" in AMBIGUOUS_TICKERS

  def test_on_is_ambiguous(self):
    assert "ON" in AMBIGUOUS_TICKERS

  def test_all_is_ambiguous(self):
    assert "ALL" in AMBIGUOUS_TICKERS

  def test_go_is_ambiguous(self):
    assert "GO" in AMBIGUOUS_TICKERS

  def test_real_is_ambiguous(self):
    assert "REAL" in AMBIGUOUS_TICKERS

  def test_a_is_ambiguous(self):
    assert "A" in AMBIGUOUS_TICKERS

  def test_nvda_not_ambiguous(self):
    assert "NVDA" not in AMBIGUOUS_TICKERS

  def test_aapl_not_ambiguous(self):
    assert "AAPL" not in AMBIGUOUS_TICKERS

  def test_tsla_not_ambiguous(self):
    assert "TSLA" not in AMBIGUOUS_TICKERS

  def test_intc_not_ambiguous(self):
    assert "INTC" not in AMBIGUOUS_TICKERS

  def test_crm_not_ambiguous(self):
    assert "CRM" not in AMBIGUOUS_TICKERS

  def test_all_entries_uppercase(self):
    for t in AMBIGUOUS_TICKERS:
      assert t == t.upper(), f"{t} should be uppercase"

  def test_set_is_not_empty(self):
    assert len(AMBIGUOUS_TICKERS) > 20


# ── has_ambiguous() tests ────────────────────────────────────────

class TestHasAmbiguous:
  """Tests for ContextDisambiguator.has_ambiguous()."""

  def test_detects_ai(self, disambiguator):
    assert disambiguator.has_ambiguous(["AI", "NVDA"]) is True

  def test_detects_it(self, disambiguator):
    assert disambiguator.has_ambiguous(["IT"]) is True

  def test_no_ambiguous(self, disambiguator):
    assert disambiguator.has_ambiguous(["NVDA", "AAPL", "TSLA"]) is False

  def test_empty_list(self, disambiguator):
    assert disambiguator.has_ambiguous([]) is False

  def test_single_ambiguous(self, disambiguator):
    assert disambiguator.has_ambiguous(["ON"]) is True

  def test_mixed_case(self, disambiguator):
    # Tickers should be compared uppercase
    assert disambiguator.has_ambiguous(["ai"]) is True


# ── disambiguate() tests ─────────────────────────────────────────

class TestDisambiguate:
  """Tests for ContextDisambiguator.disambiguate() with mocked LLM."""

  @pytest.mark.asyncio
  async def test_no_tickers_returns_empty(self, disambiguator):
    result = await disambiguator.disambiguate([], "some text")
    assert result == []

  @pytest.mark.asyncio
  async def test_no_ambiguous_skips_llm(self, disambiguator):
    """Non-ambiguous tickers should pass through without LLM call."""
    with patch.object(disambiguator, "_llm_validate") as mock_llm:
      result = await disambiguator.disambiguate(
        ["NVDA", "AAPL", "TSLA"],
        "NVDA is up 10% and AAPL reported earnings",
      )
      mock_llm.assert_not_called()
      assert set(result) == {"NVDA", "AAPL", "TSLA"}

  @pytest.mark.asyncio
  async def test_ai_rejected_as_common_word(self, disambiguator):
    """AI used as 'artificial intelligence' should be rejected."""
    with patch.object(disambiguator, "_llm_validate", new_callable=AsyncMock) as mock_llm:
      # LLM says AI is NOT a stock reference
      mock_llm.return_value = []
      result = await disambiguator.disambiguate(
        ["NVDA", "AI"],
        "AI is transforming the healthcare industry. NVDA makes GPUs.",
      )
      assert "NVDA" in result
      assert "AI" not in result

  @pytest.mark.asyncio
  async def test_ai_accepted_as_stock(self, disambiguator):
    """AI used as stock reference should be accepted."""
    with patch.object(disambiguator, "_llm_validate", new_callable=AsyncMock) as mock_llm:
      mock_llm.return_value = ["AI"]
      result = await disambiguator.disambiguate(
        ["NVDA", "AI"],
        "$AI reported strong earnings. C3.ai stock is up 15%.",
      )
      assert "NVDA" in result
      assert "AI" in result

  @pytest.mark.asyncio
  async def test_mixed_ambiguous(self, disambiguator):
    """Multiple ambiguous tickers: some confirmed, some rejected."""
    with patch.object(disambiguator, "_llm_validate", new_callable=AsyncMock) as mock_llm:
      # IT is actually Gartner stock, AI is just artificial intelligence
      mock_llm.return_value = ["IT"]
      result = await disambiguator.disambiguate(
        ["NVDA", "AI", "IT"],
        "Gartner (IT) stock beat estimates. AI is the future of tech.",
      )
      assert "NVDA" in result
      assert "IT" in result
      assert "AI" not in result

  @pytest.mark.asyncio
  async def test_llm_failure_rejects_all_ambiguous(self, disambiguator):
    """On LLM failure, all ambiguous tickers should be rejected (safe default)."""
    with patch.object(disambiguator, "_llm_validate", new_callable=AsyncMock) as mock_llm:
      mock_llm.side_effect = Exception("LLM timeout")
      result = await disambiguator.disambiguate(
        ["NVDA", "AI", "ON"],
        "Some context about stocks",
      )
      # Non-ambiguous should still be present
      assert "NVDA" in result
      # Ambiguous should be rejected on failure
      assert "AI" not in result
      assert "ON" not in result

  @pytest.mark.asyncio
  async def test_all_ambiguous_rejected(self, disambiguator):
    """If all tickers are ambiguous and LLM rejects them all."""
    with patch.object(disambiguator, "_llm_validate", new_callable=AsyncMock) as mock_llm:
      mock_llm.return_value = []
      result = await disambiguator.disambiguate(
        ["AI", "IT", "ON"],
        "AI and IT solutions are on the rise",
      )
      assert result == []

  @pytest.mark.asyncio
  async def test_all_ambiguous_confirmed(self, disambiguator):
    """If all tickers are ambiguous and LLM confirms them all."""
    with patch.object(disambiguator, "_llm_validate", new_callable=AsyncMock) as mock_llm:
      mock_llm.return_value = ["AI", "IT", "ON"]
      result = await disambiguator.disambiguate(
        ["AI", "IT", "ON"],
        "$AI C3.ai stock, $IT Gartner stock, $ON semiconductor",
      )
      assert set(result) == {"AI", "IT", "ON"}


# ── _llm_validate() tests ───────────────────────────────────────

class TestLlmValidate:
  """Tests for the LLM validation call."""

  @pytest.mark.asyncio
  async def test_llm_returns_correct_json(self, disambiguator):
    """LLM returns proper JSON mapping."""
    mock_response = json.dumps({"AI": False, "IT": True})
    with patch.object(disambiguator.llm, "chat", new_callable=AsyncMock) as mock_chat:
      mock_chat.return_value = mock_response
      result = await disambiguator._llm_validate(
        ["AI", "IT"],
        "Gartner (IT) beat earnings, AI is the future of tech",
      )
      assert "IT" in result
      assert "AI" not in result

  @pytest.mark.asyncio
  async def test_llm_returns_non_dict(self, disambiguator):
    """LLM returns a list instead of dict — should reject all."""
    with patch.object(disambiguator.llm, "chat", new_callable=AsyncMock) as mock_chat:
      mock_chat.return_value = '["AI"]'
      result = await disambiguator._llm_validate(
        ["AI", "IT"],
        "some text",
      )
      assert result == []

  @pytest.mark.asyncio
  async def test_llm_returns_garbage(self, disambiguator):
    """LLM returns unparseable response — should reject all."""
    with patch.object(disambiguator.llm, "chat", new_callable=AsyncMock) as mock_chat:
      mock_chat.return_value = "not valid json at all"
      result = await disambiguator._llm_validate(
        ["AI"],
        "some text",
      )
      assert result == []

  @pytest.mark.asyncio
  async def test_llm_exception(self, disambiguator):
    """LLM call raises exception — should reject all."""
    with patch.object(disambiguator.llm, "chat", new_callable=AsyncMock) as mock_chat:
      mock_chat.side_effect = TimeoutError("connection timed out")
      result = await disambiguator._llm_validate(
        ["AI", "IT"],
        "some text",
      )
      assert result == []

  @pytest.mark.asyncio
  async def test_low_temperature_used(self, disambiguator):
    """Verify low temperature is passed for deterministic judgement."""
    mock_response = json.dumps({"AI": False})
    with patch.object(disambiguator.llm, "chat", new_callable=AsyncMock) as mock_chat:
      mock_chat.return_value = mock_response
      await disambiguator._llm_validate(["AI"], "AI is the future")
      call_kwargs = mock_chat.call_args
      assert call_kwargs.kwargs.get("temperature") == 0.1

  @pytest.mark.asyncio
  async def test_missing_ticker_in_response_is_rejected(self, disambiguator):
    """Tickers omitted from LLM JSON response should be rejected (default=False)."""
    # LLM only returns verdict for AI, omits IT entirely
    mock_response = json.dumps({"AI": True})
    with patch.object(disambiguator.llm, "chat", new_callable=AsyncMock) as mock_chat:
      mock_chat.return_value = mock_response
      result = await disambiguator._llm_validate(
        ["AI", "IT"],
        "C3.ai (AI) stock is up. IT infrastructure is growing.",
      )
      # AI explicitly confirmed → accepted
      assert "AI" in result
      # IT not in response → should default to rejected
      assert "IT" not in result
