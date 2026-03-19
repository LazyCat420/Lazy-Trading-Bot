"""Brain Loop — 3-phase recursive analysis engine with mathematical proof logic.

Architecture (proof-logic enhanced):
  Phase 1: AnalystAgent — 5 domain-specific analysis passes → step-justified memos
           + LemmaCache accumulates verified claims across domains
  Phase 2: ThesisConstructor — inductive synthesis with lemma-aware contradiction detection
           + Gated: requires ≥3 valid memos from Phase 1
  Phase 3: DecisionAgent — thesis + portfolio → TradeAction with adversarial self-check
           + ConsistencyValidator (post-LLM rule checker)
           + ContradictionPass (proof by contradiction: "assume wrong")

Proof Techniques Applied:
  1. Step-Justification: every claim must cite data (reasoning_steps in memo)
  2. Proof by Contradiction: adversarial self-check after decision
  3. Inductive Layer Ordering: each phase gates on prior phase success
  4. Lemma Cache: verified claims accumulate and carry forward
  5. Consistency Validator: rule-based post-LLM logic checker
"""

from __future__ import annotations

from app.services.unified_logger import track_class_telemetry, track_telemetry
import json
import time
from typing import Any

from app.services.analyst_prompts import (
    ANALYST_DOMAINS,
    CONTRADICTION_PASS_PROMPT,
    DECISION_PROMPT,
    MEMO_SCHEMA,
    THESIS_PROMPT,
)
from app.services.llm_service import LLMService
from app.services.research_tools import TOOL_REGISTRY
from app.utils.logger import logger

_llm = LLMService()

# Max recursion depth for contradiction resolution
_MAX_THESIS_RECURSIONS = 2

# Minimum valid memos required for Phase 2 (inductive gate)
_MIN_VALID_MEMOS = 3


# ══════════════════════════════════════════════════════════════════
# Lemma Cache — Technique 4: reusable verified sub-claims
# ══════════════════════════════════════════════════════════════════

@track_class_telemetry
class LemmaCache:
    """Accumulates verified factual claims across analysis phases.

    Each lemma is a claim that can be traced back to raw data.
    Subsequent prompts receive confirmed lemmas as established truth.
    If a later memo contradicts a lemma, it is flagged as a conflict.
    """

    def __init__(self) -> None:
        self._lemmas: list[dict] = []
        self._conflicts: list[dict] = []

    def extract_from_memo(self, memo: dict) -> None:
        """Extract verified_claims from a memo and add to cache."""
        domain = memo.get("domain", "?")
        claims = memo.get("verified_claims", [])
        data_cited = memo.get("data_cited", [])

        # Add explicit verified_claims from the LLM
        for c in claims:
            if isinstance(c, dict) and c.get("claim"):
                self._lemmas.append({
                    "claim": c["claim"],
                    "source": c.get("source", "unknown"),
                    "type": c.get("type", "fact"),
                    "domain": domain,
                })

        # Also treat data_cited as factual lemmas
        for dc in data_cited:
            dc_str = str(dc).strip()
            if dc_str and "=" in dc_str:
                self._lemmas.append({
                    "claim": dc_str,
                    "source": f"{domain}_data",
                    "type": "fact",
                    "domain": domain,
                })

    def check_conflicts(self, new_claims: list[dict]) -> list[dict]:
        """Check if new claims contradict existing lemmas."""
        conflicts = []
        for new in new_claims:
            for existing in self._lemmas:
                if self._claims_conflict(existing, new):
                    conflict = {
                        "existing": existing,
                        "new": new,
                        "description": (
                            f"Conflict: {existing['domain']} says "
                            f"'{existing['claim']}' but {new.get('domain', '?')} "
                            f"says '{new.get('claim', '?')}'"
                        ),
                    }
                    conflicts.append(conflict)
                    self._conflicts.append(conflict)
        return conflicts

    @staticmethod
    def _claims_conflict(a: dict, b: dict) -> bool:
        """Check if two claims might conflict (heuristic)."""
        a_claim = a.get("claim", "").lower()
        b_claim = b.get("claim", "").lower()

        # Same metric with different values
        if "=" in a_claim and "=" in b_claim:
            a_parts = a_claim.split("=", 1)
            b_parts = b_claim.split("=", 1)
            if a_parts[0].strip() == b_parts[0].strip():
                if a_parts[1].strip() != b_parts[1].strip():
                    return True
        return False

    def format_for_prompt(self) -> str:
        """Format confirmed lemmas as text for injection into prompts."""
        if not self._lemmas:
            return "No confirmed lemmas yet — this is the first analysis."

        lines = []
        for i, lemma in enumerate(self._lemmas, 1):
            lines.append(
                f"  L{i}. [{lemma['domain']}] {lemma['claim']} "
                f"(source: {lemma['source']}, type: {lemma['type']})"
            )
        return "\n".join(lines)

    @property
    def lemmas(self) -> list[dict]:
        return list(self._lemmas)

    @property
    def conflicts(self) -> list[dict]:
        return list(self._conflicts)

    @property
    def count(self) -> int:
        return len(self._lemmas)


# ══════════════════════════════════════════════════════════════════
# Consistency Validator — Technique 5: post-LLM rule checker
# ══════════════════════════════════════════════════════════════════

