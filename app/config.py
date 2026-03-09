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
    DB_PATH: Path = DATA_DIR / "trading_bot.duckdb"

    # ── LLM Provider (Ollama only) ─────────────────────────────────
    OLLAMA_URL: str = os.getenv("OLLAMA_URL", "http://localhost:11434")

    # Model name (e.g. "gemma3:27b" for Ollama)
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gemma3:27b")
    LLM_CONTEXT_SIZE: int = int(os.getenv("LLM_CONTEXT_SIZE", "8192"))
    LLM_CALL_TIMEOUT_SECONDS: int = int(
        os.getenv("LLM_CALL_TIMEOUT_SECONDS", "180")
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

    @property
    def LLM_BASE_URL(self) -> str:
        """Computed: returns the Ollama URL."""
        return self.OLLAMA_URL.rstrip("/")

    # Server
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))

    # Feature flags
    MOCK_DATA: bool = os.getenv("MOCK_DATA", "false").lower() == "true"
    USE_NEW_PIPELINE: bool = os.getenv("USE_NEW_PIPELINE", "true").lower() == "true"
    DRY_RUN_TRADES: bool = os.getenv("DRY_RUN_TRADES", "false").lower() == "true"

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
        if "ollama_url" in data:
            self.OLLAMA_URL = str(data["ollama_url"])
        if "model" in data:
            self.LLM_MODEL = str(data["model"])
        if "context_size" in data:
            self.LLM_CONTEXT_SIZE = int(data["context_size"])
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
            "ollama_url": self.OLLAMA_URL,
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
            "system_total_vram_gb": self.SYSTEM_TOTAL_VRAM_GB,
            # RAG settings
            "embedding_model": self.RAG_EMBEDDING_MODEL,
            "rag_enabled": self.RAG_ENABLED,
            "rag_top_k": self.RAG_TOP_K,
            "rag_max_chars": self.RAG_MAX_CHARS,
        }
        # Attach VRAM measurement data for the current model (if cached)
        vram = self.LLM_VRAM_MEASUREMENTS.get(self.LLM_MODEL)
        if vram:
            proven_ctx = vram.get("proven_max_ctx", 0)
            cfg["last_measured_ctx"] = proven_ctx
            cfg["model_stats"] = {
                "model_name": self.LLM_MODEL,
                "max_proven_ctx": proven_ctx,
                # vram_usage_gb could be calculated here or omitted if frontend calculates
            }
        return cfg


settings = Settings()
