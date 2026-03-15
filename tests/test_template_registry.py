"""Tests for TemplateRegistry -- ephemeral template injection system."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# Unit tests (no network needed)
# ---------------------------------------------------------------------------


class TestFamilyLookup:
    """Test that family-to-template lookups work correctly."""

    def test_known_family_returns_template(self):
        from app.services.TemplateRegistry import get_family_template

        tpl = get_family_template("llama")
        assert tpl is not None
        assert "<|start_header_id|>" in tpl
        assert "<|eot_id|>" in tpl

    def test_gemma_family_returns_template(self):
        from app.services.TemplateRegistry import get_family_template

        tpl = get_family_template("gemma")
        assert tpl is not None
        assert "<start_of_turn>" in tpl
        assert "<end_of_turn>" in tpl

    def test_qwen2_family_returns_chatml_template(self):
        from app.services.TemplateRegistry import get_family_template

        tpl = get_family_template("qwen2")
        assert tpl is not None
        assert "<|im_start|>" in tpl
        assert "<|im_end|>" in tpl

    def test_unknown_family_returns_none(self):
        from app.services.TemplateRegistry import get_family_template

        tpl = get_family_template("totally_unknown_family_xyz")
        assert tpl is None

    def test_numbered_family_falls_back_to_base(self):
        """e.g. 'gemma3' should fall back to 'gemma' if 'gemma3' isn't registered."""
        from app.services.TemplateRegistry import get_family_template

        # gemma2 is in the registry, but gemma99 is not -- should fall back to gemma
        tpl = get_family_template("gemma99")
        # Falls back to "gemma" base family
        assert tpl is not None
        assert "<start_of_turn>" in tpl


class TestSignatureTokenLookup:
    """Test signature token retrieval."""

    def test_known_family_has_signatures(self):
        from app.services.TemplateRegistry import get_signature_tokens

        tokens = get_signature_tokens("llama")
        assert len(tokens) >= 2
        assert "<|start_header_id|>" in tokens

    def test_unknown_family_returns_empty(self):
        from app.services.TemplateRegistry import get_signature_tokens

        tokens = get_signature_tokens("nonexistent_family")
        assert tokens == []


class TestTemplateValidation:
    """Test template validation via signature tokens."""

    def test_valid_llama_template(self):
        from app.services.TemplateRegistry import validate_template

        good_template = (
            "{{- if .System }}<|start_header_id|>system<|end_header_id|>"
            "{{ .System }}<|eot_id|>{{ end }}"
        )
        assert validate_template(good_template, "llama") is True

    def test_empty_template_is_invalid(self):
        from app.services.TemplateRegistry import validate_template

        assert validate_template("", "llama") is False
        assert validate_template("   ", "llama") is False

    def test_wrong_template_for_family(self):
        """A Gemma template should be invalid for the Llama family."""
        from app.services.TemplateRegistry import validate_template

        gemma_template = (
            "<start_of_turn>user {{ .Prompt }}<end_of_turn>"
            "<start_of_turn>model {{ .Response }}<end_of_turn>"
        )
        # This should fail for llama (missing <|start_header_id|>)
        assert validate_template(gemma_template, "llama") is False

    def test_generic_template_is_invalid(self):
        """A template with just {{ .Prompt }} is broken for any known family."""
        from app.services.TemplateRegistry import validate_template

        generic = "{{ .Prompt }}{{ .Response }}"
        assert validate_template(generic, "llama") is False
        assert validate_template(generic, "gemma") is False
        assert validate_template(generic, "qwen2") is False

    def test_unknown_family_assumed_valid(self):
        """For unknown families, we can't validate -- assume it's OK."""
        from app.services.TemplateRegistry import validate_template

        assert validate_template("anything goes", "unknown_xyz") is True