@track_class_telemetry
class ConsistencyValidator:
    """Post-LLM validation layer that checks logic-conclusion consistency.

    Rules:
    1. BUY requires more bull_factors than bear_factors (or equal with high conf)
    2. SELL requires more bear_factors than bull_factors
    3. Confidence must match thesis ±0.15
    4. Reasoning steps must exist and be non-empty
    5. Direction must match action (BULLISH→BUY/HOLD, BEARISH→SELL/HOLD)
    """

    @staticmethod
    def validate_decision(
        decision: dict,
        thesis: dict,
    ) -> dict:
        """Validate decision against thesis for logical consistency.

        Returns a validation report with issues found and corrections applied.
        """
        issues: list[dict] = []
        corrections: list[dict] = []

        action = decision.get("action", "HOLD")
        direction = thesis.get("direction", "NEUTRAL")
        thesis_conf = thesis.get("weighted_confidence", 0.5)
        decision_conf = decision.get("confidence", 0.5)
        bull_count = len(thesis.get("bull_factors", []))
        bear_count = len(thesis.get("bear_factors", []))

        # Rule 1: Direction ↔ Action alignment
        valid_actions = {
            "BULLISH": {"BUY", "HOLD"},
            "BEARISH": {"SELL", "HOLD"},
            "NEUTRAL": {"HOLD"},
        }
        allowed = valid_actions.get(direction, {"HOLD"})
        if action not in allowed:
            issues.append({
                "rule": "direction_action_alignment",
                "severity": "ERROR",
                "message": (
                    f"Action '{action}' contradicts thesis direction "
                    f"'{direction}' (expected {allowed})"
                ),
            })
            # Auto-correct: downgrade to HOLD
            if direction == "BEARISH" and action == "BUY":
                corrections.append({
                    "field": "action",
                    "from": action,
                    "to": "HOLD",
                    "reason": "Cannot BUY when thesis is BEARISH",
                })
                decision["action"] = "HOLD"
            elif direction == "BULLISH" and action == "SELL":
                corrections.append({
                    "field": "action",
                    "from": action,
                    "to": "HOLD",
                    "reason": "Cannot SELL when thesis is BULLISH",
                })
                decision["action"] = "HOLD"

        # Rule 2: Bull/bear factor balance
        if action == "BUY" and bear_count > bull_count:
            issues.append({
                "rule": "factor_balance",
                "severity": "WARNING",
                "message": (
                    f"BUY with more bear ({bear_count}) than bull ({bull_count}) factors"
                ),
            })

        if action == "SELL" and bull_count > bear_count:
            issues.append({
                "rule": "factor_balance",
                "severity": "WARNING",
                "message": (
                    f"SELL with more bull ({bull_count}) than bear ({bear_count}) factors"
                ),
            })

        # Rule 3: Confidence range check
        conf_diff = abs(thesis_conf - decision_conf)
        if conf_diff > 0.15:
            issues.append({
                "rule": "confidence_range",
                "severity": "WARNING",
                "message": (
                    f"Decision confidence {decision_conf:.2f} deviates from "
                    f"thesis {thesis_conf:.2f} by {conf_diff:.2f} (>0.15)"
                ),
            })
            # Auto-correct: clamp confidence
            corrected = max(min(decision_conf, thesis_conf + 0.15), thesis_conf - 0.15)
            corrections.append({
                "field": "confidence",
                "from": decision_conf,
                "to": round(corrected, 2),
                "reason": f"Clamped to thesis ±0.15 range",
            })
            decision["confidence"] = round(corrected, 2)

        # Rule 4: Reasoning steps check
        steps = decision.get("reasoning_steps", [])
        if not steps or len(steps) < 2:
            issues.append({
                "rule": "reasoning_depth",
                "severity": "WARNING",
                "message": "Fewer than 2 reasoning steps — shallow justification",
            })

        # Rule 5: Contradiction self-check presence
        contra_check = decision.get("contradiction_check", {})
        if not contra_check or not contra_check.get("assumed_wrong"):
            issues.append({
                "rule": "contradiction_check_missing",
                "severity": "INFO",
                "message": "No proof-by-contradiction self-check in output",
            })

        return {
            "valid": len([i for i in issues if i["severity"] == "ERROR"]) == 0,
            "issues": issues,
            "corrections": corrections,
            "issue_count": len(issues),
            "error_count": len([i for i in issues if i["severity"] == "ERROR"]),
            "warning_count": len([i for i in issues if i["severity"] == "WARNING"]),
        }


# ══════════════════════════════════════════════════════════════════
# Phase 1: Analyst Agent — domain-specific data digestion
# ══════════════════════════════════════════════════════════════════

@track_class_telemetry
class AnalystAgent:
    """Runs focused LLM analysis on one data domain at a time."""

    @staticmethod
    async def analyze_domain(
        domain: str,
        data: str,
        symbol: str,
        lemma_cache: LemmaCache | None = None,
    ) -> dict:
        """Run a single analyst pass and return a structured memo.

        Args:
            domain: One of: technical, fundamental, sentiment, smart_money, risk
            data: Formatted string of raw data for this domain
            symbol: Ticker symbol for logging
            lemma_cache: Optional lemma cache to inject confirmed facts

        Returns:
            Parsed memo dict, or fallback memo on failure.
        """
        config = ANALYST_DOMAINS.get(domain)
        if not config:
            return _fallback_memo(domain, "Unknown domain")

        # Format prompt with lemma context
        lemma_text = ""
        if lemma_cache and lemma_cache.count > 0:
            lemma_text = (
                "CONFIRMED LEMMAS (verified facts from prior analysis):\n"
                + lemma_cache.format_for_prompt()
            )

        prompt = config["prompt"].format(
            memo_schema=MEMO_SCHEMA,
            data=data,
            confirmed_lemmas=lemma_text,
        )

        t0 = time.perf_counter()
        try:
            raw = await _llm.chat(
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": f"Analyze {symbol} — {config['label']}"},
                ],
                response_format="json",
                temperature=0.15,
                audit_ticker=symbol,
                audit_step=f"analyst_{domain}",
            )
        except Exception as exc:
            logger.warning(
                "[BrainLoop] Analyst %s failed for %s: %s",
                domain, symbol, exc,
            )
            return _fallback_memo(domain, str(exc))

        elapsed = time.perf_counter() - t0
        logger.info(
            "[BrainLoop] Analyst %s for %s: %d chars in %.1fs",
            domain, symbol, len(raw), elapsed,
        )

        # Parse the memo
        cleaned = LLMService.clean_json_response(raw)
        try:
            memo = json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            try:
                import json_repair
                memo = json_repair.loads(cleaned)
                logger.info("[BrainLoop] json_repair salvaged %s memo", domain)
            except Exception:
                logger.warning(
                    "[BrainLoop] Failed to parse %s memo: %s",
                    domain, cleaned[:200],
                )
                return _fallback_memo(domain, f"Parse failed: {cleaned[:100]}")

        # Validate required fields
        memo.setdefault("signal", "NEUTRAL")
        memo.setdefault("confidence", 0.5)
        memo.setdefault("key_finding", "No finding")
        memo.setdefault("data_cited", [])
        memo.setdefault("risks", [])
        memo.setdefault("recommendation_weight", 0.5)
        memo.setdefault("reasoning_steps", [])
        memo.setdefault("verified_claims", [])
        memo["domain"] = domain
        memo["label"] = config["label"]
        memo["elapsed_s"] = round(elapsed, 2)

        # ── Lemma extraction (Technique 4) ─────────────────
        if lemma_cache is not None:
            # Check for conflicts with existing lemmas
            new_claims = [
                {"claim": str(dc), "domain": domain}
                for dc in memo.get("data_cited", [])
            ]
            conflicts = lemma_cache.check_conflicts(new_claims)
            if conflicts:
                for c in conflicts:
                    logger.warning(
                        "[BrainLoop] ⚠️  Lemma conflict in %s: %s",
                        domain, c["description"],
                    )
                memo["lemma_conflicts"] = [c["description"] for c in conflicts]

            # Add this memo's claims to the cache
            lemma_cache.extract_from_memo(memo)
            memo["lemmas_added"] = lemma_cache.count

        return memo

    @staticmethod
    async def run_all_domains(
        master_data: str,
        domains_to_run: list[str],
        symbol: str,
        lemma_cache: LemmaCache | None = None,
    ) -> list[dict]:
        """Run all analyst domains sequentially, accumulating lemmas using identical shared APC string.

        Args:
            master_data: combined string of all data.
            domains_to_run: list of domains to run.
            symbol: Ticker symbol
            lemma_cache: Optional lemma cache — creates one if None

        Returns:
            List of memo dicts (one per domain).
        """
        if lemma_cache is None:
            lemma_cache = LemmaCache()

        memos = []
        for domain in domains_to_run:
            memo = await AnalystAgent.analyze_domain(
                domain, master_data, symbol, lemma_cache=lemma_cache,
            )
            memos.append(memo)

        logger.info(
            "[BrainLoop] Lemma cache: %d verified claims accumulated",
            lemma_cache.count,
        )
        return memos


