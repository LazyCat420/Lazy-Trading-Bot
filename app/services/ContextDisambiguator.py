"""Context Disambiguator — LLM-powered validation for ambiguous stock tickers.

Many short tickers (AI, IT, A, ON, GO, ALL, REAL) are also common English
words or acronyms.  This service takes extracted tickers + their source text,
sends the ambiguous ones to the LLM in a single batch call, and returns only
tickers that were genuinely discussed as stocks/companies.

Non-ambiguous tickers (e.g. NVDA, AAPL) pass through untouched.
"""

from __future__ import annotations

import json
from typing import Any

from app.services.llm_service import LLMService
from app.utils.logger import logger


# ── Tickers that are also common English words/acronyms ──────────
# These REQUIRE context validation before being accepted.
# Each entry is a real NYSE/NASDAQ ticker that happens to overlap with
# a common word, abbreviation, or tech buzzword.
AMBIGUOUS_TICKERS: set[str] = {
  # 1-2 letter tickers that are common words
  "A",      # Agilent Technologies — also the article "a"
  "AI",     # C3.ai — also "artificial intelligence"
  "AN",     # AutoNation — also "an" (article)
  "AM",     # Antero Midstream — also "AM" (morning)
  "BE",     # Bloom Energy — also verb "be"
  "BIG",    # Big Lots — also adjective
  "CAN",    # Canaan — also "can" (verb)
  "DNA",    # Ginkgo Bioworks — also genetic acronym
  "DO",     # Ditto (not real, but protects verb)
  "EAT",    # Brinker International — also verb
  "FAST",   # Fastenal — also adjective
  "FOR",    # not a real ticker, protects preposition
  "FUN",    # Cedar Fair — also "fun"
  "GE",     # GE Aerospace — also abbreviation
  "GO",     # Grocery Outlet — also verb "go"
  "HAS",    # Hasbro — also verb
  "HE",     # Hawaiian Electric — also pronoun
  "IT",     # Gartner — also "information technology"
  "LOW",    # Lowe's — also adjective "low"
  "MAN",    # ManpowerGroup — also "man"
  "MAS",    # Masco — also "mas" (more)
  "MAY",    # not a current ticker, protects month name
  "NEW",    # not a real ticker, protects adjective
  "NOW",    # ServiceNow — also adverb "now"
  "OLD",    # not a real ticker, protects adjective
  "ON",     # ON Semiconductor — also preposition
  "ONE",    # Realty Income (but listed as O) — protects "one"
  "OUT",    # Outfront Media — also preposition
  "OWN",    # not a real ticker, protects pronoun
  "PAY",    # Paymentus — also verb
  "PLAY",   # Dave & Buster's — also verb/noun
  "RARE",   # Ultragenyx Pharmaceutical — also adjective
  "RAW",    # not a real ticker, protects adjective
  "REAL",   # The RealReal — also adjective
  "RUN",    # not a real ticker, protects verb
  "SEE",    # Sealed Air — also verb "see"
  "SIX",    # Six Flags — also number
  "SO",     # Southern Company — also "so" (conjunction)
  "SOS",    # SOS Limited — also "SOS" (distress)
  "SPY",    # SPDR S&P 500 ETF — also word "spy"
  "SUN",    # Sunoco — also "sun"
  "TEN",    # Tenneco — also number
  "TRUE",   # TrueCar — also adjective
  "TWO",    # not a real ticker, protects number
  "UP",     # not a real ticker, protects preposition
  "VERY",   # not a real ticker, protects adverb
  "WELL",   # not a real ticker, protects adverb
  "WIT",    # Wipro — also "wit"
  "YOU",    # not a current ticker, protects pronoun
  "ZERO",   # not a real ticker, protects noun
  "ARE",    # not a real ticker, protects verb
  "CAR",    # not a real ticker, protects noun
  "OIL",    # not a real ticker, protects noun
  "FREE",   # not a real ticker, protects adjective
  "HOT",    # not a real ticker, protects adjective
  "NET",    # not a real ticker, protects noun
  "ALL",    # Allstate — also "all"
  "BEST",   # Best Buy? (BBY) — protects adjective
  "OPEN",   # Opendoor — also adjective/verb
  "BILL",   # Bill Holdings — also name/noun
  "PATH",   # UiPath — also noun
  "ROKU",   # Roku — could be confused but is usually stock
  "LIFE",   # not a real ticker, protects noun
  "GOOD",   # not a real ticker, protects adjective
  "ARM",    # Arm Holdings — also "arm"
}

