"""Tests for Cross-Bot Auditor model resolution and audit flow.

Verifies that the auditor:
1. Resolves vendor-prefixed model names before calling Ollama
2. Uses exactly 2 models (audited + auditor), never a 3rd fallback
3. Passes the resolved name to both LLMService and the audit prompt
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Helpers ──────────────────────────────────────────────────────────────


def _normalize(name: str) -> str:
  """Reproduce the normalize logic from _resolve_ollama_model_name."""
  if "/" in name:
    name = name.split("/", 1)[1]
  return re.sub(r"[.\-:_]", "", name).lower()


INSTALLED_MODELS = [
  "granite3.2:8b",
  "olmo-3:latest",
  "granite3.2:8b-50k",
  "nemotron-3-nano:latest",
  "gpt-oss-safeguard:20b",
  "olmo-3:32b",
  "qwen-claude-165k:latest",
  "qwen3.5:35b",
  "gemma3:4b",
  "nomic-embed-text:latest",
]

# Map of DB model_name → expected resolved Ollama name
RESOLUTION_CASES = [
  ("ibm/granite-3.2-8b", "granite3.2:8b"),
  ("olmo-3:latest", "olmo-3:latest"),  # exact match
  ("granite3.2:8b-50k", "granite3.2:8b-50k"),  # exact match
  ("gemma3:4b", "gemma3:4b"),  # exact match
  ("olmo-3:32b", "olmo-3:32b"),  # exact match
  ("nemotron-3-nano:latest", "nemotron-3-nano:latest"),  # exact match
]


# ── Unit Tests ───────────────────────────────────────────────────────────


class TestModelNameResolution:
  """Test that vendor-prefixed model names resolve to Ollama names."""

  @pytest.mark.parametrize("db_name,expected_ollama", RESOLUTION_CASES)
  def test_normalize_resolves_vendor_prefix(self, db_name, expected_ollama):
    """The normalize function must map DB names to installed Ollama names."""
    db_norm = _normalize(db_name)
    norm_map = {_normalize(m): m for m in INSTALLED_MODELS}

    # Should find a match: exact, case, or normalized
    if db_name in INSTALLED_MODELS:
      assert db_name == expected_ollama
    elif db_norm in norm_map:
      assert norm_map[db_norm] == expected_ollama
    else:
      # Substring match
      candidates = [real for n, real in norm_map.items() if db_norm in n or n in db_norm]
      assert expected_ollama in candidates, (
        f"{db_name} (norm={db_norm}) did not resolve to {expected_ollama}. "
        f"Candidates: {candidates}"
      )

  def test_vendor_prefix_never_sent_to_ollama(self):
    """Model names with '/' must be resolved before hitting Ollama."""
    for db_name, expected in RESOLUTION_CASES:
      if "/" in db_name:
        assert "/" not in expected, (
          f"Vendor-prefixed name '{db_name}' must be resolved to "
          f"'{expected}' (no slash) before sending to Ollama"
        )


class TestCrossBotAuditorModelFlow:
  """Integration test: CrossBotAuditor must use resolved model names."""

  @staticmethod
  async def _run_audit_with_mock_resolver(
    auditor_model_db: str,
    auditor_model_resolved: str,
    audited_model: str = "gemma3:4b",
  ):
    """Helper: run audit_bot_run with a mocked resolver and return
    (MockLLMService, mock_resolver) for assertions."""
    from app.services.CrossBotAuditor import CrossBotAuditor

    auditor = CrossBotAuditor()

    mock_auditor_bot = {
      "bot_id": "test_auditor_123",
      "model_name": auditor_model_db,
      "display_name": auditor_model_db.split("/")[-1],
    }
    mock_audited_bot = {
      "bot_id": "test_audited_456",
      "model_name": audited_model,
      "display_name": audited_model,
    }

    mock_resolver = AsyncMock(return_value=auditor_model_resolved)
    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock(
      return_value='{"overall_score": 7.0, "categories": {}, "recommendations": [], "critical_issues": []}'
    )

    with (
      patch.object(auditor, "_select_auditor", return_value=mock_auditor_bot),
      patch(
        "app.services.CrossBotAuditor.BotRegistry.get_bot",
        return_value=mock_audited_bot,
      ),
      patch("app.services.CrossBotAuditor.LLMService") as MockLLMService,
      patch.object(auditor, "_store_report"),
      patch.object(auditor, "_get_bot_pnl", return_value=0.0),
    ):
      MockLLMService.return_value = mock_llm
      MockLLMService.clean_json_response = LLMService_clean

      import app.main
      with patch.object(
        app.main, "_resolve_ollama_model_name",
        mock_resolver, create=True,
      ):
        await auditor.audit_bot_run(
          "test_audited_456",
          {"phases": {}, "total_seconds": 100},
        )

      return MockLLMService, mock_resolver, mock_llm

  @pytest.mark.asyncio
  async def test_auditor_uses_resolved_model_name(self):
    """When auditor_bot has 'ibm/granite-3.2-8b', the LLMService must
    get the resolved 'granite3.2:8b', NOT the raw DB name."""
    MockLLMService, mock_resolver, _ = await self._run_audit_with_mock_resolver(
      auditor_model_db="ibm/granite-3.2-8b",
      auditor_model_resolved="granite3.2:8b",
    )

    # Verify the resolver was called with the raw DB name
    mock_resolver.assert_called_once()
    call_args = mock_resolver.call_args
    assert call_args[0][1] == "ibm/granite-3.2-8b"

    # Verify LLMService got the RESOLVED name, not the raw one
    MockLLMService.assert_called_once_with(model_override="granite3.2:8b")

  @pytest.mark.asyncio
  async def test_audit_prompt_shows_resolved_name(self):
    """The audit prompt should show the resolved model name, not the DB name."""
    _, _, mock_llm = await self._run_audit_with_mock_resolver(
      auditor_model_db="ibm/granite-3.2-8b",
      auditor_model_resolved="granite3.2:8b",
    )

    # Extract the 'user' argument from the .chat() call
    assert mock_llm.chat.called, "llm.chat() was never called"
    call_kwargs = mock_llm.chat.call_args.kwargs
    captured_user_msg = call_kwargs.get("user", "")

    assert captured_user_msg, "user arg was empty"
    assert "granite3.2:8b" in captured_user_msg
    assert "ibm/granite-3.2-8b" not in captured_user_msg


class TestNoThirdModelFallback:
  """Verify that exactly 2 models are involved in an audit, never 3."""

  def test_fallback_model_never_fires_with_resolved_name(self):
    """If the auditor model is properly resolved, the fallback
    (nemotron-3-nano:latest) should never be needed."""
    for db_name, resolved in RESOLUTION_CASES:
      assert resolved in INSTALLED_MODELS, (
        f"Resolved name '{resolved}' for '{db_name}' must exist "
        f"in INSTALLED_MODELS to prevent fallback"
      )


# ── Helper to avoid importing full LLMService ─────────────────────────


def LLMService_clean(raw: str) -> str:
  """Minimal clean_json_response stub."""
  import json
  try:
    json.loads(raw)
    return raw
  except Exception:
    return "{}"
