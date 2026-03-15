"""TemplateRegistry -- manages ephemeral template-injected Ollama models.

When an Ollama model is missing its chat template (or has a broken/generic
one that lacks the correct special tokens), inference silently degrades
by 10-15%.

This service:
  1. Detects missing/broken templates via POST /api/show
  2. Validates templates by checking for family-specific signature tokens
  3. Creates an ephemeral wrapper model with the correct native template
     via POST /api/create (instant -- no re-download, just a new manifest)
  4. Cleans up the wrapper via DELETE /api/delete when done

Template data is loaded from user_config/template_registry.json so users
can add new families or tweak templates without editing Python code.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from app.utils.logger import logger

# -- Constants ----------------------------------------------------------------
TEMPLATED_SUFFIX = "-templated"

_REGISTRY_PATH = (
    Path(__file__).resolve().parent.parent / "user_config" / "template_registry.json"
)

# In-memory cache of known-good templates learned from running models.
# Key = family name, value = template string.
_dynamic_cache: dict[str, str] = {}

# Track which ephemeral models we created so we can clean them up.
_active_ephemeral_models: set[str] = set()


def _load_registry() -> dict:
    """Load the family template registry from JSON."""
    if not _REGISTRY_PATH.exists():
        logger.warning(
            "[TemplateRegistry] Registry file not found: %s", _REGISTRY_PATH,
        )
        return {}
    try:
        data = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
        return data.get("families", {})
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("[TemplateRegistry] Failed to load registry: %s", exc)
        return {}


def get_family_template(family: str) -> str | None:
    """Look up the correct native template for a model family.

    Checks the JSON registry first, then the dynamic cache (templates
    learned at runtime from models that had good templates).

    Returns None if the family is unknown.
    """
    # Normalize: some Ollama models report "gemma2" or "gemma3" etc.
    # Try exact match first, then strip trailing digits.
    registry = _load_registry()

    if family in registry:
        return registry[family].get("template")

    # Try base family (e.g. "gemma3" -> "gemma")
    import re
    base_family = re.sub(r"\d+$", "", family)
    if base_family and base_family in registry:
        return registry[base_family].get("template")

    # Check dynamic cache
    if family in _dynamic_cache:
        return _dynamic_cache[family]
    if base_family and base_family in _dynamic_cache:
        return _dynamic_cache[base_family]

    return None


def get_signature_tokens(family: str) -> list[str]:
    """Get the signature tokens that MUST be present in a correct template."""
    import re
    registry = _load_registry()

    if family in registry:
        return registry[family].get("signature_tokens", [])

    base_family = re.sub(r"\d+$", "", family)
    if base_family and base_family in registry:
        return registry[base_family].get("signature_tokens", [])

    return []


def validate_template(template: str, family: str) -> bool:
    """Check if a template contains the correct signature tokens for its family.

    Returns True if the template is valid (has all required tokens).
    Returns False if:
      - Template is empty/whitespace
      - Template is missing required signature tokens
      - Family is unknown (we can't validate)
    """
    if not template or not template.strip():
        return False

    sig_tokens = get_signature_tokens(family)
    if not sig_tokens:
        # Unknown family -- we can't validate, assume it's fine
        return True

    # ALL signature tokens must be present
    for token in sig_tokens:
        if token not in template:
            logger.info(
                "[TemplateRegistry] Template for family '%s' is MISSING "
                "signature token '%s' -- template is broken",
                family, token,
            )
            return False

    return True


def cache_good_template(family: str, template: str) -> None:
    """Cache a known-good template learned from a running model.

    When we see a model that HAS a correct template, we cache it
    so future models in the same family can use it as a fallback.
    """
    if family and template and template.strip():
        _dynamic_cache[family] = template
        logger.debug(
            "[TemplateRegistry] Cached good template for family '%s' (%d chars)",
            family, len(template),
        )


def ephemeral_model_name(base_model: str) -> str:
    """Generate the name for an ephemeral templated model."""
    # Remove existing :tag if present, append our suffix
    if ":" in base_model:
        name, tag = base_model.rsplit(":", 1)
        return f"{name}{TEMPLATED_SUFFIX}:{tag}"
    return f"{base_model}{TEMPLATED_SUFFIX}"


def is_ephemeral_model(model_name: str) -> bool:
    """Check if a model name is one of our ephemeral wrappers."""
    return TEMPLATED_SUFFIX in model_name


async def create_ephemeral_model(
    base_url: str,
    base_model: str,
    template: str,
    *,
    system: str = "",
) -> str | None:
    """Create an ephemeral wrapper model with the given template injected.

    Uses POST /api/create with 'from' field -- this creates a new manifest
    pointing at the same GGUF blobs but with an overridden template layer.
    Takes less than 1 second, no re-download needed.

    Returns the ephemeral model name on success, None on failure.
    """
    base_url = base_url.rstrip("/")
    eph_name = ephemeral_model_name(base_model)

    payload: dict = {
        "model": eph_name,
        "from": base_model,
        "template": template,
        "stream": False,
    }
    if system:
        payload["system"] = system

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{base_url}/api/create",
                json=payload,
            )
            resp.raise_for_status()

            # Check for success in streamed response
            # When stream=False, we get the final status directly
            ct = resp.headers.get("content-type", "")
            data = resp.json() if ct.startswith("application/json") else {}
            status = data.get("status", "")

            # For streamed responses, read all lines and check last one
            if not status:
                text = resp.text.strip()
                for line in text.split("\n"):
                    try:
                        line_data = json.loads(line)
                        status = line_data.get("status", "")
                    except json.JSONDecodeError:
                        pass

            if status == "success" or resp.status_code == 200:
                _active_ephemeral_models.add(eph_name)
                logger.info(
                    "[TemplateRegistry] Created ephemeral model '%s' from '%s' "
                    "with injected template (%d chars)",
                    eph_name, base_model, len(template),
                )
                return eph_name

            logger.warning(
                "[TemplateRegistry] Create returned status '%s' for '%s'",
                status, eph_name,
            )
            return None

    except Exception as exc:
        logger.warning(
            "[TemplateRegistry] Failed to create ephemeral model '%s': %s",
            eph_name, exc,
        )
        return None


async def delete_ephemeral_model(base_url: str, model_name: str) -> bool:
    """Delete an ephemeral wrapper model.

    Returns True if deleted successfully (or model didn't exist).
    """
    base_url = base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.request(
                "DELETE",
                f"{base_url}/api/delete",
                json={"model": model_name},
            )
            if resp.status_code in (200, 404):
                _active_ephemeral_models.discard(model_name)
                logger.info(
                    "[TemplateRegistry] Deleted ephemeral model '%s'",
                    model_name,
                )
                return True
            logger.warning(
                "[TemplateRegistry] Delete returned %d for '%s'",
                resp.status_code, model_name,
            )
            return False
    except Exception as exc:
        logger.warning(
            "[TemplateRegistry] Failed to delete '%s': %s",
            model_name, exc,
        )
        return False


async def cleanup_all_ephemeral_models(base_url: str) -> int:
    """Delete ALL ephemeral models (call on startup/shutdown).

    Scans Ollama's model list for any model containing our suffix
    and deletes them.  Returns count of models deleted.
    """
    base_url = base_url.rstrip("/")
    deleted = 0
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{base_url}/api/tags")
            resp.raise_for_status()
            models = resp.json().get("models", [])

            for m in models:
                name = m.get("name", "")
                if TEMPLATED_SUFFIX in name:
                    ok = await delete_ephemeral_model(base_url, name)
                    if ok:
                        deleted += 1
    except Exception as exc:
        logger.warning(
            "[TemplateRegistry] Cleanup sweep failed: %s", exc,
        )

    if deleted:
        logger.info(
            "[TemplateRegistry] Cleaned up %d stale ephemeral model(s)",
            deleted,
        )
    return deleted


async def inspect_model_template(base_url: str, model: str) -> dict:
    """Query /api/show to get a model's template and family info.

    Returns dict with keys:
      - template: str (the current template, may be empty)
      - family: str (the model family, e.g. "llama", "gemma")
      - families: list[str] (all reported families)
      - is_valid: bool (whether the template has correct signature tokens)
    """
    base_url = base_url.rstrip("/")
    result = {
        "template": "",
        "family": "",
        "families": [],
        "is_valid": False,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{base_url}/api/show",
                json={"name": model},
            )
            resp.raise_for_status()
            data = resp.json()

            result["template"] = data.get("template", "")
            details = data.get("details", {})
            result["family"] = details.get("family", "")
            result["families"] = details.get("families", [])

            # Validate the template against the family
            if result["family"]:
                result["is_valid"] = validate_template(
                    result["template"], result["family"],
                )
                # If valid, cache it for future use
                if result["is_valid"] and result["template"]:
                    cache_good_template(result["family"], result["template"])

    except Exception as exc:
        logger.warning(
            "[TemplateRegistry] inspect_model_template failed for '%s': %s",
            model, exc,
        )

    return result


async def ensure_template(
    base_url: str,
    model: str,
    *,
    mode: str = "missing_only",
) -> str:
    """Main entry point: ensure a model has a correct template.

    Modes:
      - "missing_only": Only inject if template is missing or broken (default)
      - "always": Always create an ephemeral model with our template
      - "never": Skip injection entirely (passthrough)

    Returns the effective model name to use (either original or ephemeral).
    """
    if mode == "never":
        logger.debug("[TemplateRegistry] Mode=never, skipping for '%s'", model)
        return model

    # Don't re-wrap an already-ephemeral model
    if is_ephemeral_model(model):
        logger.debug(
            "[TemplateRegistry] '%s' is already ephemeral, skipping", model,
        )
        return model

    # Inspect the model's current state
    info = await inspect_model_template(base_url, model)
    family = info["family"]
    current_template = info["template"]
    is_valid = info["is_valid"]

    if not family:
        logger.info(
            "[TemplateRegistry] No family detected for '%s' -- skipping injection",
            model,
        )
        return model

    # In "missing_only" mode, skip if template is already valid
    if mode == "missing_only" and is_valid:
        logger.info(
            "[TemplateRegistry] Template for '%s' (family=%s) is VALID "
            "-- no injection needed",
            model, family,
        )
        return model

    # Look up the correct template for this family
    correct_template = get_family_template(family)
    if not correct_template:
        logger.warning(
            "[TemplateRegistry] No template available for family '%s' "
            "(model '%s') -- skipping injection",
            family, model,
        )
        return model

    # Log what we're about to do
    if not current_template or not current_template.strip():
        logger.info(
            "[TemplateRegistry] Template for '%s' (family=%s) is EMPTY "
            "-- injecting native template",
            model, family,
        )
    elif not is_valid:
        sig_tokens = get_signature_tokens(family)
        missing = [t for t in sig_tokens if t not in current_template]
        logger.info(
            "[TemplateRegistry] Template for '%s' (family=%s) is BROKEN "
            "(missing tokens: %s) -- injecting native template",
            model, family, missing,
        )
    else:
        logger.info(
            "[TemplateRegistry] Mode=always: overriding template for '%s' "
            "(family=%s)",
            model, family,
        )

    # Create the ephemeral model
    eph_name = await create_ephemeral_model(
        base_url, model, correct_template,
    )
    if eph_name:
        return eph_name

    # Failed to create -- fall back to original model
    logger.warning(
        "[TemplateRegistry] Ephemeral creation failed for '%s' -- "
        "using original model",
        model,
    )
    return model


def get_active_ephemeral_models() -> set[str]:
    """Return the set of currently active ephemeral models."""
    return _active_ephemeral_models.copy()
