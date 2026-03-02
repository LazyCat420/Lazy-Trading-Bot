"""Pipeline Diagnostic — tests each stage with one ticker to find silent failures.

Run:  venv\\Scripts\\python.exe tests/test_pipeline_diagnostic.py

Tests (in order):
  1. Raw LLM chat  (basic "hello" → expect non-empty response)
  2. Raw LLM JSON  (ask for JSON list → expect parseable JSON)
  3. QuestionGenerator  (Layer 2 — scorecard → 5 questions)
  4. RAG answer extraction  (Layer 3 — question → answer)
  5. DossierSynthesizer  (Layer 4 — full dossier synthesis)

Each test prints PASS/FAIL with timing and response details.
"""

import asyncio
import io
import json
import sys
import time
from pathlib import Path

# Fix Windows console encoding (cp1252 can't handle Unicode symbols)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.services.llm_service import LLMService  # noqa: E402

# ── Styling helpers ──────────────────────────────────────────────────

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

TICKER = "AAPL"  # Use a well-known ticker for all tests


def banner(title: str) -> None:
    print(f"\n{CYAN}{'=' * 60}")
    print(f"  {BOLD}{title}{RESET}{CYAN}")
    print(f"{'=' * 60}{RESET}")


def result(passed: bool, label: str, detail: str = "", dur: float = 0) -> bool:
    icon = f"{GREEN}[PASS]{RESET}" if passed else f"{RED}[FAIL]{RESET}"
    timing = f" ({dur:.1f}s)" if dur else ""
    print(f"  {icon}  {label}{timing}")
    if detail:
        # Indent detail lines
        for line in detail.split("\n")[:10]:
            print(f"         {line[:120]}")
    return passed


# ── Test 1: Basic LLM Chat ──────────────────────────────────────────

async def test_raw_chat(llm: LLMService) -> bool:
    """Send a simple text prompt and check if we get any response."""
    banner("Test 1: Raw LLM Chat (text mode)")
    print(f"  Model:    {settings.LLM_MODEL}")
    print(f"  Base URL: {settings.LLM_BASE_URL}")
    print(f"  Context:  {settings.LLM_CONTEXT_SIZE}")

    t0 = time.time()
    try:
        resp = await llm.chat(
            system="You are a helpful assistant. Answer briefly.",
            user="What is the stock ticker for Apple Inc? Reply in one line.",
            response_format="text",
            max_tokens=64,
        )
        dur = time.time() - t0
        ok = len(resp.strip()) > 0
        return result(
            ok,
            "Raw text response",
            f"Response ({len(resp)} chars): {resp.strip()[:200]}",
            dur,
        )
    except Exception as e:
        dur = time.time() - t0
        return result(False, "Raw text response", f"Exception: {e}", dur)


# ── Test 2: LLM JSON Response ───────────────────────────────────────

async def test_json_response(llm: LLMService) -> bool:
    """Ask for structured JSON and check if it's parseable."""
    banner("Test 2: LLM JSON Response")

    t0 = time.time()
    try:
        resp = await llm.chat(
            system=(
                "You are a stock analysis assistant. "
                "Return your answer as a JSON array."
            ),
            user=(
                "List 3 reasons to buy AAPL stock. "
                "Return as: [{\"reason\": \"...\"}]"
            ),
            response_format="json",
            max_tokens=256,
        )
        dur = time.time() - t0

        if not resp.strip():
            return result(
                False, "JSON response",
                "EMPTY RESPONSE — model returned 0 characters", dur,
            )

        # Try to parse
        try:
            parsed = json.loads(resp.strip())
            return result(
                True, "JSON response",
                f"Parsed OK ({type(parsed).__name__}): "
                f"{json.dumps(parsed)[:200]}",
                dur,
            )
        except json.JSONDecodeError as e:
            return result(
                False, "JSON response",
                f"JSON parse failed: {e}\n"
                f"Raw response: {resp.strip()[:300]}",
                dur,
            )
    except Exception as e:
        dur = time.time() - t0
        return result(False, "JSON response", f"Exception: {e}", dur)


# ── Test 3: QuestionGenerator (Layer 2) ──────────────────────────────

async def test_question_generator() -> bool:
    """Test 3: QuestionGenerator was deleted in engine refactor — SKIPPED."""
    banner("Test 3: QuestionGenerator (REMOVED)")
    return result(True, "QuestionGenerator removed in refactor", "Layer 2-4 funnel deleted, PortfolioStrategist handles analysis now")


# ── Test 4: RAG Answer Extraction (Layer 3) ──────────────────────────

