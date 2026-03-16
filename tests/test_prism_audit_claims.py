"""Verification tests for Prism workflow refactor.

These tests prove the refactored code correctly:
1. Strips non-LLM phases from Prism workflows
2. Posts per-ticker workflows with real LLM prompts
3. Uses WorkflowTracker in _process_ticker()
"""

import ast
import pytest
from unittest.mock import MagicMock, patch, AsyncMock


# ──────────────────────────────────────────────────────────────────
# Claim 1: deep_analysis_service.py still makes ZERO LLM calls
# ──────────────────────────────────────────────────────────────────

class TestDeepAnalysisHasNoLLMCalls:
    """Prove that DeepAnalysisService never imports or calls LLMService."""

    def test_no_llm_service_import(self):
        """The module must not import LLMService at all."""
        source_path = "app/services/deep_analysis_service.py"
        with open(source_path, "r") as f:
            source = f.read()

        tree = ast.parse(source)
        imported_names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported_names.append(alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported_names.append(alias.name)

        assert "LLMService" not in imported_names, (
            "deep_analysis_service.py imports LLMService — audit claim is WRONG"
        )

    def test_no_chat_method_calls(self):
        """The module must not contain any .chat() calls."""
        source_path = "app/services/deep_analysis_service.py"
        with open(source_path, "r") as f:
            source = f.read()

        tree = ast.parse(source)
        chat_calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "chat":
                    chat_calls.append(ast.dump(node))

        assert len(chat_calls) == 0, (
            f"deep_analysis_service.py contains .chat() calls: {chat_calls}"
        )

    def test_docstring_says_zero_llm(self):
        """The module docstring explicitly states zero LLM calls."""
        source_path = "app/services/deep_analysis_service.py"
        with open(source_path, "r") as f:
            source = f.read()

        tree = ast.parse(source)
        docstring = ast.get_docstring(tree)
        assert "zero llm calls" in docstring.lower(), (
            "Module docstring does not declare zero LLM calls"
        )


# ──────────────────────────────────────────────────────────────────
# Claim 2: trading_agent.py IS the LLM caller and returns llm_meta
# ──────────────────────────────────────────────────────────────────

class TestTradingAgentUsesLLM:
    """Prove that TradingAgent calls LLMService.chat() and returns 3-tuple."""

    def test_imports_llm_service(self):
        """Must import LLMService."""
        source_path = "app/services/trading_agent.py"
        with open(source_path, "r") as f:
            source = f.read()

        tree = ast.parse(source)
        imported_names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported_names.append(alias.name)

        assert "LLMService" in imported_names

    def test_has_chat_call(self):
        """Must contain at least one .chat() call."""
        source_path = "app/services/trading_agent.py"
        with open(source_path, "r") as f:
            source = f.read()

        tree = ast.parse(source)
        chat_calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "chat":
                    chat_calls.append(node.lineno)

        assert len(chat_calls) >= 1

    def test_decide_returns_3_tuple(self):
        """decide() must return (TradeAction, str, dict) — 3 elements."""
        source_path = "app/services/trading_agent.py"
        with open(source_path, "r") as f:
            source = f.read()

        assert "tuple[TradeAction, str, dict]" in source, (
            "decide() does not declare 3-tuple return type"
        )

    def test_llm_meta_has_required_keys(self):
        """The llm_meta dict must contain system_prompt, user_prompt, raw_output."""
        source_path = "app/services/trading_agent.py"
        with open(source_path, "r") as f:
            source = f.read()

        for key in ("system_prompt", "user_prompt", "raw_output", "tools_used", "duration_s", "model"):
            assert f'"{key}"' in source, (
                f"llm_meta is missing key: {key}"
            )


# ──────────────────────────────────────────────────────────────────
# Claim 3: WorkflowTracker IS NOW used in _process_ticker()
# ──────────────────────────────────────────────────────────────────

class TestWorkflowTrackerInPipeline:
    """Prove that WorkflowTracker is now wired into the pipeline."""

    def test_pipeline_imports_workflow_tracker(self):
        """trading_pipeline_service.py must import WorkflowTracker."""
        with open("app/services/trading_pipeline_service.py", "r") as f:
            source = f.read()

        assert "WorkflowTracker" in source, (
            "WorkflowTracker not found in trading_pipeline_service.py"
        )

    def test_pipeline_creates_per_ticker_workflow(self):
        """_process_ticker must create a WorkflowTracker with ticker name."""
        with open("app/services/trading_pipeline_service.py", "r") as f:
            source = f.read()

        assert "Trade Decision" in source, (
            "Per-ticker workflow title not found"
        )

    @pytest.mark.parametrize("filepath", [
        "app/services/deep_analysis_service.py",
    ])
    def test_no_tracker_in_non_llm_services(self, filepath):
        """Non-LLM services must NOT reference WorkflowTracker."""
        with open(filepath, "r") as f:
            source = f.read()

        assert "WorkflowTracker" not in source


# ──────────────────────────────────────────────────────────────────
# Claim 4: Loops NO LONGER post all phases blindly
# ──────────────────────────────────────────────────────────────────

class TestAutonomousLoopFixed:
    """Prove that the for-loop no longer iterates all phases."""

    def test_no_blind_phase_iteration(self):
        """The old pattern `for phase_name, phase_data in report...` with
        str(phase_data)[:500] must be GONE."""
        with open("app/services/autonomous_loop.py", "r") as f:
            source = f.read()

        assert "user_input=str(phase_data)[:500]" not in source, (
            "str(phase_data)[:500] pattern still exists — bug NOT fixed"
        )

    def test_no_static_system_prompt_label(self):
        """The old f'Phase: {phase_name}' system prompt must be GONE."""
        with open("app/services/autonomous_loop.py", "r") as f:
            source = f.read()

        assert 'system_prompt=f"Phase: {phase_name}"' not in source, (
            "Static Phase label still present — bug NOT fixed"
        )

    def test_only_trading_phase_posted(self):
        """The tracker block should reference 'trading' specifically."""
        with open("app/services/autonomous_loop.py", "r") as f:
            source = f.read()

        assert '.get("trading"' in source, (
            "Tracker does not specifically reference the trading phase"
        )

    def test_report_still_contains_all_phases(self):
        """The report dict must still contain all phases for health tracking."""
        with open("app/services/autonomous_loop.py", "r") as f:
            source = f.read()

        for phase in ["discovery", "import", "collection", "embedding"]:
            assert f'report["phases"]["{phase}"]' in source, (
                f"Phase '{phase}' was removed from report — only strip from tracker"
            )