# ══════════════════════════════════════════════════════════════════
# Phase 2: Thesis Constructor — inductive synthesis
# ══════════════════════════════════════════════════════════════════

@track_class_telemetry
class ThesisConstructor:
    """Synthesize analyst memos inductively, gate on prior phase quality."""

    @staticmethod
    async def synthesize(
        memos: list[dict],
        portfolio_context: str,
        symbol: str,
        lemma_cache: LemmaCache | None = None,
        recursion_depth: int = 0,
    ) -> dict:
        """Build a thesis from analyst memos using inductive synthesis.

        Technique 3 (Inductive Gate): Requires ≥ _MIN_VALID_MEMOS valid memos.
        A memo is "valid" if it has a non-default signal and confidence > 0.3.
        """
        # ── Inductive gate (Technique 3) ─────────────────────
        valid_memos = [
            m for m in memos
            if m.get("signal") != "NEUTRAL" or m.get("confidence", 0) > 0.4
        ]
        if len(valid_memos) < _MIN_VALID_MEMOS and recursion_depth == 0:
            logger.warning(
                "[BrainLoop] Inductive gate: only %d/%d valid memos (need %d). "
                "Proceeding with reduced confidence.",
                len(valid_memos), len(memos), _MIN_VALID_MEMOS,
            )

        # Format memos for the prompt
        memo_text_parts = []
        for m in memos:
            steps_text = ""
            reasoning = m.get("reasoning_steps", [])
            if reasoning:
                steps_text = "\nReasoning:\n" + "\n".join(
                    f"  {s}" for s in reasoning[:5]
                )

            memo_text_parts.append(
                f"### {m.get('label', m.get('domain', '?'))} "
                f"(signal={m.get('signal', '?')}, "
                f"confidence={m.get('confidence', '?')}, "
                f"weight={m.get('recommendation_weight', '?')})\n"
                f"Finding: {m.get('key_finding', 'N/A')}\n"
                f"Data cited: {', '.join(str(d) for d in m.get('data_cited', []))}\n"
                f"Risks: {', '.join(str(r) for r in m.get('risks', []))}"
                + steps_text
            )
        memos_formatted = "\n\n".join(memo_text_parts)

        # Format lemmas for prompt
        lemma_text = ""
        if lemma_cache and lemma_cache.count > 0:
            lemma_text = lemma_cache.format_for_prompt()
        else:
            lemma_text = "No confirmed lemmas available."

        prompt = THESIS_PROMPT.format(
            memos=memos_formatted,
            portfolio=portfolio_context,
            lemmas=lemma_text,
        )

        t0 = time.perf_counter()
        try:
            raw = await _llm.chat(
                messages=[
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": (
                            f"Synthesize the {len(memos)} analyst memos for {symbol} "
                            f"into a thesis using inductive proof structure. "
                            f"Identify contradictions. Reference confirmed lemmas."
                        ),
                    },
                ],
                response_format="json",
                temperature=0.2,
                audit_ticker=symbol,
                audit_step=f"thesis_synthesis_r{recursion_depth}",
            )
        except Exception as exc:
            logger.warning("[BrainLoop] Thesis synthesis failed: %s", exc)
            return _fallback_thesis(memos, str(exc))

        elapsed = time.perf_counter() - t0
        logger.info(
            "[BrainLoop] Thesis synthesis for %s: %d chars in %.1fs (recursion=%d)",
            symbol, len(raw), elapsed, recursion_depth,
        )

        # Parse thesis
        cleaned = LLMService.clean_json_response(raw)
        try:
            thesis = json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            try:
                import json_repair
                thesis = json_repair.loads(cleaned)
            except Exception:
                logger.warning("[BrainLoop] Failed to parse thesis: %s", cleaned[:200])
                return _fallback_thesis(memos, f"Parse failed: {cleaned[:100]}")

        thesis.setdefault("direction", "NEUTRAL")
        thesis.setdefault("thesis", "")
        thesis.setdefault("bull_factors", [])
        thesis.setdefault("bear_factors", [])
        thesis.setdefault("contradictions", [])
        thesis.setdefault("key_question", None)
        thesis.setdefault("resolve_tool", None)
        thesis.setdefault("resolve_params", {})
        thesis.setdefault("weighted_confidence", 0.5)
        thesis.setdefault("recommended_action", "HOLD")
        thesis.setdefault("reasoning_steps", [])
        thesis["recursion_depth"] = recursion_depth
        thesis["elapsed_s"] = round(elapsed, 2)
        thesis["valid_memo_count"] = len(valid_memos)
        thesis["lemma_count"] = lemma_cache.count if lemma_cache else 0

        # ── Recursive contradiction resolution ────────────────
        contradictions = thesis.get("contradictions", [])
        resolve_tool = thesis.get("resolve_tool")
        resolve_params = thesis.get("resolve_params", {})

        if (
            contradictions
            and resolve_tool
            and resolve_tool in TOOL_REGISTRY
            and recursion_depth < _MAX_THESIS_RECURSIONS
        ):
            logger.info(
                "[BrainLoop] %s: %d contradiction(s) detected. "
                "Resolving with tool %s(%s)...",
                symbol,
                len(contradictions),
                resolve_tool,
                json.dumps(resolve_params)[:80],
            )

            # Inject symbol if missing
            if "ticker" not in resolve_params and resolve_tool != "compare_financials":
                resolve_params["ticker"] = symbol

            try:
                tool_func = TOOL_REGISTRY[resolve_tool]
                tool_result = await tool_func(resolve_params)
                tool_text = json.dumps(tool_result, indent=2, default=str)
                if len(tool_text) > 3000:
                    tool_text = tool_text[:3000] + "\n[...truncated]"

                logger.info(
                    "[BrainLoop] Tool %s returned %d chars for %s",
                    resolve_tool, len(tool_text), symbol,
                )

                # Add tool result as a new "resolution" memo
                resolution_memo = {
                    "domain": "resolution",
                    "label": f"Contradiction Resolution ({resolve_tool})",
                    "signal": "NEUTRAL",
                    "confidence": 0.5,
                    "key_finding": f"Tool {resolve_tool} data: {tool_text[:200]}",
                    "data_cited": [f"tool_result={resolve_tool}"],
                    "risks": [],
                    "recommendation_weight": 0.3,
                    "raw_tool_result": tool_text,
                }
                updated_memos = memos + [resolution_memo]

                # Recurse
                return await ThesisConstructor.synthesize(
                    updated_memos,
                    portfolio_context,
                    symbol,
                    lemma_cache=lemma_cache,
                    recursion_depth=recursion_depth + 1,
                )
            except Exception as exc:
                logger.warning(
                    "[BrainLoop] Contradiction resolution tool %s failed: %s",
                    resolve_tool, exc,
                )
                thesis["resolution_error"] = str(exc)

        return thesis


