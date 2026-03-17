"""Application configuration — environment variables and defaults.

All LLM settings live HERE. Change them once, affects everything.
Persistent LLM settings are stored in user_config/llm_config.json.
"""

import contextlib
import json
import os
from pathlib import Path
from typing import Any


class Settings:
    """Central configuration pulled from environment with safe defaults."""

    # Paths
    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    DATA_DIR: Path = BASE_DIR / "data"
    CACHE_DIR: Path = DATA_DIR / "cache"
    REPORTS_DIR: Path = DATA_DIR / "reports"
    LOGS_DIR: Path = BASE_DIR / "logs"
    PROMPTS_DIR: Path = Path(__file__).resolve().parent / "prompts"
    USER_CONFIG_DIR: Path = Path(__file__).resolve().parent / "user_config"

    # Database
    DB_PROFILE: str = os.getenv("DB_PROFILE", "main")
    _db_path_override: Path | None = None  # Set by tests to redirect DB

    @property
    def DB_PATH(self) -> Path:
        """Compute DB path from profile: main → trading_bot.duckdb, test → trading_bot_test.duckdb.

        If _db_path_override is set (e.g. by conftest.py), that takes priority.
        """
        if self._db_path_override is not None:
            return self._db_path_override
        if self.DB_PROFILE == "test":
            return self.DATA_DIR / "trading_bot_test.duckdb"
        return self.DATA_DIR / "trading_bot.duckdb"

    @DB_PATH.setter
    def DB_PATH(self, value: Path) -> None:
        """Allow direct override of DB_PATH (used by test conftest)."""
        self._db_path_override = value

    # ── LLM Provider ───────────────────────────────────────────────
    # Provider selection: "prism" (Ollama via Prism gateway) or "vllm"
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "prism")

    # Prism AI Gateway (centralized LLM proxy)
    PRISM_URL: str = os.getenv("PRISM_URL", "http://localhost:3020")
    PRISM_SECRET: str = os.getenv("PRISM_SECRET", "banana")
    PRISM_PROJECT: str = os.getenv("PRISM_PROJECT", "lazy-trading-bot")

    # Ollama direct URL (used only for model warm-up and VRAM estimation)
    OLLAMA_URL: str = os.getenv("OLLAMA_URL", "http://localhost:11434")

    # vLLM (OpenAI-compatible API on remote GPU, e.g. Jetson Orin)
    VLLM_URL: str = os.getenv("VLLM_URL", "http://10.0.0.30:8000")

    # Model name (e.g. "gemma3:27b" for Ollama)
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gemma3:27b")

    # Hard cap on context size to prevent OOM / timeout on large models.
    # No model will ever load above this, regardless of DB or config.
    MAX_CONTEXT_SIZE: int = 32768

    LLM_CONTEXT_SIZE: int = min(
        int(os.getenv("LLM_CONTEXT_SIZE", "8192")),
        MAX_CONTEXT_SIZE,
    )
    LLM_CALL_TIMEOUT_SECONDS: int = int(
        os.getenv("LLM_CALL_TIMEOUT_SECONDS", "180")
    )
    # Idle timeout for streaming: abort if no tokens arrive for this many seconds.
    # Only applies during active streaming — tokens flowing = never timeout.
    LLM_IDLE_TIMEOUT_SECONDS: int = int(
        os.getenv("LLM_IDLE_TIMEOUT_SECONDS", "120")
    )
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.3"))
    LLM_DISCOVERY_TEMPERATURE: float = float(
        os.getenv("LLM_DISCOVERY_TEMPERATURE", "0.6")
    )
    LLM_TRADING_TEMPERATURE: float = float(os.getenv("LLM_TRADING_TEMPERATURE", "0.3"))
    LLM_TOP_P: float = float(os.getenv("LLM_TOP_P", "1.0"))
    LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "0"))
    LLM_EVAL_BATCH_SIZE: int = int(os.getenv("LLM_EVAL_BATCH_SIZE", "512"))
    LLM_FLASH_ATTENTION: bool = (
        os.getenv("LLM_FLASH_ATTENTION", "true").lower() == "true"
    )
    LLM_NUM_EXPERTS: int = int(os.getenv("LLM_NUM_EXPERTS", "0"))
    LLM_GPU_OFFLOAD: bool = os.getenv("LLM_GPU_OFFLOAD", "true").lower() == "true"

    # Template injection: ephemeral wrapper models with correct chat templates
    TEMPLATE_INJECTION_ENABLED: bool = (
        os.getenv("TEMPLATE_INJECTION_ENABLED", "true").lower() == "true"
    )
    # "missing_only" = inject only when template is missing/broken
    # "always"       = always create ephemeral model with our template
    # "never"        = disable template injection entirely
    TEMPLATE_INJECTION_MODE: str = os.getenv(
        "TEMPLATE_INJECTION_MODE", "missing_only",
    )

    # Persistent VRAM measurement cache.
    # Key = model name, value = {"ctx": int, "size_vram": int, "kv_rate": float}
    # Persisted to llm_config.json so it survives server restarts.
    LLM_VRAM_MEASUREMENTS: dict = {}

    # Total system GPU memory in GB (0 = auto-detect via nvidia-smi).
    # Override for unified-memory systems like Jetson if auto-detect fails.
    SYSTEM_TOTAL_VRAM_GB: int = int(os.getenv("SYSTEM_TOTAL_VRAM_GB", "0"))

    # ── RAG (Retrieval-Augmented Generation) ────────────────────
    RAG_EMBEDDING_MODEL: str = "nomic-embed-text:latest"
    RAG_ENABLED: bool = True
    RAG_TOP_K: int = 5
    RAG_MAX_CHARS: int = 3000

    # SEC EDGAR API — required User-Agent header
    SEC_USER_AGENT: str = os.getenv(
        "SEC_USER_AGENT",
        "LazyTradingBot/1.0 (contact@example.com)",
    )

    # ── Data Collection Limits ──────────────────────────────────
    # How many items to fetch per source per ticker during collection.
    # Lower these for faster debugging, raise for production.
    YOUTUBE_MAX_VIDEOS: int = int(os.getenv("YOUTUBE_MAX_VIDEOS", "3"))
    REDDIT_MAX_POSTS_PER_SUB: int = int(os.getenv("REDDIT_MAX_POSTS_PER_SUB", "3"))
    NEWS_FETCH_LIMIT: int = int(os.getenv("NEWS_FETCH_LIMIT", "3"))
    SEC_13F_MAX_FILERS: int = int(os.getenv("SEC_13F_MAX_FILERS", "3"))

    @property
    def LLM_BASE_URL(self) -> str:
        """Computed: returns the Prism gateway URL for LLM calls."""
        return self.PRISM_URL.rstrip("/")

    # Server
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))

    # Feature flags
    MOCK_DATA: bool = os.getenv("MOCK_DATA", "false").lower() == "true"
    USE_NEW_PIPELINE: bool = os.getenv("USE_NEW_PIPELINE", "true").lower() == "true"
    DRY_RUN_TRADES: bool = os.getenv("DRY_RUN_TRADES", "false").lower() == "true"

    # Cross-Bot Audit: randomly select a different bot to audit after each run
    CROSS_AUDIT_ENABLED: bool = os.getenv("CROSS_AUDIT_ENABLED", "true").lower() == "true"

    # ── LLM Config JSON path ──────────────────────────────────────
    LLM_CONFIG_PATH: Path = (
        Path(__file__).resolve().parent / "user_config" / "llm_config.json"
    )

    def __init__(self) -> None:
        """Ensure runtime directories exist and load persisted LLM config."""
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        self.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        self.load_llm_config()

    # ── Persistent LLM configuration ──────────────────────────────

    def load_llm_config(self) -> None:
        """Load LLM settings from llm_config.json, overriding env-var defaults."""
        if not self.LLM_CONFIG_PATH.exists():
            return
        try:
            data = json.loads(self.LLM_CONFIG_PATH.read_text(encoding="utf-8"))
            self._apply_llm_config(data)
        except (json.JSONDecodeError, OSError):
            pass  # Corrupted file — fall back to defaults

    def _apply_llm_config(self, data: dict[str, Any]) -> None:
        """Apply a config dict to the running settings instance."""
        if "llm_provider" in data:
            self.LLM_PROVIDER = str(data["llm_provider"])
        if "prism_url" in data:
            self.PRISM_URL = str(data["prism_url"])
        if "prism_secret" in data:
            self.PRISM_SECRET = str(data["prism_secret"])
        if "prism_project" in data:
            self.PRISM_PROJECT = str(data["prism_project"])
        if "ollama_url" in data:
            self.OLLAMA_URL = str(data["ollama_url"])
        if "vllm_url" in data:
            self.VLLM_URL = str(data["vllm_url"])
        if "model" in data:
            self.LLM_MODEL = str(data["model"])
        if "context_size" in data:
            self.LLM_CONTEXT_SIZE = min(
                int(data["context_size"]), self.MAX_CONTEXT_SIZE,
            )
        if "temperature" in data:
            self.LLM_TEMPERATURE = float(data["temperature"])
        if "discovery_temperature" in data:
            self.LLM_DISCOVERY_TEMPERATURE = float(data["discovery_temperature"])
        if "trading_temperature" in data:
            self.LLM_TRADING_TEMPERATURE = float(data["trading_temperature"])
        if "top_p" in data:
            self.LLM_TOP_P = float(data["top_p"])
        if "max_tokens" in data:
            self.LLM_MAX_TOKENS = int(data["max_tokens"])
        if "eval_batch_size" in data:
            self.LLM_EVAL_BATCH_SIZE = int(data["eval_batch_size"])
        if "flash_attention" in data:
            self.LLM_FLASH_ATTENTION = bool(data["flash_attention"])
        if "num_experts" in data:
            self.LLM_NUM_EXPERTS = int(data["num_experts"])
        if "gpu_offload" in data:
            self.LLM_GPU_OFFLOAD = bool(data["gpu_offload"])
        if "template_injection_enabled" in data:
            self.TEMPLATE_INJECTION_ENABLED = bool(data["template_injection_enabled"])
        if "template_injection_mode" in data:
            self.TEMPLATE_INJECTION_MODE = str(data["template_injection_mode"])
        if "system_total_vram_gb" in data:
            self.SYSTEM_TOTAL_VRAM_GB = int(data["system_total_vram_gb"])
        if "vram_measurements" in data and isinstance(
            data["vram_measurements"], dict,
        ):
            self.LLM_VRAM_MEASUREMENTS = data["vram_measurements"]
        # RAG settings
        if "embedding_model" in data:
            self.RAG_EMBEDDING_MODEL = str(data["embedding_model"])
        if "rag_enabled" in data:
            self.RAG_ENABLED = bool(data["rag_enabled"])
        if "rag_top_k" in data:
            self.RAG_TOP_K = int(data["rag_top_k"])
        if "rag_max_chars" in data:
            self.RAG_MAX_CHARS = int(data["rag_max_chars"])
        # Data collection limits
        if "youtube_max_videos" in data:
            self.YOUTUBE_MAX_VIDEOS = int(data["youtube_max_videos"])
        if "reddit_max_posts_per_sub" in data:
            self.REDDIT_MAX_POSTS_PER_SUB = int(data["reddit_max_posts_per_sub"])
        if "news_fetch_limit" in data:
            self.NEWS_FETCH_LIMIT = int(data["news_fetch_limit"])
        if "sec_13f_max_filers" in data:
            self.SEC_13F_MAX_FILERS = int(data["sec_13f_max_filers"])
        if "db_profile" in data:
            self.DB_PROFILE = str(data["db_profile"])

    def update_llm_config(self, data: dict[str, Any]) -> dict[str, Any]:
        """Write new LLM settings to disk and hot-patch the running singleton.

        Returns the saved config dict.
        """
        # Merge with existing file (so partial updates work)
        existing: dict[str, Any] = {}
        if self.LLM_CONFIG_PATH.exists():
            with contextlib.suppress(json.JSONDecodeError, OSError):
                existing = json.loads(self.LLM_CONFIG_PATH.read_text(encoding="utf-8"))

        merged = {**existing, **data}
        self.LLM_CONFIG_PATH.write_text(
            json.dumps(merged, indent=4) + "\n", encoding="utf-8"
        )

        # Hot-patch the running singleton with ONLY the new keys.
        # We must NOT re-apply the full merged dict because the file
        # may contain stale values (e.g. an old model name) that would
        # overwrite a runtime hot-patch made by the run-all bot loop.
        self._apply_llm_config(data)
        return merged

    def get_llm_config(self) -> dict[str, Any]:
        """Return the current LLM configuration as a dict."""
        cfg: dict[str, Any] = {
            "llm_provider": self.LLM_PROVIDER,
            "prism_url": self.PRISM_URL,
            "prism_secret": self.PRISM_SECRET,
            "prism_project": self.PRISM_PROJECT,
            "ollama_url": self.OLLAMA_URL,
            "vllm_url": self.VLLM_URL,
            "model": self.LLM_MODEL,
            "context_size": self.LLM_CONTEXT_SIZE,
            "temperature": self.LLM_TEMPERATURE,
            "discovery_temperature": self.LLM_DISCOVERY_TEMPERATURE,
            "trading_temperature": self.LLM_TRADING_TEMPERATURE,
            "top_p": self.LLM_TOP_P,
            "max_tokens": self.LLM_MAX_TOKENS,
            "eval_batch_size": self.LLM_EVAL_BATCH_SIZE,
            "flash_attention": self.LLM_FLASH_ATTENTION,
            "num_experts": self.LLM_NUM_EXPERTS,
            "gpu_offload": self.LLM_GPU_OFFLOAD,
            "template_injection_enabled": self.TEMPLATE_INJECTION_ENABLED,
            "template_injection_mode": self.TEMPLATE_INJECTION_MODE,
            "system_total_vram_gb": self.SYSTEM_TOTAL_VRAM_GB,
            # RAG settings
            "embedding_model": self.RAG_EMBEDDING_MODEL,
            "rag_enabled": self.RAG_ENABLED,
            "rag_top_k": self.RAG_TOP_K,
            "rag_max_chars": self.RAG_MAX_CHARS,
            # Data collection limits
            "youtube_max_videos": self.YOUTUBE_MAX_VIDEOS,
            "reddit_max_posts_per_sub": self.REDDIT_MAX_POSTS_PER_SUB,
            "news_fetch_limit": self.NEWS_FETCH_LIMIT,
            "sec_13f_max_filers": self.SEC_13F_MAX_FILERS,
            # Database profile
            "db_profile": self.DB_PROFILE,
        }
        return cfg


settings = Settings()
