from typing import Protocol


class LLMClient(Protocol):
    async def generate(
        self,
        messages: list[dict[str, str]],
    ) -> str:
        ...