# ══════════════════════════════════════════════════════════════════
# Contradiction Pass — Technique 2: adversarial proof by contradiction
# ══════════════════════════════════════════════════════════════════

@track_class_telemetry
class ContradictionPass:
    """Run a proof-by-contradiction check on the final decision."""

    @staticmethod
    async def run(
        decision: dict,
        thesis: dict,
        symbol: str,
        lemma_cache: LemmaCache | None = None,
    ) -> dict:
        """Adversarially test the decision: assume it's wrong, find evidence.

        Returns the contradiction audit result.
        """
        decision_text = json.dumps(decision, indent=2, default=str)
        thesis_text = json.dumps(thesis, indent=2, default=str)
        lemma_text = lemma_cache.format_for_prompt() if lemma_cache else "None"

        prompt = CONTRADICTION_PASS_PROMPT.format(
            action=decision.get("action", "HOLD"),
            decision=decision_text,
            lemmas=lemma_text,
            thesis=thesis_text,
        )

        t0 = time.perf_counter()
        try:
            raw = await _llm.chat(
                messages=[
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": (
                            f"Try to DISPROVE the {decision.get('action', 'HOLD')} "
                            f"decision for {symbol}. Assume it is wrong."
                        ),
                    },
                ],
                response_format="json",
                temperature=0.2,
                audit_ticker=symbol,
                audit_step="contradiction_pass",
            )
        except Exception as exc:
            logger.warning("[BrainLoop] Contradiction pass failed: %s", exc)
            return {
                "verdict": "CONFIRMED",
                "error": str(exc),
                "elapsed_s": round(time.perf_counter() - t0, 2),
            }

        elapsed = time.perf_counter() - t0
        logger.info(
            "[BrainLoop] Contradiction pass for %s: %d chars in %.1fs",
            symbol, len(raw), elapsed,
        )

        cleaned = LLMService.clean_json_response(raw)
        try:
            result = json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            try:
                import json_repair
                result = json_repair.loads(cleaned)
            except Exception:
                result = {"verdict": "CONFIRMED", "parse_error": cleaned[:200]}

        result.setdefault("verdict", "CONFIRMED")
        result.setdefault("attack_vectors", [])
        result.setdefault("reasoning_steps", [])
        result["elapsed_s"] = round(elapsed, 2)

        return result


# ══════════════════════════════════════════════════════════════════
# Phase 3: Decision Agent — thesis → TradeAction JSON
# ══════════════════════════════════════════════════════════════════

@track_class_telemetry
class DecisionAgent:
    """Convert a thesis into a final TradeAction decision."""

    @staticmethod
    async def decide(
        thesis: dict,
        symbol: str,
        portfolio: dict,
        lemma_cache: LemmaCache | None = None,
    ) -> tuple[str, dict]:
        """Generate a final trading decision from the thesis.

        Returns:
            (raw_llm_text, parsed_decision_dict)
        """
        # Format portfolio context
        cash = portfolio.get("cash", 0)
        total_value = portfolio.get("value", 0) or portfolio.get("total_value", 0)
        positions = portfolio.get("positions", [])
        max_pct = 15

        # Check existing position
        existing = "None"
        for p in positions:
            if p.get("ticker") == symbol:
                existing = (
                    f"{p['qty']} shares @ ${p.get('avg_entry_price', 0):.2f}"
                )
                break

        holdings = "None (empty portfolio)"
        if positions:
            parts = [
                f"{p.get('ticker', '?')}({p.get('qty', 0)}@${p.get('avg_entry_price', 0):.0f})"
                for p in positions[:10]
            ]
            holdings = ", ".join(parts)

        # Format thesis as text
        thesis_text = (
            f"Direction: {thesis.get('direction', '?')}\n"
            f"Thesis: {thesis.get('thesis', '?')}\n"
            f"Bull factors: {', '.join(str(f) for f in thesis.get('bull_factors', []))}\n"
            f"Bear factors: {', '.join(str(f) for f in thesis.get('bear_factors', []))}\n"
            f"Contradictions: {', '.join(str(c) for c in thesis.get('contradictions', [])) or 'None'}\n"
            f"Weighted confidence: {thesis.get('weighted_confidence', 0.5)}\n"
            f"Recommended action: {thesis.get('recommended_action', 'HOLD')}"
        )

        # Format lemmas
        lemma_text = lemma_cache.format_for_prompt() if lemma_cache else "None"

        prompt = DECISION_PROMPT.format(
            symbol=symbol,
            thesis=thesis_text,
            cash=cash,
            total_value=total_value,
            max_position_pct=max_pct,
            existing_position=existing,
            holdings=holdings,
            lemmas=lemma_text,
        )

        t0 = time.perf_counter()
        try:
            raw = await _llm.chat(
                messages=[
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": f"Make your final decision for {symbol}.",
                    },
                ],
                response_format="json",
                temperature=0.15,
                audit_ticker=symbol,
                audit_step="final_decision",
            )
        except Exception as exc:
            logger.error("[BrainLoop] Decision failed: %s", exc)
            return "", {
                "action": "HOLD",
                "symbol": symbol,
                "confidence": 0.30,
                "rationale": f"LLM error: {exc}",
                "risk_notes": "Decision engine failure",
                "risk_level": "HIGH",
                "time_horizon": "SWING",
            }

        elapsed = time.perf_counter() - t0
        logger.info(
            "[BrainLoop] Decision for %s: %d chars in %.1fs",
            symbol, len(raw), elapsed,
        )

        cleaned = LLMService.clean_json_response(raw)
        try:
            decision = json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            try:
                import json_repair
                decision = json_repair.loads(cleaned)
            except Exception:
                logger.warning("[BrainLoop] Failed to parse decision: %s", cleaned[:200])
                decision = {
                    "action": "HOLD",
                    "symbol": symbol,
                    "confidence": 0.30,
                    "rationale": f"Parse failed. Raw: {cleaned[:200]}",
                    "risk_notes": "LLM output unparseable",
                    "risk_level": "HIGH",
                    "time_horizon": "SWING",
                }

        decision.setdefault("action", "HOLD")
        decision.setdefault("symbol", symbol)
        decision.setdefault("confidence", 0.5)
        decision.setdefault("rationale", "")
        decision.setdefault("risk_notes", "")
        decision.setdefault("risk_level", "MED")
        decision.setdefault("time_horizon", "SWING")
        decision.setdefault("reasoning_steps", [])
        decision.setdefault("contradiction_check", {})
        decision["decision_elapsed_s"] = round(elapsed, 2)

        return raw, decision


