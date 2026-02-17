"""Base agent — abstract class all specialist agents inherit from."""

from __future__ import annotations

import json
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.config import settings
from app.services.llm_service import LLMService
from app.utils.logger import logger

T = TypeVar("T", bound=BaseModel)


class BaseAgent:
    """Abstract base for all analysis agents.

    Lifecycle:
        1. Load system prompt from .md file
        2. Inject ticker data as context
        3. Call LLM with system + user prompt
        4. Parse and validate JSON response against output schema
        5. Return typed report (with structural rescue if needed)
    """
    # Subclasses set these as class attributes
    prompt_file: str = ""
    output_model: type[T] = BaseModel  # type: ignore[assignment]

    def __init__(
        self,
        prompt_file: str | None = None,
        output_model: type[T] | None = None,
    ) -> None:
        pf = prompt_file or self.__class__.prompt_file
        om = output_model or self.__class__.output_model
        if not pf:
            raise ValueError(f"{self.__class__.__name__} has no prompt_file set")
        self.prompt_path = settings.PROMPTS_DIR / pf
        self.output_model = om
        self.llm = LLMService()
        self._system_prompt: str | None = None

    @property
    def system_prompt(self) -> str:
        """Lazy-load the system prompt from disk."""
        if self._system_prompt is None:
            if not self.prompt_path.exists():
                raise FileNotFoundError(
                    f"Prompt file not found: {self.prompt_path}"
                )
            self._system_prompt = self.prompt_path.read_text(encoding="utf-8")
        return self._system_prompt

    def _build_system_prompt(self, ticker: str) -> str:
        """Inject the ticker and output schema into the system prompt template."""
        schema_json = json.dumps(
            self.output_model.model_json_schema(), indent=2
        )
        prompt = self.system_prompt
        prompt = prompt.replace("{ticker}", ticker)
        prompt = prompt.replace("{schema_json}", schema_json)
        return prompt

    def format_context(self, ticker: str, context: dict) -> str:
        """Subclasses override to format their specific data into a user message.

        Args:
            ticker: The stock ticker being analyzed.
            context: Dict of collected data relevant to this agent.

        Returns:
            Formatted string to use as the user message in the LLM call.
        """
        raise NotImplementedError("Subclasses must implement format_context()")

    # ------------------------------------------------------------------
    # Structural rescue helpers
    # ------------------------------------------------------------------

    def _get_required_keys(self) -> set[str]:
        """Return the set of required top-level keys for the output model."""
        schema = self.output_model.model_json_schema()
        return set(schema.get("required", []))

    def _diagnose_response(self, cleaned: str) -> dict[str, Any] | None:
        """Parse *cleaned* JSON and check if it matches the expected schema.

        Returns the parsed dict if it is valid JSON but **missing** required
        keys (i.e. wrong structure). Returns None if parsing fails outright
        or if the dict already has the required keys (validation should work).
        """
        try:
            data = json.loads(cleaned)
        except (json.JSONDecodeError, TypeError):
            return None

        if not isinstance(data, dict):
            return None

        required = self._get_required_keys()
        present = set(data.keys())
        if required and not required.issubset(present):
            return data  # valid JSON but wrong shape
        return None

    def _try_unwrap_nested(self, data: dict[str, Any]) -> dict[str, Any] | None:
        """Recursively search a nested dict for a sub-dict matching the schema.

        LLMs sometimes wrap their JSON output in arbitrary keys like
        ``{"Microsoft Earnings Analysis": {"Video 1": {<valid>}}}``.
        This method walks the tree and returns the first sub-dict whose
        keys are a superset of the required schema keys.

        Returns None if no matching sub-dict is found.
        """
        required = self._get_required_keys()
        if not required:
            return None

        def _search(node: Any, depth: int = 0) -> dict[str, Any] | None:
            if depth > 5 or not isinstance(node, dict):
                return None
            # Check if this dict itself has the required keys
            if required.issubset(node.keys()):
                return node
            # Recurse into dict-valued children
            for value in node.values():
                if isinstance(value, dict):
                    result = _search(value, depth + 1)
                    if result is not None:
                        return result
            return None

        return _search(data)

    def _build_rescue_prompt(
        self, ticker: str, bad_response: dict[str, Any], user_context: str,
    ) -> str:
        """Build a focused retry prompt that tells the LLM exactly what it did wrong."""
        schema_json = json.dumps(
            self.output_model.model_json_schema(), indent=2
        )
        # Truncate the bad response to avoid wasting tokens
        bad_preview = json.dumps(bad_response, indent=2)[:800]

        return (
            f"Your previous response had the WRONG structure. "
            f"You returned keys like {list(bad_response.keys())}, but the "
            f"required output must be a FLAT JSON object with these fields:\n\n"
            f"```json\n{schema_json}\n```\n\n"
            f"Your bad response (truncated):\n```json\n{bad_preview}\n```\n\n"
            f"CRITICAL RULES:\n"
            f"- Output ONLY a single flat JSON object with ALL required fields.\n"
            f"- The 'ticker' field MUST be \"{ticker}\".\n"
            f"- Do NOT nest your analysis inside 'Summary' or any wrapper key.\n"
            f"- Do NOT include markdown, commentary, or text outside the JSON.\n\n"
            f"Analyze this data and return the correct JSON:\n\n{user_context}"
        )

    def _build_fallback_report(self, ticker: str) -> T | None:
        """Build a safe default report when all LLM attempts fail.

        Only works for models that have 'ticker' as a required field.
        Returns None if the model can't be constructed with defaults.
        """
        schema = self.output_model.model_json_schema()
        required = set(schema.get("required", []))
        props = schema.get("properties", {})

        # Build a minimal dict with sensible defaults for known field types
        defaults: dict[str, Any] = {}

        for key in required:
            prop = props.get(key, {})

            if key == "ticker":
                defaults[key] = ticker
            elif key == "reasoning":
                defaults[key] = "Fallback: LLM returned an invalid response structure."
            elif key == "confidence":
                defaults[key] = 0.0
            elif key == "sentiment_score":
                defaults[key] = 0.0
            elif key == "signal":
                defaults[key] = "HOLD"
            elif key == "overall_sentiment":
                defaults[key] = "NEUTRAL"
            elif "enum" in prop:
                # Pick the most neutral / middle enum value, or first
                enums = prop["enum"]
                for neutral_candidate in ["NEUTRAL", "SIDEWAYS", "MODERATE", "FAIR", "HOLD"]:
                    if neutral_candidate in enums:
                        defaults[key] = neutral_candidate
                        break
                else:
                    defaults[key] = enums[0] if enums else ""
            elif prop.get("type") == "string":
                defaults[key] = ""
            elif prop.get("type") == "number":
                defaults[key] = 0.0
            elif prop.get("type") == "integer":
                defaults[key] = 0
            elif prop.get("type") == "boolean":
                defaults[key] = False
            elif prop.get("type") == "array":
                defaults[key] = []
            else:
                defaults[key] = ""

        try:
            return self.output_model.model_validate(defaults)
        except (ValidationError, Exception) as exc:
            logger.error("Fallback report construction failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Main analysis flow
    # ------------------------------------------------------------------

    async def analyze(self, ticker: str, context: dict) -> T:
        """Run the agent's analysis — the main entry point.

        Args:
            ticker: The stock ticker to analyze.
            context: Dict of collected data (varies by agent).

        Returns:
            A validated Pydantic model of the agent's report.
        """
        agent_name = self.__class__.__name__
        logger.info("[%s] Starting analysis for %s", agent_name, ticker)

        try:
            return await self._analyze_inner(ticker, context)
        except Exception as outer_err:
            # Catch ALL errors (LLM timeouts, connection errors, parse failures)
            # and attempt fallback so the pipeline never crashes on a single agent.
            logger.error(
                "[%s] Analysis failed for %s (%s: %s) — attempting fallback",
                agent_name, ticker, type(outer_err).__name__, outer_err,
            )
            fallback = self._build_fallback_report(ticker)
            if fallback is not None:
                logger.info(
                    "[%s] Fallback report generated for %s (NEUTRAL/HOLD defaults)",
                    agent_name, ticker,
                )
                return fallback
            # Re-raise with useful context if fallback also fails
            raise RuntimeError(
                f"[{agent_name}] All analysis attempts and fallback failed for {ticker}: "
                f"{type(outer_err).__name__}: {outer_err}"
            ) from outer_err

    async def _analyze_inner(self, ticker: str, context: dict) -> T:
        """Core analysis logic — called by analyze() within error boundary."""
        agent_name = self.__class__.__name__

        system = self._build_system_prompt(ticker)
        user = self.format_context(ticker, context)

        # --- Attempt 1: standard call ---
        raw_response = await self.llm.chat(
            system=system,
            user=user,
            response_format="json",
        )

        cleaned = LLMService.clean_json_response(raw_response)
        try:
            report = self.output_model.model_validate_json(cleaned)
            logger.info("[%s] Analysis complete for %s", agent_name, ticker)
            return report
        except (ValidationError, json.JSONDecodeError) as first_err:
            logger.warning(
                "[%s] First parse failed for %s: %s",
                agent_name, ticker, first_err,
            )

        # --- Attempt 1b: try to unwrap nested JSON programmatically ---
        bad_dict = self._diagnose_response(cleaned)
        if bad_dict is not None:
            unwrapped = self._try_unwrap_nested(bad_dict)
            if unwrapped is not None:
                logger.info(
                    "[%s] Found valid sub-dict in nested response for %s — "
                    "unwrapped keys: %s",
                    agent_name, ticker, list(unwrapped.keys()),
                )
                try:
                    report = self.output_model.model_validate(unwrapped)
                    logger.info(
                        "[%s] Analysis complete for %s (auto-unwrap succeeded)",
                        agent_name, ticker,
                    )
                    return report
                except (ValidationError, Exception) as unwrap_err:
                    logger.warning(
                        "[%s] Auto-unwrap validation failed for %s: %s",
                        agent_name, ticker, unwrap_err,
                    )

            # Unwrap didn't work — send structural rescue prompt
            logger.warning(
                "[%s] LLM returned wrong structure for %s — keys: %s. "
                "Sending structural rescue prompt.",
                agent_name, ticker, list(bad_dict.keys()),
            )
            rescue_user = self._build_rescue_prompt(ticker, bad_dict, user)
        else:
            # Generic JSON retry (original Attempt 2 logic)
            schema_json = json.dumps(
                self.output_model.model_json_schema(), indent=2
            )
            rescue_user = (
                f"Your previous response was not valid JSON. "
                f"You MUST respond with ONLY a valid JSON object matching this schema:\n"
                f"```json\n{schema_json}\n```\n\n"
                f"Do NOT include any markdown, commentary, or text outside the JSON. "
                f"Here is the data to analyze again:\n\n{user}"
            )

        # --- Attempt 2: structural rescue / stronger instruction ---
        raw_response = await self.llm.chat(
            system=system,
            user=rescue_user,
            response_format="json",
        )

        cleaned = LLMService.clean_json_response(raw_response)
        try:
            report = self.output_model.model_validate_json(cleaned)
            logger.info("[%s] Analysis complete for %s (rescue succeeded)", agent_name, ticker)
            return report
        except (ValidationError, json.JSONDecodeError) as rescue_err:
            logger.error(
                "[%s] Rescue parse also failed for %s: %s\nRaw: %s",
                agent_name, ticker, rescue_err, cleaned[:500],
            )

        # --- Attempt 3: fallback default report ---
        logger.warning(
            "[%s] All LLM attempts failed for %s — building fallback report",
            agent_name, ticker,
        )
        fallback = self._build_fallback_report(ticker)
        if fallback is not None:
            logger.info(
                "[%s] Fallback report generated for %s (NEUTRAL/HOLD defaults)",
                agent_name, ticker,
            )
            return fallback

        # If even fallback fails, raise with context
        raise RuntimeError(
            f"[{agent_name}] All LLM attempts and fallback construction failed for {ticker}"
        )

