"""
llm/fallback_provider.py

Auto-fallback LLM provider: tries primary provider first, falls back to
a safe canned response on any failure (timeout, auth error, rate limit, etc.).

This ensures the voice pipeline never hangs waiting for an unreachable
provider — it gets a safe fallback response.
"""

import logging
import os
import random
from typing import Optional

from llm.provider import CompletionResult, LLMProvider, ProviderError

logger = logging.getLogger("llm.fallback")

# Primary/secondary provider names from env (defaults: nemotron -> fallback)
PRIMARY_PROVIDER = os.environ.get("LLM_PROVIDER_PRIMARY", "nemotron").lower()
SECONDARY_PROVIDER = os.environ.get("LLM_PROVIDER_SECONDARY", "fallback").lower()


# Safe fallback responses (match SAFE_FALLBACK_RESPONSE from guardrails.py)
SAFE_RESPONSES = [
    "I'm here with you.",
    "I hear you. I'm right here.",
    "I'm listening. Go on.",
    "That sounds important. Tell me more.",
    "I'm right here with you.",
]


class FallbackProvider:
    """Wraps primary provider with automatic safe fallback on failure."""

    def __init__(
        self,
        primary_name: Optional[str] = None,
        secondary_name: Optional[str] = None,
    ):
        self.primary_name = (primary_name or PRIMARY_PROVIDER).lower()
        self.secondary_name = (secondary_name or SECONDARY_PROVIDER).lower()
        self._primary: Optional[LLMProvider] = None
        self._secondary: Optional[LLMProvider] = None

    def _get_provider(self, name: str) -> LLMProvider:
        """Lazy-load provider by name."""
        if name == "nemotron":
            from llm.nemotron_client import NemotronClient
            return NemotronClient()
        elif name == "fallback":
            # Simple fallback that always returns a safe response
            return _FallbackProvider()
        else:
            raise ProviderError(f"Unknown provider '{name}'")

    @property
    def primary(self) -> LLMProvider:
        if self._primary is None:
            self._primary = self._get_provider(self.primary_name)
        return self._primary

    @property
    def secondary(self) -> LLMProvider:
        if self._secondary is None:
            self._secondary = self._get_provider(self.secondary_name)
        return self._secondary

    def _try_provider(self, provider: LLMProvider, name: str, **kwargs) -> CompletionResult:
        """Try a provider, log result, return CompletionResult or raise ProviderError."""
        try:
            result = provider.complete(**kwargs)
            logger.info("LLM provider %s succeeded", name)
            return result
        except ProviderError:
            raise
        except Exception as e:
            logger.warning("Provider %s failed with unexpected error: %s", name, e)
            raise ProviderError(f"Provider {name} failed: {e}") from e

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 300,
        temperature: float = 0.7,
    ) -> CompletionResult:
        kwargs = dict(system=system, user=user, max_tokens=max_tokens, temperature=temperature)
        
        # Try primary
        try:
            return self._try_provider(self.primary, self.primary_name, **kwargs)
        except ProviderError as e:
            logger.warning("Primary provider %s failed: %s", self.primary_name, e)
        
        # Try secondary (fallback)
        try:
            return self._try_provider(self.secondary, self.secondary_name, **kwargs)
        except ProviderError as e:
            logger.error("Secondary provider %s also failed: %s", self.secondary_name, e)
            raise ProviderError(f"Both providers failed. Primary: {self.primary_name}, Secondary: {self.secondary_name}")

    def complete_from_prompt_bundle(self, bundle, **kwargs) -> CompletionResult:
        """Adapter for llm/response_policy.py's PromptBundle."""
        return self.complete(
            system=bundle.system,
            user=bundle.user,
            **kwargs
        )

    def judge_escalation(self, system: str, user: str) -> CompletionResult:
        """Use primary provider for escalation judgment (may need reasoning)."""
        try:
            return self.primary.judge_escalation(system, user)
        except ProviderError as e:
            logger.warning("Primary provider failed for judge_escalation: %s", e)
            return self.secondary.judge_escalation(system, user)


class _FallbackProvider:
    """Simple provider that always returns a safe canned response."""
    
    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 300,
        temperature: float = 0.7,
    ) -> CompletionResult:
        return CompletionResult(
            text=random.choice(SAFE_RESPONSES),
            model="fallback",
        )

    def complete_from_prompt_bundle(self, bundle, **kwargs) -> CompletionResult:
        # Handle both calling conventions
        if hasattr(bundle, 'system') and hasattr(bundle, 'user'):
            return self.complete(system=bundle.system, user=bundle.user, **kwargs)
        # If called with system/user kwargs directly
        return self.complete(**kwargs)
    
    def judge_escalation(self, system: str, user: str) -> CompletionResult:
        return CompletionResult(
            text="I'm not sure. Let me play it safe.",
            model="fallback",
        )