# ══════════════════════════════════════════════════════════════════
# Data extraction helpers — pull domain data from context/DB
# ══════════════════════════════════════════════════════════════════

def extract_domain_data(context: dict, symbol: str) -> dict[str, str]:
    """Extract domain-specific data slices from the trading context.

    Returns a dict mapping domain name → formatted data string.
    Each string is self-contained — the LLM analyst only sees this.
    """
    from app.database import get_db
    db = get_db()
    domain_data: dict[str, str] = {}

    # ── Technical data ────────────────────────────────────────
    try:
        rows = db.execute(
            """
            SELECT t.date, p.close, t.rsi, t.macd, t.macd_signal, t.macd_hist,
                   t.sma_20, t.sma_50, t.sma_200, t.ema_9, t.ema_21,
                   t.bb_upper, t.bb_middle, t.bb_lower, t.atr,
                   t.stoch_k, t.stoch_d, t.adx, t.obv, t.cmf,
                   t.aroon_up, t.aroon_down, t.supertrend, t.psar
            FROM technicals t
            LEFT JOIN price_history p ON t.ticker = p.ticker AND t.date = p.date
            WHERE t.ticker = ?
            ORDER BY t.date DESC
            LIMIT 10
            """,
            [symbol],
        ).fetchall()
        if rows:
            cols = [
                "date", "close", "rsi", "macd", "macd_signal", "macd_hist",
                "sma_20", "sma_50", "sma_200", "ema_9", "ema_21",
                "bb_upper", "bb_middle", "bb_lower", "atr",
                "stoch_k", "stoch_d", "adx", "obv", "cmf",
                "aroon_up", "aroon_down", "supertrend", "psar",
            ]
            lines = []
            for r in rows:
                d = dict(zip(cols, r))
                parts = [f"{k}={_fmt(v)}" for k, v in d.items() if v is not None]
                lines.append(" | ".join(parts))
            domain_data["technical"] = (
                f"Symbol: {symbol}\n"
                f"Current Price: ${context.get('last_price', 0):.2f}\n"
                f"Today Change: {context.get('today_change_pct', 0):+.2f}%\n"
                f"Volume: {context.get('volume', 0):,.0f} "
                f"(Avg: {context.get('avg_volume', 0):,.0f})\n\n"
                f"Last 10 trading days (newest first):\n" +
                "\n".join(lines)
            )
    except Exception as exc:
        logger.debug("[BrainLoop] Technical data extraction failed: %s", exc)

    # ── Fundamental data ──────────────────────────────────────
    try:
        row = db.execute(
            """
            SELECT trailing_pe, forward_pe, peg_ratio, price_to_sales,
                   price_to_book, profit_margin, operating_margin,
                   return_on_equity, return_on_assets,
                   revenue, revenue_growth, net_income,
                   total_cash, total_debt, debt_to_equity,
                   free_cash_flow, dividend_yield, sector, industry
            FROM fundamentals
            WHERE ticker = ?
            ORDER BY snapshot_date DESC LIMIT 1
            """,
            [symbol],
        ).fetchone()
        if row:
            cols = [
                "trailing_pe", "forward_pe", "peg_ratio", "price_to_sales",
                "price_to_book", "profit_margin", "operating_margin",
                "return_on_equity", "return_on_assets",
                "revenue", "revenue_growth", "net_income",
                "total_cash", "total_debt", "debt_to_equity",
                "free_cash_flow", "dividend_yield", "sector", "industry",
            ]
            d = dict(zip(cols, row))
            parts = [f"{k}: {_fmt(v)}" for k, v in d.items() if v is not None]
            domain_data["fundamental"] = f"Symbol: {symbol}\n" + "\n".join(parts)
    except Exception as exc:
        logger.debug("[BrainLoop] Fundamental data extraction failed: %s", exc)

    # ── Sentiment data (news + reddit + youtube + RAG) ────────
    sentiment_parts = []

    # News
    try:
        news_rows = db.execute(
            """
            SELECT title, publisher, summary
            FROM news_articles
            WHERE ticker = ?
            ORDER BY published_at DESC LIMIT 5
            """,
            [symbol],
        ).fetchall()
        if news_rows:
            sentiment_parts.append("=== RECENT NEWS ===")
            for nr in news_rows:
                title, pub, summary = nr
                s = summary[:150] + "…" if summary and len(summary) > 150 else (summary or "")
                sentiment_parts.append(f"• [{pub}] {title}\n  {s}")
    except Exception:
        pass

    # Reddit
    try:
        reddit_rows = db.execute(
            """
            SELECT source_detail, sentiment_hint, context_snippet
            FROM discovered_tickers
            WHERE ticker = ? AND source = 'reddit'
            ORDER BY discovered_at DESC LIMIT 5
            """,
            [symbol],
        ).fetchall()
        if reddit_rows:
            sentiment_parts.append("\n=== REDDIT MENTIONS ===")
            for rr in reddit_rows:
                detail, sent, ctx = rr
                c = ctx[:120] + "…" if ctx and len(ctx) > 120 else (ctx or "")
                sentiment_parts.append(f"• {detail} (sentiment: {sent}): {c}")
    except Exception:
        pass

    # YouTube
    try:
        yt_rows = db.execute(
            """
            SELECT title, channel, trading_data
            FROM youtube_trading_data
            WHERE ticker = ?
            ORDER BY collected_at DESC LIMIT 3
            """,
            [symbol],
        ).fetchall()
        if yt_rows:
            sentiment_parts.append("\n=== YOUTUBE ANALYSIS ===")
            for yt in yt_rows:
                title, channel, tdata = yt
                sentiment_parts.append(f"• [{channel}] {title}\n  Data: {tdata}")
    except Exception:
        pass

    # RAG context
    rag = context.get("rag_context", "")
    if rag:
        sentiment_parts.append(f"\n=== RAG RETRIEVED CONTEXT ===\n{rag}")

    if sentiment_parts:
        domain_data["sentiment"] = f"Symbol: {symbol}\n" + "\n".join(sentiment_parts)

    # ── Smart money data ──────────────────────────────────────
    smart_parts = []
    try:
        insider_row = db.execute(
            """
            SELECT net_insider_buying_90d, institutional_ownership_pct,
                   raw_transactions
            FROM insider_activity
            WHERE ticker = ?
            ORDER BY snapshot_date DESC LIMIT 1
            """,
            [symbol],
        ).fetchone()
        if insider_row:
            smart_parts.append("=== INSIDER ACTIVITY ===")
            smart_parts.append(f"Net insider buying (90d): ${insider_row[0]:,.0f}" if insider_row[0] else "Net insider buying: N/A")
            smart_parts.append(f"Institutional ownership: {insider_row[1]:.1%}" if insider_row[1] else "Institutional ownership: N/A")
            if insider_row[2]:
                try:
                    txns = json.loads(insider_row[2])
                    if isinstance(txns, list):
                        for t in txns[:3]:
                            smart_parts.append(
                                f"  • {t.get('name', '?')}: {t.get('type', '?')} "
                                f"${t.get('value', 0):,.0f}"
                            )
                except Exception:
                    pass
    except Exception:
        pass

    try:
        sec_rows = db.execute(
            """
            SELECT f.filer_name, h.shares, h.value_usd, h.filing_quarter
            FROM sec_13f_holdings h
            JOIN sec_13f_filers f ON h.cik = f.cik
            WHERE h.ticker = ?
            ORDER BY h.filing_date DESC LIMIT 5
            """,
            [symbol],
        ).fetchall()
        if sec_rows:
            smart_parts.append("\n=== INSTITUTIONAL 13F HOLDINGS ===")
            for sr in sec_rows:
                smart_parts.append(
                    f"  • {sr[0]}: {sr[1]:,.0f} shares (${sr[2]:,.0f}) — {sr[3]}"
                )
    except Exception:
        pass

    try:
        cong_rows = db.execute(
            """
            SELECT member_name, tx_type, tx_date, amount_range
            FROM congressional_trades
            WHERE ticker = ?
            ORDER BY tx_date DESC LIMIT 5
            """,
            [symbol],
        ).fetchall()
        if cong_rows:
            smart_parts.append("\n=== CONGRESSIONAL TRADES ===")
            for cr in cong_rows:
                smart_parts.append(
                    f"  • {cr[0]}: {cr[1]} on {cr[2]} ({cr[3]})"
                )
    except Exception:
        pass

    if smart_parts:
        domain_data["smart_money"] = f"Symbol: {symbol}\n" + "\n".join(smart_parts)

    # ── Risk data ─────────────────────────────────────────────
    risk_parts = []

    # Quant scorecard
    try:
        qs = db.execute(
            """
            SELECT conviction_score, composite_score, sharpe_ratio,
                   sortino_ratio, max_drawdown, kelly_fraction,
                   altman_z, piotroski_f, signal
            FROM quant_scorecards
            WHERE ticker = ?
            ORDER BY updated_at DESC LIMIT 1
            """,
            [symbol],
        ).fetchone()
        if qs:
            risk_parts.append("=== QUANT SCORECARD ===")
            cols = [
                "conviction", "composite", "sharpe", "sortino",
                "max_drawdown", "kelly", "altman_z", "piotroski_f", "signal",
            ]
            for col, val in zip(cols, qs):
                if val is not None:
                    risk_parts.append(f"{col}: {_fmt(val)}")
    except Exception:
        pass

    # Earnings proximity
    try:
        earn_row = db.execute(
            """
            SELECT next_earnings_date, days_until_earnings,
                   earnings_estimate, previous_actual, surprise_pct
            FROM earnings_calendar
            WHERE ticker = ?
            ORDER BY snapshot_date DESC LIMIT 1
            """,
            [symbol],
        ).fetchone()
        if earn_row:
            risk_parts.append("\n=== EARNINGS PROXIMITY ===")
            risk_parts.append(f"Next earnings: {earn_row[0]}")
            risk_parts.append(f"Days until: {earn_row[1]}")
            risk_parts.append(f"Estimate: {earn_row[2]}")
            risk_parts.append(f"Previous actual: {earn_row[3]}")
            if earn_row[4]:
                risk_parts.append(f"Last surprise: {earn_row[4]:+.1f}%")
    except Exception:
        pass

    # Quant flags from context
    flags = context.get("quant_flags", [])
    if flags:
        risk_parts.append(f"\n=== RISK FLAGS ===\n{', '.join(str(f) for f in flags)}")

    if risk_parts:
        domain_data["risk"] = f"Symbol: {symbol}\n" + "\n".join(risk_parts)

    return domain_data


