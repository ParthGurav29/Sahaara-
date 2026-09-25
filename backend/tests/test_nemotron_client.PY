"""
backend/tests/test_nemotron_client.py

We have no live Nebius network access in this environment, so these
tests mock the OpenAI SDK's response shape and verify the client's
PARSING and FALLBACK logic — especially the reasoning_content fallback,
which is the real, documented gotcha with Nemotron models on Nebius
(see llm/nemotron_client.py's module docstring for the source).

Run for real against Nebius once NEBIUS_API_KEY is set:
    python -m llm.nemotron_client
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llm.nemotron_client import NemotronClient, NemotronClientError


def _fake_response(content=None, reasoning_content=None, finish_reason="stop"):
    """Builds an object shaped like the OpenAI SDK's ChatCompletion
    response, enough for our client's parsing code to walk.
    """
    message = SimpleNamespace(content=content, reasoning_content=reasoning_content)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


def _client_with_mocked_openai(fake_create_return):
    with patch("llm.nemotron_client.OpenAI") as MockOpenAI:
        mock_instance = MagicMock()
        mock_instance.chat.completions.create.return_value = fake_create_return
        MockOpenAI.return_value = mock_instance
        client = NemotronClient(api_key="fake-key-for-test")
        return client, mock_instance


def test_normal_response_uses_content_field():
    fake = _fake_response(content="Hello there, how are you?")
    client, _ = _client_with_mocked_openai(fake)

    result = client.complete(system="sys", user="hi")

    assert result.text == "Hello there, how are you?"
    assert result.used_reasoning_fallback is False


def test_empty_content_falls_back_to_reasoning_content():
    # This is the real documented gotcha: reasoning models can return
    # empty `content` with the real answer in `reasoning_content`.
    fake = _fake_response(content="", reasoning_content="Actual answer buried here.")
    client, _ = _client_with_mocked_openai(fake)

    result = client.complete(system="sys", user="hi")

    assert result.text == "Actual answer buried here."
    assert result.used_reasoning_fallback is True


def test_both_empty_raises_clear_error():
    fake = _fake_response(content="", reasoning_content=None)
    client, _ = _client_with_mocked_openai(fake)

    try:
        client.complete(system="sys", user="hi")
        assert False, "expected NemotronClientError"
    except NemotronClientError as e:
        assert "Empty response" in str(e)


def test_enable_thinking_false_is_passed_through_by_default():
    fake = _fake_response(content="ok")
    client, mock_instance = _client_with_mocked_openai(fake)

    client.complete(system="sys", user="hi")

    _, kwargs = mock_instance.chat.completions.create.call_args
    assert kwargs["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False


def test_judge_escalation_enables_thinking_and_uses_ultra_model():
    from llm.nemotron_client import MODEL_ULTRA
    fake = _fake_response(content="escalate: yellow")
    client, mock_instance = _client_with_mocked_openai(fake)

    client.judge_escalation(system="sys", user="pattern description")

    _, kwargs = mock_instance.chat.completions.create.call_args
    assert kwargs["model"] == MODEL_ULTRA
    assert kwargs["extra_body"]["chat_template_kwargs"]["enable_thinking"] is True


def test_missing_api_key_raises_clear_error():
    import os
    old_val = os.environ.pop("NEBIUS_API_KEY", None)
    try:
        try:
            NemotronClient(api_key=None)
            assert False, "expected NemotronClientError for missing key"
        except NemotronClientError as e:
            assert "No Nebius API key" in str(e)
    finally:
        if old_val is not None:
            os.environ["NEBIUS_API_KEY"] = old_val


def test_complete_from_prompt_bundle_adapts_system_and_user():
    from llm.response_policy import PromptBundle
    fake = _fake_response(content="response text")
    client, mock_instance = _client_with_mocked_openai(fake)

    bundle = PromptBundle(system="SYSTEM TEXT", user="USER TEXT")
    result = client.complete_from_prompt_bundle(bundle)

    assert result.text == "response text"
    _, kwargs = mock_instance.chat.completions.create.call_args
    assert kwargs["messages"][0] == {"role": "system", "content": "SYSTEM TEXT"}
    assert kwargs["messages"][1] == {"role": "user", "content": "USER TEXT"}


if __name__ == "__main__":
    import inspect
    current_module = sys.modules[__name__]
    test_fns = [obj for name, obj in inspect.getmembers(current_module)
                if name.startswith("test_") and callable(obj)]
    passed, failed = 0, 0
    for fn in test_fns:
        try:
            fn()
            print(f"PASS: {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL: {fn.__name__} -> {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")