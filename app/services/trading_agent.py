"""Trading Agent — V3 seed-driven multi-layered brain loop → TradeAction JSON.

Architecture (Brain Loop V3 — seed-driven investigation):
  Phase 0: SignalRanker — pure-Python anomaly scanner → top-N research seeds
  Phase 1: InvestigationAgent — ReAct loop per seed → evidence-backed memos
           Each seed: iterate (pick 2-3 tools → call → write finding → repeat)
  Phase 2: ThesisConstructor — inductive synthesis with lemma-aware contradiction detection
  Phase 3: DecisionAgent — thesis + portfolio → TradeAction with adversarial self-check
           + ConsistencyValidator (post-LLM rule checker)
           + ContradictionPass (proof by contradiction)
"""

from __future__ import annotations

from app.services.unified_logger import track_class_telemetry, track_telemetry
import json
import time
from datetime import datetime

from app.models.trade_action import TradeAction
from app.services.llm_service import LLMService
from app.utils.logger import logger

_llm = LLMService()


def _log_tool_usage(
    symbol: str,
    bot_id: str,
    tools_used: list[str],
    turns_taken: int,
) -> None:
    """Log tool usage to pipeline_events for diagnostics."""
    try:
        from app.services.event_logger import log_event
        event_type = "tool_usage" if tools_used else "no_tools_used"
        log_event(
            phase="trading",
            event_type=f"trading_agent:{event_type}",
            detail=f"Tool usage: {len(tools_used)} tools",
            ticker=symbol.upper(),
            metadata={
                "symbol": symbol,
                "tools_used": tools_used,
                "tools_count": len(tools_used),
                "turns_taken": turns_taken,
            },
            bot_id=bot_id,
        )
    except Exception as exc:
        logger.debug("[TradingAgent] Failed to log tool event: %s", exc)