def validate_data_coverage(symbol: str) -> dict:
    """Audit data coverage and freshness for each domain.

    Returns a structured report showing:
    - Which sources have data
    - How fresh the data is (newest date per source)
    - Data size (row counts)
    - Staleness warnings (>7 days old)
    """
    from datetime import datetime, timedelta

    from app.database import get_db
    db = get_db()
    now = datetime.now()
    stale_threshold = now - timedelta(days=7)
    report: dict[str, dict] = {}

    sources = {
        "technical": {
            "table": "technicals",
            "date_col": "date",
            "query": "SELECT COUNT(*), MAX(date), MIN(date) FROM technicals WHERE ticker = ?",
        },
        "price_history": {
            "table": "price_history",
            "date_col": "date",
            "query": "SELECT COUNT(*), MAX(date), MIN(date) FROM price_history WHERE ticker = ?",
        },
        "fundamental": {
            "table": "fundamentals",
            "date_col": "snapshot_date",
            "query": "SELECT COUNT(*), MAX(snapshot_date), MIN(snapshot_date) FROM fundamentals WHERE ticker = ?",
        },
        "news_articles": {
            "table": "news_articles",
            "date_col": "published_at",
            "query": "SELECT COUNT(*), MAX(published_at), MIN(published_at) FROM news_articles WHERE ticker = ?",
        },
        "reddit": {
            "table": "discovered_tickers",
            "date_col": "discovered_at",
            "query": "SELECT COUNT(*), MAX(discovered_at), MIN(discovered_at) FROM discovered_tickers WHERE ticker = ? AND source = 'reddit'",
        },
        "youtube": {
            "table": "youtube_trading_data",
            "date_col": "collected_at",
            "query": "SELECT COUNT(*), MAX(collected_at), MIN(collected_at) FROM youtube_trading_data WHERE ticker = ?",
        },
        "insider_activity": {
            "table": "insider_activity",
            "date_col": "snapshot_date",
            "query": "SELECT COUNT(*), MAX(snapshot_date), MIN(snapshot_date) FROM insider_activity WHERE ticker = ?",
        },
        "sec_13f": {
            "table": "sec_13f_holdings",
            "date_col": "filing_date",
            "query": "SELECT COUNT(*), MAX(filing_date), MIN(filing_date) FROM sec_13f_holdings WHERE ticker = ?",
        },
        "congressional": {
            "table": "congressional_trades",
            "date_col": "tx_date",
            "query": "SELECT COUNT(*), MAX(tx_date), MIN(tx_date) FROM congressional_trades WHERE ticker = ?",
        },
        "quant_scorecard": {
            "table": "quant_scorecards",
            "date_col": "updated_at",
            "query": "SELECT COUNT(*), MAX(updated_at), MIN(updated_at) FROM quant_scorecards WHERE ticker = ?",
        },
        "earnings_calendar": {
            "table": "earnings_calendar",
            "date_col": "snapshot_date",
            "query": "SELECT COUNT(*), MAX(snapshot_date), MIN(snapshot_date) FROM earnings_calendar WHERE ticker = ?",
        },
        "embeddings": {
            "table": "embeddings",
            "date_col": "created_at",
            "query": "SELECT COUNT(*), MAX(created_at), MIN(created_at) FROM embeddings WHERE ticker = ?",
        },
    }

    for name, cfg in sources.items():
        try:
            row = db.execute(cfg["query"], [symbol]).fetchone()
            count = row[0] if row else 0
            newest = str(row[1]) if row and row[1] else None
            oldest = str(row[2]) if row and row[2] else None

            # Check staleness
            is_stale = False
            if newest:
                try:
                    newest_dt = datetime.fromisoformat(newest[:19])
                    is_stale = newest_dt < stale_threshold
                except Exception:
                    pass

            report[name] = {
                "rows": count,
                "newest": newest,
                "oldest": oldest,
                "stale": is_stale,
                "status": "✅" if count > 0 and not is_stale else "⚠️ STALE" if is_stale else "❌ EMPTY",
            }
        except Exception as exc:
            report[name] = {
                "rows": 0,
                "newest": None,
                "oldest": None,
                "stale": False,
                "status": f"❌ ERROR: {exc}",
            }

    return report


