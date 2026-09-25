"""
llm/nemotron_client.py

Thin wrapper around Nebius Token Factory's OpenAI-compatible API,
implementing the same llm/provider.py LLMProvider interface as
llm/gemini_client.py. Swap the active provider via the LLM_PROVIDER
env var (see llm/provider.py's get_provider()) — no other code needs
to change.

Nebius Token Factory endpoint reference (verified via Nebius/NVIDIA docs):
- base_url: https://api.tokenfactory.nebius.com/v1/
- Auth: Bearer token via the standard OpenAI client, api_key = NEBIUS_API_KEY
- Fully OpenAI-compatible /chat/completions endpoint

IMPORTANT GOTCHA (real, documented upstream):
Nemotron models are REASONING models by default. Left at default settings,
a call can return an EMPTY `content` field with the actual answer sitting
in `reasoning_content` instead — because the reasoning trace consumes the
token budget before the final answer is generated. For Sahaara's live,
low-latency conversational turns this is a real failure mode, not a
theoretical one. This client:
  1. Explicitly disables thinking mode by default (chat_template_kwargs:
     {"enable_thinking": False}) for routine conversational turns, since
     the whole pitch (see Sahaara.txt section 1) depends on fast, natural
     turn-taking, not a visible reasoning delay.
  2. Falls back to reading `reasoning_content` if `content` comes back
     empty anyway, so a misconfigured call degrades gracefully instead
     of silently returning nothing to the voice pipeline.

Model IDs below were verified live against this project's Nebius account
via `GET /v1/models` (see AGENTS.md/chat history for the exact curl used).
If you're setting this up on a different Nebius account, re-check with:
    curl -s https://api.tokenfactory.nebius.com/v1/models \
      -H "Authorization: Bearer $NEBIUS_API_KEY" | python3 -m json.tool | grep -i nemotron
"""

import os
from typing import Optional

from openai import OpenAI

from llm.provider import CompletionResult, ProviderError

BASE_URL = "https://api.tokenfactory.nebius.com/v1/"

# Model tiers per docs/architecture.md. Verified live against this project's
# Nebius account via GET /v1/models — update if your account's catalog differs.
MODEL_NANO = os.environ.get("SAHAARA_MODEL_NANO", "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B")
MODEL_ULTRA = os.environ.get("SAHAARA_MODEL_ULTRA", "nvidia/Nemotron-3-Ultra-550b-a55b")
MODEL_LIGHTNING = os.environ.get("SAHAARA_MODEL_LIGHTNING", "nvidia/Nemotron-3_5-Lightning")

DEFAULT_MAX_TOKENS = 300  # short spoken-style replies, not essays
DEFAULT_TEMPERATURE = 0.7


class NemotronClientError(ProviderError):
    """Raised for auth/config/response problems specific to Nemotron/Nebius."""


class NemotronClient:
    """Implements the LLMProvider interface using Nebius Token Factory.
    One instance can be reused across turns/sessions — it's stateless
    beyond holding the configured HTTP client.
    """

    def __init__(self, api_key: Optional[str] = None, base_url: str = BASE_URL):
        resolved_key = api_key or os.environ.get("NEBIUS_API_KEY")
        if not resolved_key:
            raise NemotronClientError(
                "No Nebius API key found. Set NEBIUS_API_KEY in your .env, "
                "or pass api_key= explicitly. See .env.example."
            )
        self._client = OpenAI(api_key=resolved_key, base_url=base_url)

    def complete(
        self,
        system: str,
        user: str,
        model: str = MODEL_NANO,
        enable_thinking: bool = False,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> CompletionResult:
        """Single-turn completion. `system` and `user` map directly onto
        response_policy.PromptBundle.system / .user — see
        complete_from_prompt_bundle() for the direct adapter.
        """
        try:
            response = self._client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
                extra_body={"chat_template_kwargs": {"enable_thinking": enable_thinking}},
            )
        except Exception as e:  # OpenAI SDK raises its own exception hierarchy
            raise NemotronClientError(f"Nebius Token Factory request failed: {e}") from e

        choice = response.choices[0]
        content = (choice.message.content or "").strip()
        used_fallback = False

        if not content:
            # The known gotcha: reasoning trace ate the budget, or thinking
            # mode wasn't actually disabled server-side. Fall back rather
            # than returning silence to the voice pipeline.
            reasoning = getattr(choice.message, "reasoning_content", None)
            if reasoning:
                content = reasoning.strip()
                used_fallback = True

        if not content:
            raise NemotronClientError(
                f"Empty response from {model} — both content and reasoning_content "
                "were empty. Check max_tokens (reasoning traces can exhaust a small "
                "budget) and enable_thinking setting."
            )

        return CompletionResult(
            text=content,
            model=model,
            used_reasoning_fallback=used_fallback,
            raw_finish_reason=getattr(choice, "finish_reason", None),
        )

    def complete_from_prompt_bundle(self, bundle, model: str = MODEL_NANO, **kwargs) -> CompletionResult:
        """Adapter for llm/response_policy.py's PromptBundle."""
        return self.complete(system=bundle.system, user=bundle.user, model=model, **kwargs)

    def judge_escalation(self, system: str, user: str) -> CompletionResult:
        """Convenience wrapper for the Ultra tier judgment calls described
        in docs/architecture.md ("is this distress pattern worth
        escalating"). Thinking mode is left ON here — these are exactly
        the harder, non-latency-critical calls reasoning mode is for.
        """
        return self.complete(system=system, user=user, model=MODEL_ULTRA, enable_thinking=True)


if __name__ == "__main__":
    # Manual smoke test: python -m llm.nemotron_client
    # Requires NEBIUS_API_KEY and real Nebius network access (not
    # available from every sandbox).
    client = NemotronClient()
    result = client.complete(
        system="You are a warm, concise assistant. Reply in one short sentence.",
        user="Say hello.",
    )
    print(f"Model: {result.model}")
    print(f"Used reasoning fallback: {result.used_reasoning_fallback}")
    print(f"Reply: {result.text}")