_DISAMBIGUATION_PROMPT = """You are a stock ticker validation engine. I will give you a list of potential stock ticker symbols that were extracted from text. For each ticker, determine whether the text is ACTUALLY discussing that company's stock/shares, or if the word is just used as a regular English word, acronym, or abbreviation.

IMPORTANT RULES:
1. A ticker is CONFIRMED only if the text discusses the actual company, its stock price, financials, or business operations.
2. A ticker is REJECTED if the word is used as:
   - A common English word (e.g. "AI" meaning artificial intelligence, not C3.ai)
   - A tech/industry buzzword (e.g. "AI revolution" = artificial intelligence)
   - A preposition, article, pronoun (e.g. "a", "on", "it", "all")
   - An abbreviation (e.g. "IT department" = information technology, not Gartner)
3. Look for STRONG SIGNALS of stock discussion:
   - Dollar signs: "$AI", "$IT"
   - Price mentions: "AI is trading at $25"
   - Company name context: "C3.ai (AI)", "Gartner (IT)"
   - Analyst/earnings context: "AI reported earnings", "IT beat estimates"
4. When in DOUBT, REJECT. False negatives are better than false positives.

SOURCE TEXT:
{source_text}

TICKERS TO VALIDATE:
{tickers_json}

Return ONLY a JSON object mapping each ticker to true (confirmed stock) or false (rejected):
{{"AI": false, "NVDA": true}}"""


class ContextDisambiguator:
  """Validates ambiguous tickers against their source text using LLM."""

  def __init__(self) -> None:
    self.llm = LLMService()

  async def disambiguate(
    self,
    tickers: list[str],
    source_text: str,
    *,
    max_context_chars: int = 4000,
  ) -> list[str]:
    """Filter out ambiguous tickers that aren't actually stock references.

    Args:
      tickers: List of extracted ticker symbols.
      source_text: The text from which tickers were extracted.
      max_context_chars: Maximum chars of source text to send to LLM.

    Returns:
      Filtered list with false-positive ambiguous tickers removed.
    """
    if not tickers:
      return []

    # Split into ambiguous (need LLM check) vs confirmed (pass through)
    ambiguous = [t for t in tickers if t.upper() in AMBIGUOUS_TICKERS]
    confirmed = [t for t in tickers if t.upper() not in AMBIGUOUS_TICKERS]

    if not ambiguous:
      # No ambiguous tickers — skip LLM call entirely
      logger.debug(
        "[Disambiguator] No ambiguous tickers in %s — skipping LLM",
        tickers,
      )
      return tickers

    logger.info(
      "[Disambiguator] Checking %d ambiguous tickers: %s (context: %d chars)",
      len(ambiguous),
      ambiguous,
      len(source_text),
    )

    # ── LLM validation ──
    try:
      validated = await self._llm_validate(
        ambiguous, source_text[:max_context_chars],
      )
    except Exception as e:
      logger.warning(
        "[Disambiguator] LLM validation failed: %s — rejecting all ambiguous",
        e,
      )
      validated = []

    result = confirmed + validated
    rejected = set(ambiguous) - set(validated)
    if rejected:
      logger.info(
        "[Disambiguator] REJECTED %d false-positive tickers: %s",
        len(rejected),
        sorted(rejected),
      )
    if validated:
      logger.info(
        "[Disambiguator] CONFIRMED %d ambiguous tickers: %s",
        len(validated),
        validated,
      )

    return result

  async def _llm_validate(
    self,
    ambiguous_tickers: list[str],
    source_text: str,
  ) -> list[str]:
    """Send ambiguous tickers to LLM for context validation.

    Returns only tickers confirmed as actual stock references.
    """
    prompt = _DISAMBIGUATION_PROMPT.format(
      source_text=source_text,
      tickers_json=json.dumps(ambiguous_tickers),
    )

    try:
      raw = await self.llm.chat(
        system=(
          "You are a stock ticker validation engine. "
          "Return ONLY raw, valid JSON mapping tickers to booleans. "
          "No markdown, no commentary."
        ),
        user=prompt,
        response_format="json",
        temperature=0.1,  # Low temperature for deterministic judgement
        audit_step="ticker_disambiguation",
        audit_ticker=",".join(ambiguous_tickers[:5]),
      )

      cleaned = LLMService.clean_json_response(raw)
      parsed = json.loads(cleaned)

      if not isinstance(parsed, dict):
        logger.warning(
          "[Disambiguator] LLM returned non-dict: %s — rejecting all ambiguous",
          type(parsed).__name__,
        )
        return []

      validated = [
        t for t in ambiguous_tickers
        if parsed.get(t, False) is True
      ]

      logger.info(
        "[Disambiguator] LLM result: %s → confirmed %s",
        {k: v for k, v in parsed.items()},
        validated,
      )
      return validated

    except Exception as e:
      logger.warning(
        "[Disambiguator] LLM validation failed: %s — rejecting all ambiguous tickers",
        e,
      )
      # On failure, reject all ambiguous tickers (safe default)
      return []

  def has_ambiguous(self, tickers: list[str]) -> bool:
    """Quick check: are any of the tickers ambiguous?"""
    return any(t.upper() in AMBIGUOUS_TICKERS for t in tickers)