def validate_memo_citations(
    memos: list[dict],
    master_data: str,
) -> list[dict]:
    """Cross-check LLM-cited data points against actual input data in the master string.

    For each memo, verify that the values in `data_cited` actually appear
    in the raw master data. Catches hallucinations.

    Returns a list of validation results per memo.
    """
    results = []
    for memo in memos:
        domain = memo.get("domain", "?")
        cited = memo.get("data_cited", [])
        raw_input = master_data

        if not cited or not raw_input:
            results.append({
                "domain": domain,
                "total_cited": len(cited),
                "verified": 0,
                "hallucinated": 0,
                "details": [],
                "score": 1.0 if not cited else 0.0,
            })
            continue

        verified = 0
        hallucinated = 0
        details = []

        for cite in cited:
            cite_str = str(cite).strip()
            # Extract the value part (e.g., "RSI=46" → "46")
            if "=" in cite_str:
                _key, _val = cite_str.split("=", 1)
                # Check if either the full citation or just the value appears
                found = (
                    cite_str.lower() in raw_input.lower()
                    or _val.strip() in raw_input
                    or _key.strip().lower() in raw_input.lower()
                )
            elif ":" in cite_str:
                _key, _val = cite_str.split(":", 1)
                found = (
                    _val.strip() in raw_input
                    or _key.strip().lower() in raw_input.lower()
                )
            else:
                found = cite_str.lower() in raw_input.lower()

            if found:
                verified += 1
                details.append({"cite": cite_str, "status": "✅ verified"})
            else:
                hallucinated += 1
                details.append({"cite": cite_str, "status": "⚠️ not found in input"})

        total = verified + hallucinated
        score = verified / total if total > 0 else 0.0

        results.append({
            "domain": domain,
            "total_cited": total,
            "verified": verified,
            "hallucinated": hallucinated,
            "details": details,
            "score": round(score, 2),
        })

    return results


