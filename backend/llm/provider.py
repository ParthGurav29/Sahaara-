"""
llm/provider.py

The common interface every LLM backend must implement. main.py and the
rest of the pipeline depend on THIS interface, never on a specific
vendor SDK — that's what makes "swap Gemini for Nemotron" a one-line
change instead of a rewrite.

Current providers:
  - llm/gemini_client.py   — fallback/dev option
  - llm/nemotron_client.py — intended production model (Nebius Token Factory)
  - llm/fallback_provider.py — auto-fallback chain: Nemotron -> Gemini

Both return the same CompletionResult shape and raise ProviderError (or
a subclass) on failure, so main.py's error handling doesn't need to
know which provider is active.
"""

from dataclasses import dataclass
from typing import Optional, Protocol


class ProviderError(RuntimeError):
    """Base class for provider-specific errors (missing key, network
    failure, empty response). Catch this in main.py rather than a
    bare Exception, and catch a specific subclass if you need to
    handle one provider's failure mode differently.
    """


@dataclass
class CompletionResult:
    text: str
    model: str
    used_reasoning_fallback: bool = False  # relevant to Nemotron, always False for Gemini
    raw_finish_reason: Optional[str] = None


class LLMProvider(Protocol):
    """Structural interface — GeminiClient and NemotronClient both
    satisfy this without explicitly inheriting from it (duck typing,
    checked structurally by type checkers that support Protocol).
    """

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 300,
        temperature: float = 0.7,
    ) -> CompletionResult:
        ...

    def complete_from_prompt_bundle(self, bundle, **kwargs) -> CompletionResult:
        """Adapter for llm/response_policy.py's PromptBundle:
            bundle = build_prompt(profile, user_message, intent, ...)
            result = provider.complete_from_prompt_bundle(bundle)
        """
        ...


def get_provider(name: Optional[str] = None) -> LLMProvider:
    """Factory: returns the configured provider instance. This is the
    ONE place in the codebase that knows both vendor classes — every
    other module (main.py included) should call this instead of
    importing GeminiClient or NemotronClient directly.

    Provider selection (in order):
    1. Explicit `name` argument
    2. LLM_PROVIDER env var
    3. LLM_PROVIDER_PRIMARY + LLM_PROVIDER_SECONDARY (auto-fallback chain)
    4. Default: "nemotron" with "gemini" fallback

    If both primary and secondary are set, a FallbackProvider is returned
    that tries primary first, then secondary on any failure.
    """
    import os

    # Check if fallback chain is explicitly configured
    primary = os.environ.get("LLM_PROVIDER_PRIMARY")
    secondary = os.environ.get("LLM_PROVIDER_SECONDARY")

    if primary or secondary:
        # Auto-fallback chain configured
        from llm.fallback_provider import FallbackProvider
        return FallbackProvider(
            primary_name=primary or "nemotron",
            secondary_name=secondary or "gemini",
        )

    # Legacy single-provider mode
    resolved = (name or os.environ.get("LLM_PROVIDER") or "nemotron").lower()

    if resolved == "gemini":
        from llm.gemini_client import GeminiClient
        return GeminiClient()
    elif resolved == "nemotron":
        from llm.nemotron_client import NemotronClient
        return NemotronClient()
    else:
        raise ProviderError(
            f"Unknown LLM_PROVIDER '{resolved}'. Expected 'gemini' or 'nemotron'."
        )