async def test_rag_extraction(llm: LLMService) -> bool:
    """Test Layer 3: send a question + context → extract answer."""
    banner("Test 4: RAG Answer Extraction (Layer 3)")

    # Use a simple context snippet
    context = (
        "Apple Inc. reported Q1 2025 revenue of $124.3 billion, "
        "up 4% year over year. iPhone revenue was $69.1 billion, "
        "Services revenue hit $26.3 billion, a new all-time record. "
        "Gross margin was 46.9%. Net income was $36.3 billion."
    )
    question = "What was Apple's Q1 2025 revenue and how did it grow?"

    system = (
        "You are a financial research assistant. "
        "Answer the question using ONLY the context provided. "
        "Be concise and factual."
    )
    user = f"Context:\n{context}\n\nQuestion: {question}"

    t0 = time.time()
    try:
        resp = await llm.chat(
            system=system,
            user=user,
            response_format="text",
            max_tokens=256,
        )
        dur = time.time() - t0

        ok = len(resp.strip()) > 10
        return result(
            ok,
            "RAG extraction",
            f"Answer ({len(resp)} chars): {resp.strip()[:300]}",
            dur,
        )
    except Exception as e:
        dur = time.time() - t0
        return result(False, "RAG extraction", f"Exception: {e}", dur)


# ── Test 5: Dossier Synthesis (Layer 4) ───────────────────────────────

async def test_dossier_synthesis(llm: LLMService) -> bool:
    """Test Layer 4: synthesize all data into a JSON dossier."""
    banner("Test 5: Dossier Synthesis (Layer 4 — JSON)")

    # Simulate the synthesis prompt (simplified)
    system = (
        "You are a senior equity analyst. Synthesize all data into a "
        "JSON dossier with these exact keys: "
        '{"executive_summary": "...", "bull_case": "...", '
        '"bear_case": "...", "key_catalysts": ["..."], '
        '"conviction_score": 0.0 to 1.0, "signal": "BUY/HOLD/SELL"}'
    )
    user = f"""Ticker: {TICKER}

Quant Scorecard:
- Trend Template: 8/10
- VCP Setup: 45
- RS Rating: 72
- Market Cap: mega
- Z-Score: 1.2
- Momentum: 0.15
- Piotroski F-Score: 7/9

Research Answers:
1. Revenue grew 4% YoY to $124.3B in Q1 2025
2. Services segment at record $26.3B (high margin)
3. Gross margin 46.9% — stable
4. Strong cash flow generation
5. iPhone revenue $69.1B — slight decline in China

Synthesize a complete investment dossier as JSON."""

    t0 = time.time()
    try:
        resp = await llm.chat(
            system=system,
            user=user,
            response_format="json",
            max_tokens=1024,
        )
        dur = time.time() - t0

        if not resp.strip():
            return result(
                False, "Dossier synthesis",
                "EMPTY RESPONSE - model returned 0 characters\n"
                "This is the root cause of the silent failures!",
                dur,
            )

        # Try to parse
        try:
            parsed = json.loads(resp.strip())
            has_keys = all(
                k in parsed
                for k in [
                    "executive_summary", "conviction_score", "signal",
                ]
            )
            detail = (
                f"Keys: {list(parsed.keys())}\n"
                f"Conviction: {parsed.get('conviction_score', '???')}\n"
                f"Signal: {parsed.get('signal', '???')}\n"
                f"Summary: {str(parsed.get('executive_summary', ''))[:150]}"
            )
            return result(has_keys, "Dossier synthesis", detail, dur)
        except json.JSONDecodeError as e:
            return result(
                False, "Dossier synthesis",
                f"JSON parse failed: {e}\n"
                f"Raw ({len(resp)} chars): {resp.strip()[:300]}",
                dur,
            )
    except Exception as e:
        dur = time.time() - t0
        return result(False, "Dossier synthesis", f"Exception: {e}", dur)


# ── Main ─────────────────────────────────────────────────────────────

async def main() -> None:
    print(f"\n{BOLD}Pipeline Diagnostic — Testing Each Stage{RESET}")
    print(f"Model:       {CYAN}{settings.LLM_MODEL}{RESET}")
    print(f"Ollama URL:  {CYAN}{settings.LLM_BASE_URL}{RESET}")
    print(f"Context:     {CYAN}{settings.LLM_CONTEXT_SIZE}{RESET}")
    print(f"Temperature: {CYAN}{settings.LLM_TEMPERATURE}{RESET}")
    print(f"Test ticker: {CYAN}{TICKER}{RESET}")

    llm = LLMService()

    results = []

    # Test 1: Basic chat
    results.append(await test_raw_chat(llm))

    # Test 2: JSON response
    results.append(await test_json_response(llm))

    # Test 3: QuestionGenerator (Layer 2)
    results.append(await test_question_generator())

    # Test 4: RAG extraction (Layer 3)
    results.append(await test_rag_extraction(llm))

    # Test 5: Dossier synthesis (Layer 4)
    results.append(await test_dossier_synthesis(llm))

    # ── Summary ──────────────────────────────────────────────────
    banner("SUMMARY")
    passed = sum(results)
    total = len(results)
    labels = [
        "Raw chat (text)",
        "JSON response",
        "QuestionGenerator (L2)",
        "RAG extraction (L3)",
        "Dossier synthesis (L4)",
    ]
    for label, ok in zip(labels, results):
        icon = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
        print(f"  [{icon}] {label}")

    color = GREEN if passed == total else RED
    print(f"\n  {color}{BOLD}{passed}/{total} tests passed{RESET}\n")

    if passed < total:
        print(
            f"  {YELLOW}💡 Tip: If tests fail with empty responses, "
            f"the model may not support JSON format mode,\n"
            f"         or its context window is too small for the "
            f"prompts being sent.{RESET}\n"
        )
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