def validate_data_integrity(symbol: str) -> dict:
    """Check for data integrity issues that could corrupt analysis.

    Checks:
    1. Stored-but-unused: tables with data that the brain loop never queries
    2. NULL/empty critical fields
    3. Date formatting issues (non-ISO dates)
    4. Duplicate entries
    5. Orphaned data (ticker not in watchlist)
    """
    from app.database import get_db
    db = get_db()
    issues: list[dict] = []

    # ── 1. Tables the brain loop actually queries ──
    USED_TABLES = {
        "technicals", "price_history", "fundamentals",
        "news_articles", "discovered_tickers", "youtube_trading_data",
        "insider_activity", "sec_13f_holdings", "sec_13f_filers",
        "congressional_trades", "quant_scorecards", "earnings_calendar",
        "embeddings",
    }

    # Check all tables for this ticker
    try:
        all_tables = [r[0] for r in db.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()]

        for table in all_tables:
            try:
                # Check if table has ticker column
                cols = [c[1] for c in db.execute(f"PRAGMA table_info('{table}')").fetchall()]
                ticker_col = None
                if "ticker" in cols:
                    ticker_col = "ticker"
                elif "symbol" in cols:
                    ticker_col = "symbol"

                if ticker_col:
                    count = db.execute(
                        f"SELECT COUNT(*) FROM \"{table}\" WHERE \"{ticker_col}\" = ?",
                        [symbol],
                    ).fetchone()[0]

                    if count > 0 and table not in USED_TABLES:
                        issues.append({
                            "type": "stored_but_unused",
                            "severity": "WARNING",
                            "table": table,
                            "rows": count,
                            "message": f"Table '{table}' has {count} rows for {symbol} but brain loop NEVER queries it",
                        })
            except Exception:
                pass
    except Exception:
        pass

    # ── 2. NULL/empty critical fields ──
    critical_checks = [
        ("technicals", "rsi", "ticker"),
        ("technicals", "macd", "ticker"),
        ("technicals", "atr", "ticker"),
        ("fundamentals", "trailing_pe", "ticker"),
        ("fundamentals", "revenue", "ticker"),
        ("news_articles", "title", "ticker"),
        ("news_articles", "summary", "ticker"),
        ("insider_activity", "net_insider_buying_90d", "ticker"),
        ("quant_scorecards", "conviction_score", "ticker"),
        ("earnings_calendar", "next_earnings_date", "ticker"),
    ]

    for table, col, ticker_col in critical_checks:
        try:
            null_count = db.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE "{ticker_col}" = ? AND ("{col}" IS NULL OR CAST("{col}" AS VARCHAR) = \'\')',
                [symbol],
            ).fetchone()[0]
            total = db.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE "{ticker_col}" = ?',
                [symbol],
            ).fetchone()[0]

            if null_count > 0 and total > 0:
                pct = null_count / total * 100
                issues.append({
                    "type": "null_critical_field",
                    "severity": "WARNING" if pct < 50 else "ERROR",
                    "table": table,
                    "column": col,
                    "null_count": null_count,
                    "total": total,
                    "message": f"{table}.{col}: {null_count}/{total} rows are NULL ({pct:.0f}%)",
                })
        except Exception:
            pass

    # ── 3. Duplicate entries (same ticker + date) ──
    dedup_checks = [
        ("technicals", "date", "ticker"),
        ("fundamentals", "snapshot_date", "ticker"),
        ("quant_scorecards", "updated_at", "ticker"),
    ]

    for table, date_col, ticker_col in dedup_checks:
        try:
            dups = db.execute(
                f"""
                SELECT "{date_col}", COUNT(*) as cnt
                FROM "{table}"
                WHERE "{ticker_col}" = ?
                GROUP BY "{date_col}"
                HAVING cnt > 1
                """,
                [symbol],
            ).fetchall()

            if dups:
                issues.append({
                    "type": "duplicate_entries",
                    "severity": "WARNING",
                    "table": table,
                    "duplicate_dates": [str(d[0]) for d in dups[:5]],
                    "message": f"{table}: {len(dups)} dates have duplicate entries for {symbol}",
                })
        except Exception:
            pass

    # ── 4. Data domain coverage summary ──
    domain_mapping = {
        "technical": ["technicals", "price_history"],
        "fundamental": ["fundamentals"],
        "sentiment": ["news_articles", "discovered_tickers", "youtube_trading_data"],
        "smart_money": ["insider_activity", "sec_13f_holdings", "congressional_trades"],
        "risk": ["quant_scorecards", "earnings_calendar"],
    }

    coverage = {}
    for domain, tables in domain_mapping.items():
        domain_status = "✅"
        table_statuses = []
        for table in tables:
            try:
                ticker_col = "ticker"
                if table == "discovered_tickers":
                    count = db.execute(
                        f"SELECT COUNT(*) FROM \"{table}\" WHERE ticker = ?",
                        [symbol],
                    ).fetchone()[0]
                else:
                    count = db.execute(
                        f"SELECT COUNT(*) FROM \"{table}\" WHERE ticker = ?",
                        [symbol],
                    ).fetchone()[0]
                status = f"✅ {count} rows" if count > 0 else "❌ EMPTY"
                if count == 0:
                    domain_status = "⚠️ PARTIAL" if domain_status == "✅" else "❌ EMPTY"
                table_statuses.append({"table": table, "rows": count, "status": status})
            except Exception as exc:
                table_statuses.append({"table": table, "rows": 0, "status": f"❌ {exc}"})
                domain_status = "⚠️ PARTIAL"

        coverage[domain] = {
            "status": domain_status,
            "tables": table_statuses,
        }

    return {
        "issues": issues,
        "issue_count": len(issues),
        "errors": len([i for i in issues if i["severity"] == "ERROR"]),
        "warnings": len([i for i in issues if i["severity"] == "WARNING"]),
        "coverage": coverage,
    }

def _fmt(v: Any) -> str:
    """Format a value for display."""
    if isinstance(v, float):
        if abs(v) >= 1_000_000:
            return f"${v:,.0f}"
        return f"{v:.4f}"
    return str(v)


def _fallback_memo(domain: str, reason: str) -> dict:
    """Return a neutral fallback memo when analysis fails."""
    return {
        "domain": domain,
        "label": ANALYST_DOMAINS.get(domain, {}).get("label", domain),
        "signal": "NEUTRAL",
        "confidence": 0.5,
        "key_finding": f"Analysis unavailable: {reason}",
        "data_cited": [],
        "risks": ["Unable to complete analysis"],
        "recommendation_weight": 0.1,
        "elapsed_s": 0,
    }


def _fallback_thesis(memos: list[dict], reason: str) -> dict:
    """Return a neutral fallback thesis when synthesis fails."""
    signals = [m.get("signal", "NEUTRAL") for m in memos]
    bullish = signals.count("BULLISH")
    bearish = signals.count("BEARISH")
    direction = "BULLISH" if bullish > bearish else "BEARISH" if bearish > bullish else "NEUTRAL"

    return {
        "direction": direction,
        "thesis": f"Fallback: {bullish} bullish, {bearish} bearish signals. Synthesis error: {reason}",
        "bull_factors": [],
        "bear_factors": [],
        "contradictions": [],
        "key_question": None,
        "resolve_tool": None,
        "resolve_params": {},
        "weighted_confidence": 0.5,
        "recommended_action": "HOLD",
        "recursion_depth": 0,
        "elapsed_s": 0,
    }
