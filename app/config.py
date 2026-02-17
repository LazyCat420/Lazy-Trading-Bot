"""Application configuration — environment variables and defaults.

All LLM provider URLs live HERE. Change them once, affects everything.
Persistent LLM settings are stored in user_config/llm_config.json.
"""

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

    # ── LLM Provider URLs ──────────────────────────────────────────
    # Defaults (overridden by llm_config.json if present, then by env vars)
    OLLAMA_URL: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
    LMSTUDIO_URL: str = os.getenv("LMSTUDIO_URL", "http://localhost:1234")

    # Which provider to use: "ollama" | "lmstudio"
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "ollama")

    # Model name (provider-specific, e.g. "gemma3:27b" for Ollama)
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gemma3:27b")
    LLM_CONTEXT_SIZE: int = int(os.getenv("LLM_CONTEXT_SIZE", "8192"))
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.3"))

    # OpenAI / LM Studio API key (LM Studio usually doesn't need one)
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

    @property
    def LLM_BASE_URL(self) -> str:
        """Computed: returns the active provider URL based on LLM_PROVIDER."""
        if self.LLM_PROVIDER == "lmstudio":
            return self.LMSTUDIO_URL.rstrip("/")
        return self.OLLAMA_URL.rstrip("/")

    # Server
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))

    # Feature flags
    MOCK_DATA: bool = os.getenv("MOCK_DATA", "false").lower() == "true"

    # ── LLM Config JSON path ──────────────────────────────────────
    LLM_CONFIG_PATH: Path = Path(__file__).resolve().parent / "user_config" / "llm_config.json"

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
        if "provider" in data:
            self.LLM_PROVIDER = str(data["provider"])
        if "ollama_url" in data:
            self.OLLAMA_URL = str(data["ollama_url"])
        if "lmstudio_url" in data:
            self.LMSTUDIO_URL = str(data["lmstudio_url"])
        if "model" in data:
            self.LLM_MODEL = str(data["model"])
        if "context_size" in data:
            self.LLM_CONTEXT_SIZE = int(data["context_size"])
        if "temperature" in data:
            self.LLM_TEMPERATURE = float(data["temperature"])

    def update_llm_config(self, data: dict[str, Any]) -> dict[str, Any]:
        """Write new LLM settings to disk and hot-patch the running singleton.

        Returns the saved config dict.
        """
        # Merge with existing file (so partial updates work)
        existing: dict[str, Any] = {}
        if self.LLM_CONFIG_PATH.exists():
            try:
                existing = json.loads(
                    self.LLM_CONFIG_PATH.read_text(encoding="utf-8")
                )
            except (json.JSONDecodeError, OSError):
                pass

        merged = {**existing, **data}
        self.LLM_CONFIG_PATH.write_text(
            json.dumps(merged, indent=4) + "\n", encoding="utf-8"
        )

        # Hot-patch the running singleton
        self._apply_llm_config(merged)
        return merged

    def get_llm_config(self) -> dict[str, Any]:
        """Return the current LLM configuration as a dict."""
        return {
            "provider": self.LLM_PROVIDER,
            "ollama_url": self.OLLAMA_URL,
            "lmstudio_url": self.LMSTUDIO_URL,
            "model": self.LLM_MODEL,
            "context_size": self.LLM_CONTEXT_SIZE,
            "temperature": self.LLM_TEMPERATURE,
        }


settings = Settings()