class TestEphemeralModelNaming:
    """Test ephemeral model name generation."""

    def test_model_with_tag(self):
        from app.services.TemplateRegistry import ephemeral_model_name

        name = ephemeral_model_name("gemma3:27b")
        assert name == "gemma3-templated:27b"

    def test_model_without_tag(self):
        from app.services.TemplateRegistry import ephemeral_model_name

        name = ephemeral_model_name("gemma3")
        assert name == "gemma3-templated"

    def test_is_ephemeral(self):
        from app.services.TemplateRegistry import is_ephemeral_model

        assert is_ephemeral_model("gemma3-templated:27b") is True
        assert is_ephemeral_model("gemma3:27b") is False


class TestDynamicCache:
    """Test the dynamic template caching."""

    def test_cache_and_retrieve(self):
        from app.services.TemplateRegistry import (
            _dynamic_cache,
            cache_good_template,
            get_family_template,
        )

        # Cache a template for a fictional family
        cache_good_template("test_family_abc", "my_good_template_here")
        assert "test_family_abc" in _dynamic_cache

        # get_family_template should find it
        tpl = get_family_template("test_family_abc")
        assert tpl == "my_good_template_here"

        # Clean up
        del _dynamic_cache["test_family_abc"]


class TestRegistryJSONIntegrity:
    """Verify the template_registry.json file is valid."""

    def test_json_file_exists_and_parses(self):
        registry_path = (
            Path(__file__).resolve().parent.parent
            / "app" / "user_config" / "template_registry.json"
        )
        assert registry_path.exists(), f"Missing: {registry_path}"
        data = json.loads(registry_path.read_text(encoding="utf-8"))
        assert "families" in data

    def test_all_families_have_required_fields(self):
        registry_path = (
            Path(__file__).resolve().parent.parent
            / "app" / "user_config" / "template_registry.json"
        )
        data = json.loads(registry_path.read_text(encoding="utf-8"))
        for family_name, entry in data["families"].items():
            assert "template" in entry, f"{family_name} missing 'template'"
            assert "signature_tokens" in entry, (
                f"{family_name} missing 'signature_tokens'"
            )
            assert len(entry["signature_tokens"]) >= 2, (
                f"{family_name} needs at least 2 signature tokens"
            )
            assert len(entry["template"]) > 10, (
                f"{family_name} template too short"
            )

    def test_each_template_contains_its_signature_tokens(self):
        """Every template should contain all of its own signature tokens."""
        registry_path = (
            Path(__file__).resolve().parent.parent
            / "app" / "user_config" / "template_registry.json"
        )
        data = json.loads(registry_path.read_text(encoding="utf-8"))
        for family_name, entry in data["families"].items():
            tpl = entry["template"]
            for token in entry["signature_tokens"]:
                assert token in tpl, (
                    f"Family '{family_name}': template is missing its own "
                    f"signature token '{token}'"
                )

    def test_minimum_expected_families(self):
        """Ensure we have the core families registered."""
        registry_path = (
            Path(__file__).resolve().parent.parent
            / "app" / "user_config" / "template_registry.json"
        )
        data = json.loads(registry_path.read_text(encoding="utf-8"))
        families = set(data["families"].keys())
        expected = {"llama", "gemma", "qwen2", "phi3", "mistral"}
        missing = expected - families
        assert not missing, f"Missing expected families: {missing}"


# ---------------------------------------------------------------------------
# Integration tests (mocked HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestEnsureTemplate:
    """Test the main ensure_template flow with mocked HTTP."""

    async def test_never_mode_returns_original(self):
        from app.services.TemplateRegistry import ensure_template

        result = await ensure_template(
            "http://localhost:11434", "gemma3:27b", mode="never",
        )
        assert result == "gemma3:27b"

    async def test_already_ephemeral_skips(self):
        from app.services.TemplateRegistry import ensure_template

        result = await ensure_template(
            "http://localhost:11434", "gemma3-templated:27b", mode="missing_only",
        )
        assert result == "gemma3-templated:27b"
