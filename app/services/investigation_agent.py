"""Investigation Agent — ReAct-style iterative research loop.

Takes a Seed from SignalRanker and runs a multi-iteration investigation:
  1. Start with the seed signal + suggested tools
  2. Call 2-3 tools, feed results to LLM
  3. LLM writes a finding, updates hypothesis, picks next tools
  4. Repeat until sufficient_data=true or max iterations reached
  5. Synthesize all findings into a structured memo

This replaces the old "dump everything at once" approach with a
focused, iterative research process where the LLM builds evidence
incrementally — exactly like a human analyst would.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from app.services.investigation_prompts import (
    INVESTIGATION_PROMPT,
    INVESTIGATION_SYNTHESIS_PROMPT,
)
from app.services.llm_service import LLMService
from app.services.research_tools import TOOL_REGISTRY
from app.services.signal_ranker import Seed
from app.utils.logger import logger

_llm = LLMService()

# Constraints
MAX_ITERATIONS = 5
MAX_TOOLS_PER_ITERATION = 3


@dataclass
class Finding:
    """A single finding from one iteration of investigation."""
    iteration: int
    finding: str
    hypothesis: str
    confidence: float
    evidence: list[str] = field(default_factory=list)
    connections: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    tool_results_summary: str = ""


@dataclass
class InvestigationResult:
    """Complete result of a seed investigation."""
    seed: Seed
    findings: list[Finding] = field(default_factory=list)
    memo: dict = field(default_factory=dict)
    total_tool_calls: int = 0
    total_llm_calls: int = 0
    elapsed_s: float = 0.0


class InvestigationAgent:
    """Run a ReAct-style investigation loop for a single seed.

    Usage:
        agent = InvestigationAgent()
        result = await agent.investigate(seed, symbol)
        # result.memo is a structured analysis memo dict
    """

    async def investigate(
        self,
        seed: Seed,
        symbol: str,
    ) -> InvestigationResult:
        """Run the full investigation loop for a seed."""
        t0 = time.perf_counter()
        result = InvestigationResult(seed=seed)

        used_tools: set[str] = set()
        findings: list[Finding] = []
        all_evidence: list[str] = []
        all_connections: list[str] = []

        # Start with the seed's suggested tools
        next_tools = [
            {"tool": t, "params": {"ticker": symbol}, "reason": "seed suggestion"}
            for t in seed.suggested_tools[:MAX_TOOLS_PER_ITERATION]
        ]

        logger.info(
            "[InvestigationAgent] Starting investigation: %s for %s (seed score=%.2f)",
            seed.category, symbol, seed.score,
        )

        for iteration in range(1, MAX_ITERATIONS + 1):
            if not next_tools:
                logger.info(
                    "[InvestigationAgent] No more tools to call — stopping at iteration %d",
                    iteration,
                )
                break

            # ── 1. Call tools ────────────────────────────────
            tool_results: dict[str, Any] = {}
            tools_called: list[str] = []

            for tool_req in next_tools[:MAX_TOOLS_PER_ITERATION]:
                tool_name = tool_req.get("tool", "")
                params = tool_req.get("params", {})

                if tool_name not in TOOL_REGISTRY:
                    logger.warning(
                        "[InvestigationAgent] Unknown tool %s — skipping",
                        tool_name,
                    )
                    continue

                if tool_name in used_tools:
                    logger.debug(
                        "[InvestigationAgent] Tool %s already used — skipping",
                        tool_name,
                    )
                    continue

                try:
                    data = await TOOL_REGISTRY[tool_name](params)
                    tool_results[tool_name] = data
                    tools_called.append(tool_name)
                    used_tools.add(tool_name)
                    result.total_tool_calls += 1

                    logger.info(
                        "[InvestigationAgent] iter=%d tool=%s → %d chars",
                        iteration, tool_name, len(json.dumps(data, default=str)),
                    )
                except Exception as exc:
                    logger.warning(
                        "[InvestigationAgent] Tool %s failed: %s",
                        tool_name, exc,
                    )
                    tool_results[tool_name] = {"error": str(exc)}

            if not tool_results:
                logger.info("[InvestigationAgent] No tools returned data — stopping")
                break

            # ── 2. Format tool results for LLM ──────────────
            tool_text_parts = []
            for name, data in tool_results.items():
                serialized = json.dumps(data, indent=2, default=str)
                # Cap each tool result to prevent context blowup
                if len(serialized) > 2000:
                    serialized = serialized[:2000] + "\n[...truncated]"
                tool_text_parts.append(f"### {name}\n```json\n{serialized}\n```")
            tool_text = "\n\n".join(tool_text_parts)

            # ── 3. Build LLM prompt ─────────────────────────
            prev_findings_text = "None yet." if not findings else "\n".join(
                f"Iteration {f.iteration}: {f.finding} (confidence={f.confidence:.2f})"
                for f in findings
            )

            prompt = INVESTIGATION_PROMPT.format(
                symbol=symbol,
                seed_summary=seed.summary,
                seed_category=seed.category,
                seed_evidence=json.dumps(seed.raw_evidence, default=str),
                previous_findings=prev_findings_text,
                tool_results=tool_text,
                used_tools=", ".join(sorted(used_tools)),
            )

            # ── 4. Call LLM ─────────────────────────────────
            try:
                raw = await _llm.chat(
                    messages=[
                        {"role": "system", "content": prompt},
                        {
                            "role": "user",
                            "content": (
                                f"Analyze the new data for {symbol}. "
                                f"This is iteration {iteration} of your investigation "
                                f"into: {seed.category}."
                            ),
                        },
                    ],
                    response_format="json",
                    temperature=0.15,
                    audit_ticker=symbol,
                    audit_step=f"investigate_{seed.category}_iter{iteration}",
                )
                result.total_llm_calls += 1
            except Exception as exc:
                logger.warning(
                    "[InvestigationAgent] LLM call failed at iteration %d: %s",
                    iteration, exc,
                )
                break

            # ── 5. Parse LLM response ───────────────────────
            cleaned = LLMService.clean_json_response(raw)
            try:
                parsed = json.loads(cleaned)
            except (json.JSONDecodeError, ValueError):
                try:
                    import json_repair
                    parsed = json_repair.loads(cleaned)
                except Exception:
                    logger.warning(
                        "[InvestigationAgent] Failed to parse iteration %d response",
                        iteration,
                    )
                    parsed = {}

            if not isinstance(parsed, dict):
                logger.warning(
                    "[InvestigationAgent] Iteration %d returned %s, skipping",
                    iteration, type(parsed).__name__,
                )
                break

            # ── 6. Extract finding ──────────────────────────
            finding = Finding(
                iteration=iteration,
                finding=parsed.get("finding", "No finding produced"),
                hypothesis=parsed.get("hypothesis", "Unknown"),
                confidence=parsed.get("confidence", 0.5),
                evidence=parsed.get("evidence_collected", []),
                connections=parsed.get("connections_found", []),
                tools_used=tools_called,
                tool_results_summary=tool_text[:500],
            )
            findings.append(finding)
            all_evidence.extend(finding.evidence)
            all_connections.extend(finding.connections)

            logger.info(
                "[InvestigationAgent] iter=%d finding: %s (conf=%.2f)",
                iteration, finding.finding[:100], finding.confidence,
            )

            # ── 7. Check if done ────────────────────────────
            if parsed.get("sufficient_data", False):
                logger.info(
                    "[InvestigationAgent] Sufficient data after %d iterations",
                    iteration,
                )
                break

            # ── 8. Get next tools ───────────────────────────
            raw_next = parsed.get("next_tools", [])
            if not isinstance(raw_next, list):
                break

            # Filter out already-used tools
            next_tools = [
                t for t in raw_next
                if isinstance(t, dict)
                and t.get("tool") not in used_tools
                and t.get("tool") in TOOL_REGISTRY
            ]

        # ── Synthesize all findings ─────────────────────────
        result.findings = findings

        if findings:
            memo = await self._synthesize(seed, symbol, findings, all_evidence, all_connections)
            result.memo = memo
        else:
            result.memo = {
                "signal": "NEUTRAL",
                "confidence": 0.3,
                "key_finding": f"Investigation for {seed.category} produced no findings",
                "evidence_chain": [],
                "risks": ["No data collected"],
                "data_cited": [],
                "recommendation_weight": 0.1,
                "category": seed.category,
                "verified_claims": [],
            }

        result.elapsed_s = round(time.perf_counter() - t0, 2)

        logger.info(
            "[InvestigationAgent] Complete: %s for %s — %d findings, "
            "%d tool calls, %d LLM calls in %.1fs",
            seed.category, symbol,
            len(findings), result.total_tool_calls,
            result.total_llm_calls, result.elapsed_s,
        )

        return result

    async def _synthesize(
        self,
        seed: Seed,
        symbol: str,
        findings: list[Finding],
        all_evidence: list[str],
        all_connections: list[str],
    ) -> dict:
        """Synthesize all findings from the investigation into a memo."""
        findings_text = "\n\n".join(
            f"### Iteration {f.iteration}\n"
            f"**Finding:** {f.finding}\n"
            f"**Hypothesis:** {f.hypothesis}\n"
            f"**Confidence:** {f.confidence:.2f}\n"
            f"**Tools used:** {', '.join(f.tools_used)}\n"
            f"**Evidence:** {', '.join(f.evidence)}"
            for f in findings
        )

        evidence_text = "\n".join(f"- {e}" for e in all_evidence) or "None collected"
        connections_text = "\n".join(f"- {c}" for c in all_connections) or "None found"

        prompt = INVESTIGATION_SYNTHESIS_PROMPT.format(
            symbol=symbol,
            seed_summary=seed.summary,
            seed_category=seed.category,
            all_findings=findings_text,
            all_evidence=evidence_text,
            all_connections=connections_text,
        )

        try:
            raw = await _llm.chat(
                messages=[
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": (
                            f"Synthesize your investigation of {seed.category} for {symbol}. "
                            f"You conducted {len(findings)} iterations of research."
                        ),
                    },
                ],
                response_format="json",
                temperature=0.15,
                audit_ticker=symbol,
                audit_step=f"investigate_{seed.category}_synthesis",
            )
        except Exception as exc:
            logger.warning("[InvestigationAgent] Synthesis LLM call failed: %s", exc)
            return self._fallback_memo(seed, findings)

        cleaned = LLMService.clean_json_response(raw)
        try:
            memo = json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            try:
                import json_repair
                memo = json_repair.loads(cleaned)
            except Exception:
                return self._fallback_memo(seed, findings)

        if not isinstance(memo, dict):
            return self._fallback_memo(seed, findings)

        # Ensure required fields
        memo.setdefault("signal", "NEUTRAL")
        memo.setdefault("confidence", 0.5)
        memo.setdefault("key_finding", "No finding")
        memo.setdefault("evidence_chain", [])
        memo.setdefault("risks", [])
        memo.setdefault("data_cited", all_evidence)
        memo.setdefault("recommendation_weight", 0.5)
        memo.setdefault("category", seed.category)
        memo.setdefault("verified_claims", [])
        # Add compatibility fields for ThesisConstructor
        memo["domain"] = f"investigation_{seed.category}"
        memo["label"] = f"Investigation: {seed.category}"
        memo["reasoning_steps"] = memo.get("evidence_chain", [])

        return memo

    @staticmethod
    def _fallback_memo(seed: Seed, findings: list[Finding]) -> dict:
        """Build a fallback memo from raw findings when LLM synthesis fails."""
        best = max(findings, key=lambda f: f.confidence) if findings else None
        return {
            "signal": "NEUTRAL",
            "confidence": best.confidence if best else 0.3,
            "key_finding": best.finding if best else "Synthesis failed",
            "evidence_chain": [f.finding for f in findings],
            "risks": ["LLM synthesis failed — using raw findings"],
            "data_cited": [],
            "recommendation_weight": 0.3,
            "category": seed.category,
            "verified_claims": [],
            "domain": f"investigation_{seed.category}",
            "label": f"Investigation: {seed.category}",
            "reasoning_steps": [f.finding for f in findings],
        }