@track_class_telemetry
class TradingAgent:
    """Seed-driven multi-layered brain loop per ticker → TradeAction.

    Phase 0: SignalRanker — pure Python anomaly scan → top 3 seeds
    Phase 1: InvestigationAgent — ReAct loop per seed (2-3 tools/iteration)
    Phase 2: ThesisConstructor — synthesize + resolve contradictions
    Phase 3: DecisionAgent — thesis → final TradeAction
    """

    async def decide(
        self,
        context: dict,
        bot_id: str = "default",
    ) -> tuple[TradeAction, str, dict]:
        """Run the 3-phase brain loop to produce a trading decision.

        Falls back to legacy multi-turn loop if brain loop fails.
        """
        symbol = context.get("symbol", "UNKNOWN")
        _decide_t0 = time.time()

        logger.info(
            "[BrainLoop] ═══ Starting 3-phase analysis for %s ═══",
            symbol,
        )

        try:
            # ── Phase 0 + 1: Seed-driven investigation ────────
            from app.services.brain_loop import (
                ConsistencyValidator,
                ContradictionPass,
                DecisionAgent,
                LemmaCache,
                ThesisConstructor,
                extract_domain_data,
                validate_data_coverage,
                validate_data_integrity,
            )
            from app.services.signal_ranker import SignalRanker
            from app.services.investigation_agent import InvestigationAgent

            logger.info("[BrainLoop] Phase 0: Extracting domain data for %s", symbol)
            domain_data = extract_domain_data(context, symbol)
            domains_found = [d for d, v in domain_data.items() if v.strip()]
            logger.info(
                "[BrainLoop] Phase 0: %d domains have data: %s",
                len(domains_found), ", ".join(domains_found),
            )

            # ── Data integrity audit ──────────────────────────
            integrity = validate_data_integrity(symbol)
            if integrity["issue_count"] > 0:
                logger.warning(
                    "[BrainLoop] ⚠️  Data integrity: %d issues (%d errors, %d warnings)",
                    integrity["issue_count"], integrity["errors"], integrity["warnings"],
                )
                for issue in integrity["issues"]:
                    logger.warning(
                        "[BrainLoop]   %s [%s] %s",
                        "🔴" if issue["severity"] == "ERROR" else "🟡",
                        issue["type"],
                        issue["message"],
                    )
            else:
                logger.info("[BrainLoop] ✅ Data integrity check passed — no issues")

            # Log domain coverage
            for domain, cov in integrity.get("coverage", {}).items():
                tables_str = " | ".join(
                    f"{t['table']}={t['status']}" for t in cov["tables"]
                )
                logger.info(
                    "[BrainLoop]   📦 %s: %s — %s",
                    domain, cov["status"], tables_str,
                )

            # Log data freshness
            coverage_report = validate_data_coverage(symbol)
            for src, info in coverage_report.items():
                logger.info(
                    "[BrainLoop]   🕐 %s: %s rows=%d newest=%s",
                    info["status"], src, info["rows"],
                    info["newest"][:10] if info.get("newest") else "N/A",
                )

            # ── Phase 0: Signal Ranking (no LLM) ─────────────
            ranker = SignalRanker()
            seeds = ranker.rank(domain_data, symbol, max_seeds=3)
            logger.info(
                "[BrainLoop] Phase 0: %d seeds generated: %s",
                len(seeds),
                [(s.category, round(s.score, 2)) for s in seeds],
            )

            # ── Phase 1: Investigate each seed (ReAct loop) ──
            investigator = InvestigationAgent()
            lemma_cache = LemmaCache()
            memos: list[dict] = []
            total_tool_calls = 0
            total_llm_calls = 0

            for i, seed in enumerate(seeds, 1):
                logger.info(
                    "[BrainLoop] Phase 1: Investigating seed %d/%d: %s (score=%.2f)",
                    i, len(seeds), seed.category, seed.score,
                )
                inv_result = await investigator.investigate(seed, symbol)
                total_tool_calls += inv_result.total_tool_calls
                total_llm_calls += inv_result.total_llm_calls

                memo = inv_result.memo
                memos.append(memo)

                # Extract lemmas from investigation memo
                lemma_cache.extract_from_memo(memo)

                logger.info(
                    "[BrainLoop]   🔬 %s: %s (conf=%.2f) — %s [%.1fs, %d tools, %d LLM calls]",
                    memo.get("label", "?"),
                    memo.get("signal", "?"),
                    memo.get("confidence", 0),
                    memo.get("key_finding", "?")[:80],
                    inv_result.elapsed_s,
                    inv_result.total_tool_calls,
                    inv_result.total_llm_calls,
                )

            logger.info(
                "[BrainLoop] Phase 1 complete: %d memos, %d total tool calls, "
                "%d total LLM calls, %d lemmas",
                len(memos), total_tool_calls, total_llm_calls, lemma_cache.count,
            )

            # ── Phase 2: Synthesize thesis (inductive) ────────
            portfolio = {
                "cash": context.get("portfolio_cash", 0),
                "total_value": context.get("portfolio_value", 0),
                "positions": context.get("all_positions", []),
            }
            portfolio_text = (
                f"Cash: ${portfolio['cash']:,.0f}\n"
                f"Total Value: ${portfolio['total_value']:,.0f}\n"
                f"Positions: {len(portfolio['positions'])}"
            )

            logger.info("[BrainLoop] Phase 2: Synthesizing thesis for %s...", symbol)
            thesis = await ThesisConstructor.synthesize(
                memos, portfolio_text, symbol,
                lemma_cache=lemma_cache,
            )
            logger.info(
                "[BrainLoop]   🧠 Thesis: %s (conf=%.2f, action=%s, "
                "contradictions=%d, recursions=%d, lemmas=%d) [%.1fs]",
                thesis.get("direction", "?"),
                thesis.get("weighted_confidence", 0),
                thesis.get("recommended_action", "?"),
                len(thesis.get("contradictions", [])),
                thesis.get("recursion_depth", 0),
                thesis.get("lemma_count", 0),
                thesis.get("elapsed_s", 0),
            )
            if thesis.get("contradictions"):
                for c in thesis["contradictions"]:
                    logger.info("[BrainLoop]   ⚠️  Contradiction: %s", c)
            if thesis.get("reasoning_steps"):
                for s in thesis["reasoning_steps"][:3]:
                    logger.info("[BrainLoop]   🔗 %s", str(s)[:120])

            # ── Phase 3: Final decision (with lemmas) ─────────
            logger.info("[BrainLoop] Phase 3: Making final decision for %s...", symbol)
            raw_text, decision = await DecisionAgent.decide(
                thesis, symbol, portfolio,
                lemma_cache=lemma_cache,
            )
            logger.info(
                "[BrainLoop]   📊 Decision: %s %s (conf=%.2f) [%.1fs]",
                decision.get("action", "?"),
                symbol,
                decision.get("confidence", 0),
                decision.get("decision_elapsed_s", 0),
            )

            # Log reasoning steps from decision
            for s in decision.get("reasoning_steps", [])[:3]:
                logger.info("[BrainLoop]   🔗 Decision step: %s", str(s)[:120])

            # ── Technique 5: Consistency Validator ─────────────
            logger.info("[BrainLoop] Running consistency validator...")
            validation = ConsistencyValidator.validate_decision(decision, thesis)
            if validation["corrections"]:
                for corr in validation["corrections"]:
                    logger.warning(
                        "[BrainLoop]   🔧 Auto-corrected: %s %s → %s (%s)",
                        corr["field"], corr["from"], corr["to"], corr["reason"],
                    )
            if validation["issues"]:
                for issue in validation["issues"]:
                    icon = "🔴" if issue["severity"] == "ERROR" else "🟡" if issue["severity"] == "WARNING" else "ℹ️"
                    logger.info(
                        "[BrainLoop]   %s [%s] %s",
                        icon, issue["rule"], issue["message"],
                    )
            logger.info(
                "[BrainLoop]   ✅ Consistency: valid=%s, %d issues (%d errors, %d warnings)",
                validation["valid"], validation["issue_count"],
                validation["error_count"], validation["warning_count"],
            )

            # ── Technique 2: Contradiction Pass ────────────────
            logger.info("[BrainLoop] Running adversarial contradiction pass...")
            contra_result = await ContradictionPass.run(
                decision, thesis, symbol,
                lemma_cache=lemma_cache,
            )
            logger.info(
                "[BrainLoop]   🛡️  Contradiction pass: %s [%.1fs]",
                contra_result.get("verdict", "?"),
                contra_result.get("elapsed_s", 0),
            )
            if contra_result.get("attack_vectors"):
                for av in contra_result["attack_vectors"]:
                    verdict_icon = "✅" if av.get("verdict") == "REFUTED" else "⚠️"
                    logger.info(
                        "[BrainLoop]     %s Attack: %s → %s",
                        verdict_icon,
                        av.get("assumption", "?")[:80],
                        av.get("verdict", "?"),
                    )

            # If contradiction pass REVISED, update decision
            if contra_result.get("verdict") == "REVISED":
                old_action = decision["action"]
                revised_action = contra_result.get("revised_action", decision["action"])
                revised_conf = contra_result.get("revised_confidence", decision["confidence"])
                if revised_action != old_action:
                    logger.warning(
                        "[BrainLoop]   ⚡ Contradiction pass REVISED: %s → %s (conf %.2f → %.2f)",
                        old_action, revised_action, decision["confidence"], revised_conf,
                    )
                    decision["action"] = revised_action
                    decision["confidence"] = revised_conf
                    decision["revised_by_contradiction"] = True

            # ── Build TradeAction from decision ───────────────
            action = TradeAction(
                action=decision.get("action", "HOLD").upper(),
                symbol=symbol,
                confidence=float(decision.get("confidence", 0.5)),
                rationale=decision.get("rationale", ""),
                risk_notes=decision.get("risk_notes", ""),
                risk_level=decision.get("risk_level", "MED"),
                time_horizon=decision.get("time_horizon", "SWING"),
                bot_id=bot_id,
            )

            total_elapsed = round(time.time() - _decide_t0, 2)
            logger.info(
                "[BrainLoop] ═══ Complete for %s: %s (%.2f conf) in %.1fs ═══",
                symbol, action.action, action.confidence, total_elapsed,
            )

            # Log tool usage for diagnostics
            _log_tool_usage(symbol, bot_id, ["brain_loop"], 3)

            llm_meta = {
                "system_prompt": "brain_loop_3_phase_proof_logic",
                "user_prompt": f"3-phase proof-logic analysis for {symbol}",
                "raw_output": raw_text[:2000] if raw_text else "",
                "turns": 3,
                "tools_used": ["brain_loop"],
                "duration_s": total_elapsed,
                "model": _llm.model,
                "brain_loop": {
                    "memos": memos,
                    "thesis": thesis,
                    "decision": decision,
                    "domains_found": domains_found,
                    "data_integrity": integrity,
                    "data_coverage": coverage_report,
                    "investigation": {
                        "seeds": [(s.category, round(s.score, 2)) for s in seeds],
                        "total_tool_calls": total_tool_calls,
                        "total_llm_calls": total_llm_calls,
                    },
                    "proof_logic": {
                        "lemma_count": lemma_cache.count,
                        "lemma_conflicts": len(lemma_cache.conflicts),
                        "consistency_validation": validation,
                        "contradiction_pass": contra_result,
                    },
                },
            }

            return action, raw_text or "", llm_meta

        except Exception as exc:
            logger.error(
                "[BrainLoop] Brain loop V2 failed for %s: %s",
                symbol, exc,
            )
            import traceback
            logger.error("[BrainLoop] Traceback: %s", traceback.format_exc())

            # Safe HOLD fallback — no legacy code, just a safe default
            total_elapsed = round(time.time() - _decide_t0, 2)
            fallback = TradeAction(
                action="HOLD",
                symbol=symbol,
                confidence=0.20,
                rationale=f"Brain loop V2 error: {exc}",
                risk_notes="Pipeline failure — defaulting to safe HOLD",
                risk_level="HIGH",
                time_horizon="SWING",
                bot_id=bot_id,
            )
            llm_meta = {
                "system_prompt": "brain_loop_v2_error",
                "user_prompt": f"Failed analysis for {symbol}",
                "raw_output": "",
                "turns": 0,
                "tools_used": [],
                "duration_s": total_elapsed,
                "model": _llm.model,
                "error": str(exc),
            }
            return fallback, "", llm_meta
